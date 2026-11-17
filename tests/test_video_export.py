import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import imageio_ffmpeg
from external_tools import ProcessRunner, ToolError, find_ffprobe
from media_probe import probe_video
from video_export import save_segments, save_full_video
from output_safety import WORK_PREFIX
from settings_store import normalize


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        self.probe = find_ffprobe(self.ffmpeg)
        self.runner = ProcessRunner(self.root / 'logs')
        self.fixtures = Path(__file__).parent / 'fixtures/generated'

    def info(self, path):
        return probe_video(path, self.probe, self.runner)

    def test_accurate_fractional_cut_with_audio(self):
        source = self.fixtures / 'motion_with_audio.mp4'
        result = save_segments(source, [(1.23, 3.34)], self.ffmpeg, self.root,
                               self.runner, mode='accurate', ffprobe_path=self.probe)[0]
        info = self.info(result)
        self.assertAlmostEqual(info['duration'], 2.11, delta=.05)
        self.assertEqual(info['video']['codec_name'], 'h264')
        self.assertEqual(info['audio'][0]['codec_name'], 'aac')

    def test_precise_first_frame_long_gop(self):
        source = self.root / 'long.mp4'
        subprocess.run([self.ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
                        "nullsrc=s=64x64:r=25:d=4,geq=lum='N*2':cb=128:cr=128",
                        '-c:v', 'libx264', '-g', '250', '-crf', '0', str(source)], check=True)
        result = save_segments(source, [(1.23, 2.45)], self.ffmpeg, self.root,
                               self.runner, mode='accurate', ffprobe_path=self.probe)[0]
        cap = cv2.VideoCapture(str(source))
        cap.set(cv2.CAP_PROP_POS_FRAMES, 31)
        ok, expected = cap.read(); cap.release()
        self.assertTrue(ok)
        cap = cv2.VideoCapture(result)
        ok, actual = cap.read(); cap.release()
        self.assertTrue(ok)
        self.assertAlmostEqual(float(actual.mean()), float(expected.mean()), delta=2)

    def test_webm_converts_to_mp4_and_fast_retains_codecs(self):
        source = self.root / 'source.webm'
        subprocess.run([self.ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
                        'color=c=red:s=64x64:r=25:d=1', '-f', 'lavfi', '-i',
                        'sine=duration=1', '-c:v', 'libvpx-vp9', '-c:a', 'libopus', str(source)], check=True)
        for mode in ('fast', 'accurate'):
            directory = self.root / mode; directory.mkdir()
            result = Path(save_full_video(source, self.ffmpeg, directory, self.runner,
                                          mode=mode, ffprobe_path=self.probe))
            info = self.info(result)
            self.assertEqual(result.suffix, '.webm' if mode == 'fast' else '.mp4')
            self.assertEqual(info['video']['codec_name'], 'vp9' if mode == 'fast' else 'h264')
            self.assertEqual(info['audio'][0]['codec_name'], 'opus' if mode == 'fast' else 'aac')

    def test_silent_source_stays_silent(self):
        result = save_full_video(self.fixtures / 'static.mp4', self.ffmpeg, self.root,
                                 self.runner, mode='accurate', ffprobe_path=self.probe)
        self.assertEqual(self.info(result)['audio'], [])

    def test_only_first_audio_track(self):
        source = self.root / 'two.mkv'
        subprocess.run([self.ffmpeg, '-v', 'error', '-i', str(self.fixtures / 'motion_with_audio.mp4'),
                        '-map', '0:v', '-map', '0:a', '-map', '0:a', '-c', 'copy', str(source)], check=True)
        result = save_full_video(source, self.ffmpeg, self.root, self.runner,
                                 mode='accurate', ffprobe_path=self.probe)
        self.assertEqual(len(self.info(source)['audio']), 2)
        self.assertEqual(len(self.info(result)['audio']), 1)

    def test_failed_validation_preserves_prior_success_and_counts(self):
        messages = []
        from video_export import validate_export
        calls = []
        def validate(*args):
            calls.append(args)
            if len(calls) == 2:
                raise ToolError('bad result')
            return validate_export(*args)
        with patch('video_export.validate_export', side_effect=validate):
            with self.assertRaisesRegex(ToolError, 'bad result'):
                save_segments(self.fixtures / 'static.mp4', [(0, 1), (1, 2), (2, 3)],
                              self.ffmpeg, self.root, self.runner, mode='accurate',
                              ffprobe_path=self.probe, ui_log=messages.append)
        self.assertEqual(len(list(self.root.glob('*.mp4'))), 1)
        self.assertEqual(list(self.root.glob(WORK_PREFIX + '*')), [])
        self.assertIn('сохранено 1, ошибок 1, не завершено 1', messages[-1])

    def test_all_intervals_validated_before_export(self):
        with self.assertRaises(ValueError):
            save_segments(self.fixtures / 'static.mp4', [(0, 1), (6, 6.05)],
                          self.ffmpeg, self.root, self.runner, ffprobe_path=self.probe)
        self.assertEqual(list(self.root.glob('*.mp4')), [])

    def test_mode_migration_and_validation(self):
        self.assertEqual(normalize({})['export_mode'], 'fast')
        self.assertEqual(normalize({'export_mode': 'accurate'})['export_mode'], 'accurate')
        with self.assertRaises(ValueError):
            normalize({'export_mode': 'invalid'})


if __name__ == '__main__':
    unittest.main()
