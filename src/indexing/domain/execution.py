from dataclasses import dataclass
from enum import Enum
from typing import TypedDict


class FileIndexingOutcome(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    FAILED = "failed"


class IndexingStats(TypedDict):
    created: int
    updated: int
    deleted: int
    skipped: int


@dataclass(frozen=True)
class FileIndexingResult:
    file_path: str
    outcome: FileIndexingOutcome
    error_message: str | None = None

    def __post_init__(self) -> None:
        if not self.file_path:
            raise ValueError("File indexing result requires a file path.")
        if self.outcome is FileIndexingOutcome.FAILED and not self.error_message:
            raise ValueError("Failed file indexing result requires an error message.")
        if self.outcome is not FileIndexingOutcome.FAILED and self.error_message:
            raise ValueError("Successful file indexing result cannot contain an error message.")


@dataclass(frozen=True)
class FileIndexingBatchResult:
    items: tuple[FileIndexingResult, ...] = ()

    def __post_init__(self) -> None:
        paths = tuple(item.file_path for item in self.items)
        if len(paths) != len(set(paths)):
            raise ValueError("File indexing batch cannot contain duplicate paths.")
        if paths != tuple(sorted(paths)):
            raise ValueError("File indexing batch results must be sorted by path.")

    @property
    def created_count(self) -> int:
        return self._count(FileIndexingOutcome.CREATED)

    @property
    def updated_count(self) -> int:
        return self._count(FileIndexingOutcome.UPDATED)

    @property
    def failed_count(self) -> int:
        return self._count(FileIndexingOutcome.FAILED)

    @property
    def failures(self) -> tuple[FileIndexingResult, ...]:
        return tuple(
            item for item in self.items
            if item.outcome is FileIndexingOutcome.FAILED
        )

    def _count(self, outcome: FileIndexingOutcome) -> int:
        return sum(item.outcome is outcome for item in self.items)
