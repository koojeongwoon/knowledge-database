import json
import re
from typing import Any

from src.retrieval.domain.model import RankFusion


class BaselineSearchRepository:
    def __init__(self, db_manager, owner_id: str, release_id: str):
        self.db_manager = db_manager
        self.owner_id = owner_id
        self.release_id = release_id

    def _release_exists(self) -> bool:
        with self.db_manager.cursor() as cur:
            cur.execute("""
                SELECT 1 FROM knowledge_baseline_releases
                WHERE owner_id = %s AND release_id = %s AND status = 'confirmed'
            """, (self.owner_id, self.release_id))
            return cur.fetchone() is not None

    def similarity_search(self, embedding: list[float], limit: int) -> list[dict[str, Any]]:
        vector = "[" + ",".join(map(str, embedding)) + "]"
        with self.db_manager.cursor() as cur:
            cur.execute("""
                SELECT file_path, snapshot_path, chunk_index, doc_type, title, description,
                       tags, content, parent_content, raw_frontmatter,
                       (1 - (embedding <=> %s)) AS similarity
                FROM knowledge_baseline_documents
                WHERE owner_id = %s AND release_id = %s
                ORDER BY embedding <=> %s ASC
                LIMIT %s
            """, (vector, self.owner_id, self.release_id, vector, limit))
            columns = [column[0] for column in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    def keyword_search(self, query: str, limit: int) -> list[dict[str, Any]]:
        words = [word for word in re.findall(r"\w+", query, re.UNICODE) if len(word) > 1]
        if not words:
            return []
        tsquery = " | ".join(words)
        with self.db_manager.cursor() as cur:
            cur.execute("""
                SELECT file_path, snapshot_path, chunk_index, doc_type, title, description,
                       tags, content, parent_content, raw_frontmatter,
                       ts_rank(
                           to_tsvector('simple', coalesce(content, '') || ' ' || coalesce(title, '')),
                           to_tsquery('simple', %s)
                       ) AS rank
                FROM knowledge_baseline_documents
                WHERE owner_id = %s AND release_id = %s
                  AND to_tsvector('simple', coalesce(content, '') || ' ' || coalesce(title, ''))
                      @@ to_tsquery('simple', %s)
                ORDER BY rank DESC
                LIMIT %s
            """, (tsquery, self.owner_id, self.release_id, tsquery, limit))
            columns = [column[0] for column in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]


class BaselineSearcher:
    def __init__(self, db_manager, embedding_service, owner_id: str):
        self.db_manager = db_manager
        self.embedding_service = embedding_service
        self.owner_id = owner_id

    def search(self, query: str, release_id: str, limit: int = 5) -> list[dict[str, Any]]:
        repository = BaselineSearchRepository(self.db_manager, self.owner_id, release_id)
        if not repository._release_exists():
            raise ValueError("사용할 수 있는 확정 기준본을 찾지 못했습니다.")
        candidate_limit = max(limit * 4, 20)
        vector = repository.similarity_search(
            self.embedding_service.embed_text(query), candidate_limit,
        )
        keyword = repository.keyword_search(query, candidate_limit)
        fused = RankFusion.rrf_fusion(vector, keyword)
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for document in fused:
            path = document["file_path"]
            if path in seen:
                continue
            seen.add(path)
            raw_frontmatter = document.get("raw_frontmatter")
            if isinstance(raw_frontmatter, str):
                raw_frontmatter = json.loads(raw_frontmatter)
            results.append({
                "file_path": document["snapshot_path"],
                "source_path": path,
                "doc_type": f"{document['doc_type']} (Baseline)",
                "title": document["title"],
                "description": document.get("description", ""),
                "tags": document.get("tags", []),
                "content": document.get("parent_content", document["content"]),
                "similarity": document.get("vector_similarity", 0.0),
                "vector_similarity": document.get("vector_similarity", 0.0),
                "lexical_rank": document.get("lexical_rank", 0.0),
                "rrf_score": document.get("rrf_score", 0.0),
                "raw_frontmatter": raw_frontmatter,
                "retrieval_kind": "baseline",
            })
            if len(results) >= limit:
                break
        return results
