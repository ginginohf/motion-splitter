import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import check_ci


class CICheckTests(unittest.TestCase):
    def test_missing_and_partial_fixtures_are_distinct(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            self.assertEqual(check_ci.fixture_state(folder), 'missing')
            original = folder / 'static.mp4'
            original.write_bytes(b'keep existing fixture')
            with self.assertRaisesRegex(RuntimeError, 'Incomplete'):
                check_ci.fixture_state(folder)
            self.assertEqual(original.read_bytes(), b'keep existing fixture')

    def test_gui_missing_fixtures_fails_and_writes_summary(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(check_ci, 'ROOT', Path(temp)), patch('tools.check_ci.os.chdir'), \
             patch.object(sys, 'argv', ['check_ci.py', '--gui-only']), \
             patch.object(check_ci.ProcessRunner, 'run') as run, \
             contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(check_ci.main(), 1)
            run.assert_not_called()
            summary = json.loads((Path(temp) / 'tests/ci-artifacts/gui-summary.json').read_text())
            self.assertEqual(summary['status'], 'failed')

    def test_syntax_errors_fail_before_running_tests(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(check_ci, 'ROOT', Path(temp)):
            (Path(temp) / 'broken.py').write_text('def broken(:\n')
            with self.assertRaises(SyntaxError): check_ci.syntax_check()
