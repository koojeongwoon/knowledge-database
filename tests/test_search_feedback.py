import unittest

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


if __name__ == "__main__":
    unittest.main()
