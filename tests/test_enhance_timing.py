import os
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import imageio_ffmpeg
from external_tools import ProcessRunner, ToolError, ExternalProcessError, find_ffprobe
from enhance_resources import DiskBudget, estimate_space, GIB
from video_timing import read_timeline, compare_timeline
from media_probe import probe_video
from video_enhance import enhance_video_fallback_frames
import test_realesrgan


class BudgetTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX process-group signals; Windows uses taskkill")
    def test_permission_error_only_ignored_for_reaped_process(self):
        from unittest.mock import Mock
        from external_tools import signal_process_group
        import signal
        process = Mock(pid=123)
        with patch('external_tools.os.killpg', side_effect=[PermissionError('race'), ProcessLookupError(), PermissionError('live')]):
            signal_process_group(process, signal.SIGTERM)
            process.wait.assert_called_once_with(timeout=.1)
            process.wait.side_effect = subprocess.TimeoutExpired('child', .1)
            with self.assertRaises(PermissionError):
                signal_process_group(process, signal.SIGTERM)

    def test_estimate_scales_with_area_and_frames(self):
        self.assertGreater(estimate_space(1920, 1080, 100, 4), estimate_space(1920, 1080, 100, 2))
        self.assertGreater(estimate_space(1920, 1080, 200, 4), estimate_space(1920, 1080, 100, 4))

    def test_preflight_limit_and_reserve(self):
        with tempfile.TemporaryDirectory() as temp:
            budget = DiskBudget(temp, 1, 2)
            with patch('enhance_resources.shutil.disk_usage', return_value=SimpleNamespace(free=10*GIB)):
                budget.require(GIB // 2)
                with self.assertRaises(ToolError): budget.require(2*GIB)
            with patch('enhance_resources.shutil.disk_usage', return_value=SimpleNamespace(free=2*GIB)):
                with self.assertRaises(ToolError): budget.require(GIB // 2)

    def test_live_limit_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            budget = DiskBudget(temp, .25, .1)
            budget.limit = 4
            (Path(temp) / 'data').write_bytes(b'12345')
            with self.assertRaises(ToolError): budget.check(force=True)

    def test_guard_stops_running_process_and_logs_reason(self):
        with tempfile.TemporaryDirectory() as temp:
            runner = ProcessRunner(Path(temp) / 'logs')
            calls = []
            def guard():
                calls.append(1)
                if len(calls) > 2: raise ToolError('disk budget reached')
            runner.resource_guard = guard
            with self.assertRaises(ExternalProcessError) as caught:
                runner.run([sys.executable, '-c', 'import time; time.sleep(30)'], 'guard test')
            self.assertIn('disk budget reached', caught.exception.log_path.read_text())
            self.assertIsNotNone(caught.exception.returncode)


class TimingPipelineTests(unittest.TestCase):
    setUp = test_realesrgan.RealESRGANTests.setUp

    def timeline(self, path, name):
        ffprobe = find_ffprobe()
        info = probe_video(path, ffprobe, self.runner)
        return read_timeline(path, ffprobe, self.runner, self.root / name, info), info

    def enhance(self, source):
        result = self.root / 'result.mp4'
        enhance_video_fallback_frames(source, result, str(self.exe), 'realesrgan-x4plus', 4, 0, 0,
                                      imageio_ffmpeg.get_ffmpeg_exe(), runner=self.runner)
        before, _ = self.timeline(source, 'before.txt')
        after, info = self.timeline(result, 'after.txt')
        compare_timeline(before, after)
        return before, after, info

    def test_ntsc_fractional_rate(self):
        source = self.root / 'ntsc.mp4'
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-f', 'lavfi', '-i',
                        'testsrc2=s=32x32:r=30000/1001:d=1', '-c:v', 'libx264', str(source)], check=True)
        before, after, _ = self.enhance(source)
        self.assertEqual(len(before['pts']), 30)
        self.assertAlmostEqual(before['duration'], 1.001, places=4)

    def test_vfr_and_delayed_audio(self):
        source = self.root / 'vfr.mkv'
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-f', 'lavfi', '-i',
                        'testsrc2=s=32x32:r=25:d=1', '-itsoffset', '0.2', '-f', 'lavfi', '-i',
                        'sine=frequency=800:duration=0.8', '-vf', "select='eq(mod(n,3),0)+eq(n,4)+eq(n,24)'",
                        '-fps_mode', 'vfr', '-c:v', 'libx264', '-c:a', 'pcm_s16le', str(source)], check=True)
        before, after, info = self.enhance(source)
        self.assertEqual(len(before['pts']), 10)
        self.assertEqual(info['audio'][0]['codec_name'], 'aac')
        self.assertAlmostEqual(float(info['audio'][0]['start_time']), .2, delta=.05)

    def test_video_start_offset_audio_before_video(self):
        source = self.root / 'offset.mkv'
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-itsoffset', '0.3', '-f', 'lavfi', '-i',
                        'testsrc2=s=32x32:r=10:d=0.5', '-f', 'lavfi', '-i', 'sine=duration=1',
                        '-c:v', 'libx264', '-c:a', 'pcm_s16le', str(source)], check=True)
        before, after, info = self.enhance(source)
        self.assertAlmostEqual(before['start'], .3, places=3)
        self.assertAlmostEqual(after['start'], 0, places=3)
        self.assertLess(float(info['audio'][0]['start_time']), .05)

    def test_live_budget_failure_leaves_no_output(self):
        source = self.root / 'small.mp4'
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
                        'color=s=32x32:r=5:d=0.4', '-c:v', 'libx264', str(source)], check=True)
        original = source.read_bytes()
        old = self.root / 'old.mp4'; old.write_bytes(b'keep')
        target = self.root / 'result.mp4'
        real_check = DiskBudget.check
        def check(budget, force=False):
            if list(budget.directory.glob('frames_up/*.png')):
                raise ToolError('simulated disk exhaustion')
            return real_check(budget, force)
        with patch.object(DiskBudget, 'check', check):
            with self.assertRaises(ToolError):
                enhance_video_fallback_frames(source, target, str(self.exe), 'realesrgan-x4plus', 4, 0, 0,
                                              ffmpeg, runner=self.runner)
        self.assertFalse(target.exists())
        self.assertEqual(old.read_bytes(), b'keep')
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(list(self.root.glob('.motion-splitter-work-*')), [])
        self.assertIsNone(self.runner.resource_guard)

    def test_insufficient_disk_stops_before_extraction_and_cleans(self):
        source = Path(__file__).parent / 'fixtures/generated/static.mp4'
        target = self.root / 'result.mp4'
        with patch('enhance_resources.shutil.disk_usage', return_value=SimpleNamespace(free=1)), \
             patch('video_enhance.read_timeline', return_value={'pts': [0]}):
            with self.assertRaises(ToolError):
                enhance_video_fallback_frames(source, target, str(self.exe), 'realesrgan-x4plus', 4, 0, 0,
                                              imageio_ffmpeg.get_ffmpeg_exe(), runner=self.runner)
        self.assertFalse(target.exists())
        self.assertEqual(list(self.root.glob('.motion-splitter-work-*')), [])
        self.assertIsNone(self.runner.resource_guard)


class TimelineValidationTests(unittest.TestCase):
    def test_reject_missing_duplicate_or_nonfinite_timestamps(self):
        from unittest.mock import Mock
        info = {'video': {'width': 32, 'height': 32}, 'duration': 1}
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp) / 'timing.txt'
            for text in ('width=32|height=32|duration_time=0.04\n',
                         'width=32|height=32|best_effort_timestamp_time=nan|duration_time=0.04\n',
                         'width=32|height=32|best_effort_timestamp_time=0|duration_time=0.04\n' * 2,
                         'width=32|height=32|best_effort_timestamp_time=0\n'):
                report.write_text(text)
                with self.assertRaises(ToolError):
                    read_timeline('source', 'ffprobe', Mock(), report, info)

    def test_reject_wrong_frame_count_timestamps_and_duration(self):
        expected = {'pts': [0, .12, .16], 'duration': .2}
        for actual in ({'pts': [0, .12], 'duration': .2},
                       {'pts': [0, .08, .16], 'duration': .2},
                       {'pts': [0, .12, .16], 'duration': .3}):
            with self.assertRaises(ToolError): compare_timeline(expected, actual)

    def test_disk_settings_migration_and_validation(self):
        from settings_store import normalize
        migrated = normalize({'schema_version': 4})
        self.assertEqual(migrated['enhance_disk_limit_gb'], 32)
        for key, value in [('enhance_disk_limit_gb', 0), ('enhance_disk_reserve_gb', float('nan'))]:
            with self.assertRaises(ValueError): normalize({key: value})
