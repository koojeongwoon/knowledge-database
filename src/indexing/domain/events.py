from dataclasses import dataclass
from enum import Enum
from typing import Protocol, TypeAlias


class IndexingEventKind(str, Enum):
    STARTED = "started"
    FILE_STARTED = "file_started"
    EMBEDDING_STARTED = "embedding_started"
    ONTOLOGY_SHADOW = "ontology_shadow"
    WARNING = "warning"
    FILE_DELETED = "file_deleted"
    MODE_SELECTED = "mode_selected"
    FILE_FAILED = "file_failed"
    COMPLETED = "completed"


EventDetailValue: TypeAlias = str | int | float | bool | None


@dataclass(frozen=True)
class IndexingEvent:
    kind: IndexingEventKind
    message: str
    file_path: str | None = None
    details: tuple[tuple[str, EventDetailValue], ...] = ()


class IndexingObserver(Protocol):
    def emit(self, event: IndexingEvent) -> None: ...


class NullIndexingObserver:
    def emit(self, event: IndexingEvent) -> None:
        pass
