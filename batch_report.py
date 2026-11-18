"""Atomic per-run journal. Outputs only include published files, never staging."""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


class BatchReport:
    def __init__(self, directory, mode, files, parameters):
        self.path = Path(directory) / 'report.json'
        self.data = {'schema_version': 1, 'mode': mode, 'status': 'running',
                     'started_at': now(), 'finished_at': None, 'elapsed_sec': None,
                     'parameters': {key: str(value) if isinstance(value, Path) else value for key, value in parameters.items()},
                     'log_directory': str(Path(directory) / 'logs'), 'error': None,
                     'files': [{'source': str(Path(path).absolute()), 'status': 'not_started',
                                'started_at': None, 'finished_at': None, 'elapsed_sec': None, 'output_directory': None,
                                'outputs': [], 'logs': [], 'segments': [], 'error': None, 'note': None}
                               for path in files]}

    def counts(self):
        return {state: sum(item['status'] == state for item in self.data['files'])
                for state in ('success', 'skipped', 'failed', 'cancelled', 'not_started', 'running')}

    def save(self):
        self.data['counts'] = self.counts()
        self.data['outputs_total'] = sum(len(item['outputs']) for item in self.data['files'])
        fd, name = tempfile.mkstemp(prefix='.report-', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(self.data, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)

    def collect_outputs(self, item):
        if item['output_directory']:
            directory = Path(item['output_directory'])
            item['outputs'] = [str(p) for p in sorted(directory.iterdir()) if p.is_file() and not p.name.startswith('.')]

    def summary(self):
        c = self.counts()
        return (f"Итог: успешно {c['success']}, пропущено {c['skipped']}, "
                f"ошибок {c['failed']}, отменено {c['cancelled']}, не начато {c['not_started']}. "
                f"Сохранено файлов: {sum(len(i['outputs']) for i in self.data['files'])}.\nОтчёт: {self.path}")
