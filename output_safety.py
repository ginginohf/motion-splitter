"""Output ownership, discovery and no-overwrite publication of completed files."""
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


RUN_PREFIX = "motion_splitter_run_"
WORK_PREFIX = ".motion-splitter-work-"
RUN_MARKER = ".motion-splitter-output"
LEGACY_RESULT = re.compile(r"(?:_upscaled|_full|_segment_\d+)$", re.IGNORECASE)


def create_run_directory(parent):
    """Atomically allocate a fresh directory; never reuse a previous run."""
    parent = Path(parent).resolve(strict=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_")
    directory = Path(tempfile.mkdtemp(prefix=RUN_PREFIX + stamp, dir=parent))
    (directory / RUN_MARKER).write_text("Motion Splitter output, format 1\n", encoding="utf-8")
    return directory


def source_output_directory(run_directory, source):
    # Include extension: clip.mp4 and clip.mov must not share their outputs.
    directory = Path(run_directory) / Path(source).name
    directory.mkdir(exist_ok=False)
    return directory


def is_generated_path(path):
    path = Path(path).resolve()
    if LEGACY_RESULT.search(path.stem):
        return True
    return any(
        parent.name.startswith((RUN_PREFIX, WORK_PREFIX))
        or parent.name.lower().endswith(("_segment", "_frames", "_frames_up"))
        or (parent / RUN_MARKER).exists()
        for parent in path.parents
    )


def collect_folder_videos(folder, extensions):
    """Folder mode skips generated files; explicitly selected files are allowed."""
    return [str(path) for path in sorted(Path(folder).iterdir(), key=lambda p: p.name.casefold())
            if path.is_file() and path.suffix.lower() in extensions and not is_generated_path(path)]


def validate_output_path(source, destination):
    source = Path(source).resolve(strict=True)
    destination = Path(destination).absolute()
    if not source.is_file():
        raise ValueError(f"Источник не является файлом: {source}")
    if source == destination.resolve():
        raise ValueError("Пути исходного и выходного файла совпадают.")
    # lexists also rejects dangling symlinks. Existing files, directories and
    # hardlinks are all refused, without touching their contents.
    if os.path.lexists(destination):
        raise FileExistsError(f"Результат уже существует, перезапись запрещена: {destination}")
    return destination


@contextmanager
def staged_output(source, destination):
    """Yield a private staging path, publish on success without replacing files.

    The temporary directory lives on the destination filesystem. Hard-link
    publication is atomic and fails if another writer claimed the destination.
    Filesystems without hardlink support fail safely, rather than falling back
    to an overwrite-prone rename. Media validity is checked in a later stage.
    """
    destination = validate_output_path(source, destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=WORK_PREFIX, dir=destination.parent) as work:
        staged = Path(work) / ("result" + destination.suffix)
        yield staged
        if staged.is_symlink() or not staged.is_file() or staged.stat().st_size == 0:
            raise ValueError("Обработка не создала непустой выходной файл.")
        validate_output_path(source, destination)
        try:
            os.link(staged, destination)
        except FileExistsError:
            raise FileExistsError(f"Результат уже существует, перезапись запрещена: {destination}")
        except OSError as error:
            raise OSError(
                f"Не удалось безопасно сохранить результат в {destination.parent}. "
                "Нужна файловая система с поддержкой жёстких ссылок "
                f"(например, APFS, NTFS или ext4). Причина: {error}"
            ) from error
