"""Bound DB statement counts independently of result count; no live DB required."""

from contextlib import contextmanager

from src.core.config import current_user_config
from src.retrieval.feedback import SearchFeedbackService
from src.retrieval.infrastructure.retrieval_repository import PostgresRetrievalRepository


class RecordingDatabase:
    def __init__(self):
        self.statements = []

    @contextmanager
    def transaction(self):
        yield self

    cursor = transaction

    def execute(self, sql, params=None):
        self.statements.append((sql, params))

    def executemany(self, sql, values):
        for value in values:
            self.execute(sql, value)

    def execute_batch(self, sql, values, **kwargs):
        self.executemany(sql, values)


def test_feedback_writes_one_event_and_one_candidate_statement_for_all_results():
    database = RecordingDatabase()
    results = [{"file_path": f"qa/{i}.md", "matched_chunk_index": i,
                "search_sources": ["vector", "keyword"], "vector_similarity": .8,
                "graph_context": [{"file_path": f"qa/graph-{i}.md", "retrieval_kind": "graph"}]}
               for i in range(5)]
    assert SearchFeedbackService(database).record_event("owner", "query", results)
    assert len(database.statements) <= 2


def test_citation_counter_writes_one_statement_including_repeated_paths():
    database = RecordingDatabase()
    token = current_user_config.set({"user_id": "owner"})
    try:
        PostgresRetrievalRepository(database).increment_citation_count(
            ["qa/b.md", "qa/a.md", "qa/a.md"]
        )
        assert len(database.statements) == 1
    finally:
        current_user_config.reset(token)
