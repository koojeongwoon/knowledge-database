from unittest.mock import Mock
from contextvars import ContextVar

import pytest

from src.api.exceptions import WikiBaseException
from src.api.handlers.retrieval import RetrievalApiHandler
from src.api.handlers.indexing import IndexingRetryApiHandler, IndexingRunApiHandler


def _retrieval_handler(database, searcher, feedback, audit=None):
    return RetrievalApiHandler(
        database_factory=lambda: database,
        embedding_factory=lambda: "embedding",
        searcher_factory=lambda db, embedding: searcher,
        feedback_factory=lambda db: feedback,
        formatter=lambda results: f"formatted:{results[0]['file_path']}",
        audit=audit or Mock(),
    )


def test_retrieval_handler_preserves_search_event_and_closes_database() -> None:
    database, searcher, feedback = Mock(), Mock(), Mock()
    searcher.search.return_value = [{"file_path": "qa/result.md"}]
    feedback.record_event.return_value = "event-1"

    result = _retrieval_handler(database, searcher, feedback).search("query", 5, "api-key", "owner")

    assert result == "Search Event ID: event-1\n\nformatted:qa/result.md"
    database.close.assert_called_once_with()


def test_retrieval_handler_keeps_direct_search_when_event_recording_fails() -> None:
    database, searcher, feedback = Mock(), Mock(), Mock()
    searcher.search.return_value = [{"file_path": "qa/result.md"}]
    feedback.record_event.side_effect = RuntimeError("event unavailable")

    result = _retrieval_handler(database, searcher, feedback).search("query", 5, "api-key", "owner")

    assert result == "formatted:qa/result.md"
    database.close.assert_called_once_with()


def test_retrieval_handler_wraps_search_failure_and_closes_database() -> None:
    database, searcher, feedback = Mock(), Mock(), Mock()
    searcher.search.side_effect = RuntimeError("search failed")

    with pytest.raises(WikiBaseException, match="search failed"):
        _retrieval_handler(database, searcher, feedback).search("query", 5, "api-key", "owner")

    database.close.assert_called_once_with()


def test_indexing_handler_preserves_requested_file_scope_and_closes_database() -> None:
    database, indexer = Mock(), Mock()
    indexer.run_indexing.return_value = {"created": 1}
    handler = IndexingRunApiHandler(
        database_factory=lambda: database,
        embedding_factory=lambda: "embedding",
        indexer_factory=lambda db, embedding: indexer,
        audit=Mock(),
    )

    result = handler.run(["qa/one.md"], "api-key")

    assert result == {"created": 1}
    indexer.run_indexing.assert_called_once_with(file_paths=["qa/one.md"])
    database.close.assert_called_once_with()


def test_indexing_handler_wraps_failure_and_closes_database() -> None:
    database, indexer = Mock(), Mock()
    indexer.run_indexing.side_effect = RuntimeError("index failed")
    handler = IndexingRunApiHandler(
        database_factory=lambda: database,
        embedding_factory=lambda: "embedding",
        indexer_factory=lambda db, embedding: indexer,
        audit=Mock(),
    )

    with pytest.raises(WikiBaseException, match="index failed"):
        handler.run(["qa/one.md"], "api-key")

    database.close.assert_called_once_with()


def test_retry_handler_isolates_owner_failure_and_restores_context() -> None:
    database, repository = Mock(), Mock()
    repository.claim.return_value = [
        {"owner_id": "owner-1", "file_path": "qa/one.md"},
        {"owner_id": "owner-2", "file_path": "qa/two.md"},
    ]
    settings = Mock()
    settings.get_runtime_config.side_effect = [{"key": "one"}, {"key": "two"}]
    context = ContextVar("test_retry_context", default={"original": True})
    observed = []

    def run_indexing(file_paths):
        observed.append(context.get().copy())
        if file_paths == ["qa/one.md"]:
            raise RuntimeError("owner failure")
        return '{"success": true, "data": {"created": 1}}'

    result = IndexingRetryApiHandler(
        database_factory=lambda: database,
        repository_factory=lambda db: repository,
        settings_factory=lambda: settings,
        run_indexing=run_indexing,
        config_context=context,
    ).retry(20, True)

    assert result["status"] == "partial_failure"
    assert result["processed"] == 1
    assert [item["user_id"] for item in observed] == ["owner-1", "owner-2"]
    assert context.get() == {"original": True}
    repository.fail.assert_called_once_with(["qa/one.md"], "owner failure", owner_id="owner-1")
    repository.complete.assert_called_once_with(["qa/two.md"], owner_id="owner-2")
    database.close.assert_called_once_with()
