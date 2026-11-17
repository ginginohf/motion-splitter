import os
import queue
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import imageio_ffmpeg

import processing as app
import gui
import cv2
from external_tools import ExternalProcessError, ProcessRunner, ToolError, check_tools, resolve_executable


class ExternalToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="motion-diagnostics-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runner = ProcessRunner(self.root / "logs")

    def test_complete_output_and_metadata_saved(self):
        log = self.runner.run([sys.executable, "-c",
                               "import sys; print('stdout marker'); print('stderr marker', file=sys.stderr)"],
                              "Проверка путей с пробелами")
        text = log.read_text()
        for marker in ("stdout marker", "stderr marker", "Проверка путей с пробелами",
                       '"command":', '"cwd":', "returncode=0"):
            self.assertIn(marker, text)

    def test_failure_full_log_bounded_message_no_retry(self):
        counter = self.root / "called.txt"
        code = ("import sys; from pathlib import Path; "
                "p=Path(sys.argv[1]); p.write_text(p.read_text()+'x' if p.exists() else 'x'); "
                "sys.stderr.buffer.write(b'A'*100000 + b'\\xffmodel missing\\n'); sys.exit(7)")
        with self.assertRaises(ExternalProcessError) as caught:
            self.runner.run([sys.executable, "-c", code, str(counter)], "Real-ESRGAN")
        error = caught.exception
        self.assertEqual(error.returncode, 7)
        self.assertEqual(counter.read_text(), "x")
        self.assertIn("model missing", str(error))
        self.assertIn(str(error.log_path), str(error))
        self.assertLess(len(str(error)), 5000)
        self.assertGreater(error.log_path.stat().st_size, 100000)

    def test_missing_binary_error_is_logged(self):
        with self.assertRaises(ExternalProcessError) as caught:
            self.runner.run([str(self.root / "missing-tool")], "Запуск")
        self.assertIn("Не удалось запустить", str(caught.exception))
        self.assertTrue(caught.exception.log_path.exists())

    def test_timeout_has_durable_log(self):
        with self.assertRaises(ExternalProcessError) as caught:
            self.runner.run([sys.executable, "-c", "import time; time.sleep(2)"],
                            "Проверка", timeout=.05)
        self.assertIn("Превышено время", str(caught.exception))
        self.assertTrue(caught.exception.log_path.exists())

    def test_executable_discovery_skips_directory(self):
        folder = self.root / "ffmpeg"
        folder.mkdir()
        with patch("external_tools.shutil.which", return_value=sys.executable):
            self.assertEqual(resolve_executable("ffmpeg", [folder]), str(Path(sys.executable).resolve()))

    def test_custom_path_never_silently_falls_back(self):
        with patch("external_tools.shutil.which", return_value=sys.executable) as which:
            with self.assertRaises(ToolError):
                resolve_executable("ffmpeg", [sys.executable], str(self.root / "missing"))
            which.assert_not_called()

    @unittest.skipIf(os.name == "nt", "POSIX executable permission semantics")
    def test_non_executable_file_rejected(self):
        path = self.root / "tool"
        path.write_text("not executable")
        path.chmod(0o600)
        with self.assertRaisesRegex(ToolError, "нет разрешения"):
            resolve_executable("tool", custom_path=str(path))

    def test_real_ffmpeg_probe(self):
        check_tools(self.runner, imageio_ffmpeg.get_ffmpeg_exe())
        logs = list(self.runner.log_directory.glob("*.log"))
        self.assertEqual(len(logs), 1)
        self.assertIn("ffmpeg version", logs[0].read_text())

    def test_missing_enhancer_stops_before_probe(self):
        with patch.object(self.runner, "run") as run:
            with self.assertRaises(ToolError):
                check_tools(self.runner, imageio_ffmpeg.get_ffmpeg_exe(), str(self.root / "missing"))
            run.assert_not_called()

    def test_missing_ffmpeg_stops_before_video_analysis(self):
        source = self.root / "source.mp4"
        source.write_bytes(b"unused")
        worker = SimpleNamespace(ui_queue=queue.Queue(), stop_event=threading.Event(), processing=True)
        with patch.object(app, "find_ffmpeg", side_effect=ToolError("Не найден FFmpeg")), \
             patch.object(app, "analyze_video") as analyze:
            app.run_split(worker, [str(source)], 1, .5, 1, "Оригинал", 0,
                                   True, False, .5, 1, False, "unused", "unused", 4, 0, 0)
            analyze.assert_not_called()
        errors = [m[1] for m in worker.ui_queue.queue if m[0] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("Полный журнал:", errors[0])
        self.assertEqual(len(list(self.root.rglob("application-*.log"))), 1)
        self.assertTrue(worker.processing)  # Workers must not change main-thread task state.

    def test_real_ffmpeg_bad_input_keeps_log_after_cleanup(self):
        source = self.root / "повреждённое видео.mp4"
        source.write_bytes(b"invalid video")
        with self.assertRaises(ExternalProcessError) as caught:
            app.save_full_video(source, imageio_ffmpeg.get_ffmpeg_exe(), runner=self.runner)
        error = caught.exception
        self.assertIn("FFmpeg: сохранение", str(error))
        self.assertIn("Invalid data", error.log_path.read_text())
        self.assertEqual(list(self.root.rglob(".motion-splitter-work-*")), [])
        self.assertEqual(list(self.root.rglob("*_full.mp4")), [])

    def test_no_fallback_after_enhancement_failure(self):
        fixture = Path(__file__).parent / "fixtures/generated/static.mp4"
        source = self.root / "source.mp4"
        source.write_bytes(fixture.read_bytes())
        worker = SimpleNamespace(ui_queue=queue.Queue(), stop_event=threading.Event(), processing=True)
        with patch.object(app, "find_ffmpeg", return_value=imageio_ffmpeg.get_ffmpeg_exe()), \
             patch.object(app, "find_realesrgan", return_value=sys.executable), \
             patch.object(app, "enhance_video_with_realesrgan") as direct, \
             patch.object(app, "enhance_video_fallback_frames", side_effect=ToolError("model missing")) as frames:
            app.run_split(worker, [str(source)], 1, .5, 1, "Оригинал", 0,
                                   True, False, .5, 1, True, sys.executable, "unused", 4, 0, 0)
            direct.assert_not_called()
            self.assertEqual(frames.call_count, 1)
        messages = list(worker.ui_queue.queue)
        self.assertTrue(any(m[0] == "error" and "model missing" in m[1] for m in messages))
        self.assertFalse(any(m[0] == "done" for m in messages))


if __name__ == "__main__":
    unittest.main()
