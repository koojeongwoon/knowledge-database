import unittest
from datetime import datetime, timezone
from contextlib import contextmanager

from src.retrieval.feedback import SearchFeedbackService, redact_search_query


class SearchFeedbackTests(unittest.TestCase):
    def test_redacts_common_credentials_before_storage(self):
        query = "api_key=sk-abcdefghijklmnopqrstuvwxyz password=hunter2 AKIAABCDEFGHIJKLMNOP"

        redacted = redact_search_query(query)

        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", redacted)
        self.assertNotIn("hunter2", redacted)
        self.assertNotIn("AKIAABCDEFGHIJKLMNOP", redacted)
        self.assertGreaterEqual(redacted.count("[REDACTED]"), 3)

    def test_no_answer_cannot_include_relevant_paths(self):
        service = SearchFeedbackService(db_manager=object())

        with self.assertRaisesRegex(ValueError, "동시에"):
            service.submit("owner", "search", ["qa/a.md"], [], True)

    def test_same_path_cannot_be_relevant_and_irrelevant(self):
        service = SearchFeedbackService(db_manager=object())

        with self.assertRaisesRegex(ValueError, "동시에"):
            service.submit("owner", "search", ["qa/a.md"], ["qa/a.md"], False)

    def test_same_path_cannot_have_multiple_relevance_labels(self):
        service = SearchFeedbackService(db_manager=object())
        with self.assertRaisesRegex(ValueError, "여러"):
            service.submit("owner", "search", ["qa/a.md"], [], False,
                           partially_relevant_paths=["qa/a.md"])

    def test_satisfaction_value_is_validated(self):
        service = SearchFeedbackService(db_manager=object())
        with self.assertRaisesRegex(ValueError, "만족도"):
            service.submit("owner", "search", [], [], False, satisfaction="maybe")

    def test_no_answer_cannot_include_partially_relevant_paths(self):
        service = SearchFeedbackService(db_manager=object())
        with self.assertRaisesRegex(ValueError, "동시에"):
            service.submit("owner", "search", [], [], True,
                           partially_relevant_paths=["qa/a.md"])

    def test_graph_event_contains_document_and_chunk_nodes(self):
        class Cursor:
            def execute(self, *_args): pass
            def fetchone(self):
                return ("질문", [{"file_path": "qa/a.md", "title": "A", "rank": 1,
                    "vector_chunk_index": 3, "vector_similarity": 0.8,
                    "matched_chunk_preview": "matched text", "retrieval_kind": "direct"}],
                    "pipeline-v1", datetime.now(timezone.utc))
        class Manager:
            @contextmanager
            def cursor(self): yield Cursor()
        graph = SearchFeedbackService(db_manager=Manager()).graph_for_event("owner", "search")
        kinds = {node["data"]["kind"] for node in graph["nodes"]}
        self.assertEqual(kinds, {"query", "document", "chunk"})
        self.assertEqual(graph["edges"][0]["data"]["kind"], "vector")


if __name__ == "__main__":
    unittest.main()
