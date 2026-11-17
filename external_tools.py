"""Executable discovery and durable, bounded-memory process diagnostics."""
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


class ToolError(RuntimeError):
    pass


def resolve_executable(name, candidates=(), custom_path=""):
    """An explicit path is authoritative; never silently substitute another tool."""
    if custom_path:
        path = Path(custom_path).expanduser()
        if not path.is_file():
            raise ToolError(f"{name}: указанный путь не является файлом: {path}")
        if not os.access(path, os.X_OK):
            raise ToolError(f"{name}: нет разрешения на запуск: {path}")
        return str(path.resolve())
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
    found = shutil.which(name)
    if found and Path(found).is_file():
        return str(Path(found).resolve())
    raise ToolError(f"Не найден {name}. Установите программу и добавьте её в PATH "
                    "или укажите путь к исполняемому файлу.")


class ExternalProcessError(ToolError):
    def __init__(self, stage, log_path, detail, returncode=None):
        self.stage = stage
        self.log_path = Path(log_path)
        self.returncode = returncode
        super().__init__(f"{stage}: {detail}\nПолный журнал: {self.log_path}")


class ProcessRunner:
    def __init__(self, log_directory):
        self.log_directory = Path(log_directory)

    def run(self, command, stage, timeout=None):
        """No shell, no retry; stdout/stderr go straight to a persistent file."""
        self.log_directory.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="process-", suffix=".log", dir=self.log_directory)
        log_path = Path(name).resolve()
        command = [str(part) for part in command]
        outcome = None
        returncode = None
        with os.fdopen(fd, "wb") as log:
            header = {"time_utc": datetime.now(timezone.utc).isoformat(), "stage": stage,
                      "command": command, "cwd": str(Path.cwd())}
            log.write((json.dumps(header, ensure_ascii=False) + "\n--- stdout/stderr ---\n").encode("utf-8"))
            log.flush()
            try:
                result = subprocess.run(
                    command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    shell=False, timeout=timeout,
                    creationflags=0x08000000 if os.name == "nt" else 0)
                returncode = result.returncode
                if returncode:
                    outcome = f"Программа завершилась с кодом {returncode}."
            except subprocess.TimeoutExpired:
                outcome = f"Превышено время ожидания ({timeout} с)."
            except OSError as error:
                outcome = f"Не удалось запустить программу: {error}"
            log.write((f"\n--- completion ---\nreturncode={returncode}\n"
                       f"{outcome or 'Успешно'}\n").encode("utf-8"))
        if outcome:
            with log_path.open("rb") as log:
                log.seek(0, os.SEEK_END)
                log.seek(max(0, log.tell() - 4096))
                tail = log.read().decode("utf-8", errors="replace")
            raise ExternalProcessError(stage, log_path, outcome + "\n" + tail, returncode)
        return log_path

    def note(self, stage, message):
        """Record application-side errors (paths, publishing, invalid output)."""
        self.log_directory.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="application-", suffix=".log", dir=self.log_directory)
        with os.fdopen(fd, "w", encoding="utf-8") as log:
            log.write(f"{datetime.now(timezone.utc).isoformat()}\n{stage}\n{message}\n")
        return Path(name).resolve()


def check_tools(runner, ffmpeg_path, realesrgan_path=None):
    ffmpeg = resolve_executable("ffmpeg", custom_path=ffmpeg_path)
    # Resolve both before running a probe or processing any input video.
    if realesrgan_path is not None:
        resolve_executable("realesrgan-ncnn-vulkan", custom_path=realesrgan_path)
    runner.run([ffmpeg, "-version"], "Проверка запуска FFmpeg", timeout=15)
