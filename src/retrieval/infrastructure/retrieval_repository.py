import json
import os
import re
from typing import Dict, List, Any

from src.retrieval.domain.repository import BaseRetrievalRepository


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    dot = sum(x * y for x, y in zip(v1, v2))
    norm_v1 = sum(x * x for x in v1) ** 0.5
    norm_v2 = sum(y * y for y in v2) ** 0.5
    if norm_v1 * norm_v2 == 0:
        return 0.0
    return dot / (norm_v1 * norm_v2)


class PostgresRetrievalRepository(BaseRetrievalRepository):
    """PostgreSQL 데이터베이스(pgvector 활용)를 타겟으로 지식 검색 및 RAG 문서를 조회하는 구체 인프라 구현체"""
    def __init__(self, db_manager):
        self.db_manager = db_manager

    def _get_owner_id(self) -> str:
        from src.core.config import current_user_config
        config = current_user_config.get() or {}
        return config.get("user_id", "SYSTEM")

    def keyword_search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        owner_id = self._get_owner_id()

        words = re.findall(r'\w+', query, re.UNICODE)
        words = [w for w in words if len(w) > 1]

        if not words:
            return []

        tsquery_str = " | ".join(words)

        with self.db_manager.cursor() as cur:
            sql = """
            SELECT d.file_path, d.chunk_index, d.doc_type, d.title, d.description, d.tags,
                   d.content, d.parent_content, d.raw_frontmatter,
                   COALESCE(c.citation_count, 0) AS citation_count,
                   ts_rank(
                       to_tsvector('simple', coalesce(d.content, '') || ' ' || coalesce(d.title, '')),
                       to_tsquery('simple', %s)
                   ) AS rank
            FROM knowledge_documents d
            LEFT JOIN knowledge_citations c ON d.file_path = c.file_path AND d.owner_id = c.owner_id
            WHERE to_tsvector('simple', coalesce(d.content, '') || ' ' || coalesce(d.title, '')) @@ to_tsquery('simple', %s)
                  AND d.file_path NOT LIKE 'baselines/%%'
                  AND d.file_path NOT LIKE 'baseline-drafts/%%'
                  AND ((d.visibility = 'public') OR (d.visibility = 'private' AND d.owner_id = %s))
            ORDER BY rank DESC
            LIMIT %s;
            """
            cur.execute(sql, (tsquery_str, tsquery_str, owner_id, limit))
            columns = [col[0] for col in cur.description]
            results = []
            for row in cur.fetchall():
                doc = dict(zip(columns, row))
                if isinstance(doc.get("raw_frontmatter"), str):
                    doc["raw_frontmatter"] = json.loads(doc["raw_frontmatter"])
                results.append(doc)
              
            return results

    def similarity_search(self, query_embedding: List[float], limit: int = 5) -> List[Dict[str, Any]]:
        owner_id = self._get_owner_id()
        embedding_str = "[" + ",".join(map(str, query_embedding)) + "]"
        
        with self.db_manager.cursor() as cur:
            query = """
            SELECT 
                d.file_path, d.chunk_index, d.doc_type, d.title, d.description, d.tags, d.content, d.parent_content, d.raw_frontmatter,
                (1 - (d.embedding <=> %s)) AS similarity,
                COALESCE(c.citation_count, 0) AS citation_count
            FROM knowledge_documents d
            LEFT JOIN knowledge_citations c ON d.file_path = c.file_path AND d.owner_id = c.owner_id
            WHERE ((d.visibility = 'public') OR (d.visibility = 'private' AND d.owner_id = %s))
              AND d.file_path NOT LIKE 'baselines/%%'
              AND d.file_path NOT LIKE 'baseline-drafts/%%'
            ORDER BY d.embedding <=> %s ASC
            LIMIT %s;
            """
            cur.execute(query, (embedding_str, owner_id, embedding_str, limit))
            columns = [col[0] for col in cur.description]
            results = []
            for row in cur.fetchall():
                doc = dict(zip(columns, row))
                if isinstance(doc["raw_frontmatter"], str):
                    doc["raw_frontmatter"] = json.loads(doc["raw_frontmatter"])
                results.append(doc)
            return results

    def get_connected_documents(self, file_paths: List[str], limit: int = 3) -> List[Dict[str, Any]]:
        if not file_paths:
            return []
            
        owner_id = self._get_owner_id()
        with self.db_manager.cursor() as cur:
            query_edges = """
            SELECT target_topic, MAX(weight) as weight, ARRAY_AGG(DISTINCT source_path) AS source_paths
            FROM knowledge_edges 
            WHERE source_path = ANY(%s)
              AND source_path NOT LIKE 'baselines/%%'
              AND source_path NOT LIKE 'baseline-drafts/%%'
              AND ((visibility = 'public') OR (visibility = 'private' AND owner_id = %s))
            GROUP BY target_topic
            ORDER BY weight DESC
            LIMIT %s;
            """
            cur.execute(query_edges, (file_paths, owner_id, limit))
            edges_rows = cur.fetchall()
            
            if not edges_rows:
                return []
                
            topic_to_weight = {row[0].lower(): row[1] for row in edges_rows}
            topic_to_sources = {row[0].lower(): row[2] for row in edges_rows}
            topics_lower = list(topic_to_weight.keys())
            path_patterns = [f"%/{topic}.md" for topic in topics_lower]
                
            query_docs = """
            SELECT file_path, doc_type, title, description, tags, content, parent_content
            FROM knowledge_documents
            WHERE chunk_index = 0
              AND file_path NOT LIKE 'baselines/%%'
              AND file_path NOT LIKE 'baseline-drafts/%%'
              AND ((visibility = 'public') OR (visibility = 'private' AND owner_id = %s))
              AND (
                LOWER(title) = ANY(%s) OR
                LOWER(file_path) LIKE ANY(%s)
            );
            """
            cur.execute(query_docs, (owner_id, topics_lower, path_patterns))
            
            columns = [col[0] for col in cur.description]
            results = []
            for row in cur.fetchall():
                doc = dict(zip(columns, row))
                t_title = doc["title"].lower()
                t_filename = os.path.splitext(os.path.basename(doc["file_path"]))[0].lower()
                
                weight = 1.0
                if t_title in topic_to_weight:
                    weight = topic_to_weight[t_title]
                elif t_filename in topic_to_weight:
                    weight = topic_to_weight[t_filename]
                    
                doc["edge_weight"] = weight
                matched_topic = t_title if t_title in topic_to_weight else t_filename
                doc["graph_target"] = matched_topic
                doc["graph_sources"] = topic_to_sources.get(matched_topic, [])
                results.append(doc)
            return results

    def increment_citation_count(self, file_paths: List[str]) -> None:
        """RAG 검색 결과로 인용된 문서들의 인용 횟수를 1 증가시킵니다."""
        if not file_paths:
            return
            
        import datetime
        
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        owner_id = self._get_owner_id()
        
        query = """
            INSERT INTO knowledge_citations (file_path, citation_count, last_cited_at, owner_id)
            VALUES %s
            ON CONFLICT (owner_id, file_path) DO UPDATE SET
                citation_count = knowledge_citations.citation_count + 1,
                last_cited_at = EXCLUDED.last_cited_at;
        """
        values = [(path, 1, now, owner_id) for path in file_paths]
        template = "(%s, %s, %s, %s)"
        
        self.db_manager.execute_batch(query, values, template=template)
