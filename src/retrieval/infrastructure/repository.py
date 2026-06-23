from abc import ABC, abstractmethod
from typing import Dict, List, Any
import json
import re
import os
from src.core.config import DB_TYPE

def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    dot = sum(x * y for x, y in zip(v1, v2))
    norm_v1 = sum(x * x for x in v1) ** 0.5
    norm_v2 = sum(y * y for y in v2) ** 0.5
    if norm_v1 * norm_v2 == 0:
        return 0.0
    return dot / (norm_v1 * norm_v2)


class BaseRetrievalRepository(ABC):
    @abstractmethod
    def keyword_search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def similarity_search(self, query_embedding: List[float], limit: int = 5) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def get_connected_documents(self, file_paths: List[str], limit: int = 3) -> List[Dict[str, Any]]:
        pass


class PostgresRetrievalRepository(BaseRetrievalRepository):
    def __init__(self, db_manager):
        self.db_manager = db_manager

    def keyword_search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        import re
        self.db_manager.connect()
        conn = self.db_manager.conn

        words = re.findall(r'\w+', query, re.UNICODE)
        words = [w for w in words if len(w) > 1]

        if not words:
            return []

        tsquery_str = " | ".join(words)

        with conn.cursor() as cur:
            try:
                sql = """
                SELECT file_path, chunk_index, doc_type, title, description, tags,
                       content, parent_content, raw_frontmatter,
                       ts_rank(
                           to_tsvector('simple', coalesce(content, '') || ' ' || coalesce(title, '')),
                           to_tsquery('simple', %s)
                       ) AS rank
                FROM knowledge_documents
                WHERE to_tsvector('simple', coalesce(content, '') || ' ' || coalesce(title, ''))
                      @@ to_tsquery('simple', %s)
                ORDER BY rank DESC
                LIMIT %s;
                """
                cur.execute(sql, (tsquery_str, tsquery_str, limit))
                columns = [col[0] for col in cur.description]
                results = []
                for row in cur.fetchall():
                    doc = dict(zip(columns, row))
                    if isinstance(doc.get("raw_frontmatter"), str):
                        doc["raw_frontmatter"] = json.loads(doc["raw_frontmatter"])
                    results.append(doc)
                return results
            except Exception as e:
                print(f"Warning: Keyword search failed ({e}). Falling back to empty results.")
                return []

    def similarity_search(self, query_embedding: List[float], limit: int = 5) -> List[Dict[str, Any]]:
        self.db_manager.connect()
        conn = self.db_manager.conn
        embedding_str = "[" + ",".join(map(str, query_embedding)) + "]"
        
        with conn.cursor() as cur:
            query = """
            SELECT 
                file_path, chunk_index, doc_type, title, description, tags, content, parent_content, raw_frontmatter,
                (1 - (embedding <=> %s)) AS similarity
            FROM knowledge_documents
            ORDER BY embedding <=> %s ASC
            LIMIT %s;
            """
            cur.execute(query, (embedding_str, embedding_str, limit))
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
            
        self.db_manager.connect()
        conn = self.db_manager.conn
        with conn.cursor() as cur:
            query_edges = """
            SELECT target_topic, MAX(weight) as weight
            FROM knowledge_edges 
            WHERE source_path = ANY(%s)
            GROUP BY target_topic
            ORDER BY weight DESC
            LIMIT %s;
            """
            cur.execute(query_edges, (file_paths, limit))
            edges_rows = cur.fetchall()
            
            if not edges_rows:
                return []
                
            topic_to_weight = {row[0].lower(): row[1] for row in edges_rows}
            topics_lower = list(topic_to_weight.keys())
                
            query_docs = """
            SELECT file_path, doc_type, title, description, tags, content, parent_content
            FROM knowledge_documents
            WHERE chunk_index = 0 AND (
                LOWER(title) = ANY(%s) OR
                SPLIT_PART(SPLIT_PART(file_path, '/', 2), '.', 1) = ANY(%s)
            );
            """
            cur.execute(query_docs, (topics_lower, topics_lower))
            
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
                results.append(doc)
            return results


class SqliteRetrievalRepository(BaseRetrievalRepository):
    def __init__(self, db_manager):
        self.db_manager = db_manager

    def keyword_search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        self.db_manager.connect()
        conn = self.db_manager.conn
        
        words = re.findall(r'\w+', query, re.UNICODE)
        words = [w for w in words if len(w) > 1]
        
        if not words:
            return []
            
        fts_query = " OR ".join([f'"{w}"' for w in words])
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
            SELECT d.file_path, d.chunk_index, d.doc_type, d.title, d.description, d.tags,
                   d.content, d.parent_content, d.raw_frontmatter,
                   (-knowledge_documents_fts.rank) AS rank
            FROM knowledge_documents d
            JOIN knowledge_documents_fts 
              ON d.file_path = knowledge_documents_fts.file_path AND d.chunk_index = knowledge_documents_fts.chunk_index
            WHERE knowledge_documents_fts MATCH ?
            ORDER BY rank DESC
            LIMIT ?;
            """, (fts_query, limit))
            
            results = []
            for row in cursor.fetchall():
                doc = dict(row)
                doc["tags"] = json.loads(doc["tags"]) if doc["tags"] else []
                doc["raw_frontmatter"] = json.loads(doc["raw_frontmatter"]) if doc["raw_frontmatter"] else {}
                results.append(doc)
            return results
        except Exception as e:
            print(f"Warning: SQLite FTS5 search failed ({e}). Falling back to empty results.")
            return []

    def similarity_search(self, query_embedding: List[float], limit: int = 5) -> List[Dict[str, Any]]:
        self.db_manager.connect()
        conn = self.db_manager.conn
        cursor = conn.cursor()
        cursor.execute("""
        SELECT file_path, chunk_index, doc_type, title, description, tags, content, parent_content, raw_frontmatter, embedding 
        FROM knowledge_documents;
        """)
        
        candidates = []
        for row in cursor.fetchall():
            doc = dict(row)
            if not doc["embedding"]:
                continue
            
            doc_emb = json.loads(doc["embedding"])
            similarity = cosine_similarity(query_embedding, doc_emb)
            doc["similarity"] = similarity
            
            del doc["embedding"]
            doc["tags"] = json.loads(doc["tags"]) if doc["tags"] else []
            doc["raw_frontmatter"] = json.loads(doc["raw_frontmatter"]) if doc["raw_frontmatter"] else {}
            candidates.append(doc)
            
        candidates.sort(key=lambda x: x["similarity"], reverse=True)
        return candidates[:limit]

    def get_connected_documents(self, file_paths: List[str], limit: int = 3) -> List[Dict[str, Any]]:
        if not file_paths:
            return []
            
        self.db_manager.connect()
        conn = self.db_manager.conn
        cursor = conn.cursor()
        
        placeholders = ",".join(["?"] * len(file_paths))
        try:
            cursor.execute(f"""
            SELECT target_topic, MAX(weight) as weight
            FROM knowledge_edges 
            WHERE source_path IN ({placeholders})
            GROUP BY target_topic
            ORDER BY weight DESC
            LIMIT ?;
            """, (*file_paths, limit))
            edges_rows = cursor.fetchall()
            topic_to_weight = {row["target_topic"].lower(): row["weight"] for row in edges_rows}
        except Exception:
            cursor.execute(f"""
            SELECT DISTINCT target_topic 
            FROM knowledge_edges 
            WHERE source_path IN ({placeholders})
            LIMIT ?;
            """, (*file_paths, limit))
            edges_rows = cursor.fetchall()
            topic_to_weight = {row["target_topic"].lower(): 1.0 for row in edges_rows}

        if not topic_to_weight:
            return []
            
        topics_lower = list(topic_to_weight.keys())
        results = []
        for topic in topics_lower:
            cursor.execute("""
            SELECT file_path, doc_type, title, description, tags, content, parent_content
            FROM knowledge_documents
            WHERE chunk_index = 0 AND (
                LOWER(title) = ? OR 
                LOWER(REPLACE(REPLACE(file_path, 'topics/', ''), '.md', '')) = ?
            );
            """, (topic, topic))
            
            row = cursor.fetchone()
            if row:
                doc = dict(row)
                doc["tags"] = json.loads(doc["tags"]) if doc["tags"] else []
                doc["edge_weight"] = topic_to_weight.get(topic, 1.0)
                results.append(doc)
                
        return results


def RetrievalRepository(db_manager) -> BaseRetrievalRepository:
    if DB_TYPE == "sqlite":
        return SqliteRetrievalRepository(db_manager)
    else:
        return PostgresRetrievalRepository(db_manager)
