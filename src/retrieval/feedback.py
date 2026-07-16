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
        snapshots = [{
            "file_path": item.get("file_path"),
            "rank": rank,
            "vector_similarity": float(item.get("vector_similarity", 0.0)),
            "lexical_rank": float(item.get("lexical_rank", 0.0)),
            "rrf_score": float(item.get("rrf_score", 0.0)),
        } for rank, item in enumerate(results, 1)]
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

    def list_recent(self, owner_id: str, limit: int = 30) -> List[Dict[str, Any]]:
        with self.db_manager.cursor() as cur:
            cur.execute("""
                SELECT e.search_id, e.query_text, e.returned_results, e.result_count,
                       e.pipeline_version, e.created_at,
                       f.relevant_paths, f.irrelevant_paths, f.expected_no_answer,
                       f.missing_answer_path, f.notes, f.labeled_at
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

    def submit(
        self, owner_id: str, search_id: str, relevant_paths: List[str],
        irrelevant_paths: List[str], expected_no_answer: bool,
        missing_answer_path: Optional[str] = None, notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        if expected_no_answer and relevant_paths:
            raise ValueError("정답 없음과 정답 문서는 동시에 선택할 수 없습니다.")
        if set(relevant_paths) & set(irrelevant_paths):
            raise ValueError("같은 문서를 정답과 오답으로 동시에 선택할 수 없습니다.")
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
            if not set(relevant_paths + irrelevant_paths) <= returned_paths:
                raise ValueError("반환되지 않은 문서는 relevant/irrelevant로 지정할 수 없습니다.")
            cur.execute("""
                INSERT INTO knowledge_search_feedback (
                    search_id, owner_id, relevant_paths, irrelevant_paths,
                    expected_no_answer, missing_answer_path, notes, labeled_by, labeled_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (owner_id, search_id) DO UPDATE SET
                    relevant_paths = EXCLUDED.relevant_paths,
                    irrelevant_paths = EXCLUDED.irrelevant_paths,
                    expected_no_answer = EXCLUDED.expected_no_answer,
                    missing_answer_path = EXCLUDED.missing_answer_path,
                    notes = EXCLUDED.notes,
                    labeled_by = EXCLUDED.labeled_by,
                    labeled_at = CURRENT_TIMESTAMP
                RETURNING labeled_at
            """, (
                search_id, owner_id, relevant_paths, irrelevant_paths,
                expected_no_answer, missing_answer_path or None, notes or None, owner_id,
            ))
            labeled_at = cur.fetchone()[0]
        return {"search_id": search_id, "labeled_at": labeled_at.isoformat()}
