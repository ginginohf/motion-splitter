import errno
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import imageio_ffmpeg

from external_tools import ExternalProcessError
import motion_splitter as app
from output_safety import (WORK_PREFIX, collect_folder_videos, create_run_directory,
                           source_output_directory, staged_output)


class OutputSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="motion-safety-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "исходник с пробелами.mp4"
        self.source.write_bytes(b"original")
        self.destination = self.root / "result.mp4"

    def assert_no_work(self):
        self.assertEqual(list(self.root.rglob(WORK_PREFIX + "*")), [])
        self.assertEqual(self.source.read_bytes(), b"original")

    def test_publish_only_after_success(self):
        with staged_output(self.source, self.destination) as tmp:
            self.assertEqual(tmp.suffix, ".mp4")
            tmp.write_bytes(b"complete")
            self.assertFalse(self.destination.exists())
        self.assertEqual(self.destination.read_bytes(), b"complete")
        self.assert_no_work()

    def test_failure_never_publishes_partial(self):
        with self.assertRaises(RuntimeError):
            with staged_output(self.source, self.destination) as tmp:
                tmp.write_bytes(b"partial")
                raise RuntimeError("external process failed")
        self.assertFalse(self.destination.exists())
        self.assert_no_work()

    def test_missing_and_empty_output_rejected(self):
        for empty in (False, True):
            with self.subTest(empty=empty), self.assertRaises(ValueError):
                with staged_output(self.source, self.destination) as tmp:
                    if empty:
                        tmp.touch()
            self.assertFalse(self.destination.exists())
        self.assert_no_work()

    def test_existing_output_preserved(self):
        self.destination.write_bytes(b"previous run")
        with self.assertRaises(FileExistsError):
            with staged_output(self.source, self.destination):
                self.fail("must not start processing")
        self.assertEqual(self.destination.read_bytes(), b"previous run")
        self.assert_no_work()

    def test_source_aliases_rejected(self):
        symlink = self.root / "alias.mp4"
        symlink.symlink_to(self.source)
        hardlink = self.root / "hardlink.mp4"
        os.link(self.source, hardlink)
        for path in (self.source, symlink, hardlink):
            with self.subTest(path=path), self.assertRaises((ValueError, FileExistsError)):
                with staged_output(self.source, path):
                    self.fail("must not start processing")
        self.assert_no_work()

    def test_dangling_symlink_rejected(self):
        self.destination.symlink_to(self.root / "missing.mp4")
        with self.assertRaises(FileExistsError):
            with staged_output(self.source, self.destination):
                pass
        self.assertTrue(self.destination.is_symlink())

    def test_conflicting_writer_preserved(self):
        with self.assertRaises(FileExistsError):
            with staged_output(self.source, self.destination) as tmp:
                tmp.write_bytes(b"our output")
                self.destination.write_bytes(b"other writer")
        self.assertEqual(self.destination.read_bytes(), b"other writer")
        self.assert_no_work()

    def test_race_at_publication_does_not_overwrite(self):
        original_link = os.link

        def competing_link(src, dst):
            Path(dst).write_bytes(b"race winner")
            return original_link(src, dst)

        with patch("output_safety.os.link", side_effect=competing_link):
            with self.assertRaises(FileExistsError):
                with staged_output(self.source, self.destination) as tmp:
                    tmp.write_bytes(b"loser")
        self.assertEqual(self.destination.read_bytes(), b"race winner")
        self.assert_no_work()

    def test_unsupported_filesystem_fails_without_partial(self):
        with patch("output_safety.os.link", side_effect=OSError(errno.ENOTSUP, "unsupported")):
            with self.assertRaisesRegex(OSError, "жёстких ссылок"):
                with staged_output(self.source, self.destination) as tmp:
                    tmp.write_bytes(b"complete")
        self.assertFalse(self.destination.exists())
        self.assert_no_work()

    def test_runs_and_same_stem_inputs_are_separate(self):
        first = create_run_directory(self.root)
        second = create_run_directory(self.root)
        self.assertNotEqual(first, second)
        one = source_output_directory(first, self.root / "clip.mp4")
        two = source_output_directory(first, self.root / "clip.mov")
        self.assertNotEqual(one, two)
        with self.assertRaises(FileExistsError):
            source_output_directory(first, self.root / "clip.mp4")

    def test_folder_mode_excludes_generated_and_nonfiles(self):
        for name in ("old_upscaled.mp4", "old_full.mp4", "old_segment_01.mp4"):
            (self.root / name).write_bytes(b"legacy")
        (self.root / "directory.mp4").mkdir()
        run = create_run_directory(self.root)
        result = run / "result.mp4"
        result.write_bytes(b"output")
        (self.root / "linked-result.mp4").symlink_to(result)
        legacy = self.root / "old_segment"
        legacy.mkdir()
        (legacy / "arbitrary.mp4").write_bytes(b"legacy")
        self.assertEqual(collect_folder_videos(self.root, app.VIDEO_EXTS), [str(self.source)])
        self.assertEqual(collect_folder_videos(run, app.VIDEO_EXTS), [])
        self.assertEqual(collect_folder_videos(legacy, app.VIDEO_EXTS), [])

    def test_direct_enhancement_success_and_failure(self):
        def fake_process(cmd, *args, **kwargs):
            Path(cmd[cmd.index("-o") + 1]).write_bytes(b"complete")
        with patch.object(app.ProcessRunner, "run", side_effect=fake_process):
            self.assertTrue(app.enhance_video_with_realesrgan(
                self.source, self.destination, "fake", "model", 4))
        self.assertEqual(self.destination.read_bytes(), b"complete")
        failed = self.root / "failed.mp4"

        def fail_process(cmd, *args, **kwargs):
            Path(cmd[cmd.index("-o") + 1]).write_bytes(b"partial")
            raise subprocess.CalledProcessError(1, cmd)
        with patch.object(app.ProcessRunner, "run", side_effect=fail_process):
            with self.assertRaises(subprocess.CalledProcessError):
                app.enhance_video_with_realesrgan(self.source, failed, "fake", "model", 4)
        self.assertFalse(failed.exists())
        self.assert_no_work()

    def test_enhancement_tab_repeated_folder_run(self):
        (self.root / "previous_upscaled.mp4").write_bytes(b"previous")
        ui = SimpleNamespace(
            processing=False, closing=False,
            collect_settings=lambda: dict(app.DEFAULTS),
            persist_settings=lambda settings: None,
            enh_source_var=Mock(get=lambda: str(self.root)),
            enh_realesrgan_path_var=Mock(get=lambda: "fake"),
            enh_model_var=Mock(get=lambda: "model"),
            enh_scale_var=Mock(get=lambda: "4"),
            enh_tile_var=Mock(get=lambda: "0"),
            enh_gpuid_var=Mock(get=lambda: "0"),
            btn_enh_start=Mock(), pb_enh={}, lbl_enh=Mock(), log_message_enh=Mock())

        def fake_enhance(source, destination, *args, **kwargs):
            with staged_output(source, destination) as tmp:
                tmp.write_bytes(b"enhanced")
            return True

        def launch_task(mode, target, args):
            context = SimpleNamespace(ui_queue=queue.Queue(), stop_event=threading.Event())
            target(context, *args)
            self.assertFalse(any(m[0] == "error" for m in context.ui_queue.queue))
        ui.launch_task = launch_task

        with patch.object(app, "enhance_video_fallback_frames", side_effect=fake_enhance), \
             patch.object(app, "find_ffmpeg", return_value="fake-ffmpeg"), \
             patch.object(app, "find_realesrgan", return_value="fake-esrgan"), \
             patch.object(app, "check_tools"):
            app.App.start_enhance_only(ui)
            app.App.start_enhance_only(ui)
        runs = list(self.root.glob("motion_splitter_run_*"))
        self.assertEqual(len(runs), 2)
        for run in runs:
            results = list(run.rglob("*.mp4"))
            results = [p for p in results if p.is_file()]
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].read_bytes(), b"enhanced")
        self.assertEqual((self.root / "previous_upscaled.mp4").read_bytes(), b"previous")
        self.assert_no_work()

    def test_fallback_preserves_foreign_frames_at_every_failure_stage(self):
        sentinels = []
        for name in ("result_frames", "result_frames_up", WORK_PREFIX + "abandoned"):
            directory = self.root / name
            directory.mkdir()
            sentinel = directory / "00000001.png"
            sentinel.write_bytes(b"do not delete")
            sentinels.append(sentinel)
        for fail_at in (1, 2, 3, None):
            calls = []

            def fake_process(cmd, *args, **kwargs):
                calls.append(cmd)
                if len(calls) == 1:
                    Path(cmd[-1]).with_name("00000001.png").write_bytes(b"frame")
                elif len(calls) == 2:
                    out = Path(cmd[cmd.index("-o") + 1])
                    self.assertEqual(list(out.iterdir()), [])
                    (out / "00000001.png").write_bytes(b"upscaled frame")
                else:
                    Path(cmd[-1]).write_bytes(b"video")
                if len(calls) == fail_at:
                    raise subprocess.CalledProcessError(1, cmd)

            destination = self.root / f"attempt-{fail_at}.mp4"
            with patch.object(app.cv2, "VideoCapture") as capture:
                capture.return_value.get.return_value = 25
                with patch.object(app.ProcessRunner, "run", side_effect=fake_process):
                    if fail_at is None:
                        self.assertTrue(app.enhance_video_fallback_frames(
                            self.source, destination, "fake", "model", 4, 0, 0, "ffmpeg"))
                    else:
                        with self.assertRaises(subprocess.CalledProcessError):
                            app.enhance_video_fallback_frames(
                                self.source, destination, "fake", "model", 4, 0, 0, "ffmpeg")
            self.assertEqual(destination.exists(), fail_at is None)
            for sentinel in sentinels:
                self.assertEqual(sentinel.read_bytes(), b"do not delete")
            self.assertEqual(len(list(self.root.glob(WORK_PREFIX + "*"))), 1)


class RealExportTests(unittest.TestCase):
    def test_repeated_exports_and_failure_preserve_sources(self):
        fixture = Path(__file__).parent / "fixtures/generated/motion_with_audio.mp4"
        self.assertTrue(fixture.is_file(), "Run tools/generate_fixtures.py first")
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.TemporaryDirectory(prefix="motion-export-test-") as temp:
            root = Path(temp)
            source = root / "ролик с пробелами.mp4"
            shutil.copy2(fixture, source)
            original = source.read_bytes()
            first = Path(app.save_segments(source, [(1, 3)], ffmpeg)[0])
            previous = first.read_bytes()
            second = Path(app.save_segments(source, [(1, 3)], ffmpeg)[0])
            full = Path(app.save_full_video(source, ffmpeg))
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), previous)
            self.assertEqual(source.read_bytes(), original)
            for path in (first, second, full):
                decoded = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path), "-f", "null", "-"],
                                         capture_output=True, text=True, check=True)
                self.assertIn("Audio:", decoded.stderr)
            with self.assertRaises(FileExistsError):
                app.save_segments(source, [(1, 3)], ffmpeg, first.parent)
            self.assertEqual(first.read_bytes(), previous)
            broken = root / "broken.mp4"
            broken.write_bytes(b"not a video")
            with self.assertRaises(ExternalProcessError):
                app.save_full_video(broken, ffmpeg)
            self.assertEqual(list(root.rglob(WORK_PREFIX + "*")), [])
            self.assertEqual(source.read_bytes(), original)

    def test_split_worker_groups_batch_in_one_run(self):
        fixture = Path(__file__).parent / "fixtures/generated/static.mp4"
        with tempfile.TemporaryDirectory(prefix="motion-worker-test-") as temp:
            root = Path(temp)
            inputs = [root / "one.mp4", root / "two.mp4"]
            for path in inputs:
                shutil.copy2(fixture, path)
            worker = SimpleNamespace(ui_queue=queue.Queue(), stop_event=threading.Event(), processing=True)
            with patch.object(app, "find_ffmpeg", return_value=imageio_ffmpeg.get_ffmpeg_exe()):
                app.App.worker_process(worker, list(map(str, inputs)), 1, .5, 1,
                                       "Оригинал", 0, True, False, .5, 1,
                                       False, "unused", "unused", 4, 0, 0)
            messages = list(worker.ui_queue.queue)
            self.assertFalse(any(m[0] == "error" for m in messages), messages)
            runs = list(root.glob("motion_splitter_run_*"))
            self.assertEqual(len(runs), 1)
            self.assertTrue((runs[0] / "one.mp4/one_full.mp4").is_file())
            self.assertTrue((runs[0] / "two.mp4/two_full.mp4").is_file())


if __name__ == "__main__":
    unittest.main()
