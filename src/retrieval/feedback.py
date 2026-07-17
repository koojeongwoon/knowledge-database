import hashlib
import json
import re
import uuid
from typing import Any, Dict, List, Optional

from src.core.database.factory import DatabaseManager
from src.core.database.migrations import run_database_migrations


_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)\b(password|passwd|secret|api[_ -]?key)\s*[:=]\s*\S+"),
)
_FAILURE_REASONS = {
    "missing_answer", "irrelevant_results", "wrong_order",
    "insufficient_content", "intent_mismatch", "no_knowledge",
}


def redact_search_query(query: str) -> str:
    redacted = query
    for pattern in _SECRET_PATTERNS:
        if pattern.pattern.startswith("(?i)\\b(bearer"):
            redacted = pattern.sub(r"\1[REDACTED]", redacted)
        elif pattern.pattern.startswith("(?i)\\b(password"):
            redacted = pattern.sub(lambda match: f"{match.group(1)}=[REDACTED]", redacted)
        else:
            redacted = pattern.sub("[REDACTED]", redacted)
    return redacted[:4000]


class SearchFeedbackService:
    def __init__(self, db_manager=None, pipeline_version: str = "graph-context-confidence-v1"):
        self.db_manager = db_manager or DatabaseManager()
        self.pipeline_version = pipeline_version

    def initialize(self) -> None:
        run_database_migrations(self.db_manager)

    def record_event(self, owner_id: str, query: str, results: List[Dict[str, Any]]) -> str:
        search_id = str(uuid.uuid4())
        snapshots = []
        for rank, item in enumerate(results, 1):
            snapshots.append(self._snapshot(item, rank))
            for graph_item in item.get("graph_context", []):
                snapshots.append(self._snapshot(graph_item, None))
        with self.db_manager.cursor() as cur:
            cur.execute("""
                INSERT INTO knowledge_search_events (
                    search_id, owner_id, query_text, query_hash, returned_results,
                    result_count, pipeline_version
                ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
            """, (
                search_id, owner_id, redact_search_query(query),
                hashlib.sha256(query.encode("utf-8")).hexdigest(),
                json.dumps(snapshots), len(snapshots), self.pipeline_version,
            ))
        return search_id

    @staticmethod
    def _snapshot(item: Dict[str, Any], rank: Optional[int]) -> Dict[str, Any]:
        return {
            "file_path": item.get("file_path"), "title": item.get("title", ""),
            "rank": rank, "vector_similarity": float(item.get("vector_similarity", 0.0)),
            "lexical_rank": float(item.get("lexical_rank", 0.0)),
            "rrf_score": float(item.get("rrf_score", 0.0)),
            "retrieval_kind": item.get("retrieval_kind", "direct"),
            "search_sources": item.get("search_sources", []),
            "vector_chunk_index": item.get("vector_chunk_index"),
            "keyword_chunk_index": item.get("keyword_chunk_index"),
            "matched_chunk_index": item.get("matched_chunk_index"),
            "matched_chunk_preview": item.get("matched_chunk_preview", item.get("content", ""))[:500],
            "graph_weight": float(item.get("graph_weight", 0.0)),
            "graph_sources": item.get("graph_sources", []),
            "graph_target": item.get("graph_target", ""),
            "citation_count": int(item.get("citation_count", 0)),
        }

    def list_recent(self, owner_id: str, limit: int = 30) -> List[Dict[str, Any]]:
        with self.db_manager.cursor() as cur:
            cur.execute("""
                SELECT e.search_id, e.query_text, e.returned_results, e.result_count,
                       e.pipeline_version, e.created_at,
                       f.relevant_paths, f.irrelevant_paths, f.expected_no_answer,
                       f.missing_answer_path, f.notes, f.labeled_at,
                       f.partially_relevant_paths, f.satisfaction, f.failure_reasons
                FROM knowledge_search_events e
                LEFT JOIN knowledge_search_feedback f
                  ON f.search_id = e.search_id AND f.owner_id = e.owner_id
                WHERE e.owner_id = %s
                ORDER BY e.created_at DESC
                LIMIT %s
            """, (owner_id, max(1, min(limit, 100))))
            columns = [column[0] for column in cur.description]
            rows = []
            for row in cur.fetchall():
                item = dict(zip(columns, row))
                item["search_id"] = str(item["search_id"])
                item["created_at"] = item["created_at"].isoformat()
                item["labeled_at"] = item["labeled_at"].isoformat() if item["labeled_at"] else None
                rows.append(item)
            return rows

    def graph_for_event(self, owner_id: str, search_id: str) -> Dict[str, Any]:
        with self.db_manager.cursor() as cur:
            cur.execute("""
                SELECT query_text, returned_results, pipeline_version, created_at
                FROM knowledge_search_events
                WHERE search_id = %s AND owner_id = %s
            """, (search_id, owner_id))
            row = cur.fetchone()
        if not row:
            raise KeyError("검색 이벤트를 찾을 수 없습니다.")
        results = row[1] if isinstance(row[1], list) else json.loads(row[1])
        nodes = [{"data": {"id": "query", "kind": "query", "label": row[0], "query": row[0]}}]
        edges = []
        document_ids = {}
        for index, result in enumerate(results):
            path = result.get("file_path")
            if not path:
                continue
            doc_id = f"doc:{path}"
            document_ids[path] = doc_id
            nodes.append({"data": {
                "id": doc_id, "kind": "graph" if result.get("retrieval_kind") == "graph" else "document",
                "label": result.get("title") or path.rsplit("/", 1)[-1], "file_path": path,
                "rank": result.get("rank"), "vector_similarity": result.get("vector_similarity", 0),
                "lexical_rank": result.get("lexical_rank", 0), "rrf_score": result.get("rrf_score", 0),
                "retrieval_kind": result.get("retrieval_kind", "direct"),
                "graph_weight": result.get("graph_weight", 0), "citation_count": result.get("citation_count", 0),
            }})
            chunk_indexes = []
            for source, field in (("vector", "vector_chunk_index"), ("keyword", "keyword_chunk_index")):
                chunk_index = result.get(field)
                if chunk_index is None or (source, chunk_index) in chunk_indexes:
                    continue
                chunk_indexes.append((source, chunk_index))
                chunk_id = f"chunk:{path}:{source}:{chunk_index}"
                nodes.append({"data": {
                    "id": chunk_id, "parent": doc_id, "kind": "chunk", "source": source,
                    "label": f"{source} chunk #{chunk_index}", "file_path": path,
                    "chunk_index": chunk_index, "preview": result.get("matched_chunk_preview", ""),
                    "vector_similarity": result.get("vector_similarity", 0),
                    "lexical_rank": result.get("lexical_rank", 0), "rrf_score": result.get("rrf_score", 0),
                }})
                edges.append({"data": {"id": f"hit:{index}:{source}", "source": "query", "target": chunk_id, "kind": source}})
            if not chunk_indexes:
                edges.append({"data": {"id": f"hit:{index}", "source": "query", "target": doc_id, "kind": result.get("retrieval_kind", "direct")}})
        for index, result in enumerate(results):
            if result.get("retrieval_kind") != "graph":
                continue
            target_id = document_ids.get(result.get("file_path"))
            for source_path in result.get("graph_sources", []):
                source_id = document_ids.get(source_path)
                if source_id and target_id:
                    edges.append({"data": {"id": f"graph:{index}:{source_path}", "source": source_id, "target": target_id, "kind": "graph"}})
        return {
            "search_id": search_id, "query_text": row[0], "pipeline_version": row[2],
            "created_at": row[3].isoformat(), "nodes": nodes, "edges": edges,
        }

    def submit(
        self, owner_id: str, search_id: str, relevant_paths: List[str],
        irrelevant_paths: List[str], expected_no_answer: bool,
        missing_answer_path: Optional[str] = None, notes: Optional[str] = None,
        partially_relevant_paths: Optional[List[str]] = None,
        satisfaction: Optional[str] = None,
        failure_reasons: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        partially_relevant_paths = partially_relevant_paths or []
        failure_reasons = failure_reasons or []
        if satisfaction not in (None, "satisfied", "partial", "dissatisfied"):
            raise ValueError("올바르지 않은 전체 만족도입니다.")
        if not set(failure_reasons) <= _FAILURE_REASONS:
            raise ValueError("올바르지 않은 불만족 이유입니다.")
        if expected_no_answer and (relevant_paths or partially_relevant_paths):
            raise ValueError("정답 없음과 정답 문서는 동시에 선택할 수 없습니다.")
        if set(relevant_paths) & set(irrelevant_paths):
            raise ValueError("같은 문서를 정답과 오답으로 동시에 선택할 수 없습니다.")
        labeled_sets = [set(relevant_paths), set(partially_relevant_paths), set(irrelevant_paths)]
        if any(labeled_sets[i] & labeled_sets[j] for i in range(3) for j in range(i + 1, 3)):
            raise ValueError("같은 문서에 여러 관련도 라벨을 지정할 수 없습니다.")
        with self.db_manager.transaction() as cur:
            cur.execute(
                "SELECT returned_results FROM knowledge_search_events WHERE search_id = %s AND owner_id = %s",
                (search_id, owner_id),
            )
            row = cur.fetchone()
            if not row:
                raise KeyError("검색 이벤트를 찾을 수 없습니다.")
            returned = row[0] if isinstance(row[0], list) else json.loads(row[0])
            returned_paths = {item.get("file_path") for item in returned}
            if not set(relevant_paths + partially_relevant_paths + irrelevant_paths) <= returned_paths:
                raise ValueError("반환되지 않은 문서는 relevant/irrelevant로 지정할 수 없습니다.")
            cur.execute("""
                INSERT INTO knowledge_search_feedback (
                    search_id, owner_id, relevant_paths, irrelevant_paths,
                    expected_no_answer, missing_answer_path, notes, labeled_by, labeled_at,
                    partially_relevant_paths, satisfaction, failure_reasons
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s, %s, %s)
                ON CONFLICT (owner_id, search_id) DO UPDATE SET
                    relevant_paths = EXCLUDED.relevant_paths,
                    irrelevant_paths = EXCLUDED.irrelevant_paths,
                    expected_no_answer = EXCLUDED.expected_no_answer,
                    missing_answer_path = EXCLUDED.missing_answer_path,
                    notes = EXCLUDED.notes,
                    partially_relevant_paths = EXCLUDED.partially_relevant_paths,
                    satisfaction = EXCLUDED.satisfaction,
                    failure_reasons = EXCLUDED.failure_reasons,
                    labeled_by = EXCLUDED.labeled_by,
                    labeled_at = CURRENT_TIMESTAMP
                RETURNING labeled_at
            """, (
                search_id, owner_id, relevant_paths, irrelevant_paths,
                expected_no_answer, missing_answer_path or None, notes or None, owner_id,
                partially_relevant_paths, satisfaction, failure_reasons,
            ))
            labeled_at = cur.fetchone()[0]
        return {"search_id": search_id, "labeled_at": labeled_at.isoformat()}
