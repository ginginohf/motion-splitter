"""Disk budget and cancellable monitoring for a single upscaling workspace."""
import shutil
import time
from pathlib import Path
from external_tools import ToolError

GIB = 1024 ** 3


class DiskBudget:
    def __init__(self, directory, limit_gb=32, reserve_gb=2):
        import math
        if not (math.isfinite(limit_gb) and .25 <= limit_gb <= 4096 and
                math.isfinite(reserve_gb) and .1 <= reserve_gb <= 1024):
            raise ToolError('Некорректный лимит диска или резерв свободного места.')
        self.directory = Path(directory)
        self.limit = int(limit_gb * GIB)
        self.reserve = int(reserve_gb * GIB)
        self.next_check = 0

    def require(self, estimated):
        free = shutil.disk_usage(self.directory).free
        if estimated > self.limit or free - estimated < self.reserve:
            raise ToolError(f'Недостаточно бюджета диска: оценка {estimated/GIB:.2f} ГиБ, '
                            f'лимит {self.limit/GIB:.2f} ГиБ, свободно {free/GIB:.2f} ГиБ, '
                            f'резерв {self.reserve/GIB:.2f} ГиБ. Уменьшите масштаб/длину видео '
                            'или измените лимит и освободите место.')

    def check(self, force=False):
        now = time.monotonic()
        if not force and now < self.next_check:
            return
        self.next_check = now + .25
        used = sum(p.stat().st_size for p in self.directory.rglob('*') if p.is_file())
        if used >= self.limit or shutil.disk_usage(self.directory).free < self.reserve:
            raise ToolError('Апскейл остановлен: достигнут лимит временных файлов или резерв диска.')


def estimate_space(width, height, frames, scale):
    # Raw RGBA + PNG overhead and an extra raw-size allowance for encoded output.
    return int(width * height * frames * (1 + 2 * scale * scale) * 5 + 64 * 1024 ** 2)
