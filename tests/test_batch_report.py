import json
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import imageio_ffmpeg
import processing
from batch_report import BatchReport
from external_tools import ToolError, TaskCancelled
from job_contracts import TaskContext
from task_ui import UIEvents


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.context = TaskContext(queue.Queue(), threading.Event())
        fixture = Path(__file__).parent / 'fixtures/generated/static.mp4'
        self.files = [self.root / f'{name}.mp4' for name in ('one', 'two', 'three')]
        for path in self.files: path.write_bytes(fixture.read_bytes())
        mock = patch.object(processing, 'find_ffmpeg', return_value=imageio_ffmpeg.get_ffmpeg_exe())
        mock.start(); self.addCleanup(mock.stop)

    def split(self, files=None, save_full=True):
        return processing.run_split(self.context, list(map(str, self.files if files is None else files)),
                                    1, .5, 1, 'Оригинал', 0, save_full, False, .5, 1,
                                    False, '', '', 4, 0, 0)

    def report(self, result):
        return json.loads(result.report_path.read_text())

    def assert_one_terminal(self):
        self.assertEqual(sum(event[0] in ('done', 'error', 'cancelled') for event in self.context.ui_queue.queue), 1)

    def test_real_batch_continues_after_bad_middle_file(self):
        self.files[1].write_bytes(b'not video')
        result = self.split()
        report = self.report(result)
        self.assertEqual(result.status, 'error')
        self.assertEqual(result.processed, 3)
        self.assertEqual([item['status'] for item in report['files']], ['success', 'failed', 'success'])
        self.assertEqual(report['counts']['success'], 2)
        self.assertEqual(report['counts']['failed'], 1)
        for i in (0, 2):
            self.assertEqual(len(report['files'][i]['outputs']), 1)
            self.assertTrue(Path(report['files'][i]['outputs'][0]).is_file())
            self.assertTrue(report['files'][i]['logs'])
        self.assertIn('Ошибка', report['files'][1]['error'])
        self.assertEqual(report['parameters']['export_mode'], 'fast')
        self.assert_one_terminal()

    def test_no_motion_without_full_is_skipped(self):
        result = self.split(save_full=False)
        report = self.report(result)
        self.assertEqual(result.status, 'done')
        self.assertEqual(result.counts['skipped'], 3)
        self.assertTrue(all(not item['outputs'] for item in report['files']))
        self.assertIn('Движение не найдено', report['files'][0]['note'])

    def test_partial_export_failure_keeps_outputs_in_report(self):
        real_save = processing.save_full_video
        def fail_after_publication(*args, **kwargs):
            real_save(*args, **kwargs)
            raise ToolError('failure after first output')
        with patch.object(processing, 'save_full_video', side_effect=fail_after_publication):
            result = self.split(files=self.files[:1])
        item = self.report(result)['files'][0]
        self.assertEqual(item['status'], 'failed')
        self.assertEqual(len(item['outputs']), 1)
        self.assertTrue(Path(item['outputs'][0]).is_file())

    def test_cancel_middle_file_preserves_completed_and_unstarted(self):
        real_analyze = processing.analyze_video
        def analyze(path, *args, **kwargs):
            if Path(path) == self.files[1]:
                self.context.stop_event.set()
                raise TaskCancelled('cancel during second')
            return real_analyze(path, *args, **kwargs)
        with patch.object(processing, 'analyze_video', side_effect=analyze):
            result = self.split()
        self.assertEqual(result.status, 'cancelled')
        self.assertEqual(result.processed, 1)
        self.assertEqual([item['status'] for item in self.report(result)['files']],
                         ['success', 'cancelled', 'not_started'])
        self.assert_one_terminal()

    def test_cancel_with_published_output_reports_partial_result(self):
        real_save = processing.save_full_video
        def cancel_after_output(*args, **kwargs):
            real_save(*args, **kwargs)
            raise TaskCancelled('cancel after publication')
        with patch.object(processing, 'save_full_video', side_effect=cancel_after_output):
            result = self.split()
        report = self.report(result)
        self.assertEqual(result.status, 'cancelled')
        self.assertEqual(report['outputs_total'], 1)
        self.assertEqual(report['counts']['not_started'], 2)
        self.assertEqual(report['files'][0]['status'], 'cancelled')
        self.assertTrue(Path(report['files'][0]['outputs'][0]).is_file())

    def test_checkpoint_failure_stops_remaining_files(self):
        original_save = BatchReport.save
        failures = []
        def save(report):
            if report.counts()['success'] == 1 and not failures:
                failures.append(1)
                raise OSError('checkpoint write failed')
            return original_save(report)
        with patch.object(BatchReport, 'save', save):
            result = self.split()
        report = self.report(result)
        self.assertEqual(result.status, 'error')
        self.assertEqual(report['counts']['success'], 1)
        self.assertEqual(report['counts']['not_started'], 2)
        self.assertIn('checkpoint write failed', report['error'])

    def test_common_tool_failure_leaves_all_not_started(self):
        with patch.object(processing, 'find_ffmpeg', side_effect=ToolError('missing ffmpeg')):
            result = self.split()
        report = self.report(result)
        self.assertEqual(report['status'], 'error')
        self.assertEqual(result.counts['not_started'], 3)
        self.assertIn('missing ffmpeg', report['error'])
        self.assert_one_terminal()

    def test_enhance_continues_after_failure(self):
        def enhance(source, target, *args, **kwargs):
            if Path(source) == self.files[1]: raise ToolError('GPU operation failed')
            Path(target).write_bytes(b'test output')
        with patch.object(processing, 'find_realesrgan', return_value='fake'), \
             patch.object(processing, 'check_tools'), patch.object(processing, 'preflight_realesrgan'), \
             patch.object(processing, 'enhance_video_fallback_frames', side_effect=enhance):
            result = processing.run_enhance(self.context, list(map(str, self.files)), 'fake', 'model', 4, 0, 0)
        self.assertEqual([item['status'] for item in self.report(result)['files']], ['success', 'failed', 'success'])
        self.assertEqual(result.counts['failed'], 1)
        self.assert_one_terminal()

    def test_report_written_before_work_and_updated_after_each_file(self):
        real_analyze = processing.analyze_video
        states = []
        def analyze(path, *args, **kwargs):
            current = next(self.root.glob('motion_splitter_run_*/report.json'))
            states.append([i['status'] for i in json.loads(current.read_text())['files']])
            return real_analyze(path, *args, **kwargs)
        with patch.object(processing, 'analyze_video', side_effect=analyze):
            self.split(save_full=False)
        self.assertEqual(states[0], ['running', 'not_started', 'not_started'])
        self.assertEqual(states[1], ['skipped', 'running', 'not_started'])

    def test_failed_atomic_replace_keeps_previous_report(self):
        report = BatchReport(self.root, 'split', self.files, {})
        report.save(); original = report.path.read_bytes()
        report.data['status'] = 'done'
        with patch('batch_report.os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): report.save()
        self.assertEqual(report.path.read_bytes(), original)
        self.assertEqual(list(self.root.glob('.report-*.tmp')), [])

    def test_report_save_failure_is_task_error(self):
        with patch.object(BatchReport, 'save', side_effect=OSError('disk full')), \
             patch.object(processing, 'analyze_video') as analyze:
            result = self.split()
        self.assertEqual(result.status, 'error')
        self.assertIsNone(result.report_path)
        self.assertIn('отчёт', result.error)
        analyze.assert_not_called()
        self.assert_one_terminal()

    def test_summary_survives_ui_log_overflow(self):
        events = UIEvents(log_limit=1)
        events.put(('summary', 'report path and counts'))
        for i in range(20): events.put(('info', str(i)))
        events.put(('done', 3))
        messages = events.drain(50)
        self.assertIn(('summary', 'report path and counts'), messages)
        self.assertEqual(messages[-1], ('done', 3))
