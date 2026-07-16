import json
import hashlib
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    query: str
    expected_paths: tuple[str, ...]
    expected_no_answer: bool = False
    query_type: str = "semantic"


@dataclass(frozen=True)
class BlindQuery:
    case_id: str
    query: str
    query_type: str = "semantic"


def load_evaluation_cases(path: str) -> List[EvaluationCase]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        EvaluationCase(
            case_id=item["id"],
            query=item["query"],
            expected_paths=tuple(item.get("expected_paths", [])),
            expected_no_answer=bool(item.get("expected_no_answer", False)),
            query_type=item.get("query_type", "semantic"),
        )
        for item in payload["cases"]
    ]


def load_blind_queries(path: str) -> List[BlindQuery]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("split") != "blind":
        raise ValueError("Blind query set must declare split='blind'.")
    forbidden = {"expected_paths", "expected_no_answer"}
    if any(forbidden.intersection(item) for item in payload["cases"]):
        raise ValueError("Blind query files must not contain answer fields.")
    return [
        BlindQuery(item["id"], item["query"], item.get("query_type", "semantic"))
        for item in payload["cases"]
    ]


def blind_query_fingerprint(queries: List[BlindQuery]) -> str:
    canonical = [
        {"id": item.case_id, "query": item.query, "query_type": item.query_type}
        for item in queries
    ]
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def run_blind_search(
    queries: List[BlindQuery],
    search: Callable[[str, int], List[Dict[str, Any]]],
    limit: int = 5,
) -> Dict[str, Any]:
    predictions = []
    for case in queries:
        started = time.perf_counter()
        documents = search(case.query, limit)
        predictions.append({
            "id": case.case_id,
            "returned_paths": [doc["file_path"] for doc in documents],
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        })
    return {
        "version": 1,
        "query_fingerprint": blind_query_fingerprint(queries),
        "limit": limit,
        "predictions": predictions,
    }


def score_blind_predictions(
    queries: List[BlindQuery],
    predictions_payload: Dict[str, Any],
    answers_payload: Dict[str, Any],
    development_cases: List[EvaluationCase],
    gates: Dict[str, float],
) -> Dict[str, Any]:
    fingerprint = blind_query_fingerprint(queries)
    if predictions_payload.get("query_fingerprint") != fingerprint:
        raise ValueError("Predictions were produced from a different blind query set.")
    if answers_payload.get("query_fingerprint") != fingerprint:
        raise ValueError("Answer key belongs to a different blind query set.")

    answer_map = {item["id"]: item for item in answers_payload["answers"]}
    prediction_map = {item["id"]: item for item in predictions_payload["predictions"]}
    query_ids = {item.case_id for item in queries}
    if set(answer_map) != query_ids or set(prediction_map) != query_ids:
        raise ValueError("Blind queries, predictions, and answers must have identical case IDs.")

    development_paths = {
        path for case in development_cases for path in case.expected_paths
    }
    blind_paths = {
        path for answer in answer_map.values() for path in answer.get("expected_paths", [])
    }
    overlap = sorted(development_paths.intersection(blind_paths))
    if overlap:
        raise ValueError(f"Document leakage between development and blind sets: {overlap}")

    cases = [
        EvaluationCase(
            case_id=query.case_id,
            query=query.query,
            expected_paths=tuple(answer_map[query.case_id].get("expected_paths", [])),
            expected_no_answer=bool(answer_map[query.case_id].get("expected_no_answer", False)),
            query_type=query.query_type,
        )
        for query in queries
    ]
    limit = int(predictions_payload.get("limit", 5))
    query_to_id = {case.query: case.case_id for case in cases}
    if len(query_to_id) != len(cases):
        raise ValueError("Blind queries must be unique.")
    report = evaluate_search(
        cases,
        lambda query, _limit: [
            {"file_path": path}
            for path in prediction_map[query_to_id[query]]["returned_paths"]
        ],
        limit=limit,
    )
    latencies = []
    for case_result in report["cases"]:
        latency = float(prediction_map[case_result["id"]].get("latency_ms", 0.0))
        case_result["latency_ms"] = latency
        latencies.append(latency)
    report["summary"]["latency_p50_ms"] = round(statistics.median(latencies), 2) if latencies else 0.0
    report["summary"]["latency_p95_ms"] = round(_percentile(latencies, 0.95), 2)
    summary = report["summary"]
    checks = {
        metric: {"minimum": minimum, "actual": summary.get(metric, 0.0), "passed": summary.get(metric, 0.0) >= minimum}
        for metric, minimum in gates.items()
    }
    report["quality_gate"] = {
        "passed": all(check["passed"] for check in checks.values()),
        "checks": checks,
    }
    report["query_fingerprint"] = fingerprint
    return report


def _percentile(values: List[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * percentile))))
    return ordered[index]


def evaluate_search(
    cases: List[EvaluationCase],
    search: Callable[[str, int], List[Dict[str, Any]]],
    limit: int = 5,
) -> Dict[str, Any]:
    results = []
    answer_cases = 0
    top1_hits = 0
    recall_hits = 0
    reciprocal_rank_sum = 0.0
    no_answer_expected = 0
    no_answer_correct = 0
    predicted_no_answer = 0
    latencies = []

    for case in cases:
        started = time.perf_counter()
        documents = search(case.query, limit)
        latency_ms = (time.perf_counter() - started) * 1000
        latencies.append(latency_ms)
        paths = [doc["file_path"] for doc in documents]

        rank = None
        if case.expected_paths:
            answer_cases += 1
            for index, path in enumerate(paths, 1):
                if path in case.expected_paths:
                    rank = index
                    break
            if rank == 1:
                top1_hits += 1
            if rank is not None and rank <= limit:
                recall_hits += 1
                reciprocal_rank_sum += 1.0 / rank

        is_no_answer = not paths
        if is_no_answer:
            predicted_no_answer += 1
        if case.expected_no_answer:
            no_answer_expected += 1
            if is_no_answer:
                no_answer_correct += 1

        results.append({
            "id": case.case_id,
            "query_type": case.query_type,
            "query": case.query,
            "expected_paths": list(case.expected_paths),
            "expected_no_answer": case.expected_no_answer,
            "returned_paths": paths,
            "rank": rank,
            "latency_ms": round(latency_ms, 2),
        })

    no_answer_precision = (
        no_answer_correct / predicted_no_answer if predicted_no_answer else 0.0
    )
    no_answer_recall = (
        no_answer_correct / no_answer_expected if no_answer_expected else 0.0
    )
    return {
        "summary": {
            "cases": len(cases),
            "answer_cases": answer_cases,
            "top1_accuracy": top1_hits / answer_cases if answer_cases else 0.0,
            f"recall_at_{limit}": recall_hits / answer_cases if answer_cases else 0.0,
            "mrr": reciprocal_rank_sum / answer_cases if answer_cases else 0.0,
            "no_answer_precision": no_answer_precision,
            "no_answer_recall": no_answer_recall,
            "latency_p50_ms": round(statistics.median(latencies), 2) if latencies else 0.0,
            "latency_p95_ms": round(_percentile(latencies, 0.95), 2),
        },
        "cases": results,
    }
