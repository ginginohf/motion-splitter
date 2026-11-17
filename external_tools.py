"""Executable discovery and durable, bounded-memory process diagnostics."""
import json
import sys
import os
import shutil
import subprocess
import tempfile
import time
import signal
from datetime import datetime, timezone
from pathlib import Path


class ToolError(RuntimeError):
    pass


class TaskCancelled(Exception):
    """Cooperative cancellation, distinct from an execution failure."""


def check_cancel(stop_event):
    if stop_event is not None and stop_event.is_set():
        raise TaskCancelled("Обработка отменена пользователем.")


def stop_process(process):
    """Reap the child before temporary files are removed. Called by worker only."""
    if os.name == "nt":
        # Kill the owned tree, never processes selected by name.
        if process.poll() is None:
            try:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=3, creationflags=0x08000000)
            except (OSError, subprocess.TimeoutExpired):
                pass
            if process.poll() is None:
                process.kill()
        process.wait()
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass
    # A descendant may outlive the leader or ignore SIGTERM.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


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
    def __init__(self, log_directory, stop_event=None):
        self.log_directory = Path(log_directory)
        self.stop_event = stop_event

    def check_cancel(self):
        check_cancel(self.stop_event)

    def run(self, command, stage, timeout=None):
        """No shell, no retry; stdout/stderr go straight to a persistent file."""
        self.check_cancel()
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
            process = None
            cancelled = False
            started = time.monotonic()
            try:
                self.check_cancel()
                process = subprocess.Popen(
                    command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    shell=False, start_new_session=(os.name != "nt"),
                    creationflags=0x08000000 if os.name == "nt" else 0)
                while True:
                    self.check_cancel()
                    returncode = process.poll()
                    if returncode is not None:
                        break
                    if timeout is not None and time.monotonic() - started >= timeout:
                        raise subprocess.TimeoutExpired(command, timeout)
                    if self.stop_event is not None:
                        self.stop_event.wait(.05)
                    else:
                        time.sleep(.05)
                self.check_cancel()
                if returncode:
                    outcome = f"Программа завершилась с кодом {returncode}."
            except TaskCancelled:
                cancelled = True
                outcome = "Отменено пользователем."
            except subprocess.TimeoutExpired:
                outcome = f"Превышено время ожидания ({timeout} с)."
            except OSError as error:
                outcome = f"Не удалось запустить программу: {error}"
            finally:
                if process is not None:
                    if cancelled or outcome or process.poll() is None:
                        stop_process(process)
                    else:
                        process.wait()
                    returncode = process.returncode
            log.write((f"\n--- completion ---\nreturncode={returncode}\n"
                       f"{outcome or 'Успешно'}\n").encode("utf-8"))
        if cancelled:
            raise TaskCancelled(f"{stage}: отменено. Журнал: {log_path}")
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


def find_ffmpeg():
    exe_dir = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    candidates = [exe_dir / "ffmpeg" / "ffmpeg.exe", exe_dir / "ffmpeg.exe",
                  exe_dir / "ffmpeg" / "bin" / "ffmpeg.exe",
                  exe_dir / "ffmpeg" / "bin" / "ffmpeg", exe_dir / "ffmpeg"]
    return resolve_executable("ffmpeg", candidates)


def find_realesrgan(custom_path=""):
    exe_dir = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    candidates = [exe_dir / name for name in ("realesrgan-ncnn-vulkan.exe", "realesrgan-ncnn-vulkan")]
    candidates += [exe_dir / "realesrgan" / name
                   for name in ("realesrgan-ncnn-vulkan.exe", "realesrgan-ncnn-vulkan")]
    return resolve_executable("realesrgan-ncnn-vulkan", candidates, custom_path)
