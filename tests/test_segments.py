import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import video_analysis as analysis


class SegmentTests(unittest.TestCase):
    def test_nested_unsorted_overlaps_never_shorten_end(self):
        source = [(5, 7), (1, 10), (2, 3), (11, 12)]
        self.assertEqual(analysis.merge_close_segments(source, 1), [(1, 12)])
        self.assertEqual(source, [(5, 7), (1, 10), (2, 3), (11, 12)])

    def test_zero_gap_and_exact_boundary(self):
        self.assertEqual(analysis.merge_close_segments([(2, 3), (0, 2), (4, 5)], 0),
                         [(0, 3), (4, 5)])
        self.assertEqual(analysis.merge_close_segments([(0, 1), (1.5, 2)], .5), [(0, 2)])

    def test_invalid_intervals_and_gap_rejected(self):
        for intervals, gap in (([(1, 0)], 0), ([(-1, 2)], 0), ([(1, 1)], 0),
                               ([(0, float("nan"))], 0), ([], -1), ([], float("inf"))):
            with self.subTest(intervals=intervals, gap=gap), self.assertRaises(ValueError):
                analysis.merge_close_segments(intervals, gap)

    def test_trailing_pause_does_not_make_short_motion_valid(self):
        detector = analysis.MotionSegments(2, 1)
        detector.sample(1, 1.1, True)
        detector.sample(1.1, 1.2, True)
        detector.sample(1.2, 1.3, False)
        detector.sample(3.2, 3.3, False)
        self.assertEqual(detector.finish(3.3, 0), [])

    def test_pause_confirmation_does_not_extend_segment(self):
        detector = analysis.MotionSegments(.5, .5)
        detector.sample(1, 1.1, True)
        detector.sample(1.5, 1.6, True)
        detector.sample(1.6, 1.7, False)
        detector.sample(2, 2.1, False)
        self.assertEqual(detector.finish(4, 0), [(1, 1.6)])

    def test_short_quiet_tail_at_eof_is_not_motion(self):
        detector = analysis.MotionSegments(2, .5)
        detector.sample(1, 1.1, True)
        detector.sample(2, 2.1, True)
        detector.sample(2.1, 2.2, False)
        self.assertEqual(detector.finish(2.4, 0), [(1, 2.1)])

    def test_internal_pause_remains_inside_event(self):
        detector = analysis.MotionSegments(.5, 1)
        detector.sample(1, 1.1, True)
        detector.sample(1.3, 1.4, False)
        detector.sample(1.5, 1.6, True)
        detector.sample(2, 2.1, True)
        self.assertEqual(detector.finish(2.1, 0), [(1, 2.1)])

    def test_filter_precedes_merging(self):
        detector = analysis.MotionSegments(0, 1)
        detector.sample(0, .4, True)
        detector.sample(.4, .5, False)
        detector.sample(.6, 1, True)
        detector.sample(1, 1.1, False)
        self.assertEqual(detector.finish(2, 10), [])

    def test_exact_minimum_and_skipped_tail(self):
        detector = analysis.MotionSegments(.5, 1)
        detector.sample(1, 1.1, True)
        detector.sample(1.8, 1.9, True)
        self.assertEqual(detector.finish(2, 0), [(1, 2)])

    def test_clock_falls_back_on_missing_or_repeated_timestamps(self):
        clock = analysis.FrameClock(25)
        values = [clock.read(t) for t in (0, 0, float("nan"), -1, 160)]
        np.testing.assert_allclose(values, [0, .04, .08, .12, .16])

    def test_real_motion_to_eof_with_skipped_frames(self):
        fixture = Path(__file__).parent / "fixtures/generated/motion_to_end.mp4"
        for skip in (0, 1, 4, 6):
            with self.subTest(skip=skip):
                segments = analysis.analyze_video(fixture, 1, .5, 1, "Оригинал", skip)
                self.assertEqual(len(segments), 1)
                self.assertAlmostEqual(segments[0][1], 6)
                self.assertGreaterEqual(segments[0][0], 2)
                self.assertLessEqual(segments[0][0], 2 + (skip + 1) / 25)

    def test_no_timestamp_read_after_eof_and_zero_timestamp_fallback(self):
        fixture = Path(__file__).parent / "fixtures/generated/motion_to_end.mp4"
        cap = analysis.cv2.VideoCapture(str(fixture))
        proxy = Mock(wraps=cap)
        exhausted = False

        def read():
            nonlocal exhausted
            result = cap.read()
            exhausted = not result[0]
            return result

        def get(prop):
            if prop == analysis.cv2.CAP_PROP_POS_MSEC:
                self.assertFalse(exhausted, "timestamp queried after failed read")
                return 0
            return cap.get(prop)

        proxy.read.side_effect = read
        proxy.get.side_effect = get
        with patch.object(analysis.cv2, "VideoCapture", return_value=proxy):
            segments = analysis.analyze_video(fixture, 1, .5, 1, "Оригинал", 0)
        self.assertEqual(len(segments), 1)
        self.assertAlmostEqual(segments[0][1], 6)
        proxy.release.assert_called_once()

    def test_real_pause_length_does_not_extend_motion_or_pass_minimum(self):
        fixture = Path(__file__).parent / "fixtures/generated/motion_middle.mp4"
        short_pause = analysis.analyze_video(fixture, 1, .5, 1, "Оригинал", 0)
        long_pause = analysis.analyze_video(fixture, 1, 2, 1, "Оригинал", 0)
        self.assertEqual(short_pause, long_pause)
        self.assertEqual(analysis.analyze_video(fixture, 1, 2, 3, "Оригинал", 0), [])

    def test_real_eof_at_different_frame_rates(self):
        from tools import generate_fixtures as generator
        with tempfile.TemporaryDirectory(prefix="motion-fps-test-") as temp:
            for fps in (10, 30):
                name = f"end-{fps}.mp4"
                with patch.object(generator, "OUTPUT", Path(temp)), patch.object(generator, "FPS", fps):
                    generator.generate(name, [(2, 6)])
                segments = analysis.analyze_video(Path(temp) / name, 1, .5, 1, "Оригинал", 0)
                self.assertEqual(len(segments), 1)
                self.assertAlmostEqual(segments[0][0], 2)
                self.assertAlmostEqual(segments[0][1], 6)

    def test_empty_and_single_frame_videos(self):
        for count in (0, 1):
            cap = Mock()
            cap.isOpened.return_value = True
            cap.get.side_effect = lambda p: {analysis.cv2.CAP_PROP_FPS: 25,
                analysis.cv2.CAP_PROP_FRAME_COUNT: count,
                analysis.cv2.CAP_PROP_FRAME_WIDTH: 32,
                analysis.cv2.CAP_PROP_FRAME_HEIGHT: 32}.get(p, 0)
            cap.read.side_effect = [(True, np.zeros((32, 32, 3), dtype=np.uint8))] * count + [(False, None)]
            with patch.object(analysis.cv2, "VideoCapture", return_value=cap):
                self.assertEqual(analysis.analyze_video("test", 1, .5, 1, "Оригинал", 0), [])
            cap.release.assert_called_once()


if __name__ == "__main__":
    unittest.main()
