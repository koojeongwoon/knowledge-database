from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock

import pytest

from src.indexing.application.file_executor import FileIndexingExecutor
from src.indexing.domain.execution import FileIndexingOutcome


def test_executes_files_with_isolated_databases_and_immutable_results() -> None:
    databases = [MagicMock(), MagicMock()]
    database_factory = MagicMock(side_effect=databases)

    def process_file(path: str, is_new: bool, _database) -> FileIndexingOutcome:
        if path == "qa/fail.md":
            raise RuntimeError("embedding unavailable")
        return (
            FileIndexingOutcome.CREATED
            if is_new
            else FileIndexingOutcome.UPDATED
        )

    result = FileIndexingExecutor(database_factory, max_workers=2).execute(
        targets=(("qa/new.md", True), ("qa/fail.md", False)),
        process_file=process_file,
    )

    assert tuple(item.file_path for item in result.items) == (
        "qa/fail.md",
        "qa/new.md",
    )
    assert result.created_count == 1
    assert result.updated_count == 0
    assert result.failed_count == 1
    assert result.failures[0].error_message == "embedding unavailable"
    assert all(database.close.call_count == 1 for database in databases)
    with pytest.raises(FrozenInstanceError):
        result.items[0].outcome = FileIndexingOutcome.UPDATED


def test_database_creation_failure_becomes_file_failure() -> None:
    database_factory = MagicMock(side_effect=RuntimeError("pool unavailable"))
    process_file = MagicMock()

    result = FileIndexingExecutor(database_factory, max_workers=1).execute(
        targets=(("qa/a.md", True),),
        process_file=process_file,
    )

    assert result.failed_count == 1
    assert result.failures[0].error_message == "pool unavailable"
    process_file.assert_not_called()
