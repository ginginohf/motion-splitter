import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import imageio_ffmpeg
import video_analysis


class HeadlessTests(unittest.TestCase):
    def test_real_processing_with_tkinter_import_forbidden(self):
        root = Path(__file__).resolve().parents[1]
        code = r'''
import builtins
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == "gui" or name.startswith("tkinter") or name == "PIL.ImageTk":
        raise AssertionError("GUI dependency imported: " + name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
import queue, threading, tempfile, shutil, sys
from pathlib import Path
import processing
import motion_splitter
from job_contracts import TaskContext, JobResult
from unittest.mock import patch
with tempfile.TemporaryDirectory(prefix="motion-headless-") as temp:
    source = Path(temp) / "source.mp4"
    shutil.copy2(sys.argv[1], source)
    context = TaskContext(queue.Queue(), threading.Event())
    with patch.object(processing, "find_ffmpeg", return_value=sys.argv[2]):
        result = processing.run_split(context, [str(source)], 1, .5, 1, "Оригинал", 0,
                                      True, False, .5, 1, False, "", "", 4, 0, 0)
    assert isinstance(result, JobResult)
    assert result.status == "done", result
    assert result.processed == result.total == 1
    assert result.error is None
    assert (result.output_directory / "source.mp4/source_full.mp4").is_file()
    terminals = [message for message in context.ui_queue.queue if message[0] in ("done", "error", "cancelled")]
    assert terminals == [("done", 1)], terminals
    assert motion_splitter.analyze_video is processing.analyze_video
    context = TaskContext(queue.Queue(), threading.Event())
    context.stop_event.set()
    cancelled = processing.run_enhance(context, [str(source)], "", "", 4, 0, 0)
    assert cancelled.status == "cancelled" and cancelled.processed == 0
    assert cancelled.output_directory is None
    context = TaskContext(queue.Queue(), threading.Event())
    failed = processing.run_enhance(context, [], "", "", 4, 0, 0)
    assert failed.status == "error" and failed.error and failed.total == 0
assert not any(name == "gui" or name.startswith("tkinter") for name in sys.modules)
print("Headless processing passed")
'''
        result = subprocess.run([sys.executable, "-c", code,
                                 str(root / "tests/fixtures/generated/static.mp4"),
                                 imageio_ffmpeg.get_ffmpeg_exe()],
                                cwd=root, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Headless processing passed", result.stdout)

    def test_duration_capture_released_on_error_and_unopened_input(self):
        capture = Mock()
        capture.isOpened.return_value = False
        with patch.object(video_analysis.cv2, "VideoCapture", return_value=capture):
            self.assertEqual(video_analysis.get_video_duration("bad.mp4"), 0)
        capture.release.assert_called_once()
        capture.reset_mock()
        capture.isOpened.return_value = True
        capture.get.side_effect = RuntimeError("decoder failure")
        with patch.object(video_analysis.cv2, "VideoCapture", return_value=capture):
            with self.assertRaises(RuntimeError):
                video_analysis.get_video_duration("bad.mp4")
        capture.release.assert_called_once()

    def test_analysis_capture_released_when_progress_callback_fails(self):
        fixture = Path(__file__).parent / "fixtures/generated/motion_middle.mp4"
        actual = video_analysis.cv2.VideoCapture(str(fixture))
        proxy = Mock(wraps=actual)
        with patch.object(video_analysis.cv2, "VideoCapture", return_value=proxy):
            with self.assertRaisesRegex(RuntimeError, "callback"):
                video_analysis.analyze_video(fixture, 1, .5, 1, "Оригинал", 0,
                                            progress_cb=Mock(side_effect=RuntimeError("callback")))
        proxy.release.assert_called_once()


if __name__ == "__main__":
    unittest.main()
