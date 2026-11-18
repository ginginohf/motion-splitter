import os
import queue
import sys
import shlex
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import imageio_ffmpeg
from external_tools import ToolError, ProcessRunner, TaskCancelled, ExternalProcessError
from realesrgan_config import configuration, preflight, validate_frames
from video_enhance import enhance_video_fallback_frames
from job_contracts import TaskContext
import processing


class RealESRGANTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='esrgan test ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.exe = self.root / 'realesrgan-ncnn-vulkan'
        self.script = self.root / 'fake.py'
        self.script.write_text('''
import argparse
from pathlib import Path
from PIL import Image
p = argparse.ArgumentParser()
for name in ('i', 'o', 'm', 'n', 's', 't', 'g', 'f'): p.add_argument('-' + name)
a = p.parse_args()
assert Path(a.m).is_absolute()
source, target = Path(a.i), Path(a.o)
for path in sorted(source.glob('*.png')) if source.is_dir() else [source]:
    with Image.open(path) as img:
        img.resize((img.width * int(a.s), img.height * int(a.s))).save(target / path.name if target.is_dir() else target)
''')
        self.exe.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' ' + shlex.quote(str(self.script)) + ' "$@"\n')
        self.exe.chmod(0o755)
        self.models = self.root / 'models'; self.models.mkdir()
        for stem in ('realesrgan-x4plus', 'realesr-animevideov3-x2', 'realesr-animevideov3-x3', 'realesr-animevideov3-x4'):
            for ext in ('.param', '.bin'):
                (self.models / (stem + ext)).write_bytes(b'fake model for test double')
        self.runner = ProcessRunner(self.root / 'logs')
        if os.name == 'nt':
            # Windows cannot execute a POSIX script. Adapt only this test double;
            # real Python/FFmpeg processes and production process handling are unchanged.
            real_run = self.runner.run
            def run(command, *args, **kwargs):
                if Path(command[0]).resolve() == self.exe.resolve():
                    command = [sys.executable, str(self.script)] + command[1:]
                return real_run(command, *args, **kwargs)
            self.runner.run = run

    def test_absolute_model_directory_and_scale_specific_files(self):
        for scale in (2, 3, 4):
            command = configuration(str(self.exe), 'realesr-animevideov3', scale, 32, 0)
            self.assertEqual(command[command.index('-m') + 1], str(self.models.resolve()))
            self.assertEqual(command[command.index('-s') + 1], str(scale))

    def test_reject_bad_model_parameters(self):
        for model, scale, tile, gpu in [('realesr-general-x4v3', 4, 0, 0),
                                      ('../model', 4, 0, 0), ('realesrgan-x4plus', 2, 0, 0),
                                      ('realesrgan-x4plus', 4, 1, 0), ('realesrgan-x4plus', 4, 0, -1),
                                      ('realesrgan-x4plus', True, 0, 0)]:
            with self.subTest(values=(model, scale, tile, gpu)), self.assertRaises(ToolError):
                configuration(str(self.exe), model, scale, tile, gpu)

    def test_missing_and_empty_model(self):
        path = self.models / 'realesrgan-x4plus.bin'
        for empty in (True, False):
            path.write_bytes(b'') if empty else path.unlink()
            with self.assertRaisesRegex(ToolError, 'models'):
                configuration(str(self.exe), 'realesrgan-x4plus', 4, 0, 0)

    def test_smoke_uses_real_process_with_test_double(self):
        preflight(str(self.exe), 'realesrgan-x4plus', 4, 0, 0, self.runner)
        self.assertEqual(list(self.root.rglob('esrgan-probe-*')), [])
        self.assertTrue(list((self.root / 'logs').glob('process-*.log')))

    def test_zero_exit_without_image_rejected(self):
        with patch.object(self.runner, 'run'):
            with self.assertRaises(ToolError):
                preflight(str(self.exe), 'realesrgan-x4plus', 4, 0, 0, self.runner)
        self.assertEqual(list(self.root.rglob('esrgan-probe-*')), [])

    def test_gpu_failure_keeps_log_and_no_retry(self):
        self.script.write_text('import sys\nsys.stderr.write("invalid gpu device")\nsys.exit(7)\n')
        with self.assertRaises(ExternalProcessError) as caught:
            preflight(str(self.exe), 'realesrgan-x4plus', 4, 0, 0, self.runner)
        self.assertIn('invalid gpu device', caught.exception.log_path.read_text())
        self.assertEqual(len(list((self.root / 'logs').glob('process-*'))), 1)

    def test_legacy_settings_preserve_model_without_silent_replacement(self):
        from settings_store import normalize, DEFAULTS
        result = normalize({'schema_version': 3, 'realesrgan_model': 'realesr-general-x4v3'})
        self.assertEqual(result['realesrgan_model'], 'realesr-general-x4v3')
        self.assertEqual(DEFAULTS['realesrgan_model'], 'realesrgan-x4plus')

    def test_cancel_before_probe(self):
        stop = threading.Event(); stop.set()
        with self.assertRaises(TaskCancelled):
            preflight(str(self.exe), 'realesrgan-x4plus', 4, 0, 0, ProcessRunner(self.root / 'logs', stop))
        self.assertFalse((self.root / 'logs').exists())

    def test_bad_frames_missing_wrong_size_and_corrupt(self):
        source = self.root / 'frames'; source.mkdir()
        target = self.root / 'up'; target.mkdir()
        Image.new('RGB', (8, 8)).save(source / '00000001.png')
        with self.assertRaises(ToolError): validate_frames(source, target, 4, self.runner)
        Image.new('RGB', (8, 8)).save(target / '00000001.png')
        with self.assertRaises(ToolError): validate_frames(source, target, 4, self.runner)
        (target / '00000001.png').write_bytes(b'bad png')
        with self.assertRaises(ToolError): validate_frames(source, target, 4, self.runner)
        Image.new('RGB', (32, 32)).save(target / '00000001.png')
        validate_frames(source, target, 4, self.runner)

    def test_worker_preflight_fails_before_extract_or_analysis(self):
        source = Path(__file__).parent / 'fixtures/generated/static.mp4'
        local = self.root / 'source.mp4'; local.write_bytes(source.read_bytes())
        for split in (True, False):
            context = TaskContext(queue.Queue(), threading.Event())
            with patch.object(processing, 'find_ffmpeg', return_value=imageio_ffmpeg.get_ffmpeg_exe()), \
                 patch.object(processing, 'analyze_video') as analyze, \
                 patch.object(processing, 'enhance_video_fallback_frames') as enhance:
                if split:
                    result = processing.run_split(context, [str(local)], 1, .5, 1, 'Оригинал', 0,
                        True, False, .5, 1, True, str(self.exe), 'realesrgan-x4plus', 2, 0, 0)
                else:
                    result = processing.run_enhance(context, [str(local)], str(self.exe), 'realesrgan-x4plus', 2, 0, 0)
                analyze.assert_not_called(); enhance.assert_not_called()
                self.assertTrue(any(event[0] == 'error' for event in context.ui_queue.queue))

    def test_real_ffmpeg_pipeline_with_fake_upscaler(self):
        import subprocess
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        source = self.root / 'source.mp4'; target = self.root / 'result.mp4'
        subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
                        'color=s=32x32:r=5:d=0.4', '-c:v', 'libx264', str(source)], check=True)
        enhance_video_fallback_frames(source, target, str(self.exe), 'realesrgan-x4plus',
                                      4, 0, 0, ffmpeg, runner=self.runner)
        import cv2
        cap = cv2.VideoCapture(str(target))
        try:
            self.assertEqual(cap.get(cv2.CAP_PROP_FRAME_WIDTH), 128)
            self.assertEqual(cap.get(cv2.CAP_PROP_FRAME_COUNT), 2)
        finally:
            cap.release()
        self.assertEqual(list(self.root.glob('.motion-splitter-work-*')), [])
