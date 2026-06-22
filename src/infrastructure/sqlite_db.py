import sqlite3
import json
import re
from typing import List, Dict, Any
from src.core.config import DB_NAME
from src.infrastructure.base import BaseDatabaseManager

def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    dot = sum(x * y for x, y in zip(v1, v2))
    norm_v1 = sum(x * x for x in v1) ** 0.5
    norm_v2 = sum(y * y for y in v2) ** 0.5
    if norm_v1 * norm_v2 == 0:
        return 0.0
    return dot / (norm_v1 * norm_v2)

class SqliteDatabaseManager(BaseDatabaseManager):
    def __init__(self):
        # DB_TYPE=sqlite일 때 DB_NAME이 디렉토리나 비어있으면 기본 파일명 할당
        self.db_path = DB_NAME if DB_NAME and not DB_NAME.endswith("db_name") else "knowledge.db"
        if self.db_path == "knowledge_db":
            self.db_path = "knowledge.db"
        self.conn = None

    def connect(self):
        if not self.conn:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.execute("PRAGMA foreign_keys = ON;")
            self.conn.row_factory = sqlite3.Row

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def initialize_db(self):
        """
        SQLite 테이블 및 FTS5 가상 테이블을 생성합니다.
        """
        self.connect()
        cursor = self.conn.cursor()
        
        # 1. 메인 문서 테이블 생성
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            doc_type TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            tags TEXT,
            content TEXT NOT NULL,
            parent_content TEXT NOT NULL,
            raw_frontmatter TEXT,
            content_hash TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            embedding TEXT,
            UNIQUE(file_path, chunk_index)
        );
        """)
        
        # 2. 엣지 연결 테이블 생성
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_path TEXT NOT NULL,
            target_topic TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_path, target_topic)
        );
        """)
        
        # 3. FTS5 가상 검색 테이블 생성 (SQLite Full-Text Search용)
        cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_documents_fts USING fts5(
            file_path UNINDEXED,
            chunk_index UNINDEXED,
            title,
            content
        );
        """)
        
        self.conn.commit()

    def get_all_file_hashes(self) -> Dict[str, str]:
        self.connect()
        cursor = self.conn.cursor()
        cursor.execute("SELECT DISTINCT file_path, content_hash FROM knowledge_documents;")
        rows = cursor.fetchall()
        return {row["file_path"]: row["content_hash"] for row in rows}

    def _json_serializer(self, obj):
        import datetime
        if isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        raise TypeError(f"Type {type(obj)} not serializable")

    def upsert_document_chunk(self, doc_data: Dict[str, Any]):
        self.upsert_document_chunks_batch([doc_data])

    def upsert_document_chunks_batch(self, chunks: List[Dict[str, Any]], batch_size: int = 50):
        if not chunks:
            return
        
        self.connect()
        cursor = self.conn.cursor()
        
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            for doc_data in batch:
                tags = doc_data.get("tags", [])
                if tags is None:
                    tags = []
                elif not isinstance(tags, list):
                    tags = [tags]
                
                tags_json = json.dumps(tags, ensure_ascii=False)
                embedding_json = json.dumps(doc_data["embedding"])
                raw_fm = json.dumps(doc_data.get("raw_frontmatter", {}), default=self._json_serializer, ensure_ascii=False)
                
                # 1. 기본 테이블 업서트
                cursor.execute("""
                INSERT INTO knowledge_documents (
                    file_path, chunk_index, doc_type, title, description, tags, content,
                    parent_content, raw_frontmatter, content_hash, embedding, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(file_path, chunk_index) DO UPDATE SET
                    doc_type = excluded.doc_type,
                    title = excluded.title,
                    description = excluded.description,
                    tags = excluded.tags,
                    content = excluded.content,
                    parent_content = excluded.parent_content,
                    raw_frontmatter = excluded.raw_frontmatter,
                    content_hash = excluded.content_hash,
                    embedding = excluded.embedding,
                    updated_at = CURRENT_TIMESTAMP;
                """, (
                    doc_data["file_path"],
                    doc_data.get("chunk_index", 0),
                    doc_data["doc_type"],
                    doc_data["title"],
                    doc_data.get("description", ""),
                    tags_json,
                    doc_data["content"],
                    doc_data.get("parent_content", doc_data["content"]),
                    raw_fm,
                    doc_data["content_hash"],
                    embedding_json
                ))
                
                # 2. FTS5 검색 엔진 업서트 (동기화)
                cursor.execute("""
                DELETE FROM knowledge_documents_fts 
                WHERE file_path = ? AND chunk_index = ?;
                """, (doc_data["file_path"], doc_data.get("chunk_index", 0)))
                
                cursor.execute("""
                INSERT INTO knowledge_documents_fts (file_path, chunk_index, title, content)
                VALUES (?, ?, ?, ?);
                """, (
                    doc_data["file_path"],
                    doc_data.get("chunk_index", 0),
                    doc_data["title"],
                    doc_data["content"]
                ))
                
        self.conn.commit()

    def keyword_search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        self.connect()
        
        # 쿼리 토큰 추출 및 FTS5 OR 검색 문자열 빌드
        words = re.findall(r'\w+', query, re.UNICODE)
        words = [w for w in words if len(w) > 1]
        
        if not words:
            return []
            
        fts_query = " OR ".join([f'"{w}"' for w in words])
        cursor = self.conn.cursor()
        
        try:
            # FTS5 rank는 낮을수록 우수하므로, -rank를 반환하여 높을수록 일치율이 높게 변환 (postgres ts_rank와 매칭)
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
                # JSON 문자열 복원
                doc["tags"] = json.loads(doc["tags"]) if doc["tags"] else []
                doc["raw_frontmatter"] = json.loads(doc["raw_frontmatter"]) if doc["raw_frontmatter"] else {}
                results.append(doc)
            return results
        except Exception as e:
            print(f"Warning: SQLite FTS5 search failed ({e}). Falling back to empty results.")
            return []

    def similarity_search(self, query_embedding: List[float], limit: int = 5) -> List[Dict[str, Any]]:
        """
        메모리 상에서 코사인 유사도를 연산하여 상위 K개 문서를 탐색합니다.
        (데이터 분량이 개인 위키 수준일 때 가장 단순하고 오작동 없는 안전한 방식)
        """
        self.connect()
        cursor = self.conn.cursor()
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
            # 코사인 유사도 계산
            similarity = cosine_similarity(query_embedding, doc_emb)
            doc["similarity"] = similarity
            
            # 후속 연산을 위해 불필요한 필드 삭제 및 데이터 역직렬화
            del doc["embedding"]
            doc["tags"] = json.loads(doc["tags"]) if doc["tags"] else []
            doc["raw_frontmatter"] = json.loads(doc["raw_frontmatter"]) if doc["raw_frontmatter"] else {}
            candidates.append(doc)
            
        # 유사도 내림차순 정렬 후 limit만큼 추출
        candidates.sort(key=lambda x: x["similarity"], reverse=True)
        return candidates[:limit]

    def insert_edge(self, source_path: str, target_topic: str):
        self.connect()
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
            INSERT OR IGNORE INTO knowledge_edges (source_path, target_topic)
            VALUES (?, ?);
            """, (source_path, target_topic))
            self.conn.commit()
        except sqlite3.Error:
            pass

    def delete_document(self, file_path: str):
        self.connect()
        cursor = self.conn.cursor()
        
        cursor.execute("DELETE FROM knowledge_documents WHERE file_path = ?;", (file_path,))
        cursor.execute("DELETE FROM knowledge_documents_fts WHERE file_path = ?;", (file_path,))
        cursor.execute("DELETE FROM knowledge_edges WHERE source_path = ?;", (file_path,))
        
        self.conn.commit()

    def get_connected_documents(self, file_paths: List[str], limit: int = 3) -> List[Dict[str, Any]]:
        if not file_paths:
            return []
            
        self.connect()
        cursor = self.conn.cursor()
        
        # 1. 엣지 연결 토픽명들 획득
        placeholders = ",".join(["?"] * len(file_paths))
        cursor.execute(f"""
        SELECT DISTINCT target_topic 
        FROM knowledge_edges 
        WHERE source_path IN ({placeholders})
        LIMIT ?;
        """, (*file_paths, limit))
        
        topics = [row["target_topic"] for row in cursor.fetchall()]
        if not topics:
            return []
            
        # 2. 연결된 문서 조회 (chunk_index = 0인 대표값만 추출)
        topics_lower = [t.lower() for t in topics]
        
        results = []
        for topic in topics_lower:
            # title 또는 파일명 매칭
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
                results.append(doc)
                
        return results
