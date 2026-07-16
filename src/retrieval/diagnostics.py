from collections import Counter
from typing import Any, Dict, List

from src.core.config import LEXICAL_RANK_THRESHOLD, RRF_K, SIMILARITY_THRESHOLD
from src.retrieval.domain.model import Query, RankFusion
from src.retrieval.evaluation import blind_query_fingerprint


def _first_path_rank(documents: List[Dict[str, Any]], expected_paths: set[str]) -> int | None:
    for index, document in enumerate(documents, 1):
        if document["file_path"] in expected_paths:
            return index
    return None


def _best_signal(documents, expected_paths, key):
    values = [float(doc.get(key, 0.0)) for doc in documents if doc["file_path"] in expected_paths]
    return max(values) if values else 0.0


def diagnose_retrieval_stages(queries, answers_payload, searcher, production_limit=20, diagnostic_limit=100):
    if answers_payload.get("query_fingerprint") != blind_query_fingerprint(queries):
        raise ValueError("Answer key belongs to a different diagnostic query set.")
    answer_map = {item["id"]: item for item in answers_payload["answers"]}
    cases = []
    failures = Counter()

    for case in queries:
        answer = answer_map[case.case_id]
        expected_paths = set(answer.get("expected_paths", []))
        if not expected_paths:
            continue

        query_obj = Query(case.query)
        embedding = searcher.embedding_service.embed_text(query_obj.text)
        vector = searcher.repository.similarity_search(embedding, limit=diagnostic_limit)
        keywords = query_obj.get_clean_keywords()
        keyword_query = " ".join(keywords) if keywords else query_obj.text
        keyword = searcher.repository.keyword_search(keyword_query, limit=diagnostic_limit)

        production_vector = vector[:production_limit]
        production_keyword = keyword[:production_limit]
        fused = RankFusion.rrf_fusion(production_vector, production_keyword, k=RRF_K)
        filtered = [
            doc for doc in fused
            if doc.get("vector_similarity", 0.0) >= SIMILARITY_THRESHOLD
            or doc.get("lexical_rank", 0.0) >= LEXICAL_RANK_THRESHOLD
        ]
        final_documents = searcher.search(case.query, limit=5)

        vector_rank = _first_path_rank(vector, expected_paths)
        keyword_rank = _first_path_rank(keyword, expected_paths)
        fused_rank = _first_path_rank(fused, expected_paths)
        filtered_rank = _first_path_rank(filtered, expected_paths)
        final_rank = _first_path_rank(final_documents, expected_paths)
        vector_similarity = _best_signal(vector, expected_paths, "similarity")
        lexical_rank = _best_signal(keyword, expected_paths, "rank")
        production_present = (
            (vector_rank is not None and vector_rank <= production_limit)
            or (keyword_rank is not None and keyword_rank <= production_limit)
        )
        gate_pass = (
            vector_similarity >= SIMILARITY_THRESHOLD
            or lexical_rank >= LEXICAL_RANK_THRESHOLD
        )

        if final_rank is not None:
            failure_stage = "success"
        elif not production_present:
            failure_stage = "candidate_miss"
        elif not gate_pass:
            failure_stage = "gate_reject"
        else:
            failure_stage = "fusion_or_dedup_rank"
        failures[failure_stage] += 1
        cases.append({
            "id": case.case_id,
            "query_type": case.query_type,
            "expected_paths": sorted(expected_paths),
            "vector_rank_100": vector_rank,
            "keyword_rank_100": keyword_rank,
            "vector_similarity": round(vector_similarity, 6),
            "lexical_rank": round(lexical_rank, 6),
            "production_candidate_present": production_present,
            "gate_pass": gate_pass,
            "fused_rank": fused_rank,
            "filtered_rank": filtered_rank,
            "final_rank": final_rank,
            "failure_stage": failure_stage,
        })

    return {"summary": {"answer_cases": len(cases), "failure_stages": dict(failures)}, "cases": cases}
