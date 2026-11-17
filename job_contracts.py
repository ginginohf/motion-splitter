"""GUI-independent event and result contracts for processing tasks."""
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any, Literal, Optional, Protocol, Tuple

EventKind = Literal["phase", "info", "total_init", "file_name", "file_progress", "total_progress",
                    "preview_frame", "done", "cancelled", "error"]
TaskEvent = Tuple[EventKind, Any]
Status = Literal["done", "cancelled", "error"]


class EventSink(Protocol):
    def put(self, message: TaskEvent) -> None:
        ...


@dataclass(frozen=True)
class TaskContext:
    ui_queue: EventSink
    stop_event: Event


@dataclass(frozen=True)
class JobResult:
    status: Status
    processed: int
    total: int
    output_directory: Optional[Path]
    error: Optional[str] = None


def finish(context, status, processed, total, output_directory, error=None):
    result = JobResult(status, processed, total, output_directory, error)
    context.ui_queue.put((status, error if status == "error" else processed))
    return result
