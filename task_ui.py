"""Main-thread task state and bounded worker-to-UI events (no Tk dependency)."""
import threading
from collections import deque
from enum import Enum
from job_contracts import TaskEvent


class TaskState(str, Enum):
    IDLE = "Ожидание"
    RUNNING = "Обработка"
    CANCELLING = "Отмена запрошена"
    COMPLETED = "Завершено"
    CANCELLED = "Отменено"
    FAILED = "Ошибка"


class TaskController:
    def __init__(self):
        self.owner = threading.get_ident()
        self.state = TaskState.IDLE
        self.mode = None

    def assert_owner(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError("Состояние задачи изменяется только в основном потоке.")

    @property
    def busy(self):
        return self.state in (TaskState.RUNNING, TaskState.CANCELLING)

    def begin(self, mode):
        self.assert_owner()
        if self.busy:
            return False
        self.mode = mode
        self.state = TaskState.RUNNING
        return True

    def cancel(self):
        self.assert_owner()
        if self.state == TaskState.RUNNING:
            self.state = TaskState.CANCELLING

    def finish(self, state):
        self.assert_owner()
        if state not in (TaskState.COMPLETED, TaskState.CANCELLED, TaskState.FAILED):
            raise ValueError("Ожидалось конечное состояние")
        if not self.busy:
            raise RuntimeError("Нет активной задачи")
        self.state = state


class UIEvents:
    """Keep latest progress/frame, bounded log tail and an undroppable terminal.

    A new instance belongs to each run. Sorting by sequence preserves causal
    order after coalescing; file transitions invalidate old file progress/frame.
    Producers never wait for the UI to drain the buffer.
    """
    LATEST = {"total_init", "file_name", "file_progress", "total_progress", "preview_frame"}
    TERMINAL = {"done", "error", "cancelled"}

    def __init__(self, log_limit=256):
        if log_limit < 1:
            raise ValueError("log_limit must be positive")
        self.lock = threading.Lock()
        self.logs = deque(maxlen=log_limit)
        self.latest = {}
        self.terminal = None
        self.closed = False
        self.sequence = 0
        self.dropped = 0

    def put(self, message: TaskEvent):
        kind = message[0]
        with self.lock:
            if self.closed:
                return
            self.sequence += 1
            entry = (self.sequence, message)
            if kind in self.TERMINAL:
                self.terminal = entry
                self.closed = True
            elif kind in self.LATEST:
                if kind in ("file_name", "total_init"):
                    self.latest.pop("file_progress", None)
                    self.latest.pop("preview_frame", None)
                self.latest[kind] = entry
            else:
                if len(self.logs) == self.logs.maxlen:
                    self.dropped += 1
                self.logs.append(entry)

    def drain(self, limit=50):
        if limit < 1:
            return []
        with self.lock:
            entries = list(self.logs) + list(self.latest.values())
            if self.terminal:
                entries.append(self.terminal)
            budget = limit - bool(self.dropped)
            selected = sorted(entries, key=lambda entry: entry[0])[:budget]
            ids = {entry[0] for entry in selected}
            self.logs = deque((e for e in self.logs if e[0] not in ids), maxlen=self.logs.maxlen)
            self.latest = {k: e for k, e in self.latest.items() if e[0] not in ids}
            if self.terminal and self.terminal[0] in ids:
                self.terminal = None
            messages = [entry[1] for entry in selected]
            if self.dropped:
                messages.insert(0, ("info", f"Журнал интерфейса сокращён: пропущено сообщений {self.dropped}. "
                                   "Полная диагностика внешних программ сохранена в logs/."))
                self.dropped = 0
            return messages
