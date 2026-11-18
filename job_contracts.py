"""GUI-independent event and result contracts for processing tasks."""
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any, Literal, Optional, Protocol, Tuple

EventKind = Literal["phase", "info", "total_init", "file_name", "file_progress", "total_progress",
                    "preview_frame", "summary", "done", "cancelled", "error"]
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
    report_path: Optional[Path] = None
    counts: dict = field(default_factory=dict)


def finish(context, status, processed, total, output_directory, error=None, report_path=None, counts=None):
    result = JobResult(status, processed, total, output_directory, error, report_path, counts or {})
    context.ui_queue.put((status, error if status == "error" else processed))
    return result
