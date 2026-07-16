import unittest

from src.retrieval.diagnostics import _best_signal, _first_path_rank


class RetrievalDiagnosticsTests(unittest.TestCase):
    def test_reports_first_expected_rank_and_best_signal_across_chunks(self):
        documents = [
            {"file_path": "qa/x.md", "similarity": 0.8},
            {"file_path": "qa/a.md", "similarity": 0.4},
            {"file_path": "qa/a.md", "similarity": 0.6},
        ]

        self.assertEqual(_first_path_rank(documents, {"qa/a.md"}), 2)
        self.assertEqual(_best_signal(documents, {"qa/a.md"}, "similarity"), 0.6)
        self.assertIsNone(_first_path_rank(documents, {"qa/missing.md"}))
