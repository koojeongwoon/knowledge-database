import json
import tempfile
import unittest
from pathlib import Path

from src.retrieval.evaluation import (
    BlindQuery,
    EvaluationCase,
    blind_query_fingerprint,
    evaluate_search,
    load_blind_queries,
    load_evaluation_cases,
    run_blind_search,
    score_blind_predictions,
)


class SearchEvaluationTests(unittest.TestCase):
    def test_loads_cases_from_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            path.write_text(json.dumps({"cases": [{
                "id": "case-1",
                "query": "query",
                "expected_paths": ["qa/a.md"],
                "query_type": "exact",
            }]}), encoding="utf-8")
            cases = load_evaluation_cases(str(path))
        self.assertEqual(cases[0].expected_paths, ("qa/a.md",))

    def test_computes_ranking_and_no_answer_metrics(self):
        cases = [
            EvaluationCase("top1", "q1", ("qa/a.md",)),
            EvaluationCase("rank2", "q2", ("qa/b.md",)),
            EvaluationCase("none", "q3", (), expected_no_answer=True),
        ]
        responses = {
            "q1": [{"file_path": "qa/a.md"}],
            "q2": [{"file_path": "qa/x.md"}, {"file_path": "qa/b.md"}],
            "q3": [],
        }
        report = evaluate_search(cases, lambda query, limit: responses[query], limit=3)
        summary = report["summary"]
        self.assertEqual(summary["top1_accuracy"], 0.5)
        self.assertEqual(summary["recall_at_3"], 1.0)
        self.assertEqual(summary["mrr"], 0.75)
        self.assertEqual(summary["no_answer_precision"], 1.0)
        self.assertEqual(summary["no_answer_recall"], 1.0)

    def test_blind_query_file_rejects_embedded_answers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "blind.json"
            path.write_text(json.dumps({"split": "blind", "cases": [{
                "id": "leak", "query": "query", "expected_paths": ["qa/a.md"]
            }]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must not contain answer"):
                load_blind_queries(str(path))

    def test_scores_frozen_blind_predictions_against_separate_answers(self):
        queries = [
            BlindQuery("answer", "q1"),
            BlindQuery("none", "q2", "no-answer"),
        ]
        fingerprint = blind_query_fingerprint(queries)
        predictions = {
            "query_fingerprint": fingerprint,
            "limit": 5,
            "predictions": [
                {"id": "answer", "returned_paths": ["qa/blind.md"], "latency_ms": 20},
                {"id": "none", "returned_paths": [], "latency_ms": 10},
            ],
        }
        answers = {
            "query_fingerprint": fingerprint,
            "answers": [
                {"id": "answer", "expected_paths": ["qa/blind.md"]},
                {"id": "none", "expected_paths": [], "expected_no_answer": True},
            ],
        }
        report = score_blind_predictions(
            queries, predictions, answers,
            [EvaluationCase("dev", "dev", ("qa/development.md",))],
            {"top1_accuracy": 1.0, "no_answer_recall": 1.0},
        )
        self.assertTrue(report["quality_gate"]["passed"])
        self.assertEqual(report["summary"]["latency_p50_ms"], 15.0)

    def test_rejects_document_leakage_from_development_set(self):
        queries = [BlindQuery("answer", "q1")]
        fingerprint = blind_query_fingerprint(queries)
        predictions = {"query_fingerprint": fingerprint, "limit": 5, "predictions": [
            {"id": "answer", "returned_paths": ["qa/shared.md"]}
        ]}
        answers = {"query_fingerprint": fingerprint, "answers": [
            {"id": "answer", "expected_paths": ["qa/shared.md"]}
        ]}
        with self.assertRaisesRegex(ValueError, "Document leakage"):
            score_blind_predictions(
                queries, predictions, answers,
                [EvaluationCase("dev", "dev", ("qa/shared.md",))], {},
            )


if __name__ == "__main__":
    unittest.main()
