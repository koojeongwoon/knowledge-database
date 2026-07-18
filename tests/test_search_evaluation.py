import json
import tempfile
import unittest
from pathlib import Path

from src.retrieval.evaluation import (
    BlindQuery,
    EvaluationCase,
    OntologyEvaluationCase,
    RelationExpectation,
    blind_query_fingerprint,
    compare_direct_regression,
    evaluate_ontology_search,
    evaluate_search,
    load_blind_queries,
    load_evaluation_cases,
    load_ontology_evaluation_cases,
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

    def test_loads_and_validates_ontology_evaluation_contract(self):
        payload = {"kind": "ontology-search-evaluation", "cases": [{
            "id": "relation-1", "query": "what uses postgres",
            "expected_direct_paths": ["qa/service.md"],
            "expected_context_paths": ["topics/postgresql.md"],
            "forbidden_paths": ["qa/deprecated.md"],
            "expected_relations": [{
                "subject": "knowledge-base", "predicate": "uses", "object": "postgresql",
            }],
            "expected_rules": ["prefer-current"],
            "evidence_paths": ["qa/service.md"],
            "rationale": "The service implementation records PostgreSQL as its store.",
            "review_status": "verified",
        }]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ontology.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            cases = load_ontology_evaluation_cases(str(path))
        self.assertEqual(cases[0].expected_context_paths, ("topics/postgresql.md",))
        self.assertEqual(cases[0].expected_relations[0].predicate, "uses")
        self.assertEqual(cases[0].evidence_paths, ("qa/service.md",))
        self.assertEqual(cases[0].review_status, "verified")

    def test_rejects_unknown_ontology_review_status(self):
        payload = {"kind": "ontology-search-evaluation", "cases": [{
            "id": "bad-status", "query": "query", "review_status": "approved",
        }]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ontology.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid review_status"):
                load_ontology_evaluation_cases(str(path))

    def test_ontology_evaluation_keeps_direct_and_context_metrics_separate(self):
        cases = [OntologyEvaluationCase(
            case_id="relation", query="q", expected_direct_paths=("qa/a.md",),
            expected_context_paths=("topics/db.md",), forbidden_paths=("qa/old.md",),
            expected_relations=(RelationExpectation("service", "uses", "db"),),
            expected_rules=("prefer-current",),
        )]
        report = evaluate_ontology_search(cases, lambda _query, _limit: {
            "direct_paths": ["qa/a.md"],
            "context_paths": ["topics/db.md"],
            "relations": [{"subject": "service", "predicate": "uses", "object": "db"}],
            "applied_rules": ["prefer-current"],
        })
        self.assertEqual(report["summary"]["direct_top1_accuracy"], 1.0)
        self.assertEqual(report["summary"]["ontology_context_recall"], 1.0)
        self.assertEqual(report["summary"]["forbidden_exposure_rate"], 0.0)

    def test_direct_regression_gate_rejects_excessive_drop(self):
        report = compare_direct_regression(
            {"recall_at_5": 0.90, "mrr": 0.85},
            {"recall_at_5": 0.88, "mrr": 0.70},
            {"recall_at_5": 0.03, "mrr": 0.05},
        )
        self.assertFalse(report["passed"])
        self.assertTrue(report["checks"]["recall_at_5"]["passed"])
        self.assertFalse(report["checks"]["mrr"]["passed"])


if __name__ == "__main__":
    unittest.main()
