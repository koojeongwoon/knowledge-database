from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Sequence

from src.core.database.base import BaseDatabaseManager
from src.indexing.domain.execution import (
    FileIndexingBatchResult,
    FileIndexingOutcome,
    FileIndexingResult,
)


IndexingTarget = tuple[str, bool]
FileProcessor = Callable[[str, bool, BaseDatabaseManager], FileIndexingOutcome]


@dataclass(frozen=True)
class FileIndexingExecutor:
    database_factory: Callable[[], BaseDatabaseManager]
    max_workers: int = 4

    def __post_init__(self) -> None:
        if self.max_workers < 1:
            raise ValueError("File indexing max_workers must be positive.")

    def execute(
        self,
        *,
        targets: Sequence[IndexingTarget],
        process_file: FileProcessor,
    ) -> FileIndexingBatchResult:
        if not targets:
            return FileIndexingBatchResult()

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = tuple(
                executor.submit(self._execute_one, path, is_new, process_file)
                for path, is_new in targets
            )
            results = tuple(future.result() for future in as_completed(futures))

        return FileIndexingBatchResult(
            items=tuple(sorted(results, key=lambda item: item.file_path))
        )

    def _execute_one(
        self,
        file_path: str,
        is_new: bool,
        process_file: FileProcessor,
    ) -> FileIndexingResult:
        database = None
        result: FileIndexingResult
        try:
            database = self.database_factory()
            outcome = process_file(file_path, is_new, database)
            if outcome is FileIndexingOutcome.FAILED:
                raise ValueError("File processor cannot return failed without an error.")
            result = FileIndexingResult(file_path=file_path, outcome=outcome)
        except Exception as exc:
            result = FileIndexingResult(
                file_path=file_path,
                outcome=FileIndexingOutcome.FAILED,
                error_message=str(exc) or type(exc).__name__,
            )

        if database is not None:
            try:
                database.close()
            except Exception as exc:
                close_error = str(exc) or type(exc).__name__
                prior_error = result.error_message
                result = FileIndexingResult(
                    file_path=file_path,
                    outcome=FileIndexingOutcome.FAILED,
                    error_message=(
                        f"{prior_error}; database close failed: {close_error}"
                        if prior_error
                        else f"database close failed: {close_error}"
                    ),
                )
        return result
