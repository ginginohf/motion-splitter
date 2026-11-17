"""Motion analysis using OpenCV; no GUI dependencies."""
import cv2
import numpy as np
from collections import deque
from external_tools import check_cancel

def parse_resolution(opt, orig_w, orig_h):
    if opt.strip().lower() == "оригинал":
        return orig_w, orig_h
    try:
        w, h = opt.lower().split("x")
        return int(w), int(h)
    except Exception:
        return orig_w, orig_h


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
    if not segments or merge_gap <= 0:
        return segments.copy()
    merged = []
    current_start, current_end = segments[0]
    for start, end in segments[1:]:
        if start - current_end <= merge_gap:
            current_end = end
        else:
            merged.append((current_start, current_end))
            current_start, current_end = start, end
    merged.append((current_start, current_end))
    return merged


def analyze_video(video_path, sensitivity, min_pause, min_motion, analysis_res, skip_frames,
                 progress_cb=None, stop_event=None, preview_cb=None, merge_gap=0):
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

        ret, prev = cap.read()
        if not ret:
            return []
        if (prev.shape[1], prev.shape[0]) != (width, height):
            prev = cv2.resize(prev, (width, height), interpolation=cv2.INTER_LINEAR)
        prev_gray = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY)
        prev_gray = cv2.GaussianBlur(prev_gray, (21,21), 0)

        motion_segments = []
        in_motion = False
        motion_start_ms = None
        still_counter = 0
        frame_idx = 0
        skip = max(0, int(skip_frames))
        motion_history = deque(maxlen=5)
        preview_counter = 0

        while True:
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
            motion_history.append(score)
            smoothed_score = sum(motion_history) / len(motion_history)
            preview_counter += 1
            if preview_cb and preview_counter % 5 == 0:
                preview_cb(frame.copy(), frame_idx, smoothed_score)
            if smoothed_score > sensitivity:
                if not in_motion:
                    in_motion = True
                    motion_start_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
                still_counter = 0
            else:
                still_counter += 1
                if in_motion and (still_counter > (min_pause*fps)/(skip+1)):
                    motion_end_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
                    if (motion_end_ms - motion_start_ms) >= (min_motion*1000.0):
                        motion_segments.append((motion_start_ms/1000.0, motion_end_ms/1000.0))
                    in_motion = False
                    motion_start_ms = None
                    still_counter = 0
            prev_gray = gray
            if progress_cb: progress_cb(frame_idx, total_frames)

        if in_motion and motion_start_ms is not None:
            end_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
            if (end_ms - motion_start_ms) >= (min_motion*1000.0):
                motion_segments.append((motion_start_ms/1000.0, end_ms/1000.0))
        if merge_gap > 0:
            motion_segments = merge_close_segments(motion_segments, merge_gap)
        return motion_segments
    finally:
        cap.release()
