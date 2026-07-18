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

    def test_record_event_snapshots_exposed_candidates_with_versions(self):
        class Cursor:
            def __init__(self):
                self.executed = []
                self.batch = []
            def execute(self, statement, params=None):
                self.executed.append((statement, params))
            def executemany(self, statement, values):
                self.batch.append((statement, values))
        class Manager:
            def __init__(self): self.cursor_value = Cursor()
            @contextmanager
            def transaction(self): yield self.cursor_value

        manager = Manager()
        service = SearchFeedbackService(
            db_manager=manager, ranking_config_version="rank-v2", ontology_version="ontology-v1",
        )
        search_id = service.record_event("owner", "질문", [{
            "file_path": "qa/a.md", "title": "A", "retrieval_kind": "direct",
            "vector_similarity": 0.8, "rrf_score": 1.0, "matched_chunk_index": 2,
        }])

        self.assertTrue(search_id)
        event_params = manager.cursor_value.executed[0][1]
        self.assertIn("rank-v2", event_params)
        self.assertIn("ontology-v1", event_params)
        self.assertEqual(len(manager.cursor_value.batch[0][1]), 1)
        self.assertEqual(manager.cursor_value.batch[0][1][0][2], "qa/a.md")

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

    def test_result_feedback_validates_grade_and_issue_reason(self):
        service = SearchFeedbackService(db_manager=object())
        with self.assertRaisesRegex(ValueError, "0부터 3"):
            service.submit("owner", "search", [], [], False, result_feedback=[{
                "file_path": "qa/a.md", "relevance_grade": 4,
            }])

    def test_ontology_feedback_contract_is_validated_before_database_access(self):
        service = SearchFeedbackService(db_manager=object())
        with self.assertRaisesRegex(ValueError, "관계 유형"):
            service.submit("owner", "search", [], [], False, expected_relations=[{
                "subject": "service", "predicate": "causes", "object": "outage",
            }])
        with self.assertRaisesRegex(ValueError, "그래프 경로"):
            service.submit("owner", "search", [], [], False, expected_graph_paths=[["one"]])
        with self.assertRaisesRegex(ValueError, "기대 규칙"):
            service.submit("owner", "search", [], [], False, expected_rule_types=["magic"])
        with self.assertRaisesRegex(ValueError, "맥락 관련도"):
            service.submit("owner", "search", [], [], False, result_feedback=[{
                "file_path": "qa/a.md", "relevance_grade": 2,
                "ontology_context_grade": 4,
            }])
        with self.assertRaisesRegex(ValueError, "문서 문제 이유"):
            service.submit("owner", "search", [], [], False, result_feedback=[{
                "file_path": "qa/a.md", "relevance_grade": 2,
                "issue_reasons": ["unknown_reason"],
            }])

    def test_behavior_action_is_validated_before_database_access(self):
        service = SearchFeedbackService(db_manager=object())
        with self.assertRaisesRegex(ValueError, "검색 행동"):
            service.record_behavior("owner", "search", "hover")

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
