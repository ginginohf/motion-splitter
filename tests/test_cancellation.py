import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

import processing as app
import gui
import cv2
from external_tools import ProcessRunner, TaskCancelled
from task_ui import TaskState
from test_task_ui import make_app


class CancellationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="motion-cancel-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        probe_patch = patch("video_export.probe_video", return_value={"duration": 6, "audio": [], "fps": 25})
        probe_patch.start()
        self.addCleanup(probe_patch.stop)
        validation_patch = patch("video_export.validate_export")
        validation_patch.start()
        self.addCleanup(validation_patch.stop)
        for target, kwargs in (("video_enhance.configuration", {"return_value": ["fake"]}),
                               ("video_enhance.validate_frames", {})):
            mock = patch(target, **kwargs)
            mock.start()
            self.addCleanup(mock.stop)
        self.stop = threading.Event()
        self.runner = ProcessRunner(self.root / "logs", self.stop)

    def test_cancel_before_process_launch(self):
        self.stop.set()
        with patch("external_tools.subprocess.Popen") as popen:
            with self.assertRaises(TaskCancelled):
                self.runner.run([sys.executable], "test")
            popen.assert_not_called()

    def test_running_process_is_reaped_and_cancel_logged(self):
        self.check_real_process(ignore_term=False)

    @unittest.skipIf(os.name == "nt", "POSIX signal handling")
    def test_uncooperative_process_is_killed(self):
        self.check_real_process(ignore_term=True)

    def check_real_process(self, ignore_term):
        marker = self.root / "pid.txt"
        code = "import os,sys,time,signal; from pathlib import Path; "
        if ignore_term:
            code += "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        code += "Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)"
        errors = []

        def run():
            try:
                self.runner.run([sys.executable, "-c", code, str(marker)], "cancel test")
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=run)
        thread.start()
        try:
            deadline = time.monotonic() + 3
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(marker.exists())
        finally:
            self.stop.set()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], TaskCancelled)
        self.assertIn("Отменено пользователем", next((self.root / "logs").glob("*.log")).read_text())
        if os.name != "nt":
            with self.assertRaises(ProcessLookupError):
                os.kill(int(marker.read_text()), 0)

    def test_analysis_and_auto_sensitivity_release_capture_on_cancel(self):
        fixture = Path(__file__).parent / "fixtures/generated/motion_middle.mp4"
        for auto in (False, True):
            self.stop.clear()
            capture = cv2.VideoCapture(str(fixture))
            proxy = Mock(wraps=capture)
            with patch.object(cv2, "VideoCapture", return_value=proxy):
                with self.assertRaises(TaskCancelled):
                    if auto:
                        app.calculate_auto_sensitivity(fixture, "Оригинал", 0,
                            progress_cb=lambda *args: self.stop.set(), stop_event=self.stop)
                    else:
                        app.analyze_video(fixture, 1, .5, 1, "Оригинал", 0,
                            progress_cb=lambda *args: self.stop.set(), stop_event=self.stop)
            proxy.release.assert_called_once()

    def test_cancel_partial_segment_preserves_previous(self):
        source = self.root / "source.mp4"
        source.write_bytes(b"source")
        output = self.root / "output"
        calls = []

        def run(command, *args):
            self.runner.check_cancel()
            calls.append(command)
            Path(command[-1]).write_bytes(b"video")
            if len(calls) == 2:
                self.stop.set()
                self.runner.check_cancel()
        with patch.object(self.runner, "run", side_effect=run):
            with self.assertRaises(TaskCancelled):
                app.save_segments(source, [(0, 1), (1, 2), (2, 3)], "fake", output, self.runner)
        self.assertEqual((output / "source_segment_01.mp4").read_bytes(), b"video")
        self.assertFalse((output / "source_segment_02.mp4").exists())
        self.assertEqual(len(calls), 2)
        self.assertEqual(list(self.root.rglob(".motion-splitter-work-*")), [])

    def test_cancel_each_enhancement_stage_prevents_publication(self):
        source = self.root / "source.mp4"
        source.write_bytes(b"source")
        for cancel_at in (1, 2, 3):
            self.stop.clear()
            calls = []

            def run(command, *args):
                self.runner.check_cancel()
                calls.append(command)
                if len(calls) == 3:
                    Path(command[-1]).write_bytes(b"finished but not yet published")
                if len(calls) == cancel_at:
                    self.stop.set()
                    self.runner.check_cancel()
            target = self.root / f"enhanced-{cancel_at}.mp4"
            with patch.object(self.runner, "run", side_effect=run), \
                 patch.object(cv2, "VideoCapture") as capture:
                capture.return_value.get.return_value = 25
                with self.assertRaises(TaskCancelled):
                    app.enhance_video_fallback_frames(source, target, "fake", "model", 4, 0, 0,
                                                     "fake", runner=self.runner)
            self.assertEqual(len(calls), cancel_at)
            self.assertFalse(target.exists())
            self.assertEqual(list(self.root.rglob(".motion-splitter-work-*")), [])

    def test_cancel_after_process_success_before_publication(self):
        source = self.root / "source.mp4"
        source.write_bytes(b"source")

        def run(command, *args):
            Path(command[-1]).write_bytes(b"finished")
            self.stop.set()
        with patch.object(self.runner, "run", side_effect=run):
            with self.assertRaises(TaskCancelled):
                app.save_full_video(source, "fake", self.root / "out", self.runner)
        self.assertEqual(list(self.root.rglob("*_full.mp4")), [])

    def test_close_waits_for_worker_and_does_not_rearm_poll(self):
        ui = make_app()
        ui.root.destroy = Mock()
        release = threading.Event()

        def worker(context):
            context.stop_event.wait(2)
            release.wait(2)
            context.ui_queue.put(("cancelled", 0))
        ui.launch_task("enhance", worker, ())
        self.assertEqual(ui.btn_enh_stop.values["state"], "normal")
        ui.request_close()
        ui.request_close()
        self.assertTrue(ui.stop_event.is_set())
        self.assertEqual(ui.tasks.state, TaskState.CANCELLING)
        ui.poll_queue()
        ui.root.destroy.assert_not_called()
        release.set()
        ui.worker_thread.join(timeout=2)
        scheduled = len(ui.scheduled)
        ui.poll_queue()
        ui.root.destroy.assert_called_once()
        self.assertEqual(len(ui.scheduled), scheduled)
        self.assertEqual(ui.tasks.state, TaskState.CANCELLED)

    def test_idle_close_is_immediate(self):
        ui = make_app()
        ui.root.destroy = Mock()
        ui.request_close()
        ui.root.destroy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
