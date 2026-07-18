from dataclasses import FrozenInstanceError
import logging

import pytest

from src.indexing.domain.events import IndexingEvent, IndexingEventKind
from src.indexing.infrastructure.observer import LoggingIndexingObserver


def test_indexing_event_is_immutable() -> None:
    event = IndexingEvent(
        IndexingEventKind.FILE_STARTED,
        "file indexing started",
        file_path="qa/example.md",
        details=(("is_new", True),),
    )

    with pytest.raises(FrozenInstanceError):
        event.message = "changed"  # type: ignore[misc]


def test_logging_observer_routes_warning_events_to_warning_level(caplog) -> None:
    observer = LoggingIndexingObserver(logging.getLogger("tests.indexing"))

    with caplog.at_level(logging.INFO, logger="tests.indexing"):
        observer.emit(IndexingEvent(IndexingEventKind.STARTED, "started"))
        observer.emit(IndexingEvent(IndexingEventKind.WARNING, "degraded"))

    assert [(record.levelname, record.message) for record in caplog.records] == [
        ("INFO", "started"),
        ("WARNING", "degraded"),
    ]
