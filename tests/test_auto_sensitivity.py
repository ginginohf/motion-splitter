import unittest
import threading
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import video_analysis as analysis
from external_tools import TaskCancelled
from task_ui import UIEvents


class AutoSensitivityTests(unittest.TestCase):
    def test_windows_cover_start_middle_end_with_bounded_budget(self):
        windows = analysis.auto_sample_windows(1000000, 3, 25, .2)
        self.assertEqual(len(windows), 5)
        self.assertEqual(windows[0][0], 0)
        self.assertEqual(sum(windows[-1]), 1000000)
        self.assertLessEqual(sum(span for _, span in windows), 6000)
        self.assertTrue(all(a + span <= b for (a, span), (b, _) in zip(windows, windows[1:])))

    def test_window_budget_stays_bounded_at_extreme_skip(self):
        windows = analysis.auto_sample_windows(1000000, 10001, 25, 10)
        self.assertLessEqual(sum(span for _, span in windows), 6000)

    def test_sparse_outliers_do_not_dominate_threshold(self):
        self.assertEqual(analysis.robust_motion_threshold([.2] * 99 + [255]), .5)
        self.assertEqual(analysis.robust_motion_threshold([1] * 99 + [255]), 1)
        self.assertEqual(analysis.robust_motion_threshold([]), .5)

    def test_static_video_repeatable_with_monotonic_progress(self):
        fixture = Path(__file__).parent / "fixtures/generated/static.mp4"
        for skip in (0, 2):
            results = []
            for _ in range(2):
                progress, diagnostics = [], []
                results.append(analysis.calculate_auto_sensitivity(
                    fixture, "100x100", skip, smoothing_sec=.3,
                    progress_cb=lambda cur, total: progress.append((cur, total)),
                    diagnostics_cb=diagnostics.append))
                self.assertEqual(progress[-1][0], progress[-1][1])
                self.assertEqual([cur for cur, _ in progress], sorted(cur for cur, _ in progress))
                self.assertTrue(any("участков 5" in text for text in diagnostics))
            self.assertEqual(results, [.5, .5])

    def make_capture(self, count=5000, unknown=False, seek=True, active_from=None, flash=None, pattern=None):
        cap = Mock()
        index = 0
        seeks = []
        reads = []
        cap.isOpened.return_value = True

        def get(prop):
            return {analysis.cv2.CAP_PROP_FPS: 25,
                    analysis.cv2.CAP_PROP_FRAME_COUNT: 0 if unknown else count,
                    analysis.cv2.CAP_PROP_FRAME_WIDTH: 32,
                    analysis.cv2.CAP_PROP_FRAME_HEIGHT: 32,
                    analysis.cv2.CAP_PROP_POS_MSEC: max(0, index-1) * 40}.get(prop, 0)

        def set_position(prop, position):
            nonlocal index
            seeks.append(position)
            if seek:
                index = int(position)
            return seek

        def read():
            nonlocal index
            if index >= count:
                return False, None
            reads.append(index)
            active = active_from is not None and index >= active_from
            value = (100 if index % 2 else 0) if active else 0
            if pattern is not None:
                value = pattern(index)
            if index == flash:
                value = 255
            index += 1
            return True, np.full((32, 32, 3), value, dtype=np.uint8)

        cap.get.side_effect = get
        cap.set.side_effect = set_position
        cap.read.side_effect = read
        return cap, seeks, reads

    def test_sampling_visits_later_motion_and_is_not_only_opening(self):
        cap, seeks, reads = self.make_capture(active_from=1500)
        with patch.object(analysis.cv2, "VideoCapture", return_value=cap):
            threshold = analysis.calculate_auto_sensitivity("test", "Оригинал", 0)
        self.assertEqual(len(seeks), 4)
        self.assertEqual(reads[-1], 4999)
        self.assertGreater(threshold, .5)
        self.assertLessEqual(len(reads), 6000)
        cap.release.assert_called_once()

    def test_flash_does_not_raise_static_threshold(self):
        cap, _, _ = self.make_capture(count=500, flash=50)
        with patch.object(analysis.cv2, "VideoCapture", return_value=cap):
            self.assertEqual(analysis.calculate_auto_sensitivity("test", "Оригинал", 0), .5)

    def test_unknown_length_and_failed_seeks_are_reported(self):
        for unknown, seek in ((True, True), (False, False)):
            cap, _, reads = self.make_capture(unknown=unknown, seek=seek)
            messages = []
            with patch.object(analysis.cv2, "VideoCapture", return_value=cap):
                result = analysis.calculate_auto_sensitivity("test", "Оригинал", 0,
                                                              diagnostics_cb=messages.append)
            self.assertEqual(result, .5)
            self.assertLessEqual(len(reads), 1200)
            self.assertTrue(any("неизвестна" in m or "пропущен" in m for m in messages))

    def test_cancellation_during_sampling_releases_capture(self):
        cap, seeks, reads = self.make_capture()
        stop = threading.Event()
        with patch.object(analysis.cv2, "VideoCapture", return_value=cap):
            with self.assertRaises(TaskCancelled):
                analysis.calculate_auto_sensitivity("test", "Оригинал", 0, stop_event=stop,
                                                   progress_cb=lambda *args: stop.set())
        cap.release.assert_called_once()
        self.assertEqual(len(reads), 1)
        self.assertEqual(seeks, [])

    def test_smoothing_and_skip_affect_the_sampled_scores(self):
        results = []
        for skip, smoothing in ((0, 0), (0, .2), (4, 0)):
            cap, _, _ = self.make_capture(pattern=lambda i: ((i // 5) % 2) * 10)
            with patch.object(analysis.cv2, "VideoCapture", return_value=cap):
                results.append(analysis.calculate_auto_sensitivity(
                    "test", "Оригинал", skip, smoothing_sec=smoothing))
        self.assertEqual(results[0], .5)
        self.assertGreater(results[1], results[0])
        self.assertGreater(results[2], results[0])

    def test_phase_transition_clears_auto_progress(self):
        events = UIEvents()
        events.put(("phase", "Автонастройка"))
        events.put(("file_progress", (200, 300)))
        events.put(("phase", "Анализ"))
        self.assertEqual(events.drain(), [("phase", "Анализ")])


if __name__ == "__main__":
    unittest.main()
