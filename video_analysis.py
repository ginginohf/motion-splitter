"""Motion analysis using OpenCV; no GUI dependencies."""
import cv2
import math
import numpy as np
from collections import deque
from external_tools import check_cancel

def parse_resolution(opt, orig_w, orig_h):
    """Fit inside the selected box, preserving aspect ratio; never upscale."""
    if orig_w <= 0 or orig_h <= 0:
        raise ValueError("Некорректный размер исходного кадра.")
    if opt.strip().lower() == "оригинал":
        return orig_w, orig_h
    try:
        width, height = map(int, opt.lower().split("x"))
        if width <= 0 or height <= 0:
            raise ValueError()
    except (ValueError, AttributeError):
        return orig_w, orig_h
    ratio = min(1.0, width / orig_w, height / orig_h)
    return max(1, round(orig_w * ratio)), max(1, round(orig_h * ratio))


class TimeSmoother:
    """Duration-weighted mean of scores over a trailing window in seconds."""
    def __init__(self, seconds):
        self.seconds = seconds
        self.samples = deque()

    def update(self, start, end, score):
        if self.seconds == 0:
            return score
        self.samples.append((start, end, score))
        cutoff = end - self.seconds
        while self.samples and self.samples[0][1] <= cutoff:
            self.samples.popleft()
        weighted = duration = 0.0
        for begin, finish, value in self.samples:
            weight = max(0.0, finish - max(begin, cutoff))
            weighted += weight * value
            duration += weight
        return weighted / duration if duration > 0 else score


def get_video_duration(video_path):
    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            return 0
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        return frame_count / fps
    finally:
        cap.release()


def calculate_auto_sensitivity(video_path, analysis_res, skip_frames, progress_cb=None, stop_event=None):
    check_cancel(stop_event)
    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            raise IOError(f"Не удалось открыть видео: {video_path}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

        orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        width, height = parse_resolution(analysis_res, orig_w, orig_h)
        num_pixels = width * height

        motion_scores = []
        frame_idx = 0
        skip = max(0, int(skip_frames))
        sample_size = min(300, total_frames)

        start_frame = int(total_frames * 0.05)
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

        ret, prev = cap.read()
        if not ret:
            return 1.0

        if (prev.shape[1], prev.shape[0]) != (width, height):
            prev = cv2.resize(prev, (width, height), interpolation=cv2.INTER_LINEAR)
        prev_gray = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY)
        prev_gray = cv2.GaussianBlur(prev_gray, (21,21), 0)

        while len(motion_scores) < sample_size:
            check_cancel(stop_event)
            ret, frame = cap.read()
            check_cancel(stop_event)
            if not ret:
                break
            frame_idx += 1
            if skip > 0 and (frame_idx % (skip+1)) != 0:
                if progress_cb: progress_cb(frame_idx, total_frames)
                continue
            if (frame.shape[1], frame.shape[0]) != (width, height):
                frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_LINEAR)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (21,21), 0)
            delta = cv2.absdiff(prev_gray, gray)
            score = delta.sum() / num_pixels
            motion_scores.append(score)
            prev_gray = gray
            if progress_cb: progress_cb(frame_idx, total_frames)

        if not motion_scores:
            return 1.0

        avg_score = np.mean(motion_scores)
        max_score = np.max(motion_scores)
        sorted_scores = sorted(motion_scores)
        median_score = sorted_scores[len(sorted_scores)//2]
        base_sensitivity = median_score * 1.5
        if max_score > median_score * 3:
            base_sensitivity = max_score * 0.3
        base_sensitivity = max(0.5, min(5.0, base_sensitivity))
        return base_sensitivity
    finally:
        cap.release()


def merge_close_segments(segments, merge_gap):
    """Return sorted, valid intervals; coalesce overlaps even with a zero gap."""
    if not math.isfinite(merge_gap) or merge_gap < 0:
        raise ValueError("Промежуток объединения должен быть конечным и неотрицательным.")
    ordered = []
    for start, end in segments:
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
            raise ValueError(f"Некорректный интервал: {(start, end)}")
        ordered.append((start, end))
    merged = []
    for start, end in sorted(ordered):
        if merged and start - merged[-1][1] <= merge_gap + 1e-9:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


class FrameClock:
    """Use successful-read timestamps; fall back to nominal frame steps."""
    def __init__(self, fps):
        self.step = 1.0 / fps
        self.last = None

    def read(self, timestamp_ms):
        timestamp = timestamp_ms / 1000.0
        if self.last is None:
            timestamp = timestamp if math.isfinite(timestamp) and timestamp >= 0 else 0.0
        elif not math.isfinite(timestamp) or timestamp <= self.last:
            timestamp = self.last + self.step
        self.last = timestamp
        return timestamp


class MotionSegments:
    """Confirm pauses separately from the interval's last positive sample.

    Minimum length applies before merging, to the span of an individual event.
    Short internal pauses are part of that span; confirming trailing silence is not.
    """
    def __init__(self, min_pause, min_motion):
        self.min_pause = min_pause
        self.min_motion = min_motion
        self.start = None
        self.last_motion_end = None
        self.quiet_start = None
        self.last_moving = False
        self.segments = []

    def close(self):
        if self.start is not None:
            if self.last_motion_end - self.start + 1e-9 >= self.min_motion:
                self.segments.append((self.start, self.last_motion_end))
        self.start = self.last_motion_end = self.quiet_start = None

    def sample(self, timestamp, frame_end, moving):
        self.last_moving = moving
        if moving:
            if self.start is None:
                self.start = timestamp
            self.last_motion_end = frame_end
            self.quiet_start = None
        elif self.start is not None:
            if self.quiet_start is None:
                self.quiet_start = timestamp
            if frame_end - self.quiet_start + 1e-9 >= self.min_pause:
                self.close()

    def finish(self, decoded_end, merge_gap, pre_motion=0, post_motion=0):
        # Hold a positive final sample through the unanalysed skipped tail only.
        if self.start is not None and self.last_moving:
            self.last_motion_end = decoded_end
        self.close()
        clipped = [(max(0.0, start - pre_motion), min(end + post_motion, decoded_end))
                   for start, end in self.segments
                   if min(end + post_motion, decoded_end) > max(0.0, start - pre_motion)]
        return merge_close_segments(clipped, merge_gap)


def analyze_video(video_path, sensitivity, min_pause, min_motion, analysis_res, skip_frames,
                 progress_cb=None, stop_event=None, preview_cb=None, merge_gap=0,
                 smoothing_sec=0.2, pre_motion_sec=0, post_motion_sec=0):
    for name, value in (("sensitivity", sensitivity), ("min_pause", min_pause),
                        ("min_motion", min_motion), ("merge_gap", merge_gap),
                        ("smoothing_sec", smoothing_sec), ("pre_motion_sec", pre_motion_sec),
                        ("post_motion_sec", post_motion_sec)):
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name}: требуется конечное неотрицательное число.")
    check_cancel(stop_event)
    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            raise IOError(f"Не удалось открыть видео: {video_path}")
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not math.isfinite(fps) or fps <= 0:
            fps = 25.0
        clock = FrameClock(fps)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        width, height = parse_resolution(analysis_res, orig_w, orig_h)
        num_pixels = width * height

        ret, prev = cap.read()
        if not ret:
            return []
        first_time = clock.read(cap.get(cv2.CAP_PROP_POS_MSEC))
        decoded_end = first_time + clock.step
        if (prev.shape[1], prev.shape[0]) != (width, height):
            prev = cv2.resize(prev, (width, height), interpolation=cv2.INTER_LINEAR)
        prev_gray = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY)
        prev_gray = cv2.GaussianBlur(prev_gray, (21,21), 0)

        detector = MotionSegments(min_pause, min_motion)
        frame_idx = 0
        skip = max(0, int(skip_frames))
        smoother = TimeSmoother(smoothing_sec)
        previous_sample_time = first_time
        preview_counter = 0

        while True:
            check_cancel(stop_event)
            ret, frame = cap.read()
            check_cancel(stop_event)
            if not ret:
                break
            frame_idx += 1
            timestamp = clock.read(cap.get(cv2.CAP_PROP_POS_MSEC))
            decoded_end = timestamp + clock.step
            if skip > 0 and (frame_idx % (skip+1)) != 0:
                if progress_cb: progress_cb(frame_idx, total_frames)
                continue
            if (frame.shape[1], frame.shape[0]) != (width, height):
                frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_LINEAR)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (21,21), 0)
            delta = cv2.absdiff(prev_gray, gray)
            score = delta.sum() / num_pixels
            smoothed_score = smoother.update(previous_sample_time, timestamp, score)
            previous_sample_time = timestamp
            preview_counter += 1
            if preview_cb and preview_counter % 5 == 0:
                preview_cb(frame.copy(), frame_idx, smoothed_score)
            detector.sample(timestamp, decoded_end, smoothed_score > sensitivity)
            prev_gray = gray
            if progress_cb: progress_cb(frame_idx, total_frames)

        check_cancel(stop_event)
        return detector.finish(decoded_end, merge_gap, pre_motion_sec, post_motion_sec)
    finally:
        cap.release()
