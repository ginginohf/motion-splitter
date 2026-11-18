"""Packaging boundary tests: no PATH fallback in frozen builds, explicit models, hashes."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

from app_paths import model_directory, user_models_directory
from external_tools import find_ffmpeg, find_ffprobe, ToolError
from realesrgan_config import configuration
from tools.build_installer import validate_vendor, sha256


class PackagingTests(unittest.TestCase):
    def test_frozen_missing_tools_do_not_fall_back_to_path(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(sys, 'frozen', True, create=True), \
                patch.object(sys, '_MEIPASS', temp, create=True):
            with self.assertRaises(ToolError):
                find_ffmpeg()
            with self.assertRaises(ToolError):
                find_ffprobe('/usr/bin/ffmpeg')

    def test_frozen_tools_resolve_from_bundle(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(sys, 'frozen', True, create=True), \
                patch.object(sys, '_MEIPASS', temp, create=True):
            folder = Path(temp) / 'vendor'
            folder.mkdir()
            for name, finder in [('ffmpeg', find_ffmpeg), ('ffprobe', find_ffprobe)]:
                path = folder / (name + ('.exe' if os.name == 'nt' else ''))
                path.write_bytes(b'fixture')
                path.chmod(0o755)
                self.assertEqual(finder(), str(path.resolve()))

    def test_custom_models_independent_of_engine(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            exe = base / 'engine'
            exe.write_bytes(b'fixture'); exe.chmod(0o755)
            models = base / 'custom models'; models.mkdir()
            for suffix in ('.param', '.bin'):
                (models / ('realesrgan-x4plus' + suffix)).write_bytes(b'model')
            command = configuration(str(exe), 'realesrgan-x4plus', 4, 0, 0, str(models))
            self.assertEqual(command[2], str(models.resolve()))
            with self.assertRaises(ToolError):
                configuration(str(exe), 'realesrgan-x4plus', 4, 0, 0, str(base/'missing'))

    def test_legacy_models_and_user_precedence(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); legacy = base / 'models'; legacy.mkdir()
            user = base / 'userdata' / 'models'
            with patch('app_paths.user_models_directory', return_value=user):
                self.assertEqual(model_directory(base / 'engine'), legacy.resolve())
                user.mkdir(parents=True)
                self.assertEqual(model_directory(base / 'engine'), user)

    def test_linux_relative_xdg_ignored(self):
        with patch.object(sys, 'platform', 'linux'), patch.dict(os.environ, {'XDG_DATA_HOME': 'relative'}):
            self.assertTrue(user_models_directory().is_absolute())

    def test_vendor_rejects_modified_or_unlisted_files(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); (base/'notices').mkdir()
            names = ['ffmpeg', 'ffprobe', 'notices/FFmpeg.txt', 'notices/SOURCES.txt', 'notices/DEPENDENCIES.txt']
            for name in names: (base/name).write_bytes(b'fixture')
            manifest = dict(target='linux-amd64', ffmpeg_version='test', origin_url='https://example.org',
                            source_url='https://example.org/src', configuration='test',
                            files={name: sha256(base/name) for name in names})
            (base/'manifest.json').write_text(json.dumps(manifest))
            validate_vendor(base, 'linux-amd64')
            (base/'extra').write_bytes(b'unlisted')
            with self.assertRaises(ValueError): validate_vendor(base, 'linux-amd64')
            (base/'extra').unlink(); (base/'ffmpeg').write_bytes(b'changed')
            with self.assertRaises(ValueError): validate_vendor(base, 'linux-amd64')
            with self.assertRaises(ValueError): validate_vendor(base, 'windows-x64')
