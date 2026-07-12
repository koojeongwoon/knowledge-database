import json
from typing import Dict, List, Any

import psycopg2
from psycopg2.extras import execute_values

from src.core.config import EMBEDDING_DIM
from src.indexing.domain.repository import BaseIndexingRepository


class PostgresIndexingRepository(BaseIndexingRepository):
    """PostgreSQL 데이터베이스(pgvector 활용)를 타겟으로 인덱싱 데이터를 관리하는 구체 인프라 구현체"""
    def __init__(self, db_manager):
        self.db_manager = db_manager

    def _json_serializer(self, obj):
        import datetime
        if isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        raise TypeError(f"Type {type(obj)} not serializable")

    def initialize_db(self):
        self.db_manager.connect()
        conn = self.db_manager.conn
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            
        self.db_manager.close()
        self.db_manager.connect()
        conn = self.db_manager.conn
        
        with conn.cursor() as cur:
            create_table_query = f"""
            CREATE TABLE IF NOT EXISTS knowledge_documents (
                id SERIAL PRIMARY KEY,
                file_path VARCHAR(512) NOT NULL,
                chunk_index INT NOT NULL DEFAULT 0,
                doc_type VARCHAR(50) NOT NULL,
                title VARCHAR(256) NOT NULL,
                description TEXT,
                tags TEXT[],
                content TEXT NOT NULL,
                parent_content TEXT NOT NULL,
                raw_frontmatter JSONB,
                content_hash VARCHAR(64) NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                embedding VECTOR({EMBEDDING_DIM}),
                CONSTRAINT uq_file_chunk UNIQUE (file_path, chunk_index)
            );
            """
            cur.execute(create_table_query)
            
            create_edges_query = """
            CREATE TABLE IF NOT EXISTS knowledge_edges (
                id SERIAL PRIMARY KEY,
                source_path VARCHAR(512) NOT NULL,
                target_topic VARCHAR(256) NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_edge UNIQUE (source_path, target_topic)
            );
            """
            cur.execute(create_edges_query)
            
            create_citations_query = """
            CREATE TABLE IF NOT EXISTS knowledge_citations (
                file_path VARCHAR(512) PRIMARY KEY,
                citation_count INT NOT NULL DEFAULT 0,
                last_cited_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """
            cur.execute(create_citations_query)

            create_topics_query = """
            CREATE TABLE IF NOT EXISTS knowledge_topics (
                topic_name VARCHAR(256) PRIMARY KEY,
                category VARCHAR(100) NOT NULL,
                file_path VARCHAR(512) NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """
            cur.execute(create_topics_query)

            create_audit_logs_query = """
            CREATE TABLE IF NOT EXISTS knowledge_audit_logs (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                user_id VARCHAR(50),
                action VARCHAR(100),
                status VARCHAR(50),
                payload JSONB
            );
            """
            cur.execute(create_audit_logs_query)
            
            cur.execute("ALTER TABLE knowledge_edges ADD COLUMN IF NOT EXISTS weight REAL NOT NULL DEFAULT 1.0;")
            
            try:
                cur.execute("""
                CREATE INDEX IF NOT EXISTS knowledge_documents_embedding_idx 
                ON knowledge_documents USING hnsw (embedding vector_cosine_ops);
                """)
            except psycopg2.DatabaseError as e:
                conn.rollback()
                print(f"Warning: HNSW index creation failed ({e}). Attempting IVFFlat index...")
                try:
                    cur.execute("""
                    CREATE INDEX IF NOT EXISTS knowledge_documents_embedding_idx 
                    ON knowledge_documents USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
                    """)
                except Exception as ex:
                    conn.rollback()
                    print(f"Warning: Index creation failed. Similarity search will use sequential scan. ({ex})")

            try:
                cur.execute("""
                CREATE INDEX IF NOT EXISTS knowledge_documents_fts_idx
                ON knowledge_documents USING gin (
                    to_tsvector('simple', coalesce(content, '') || ' ' || coalesce(title, ''))
                );
                """)
            except psycopg2.DatabaseError as e:
                conn.rollback()
                print(f"Warning: Full-text search GIN index creation failed ({e}). Keyword search will use sequential scan.")

    def get_all_file_hashes(self) -> Dict[str, str]:
        self.db_manager.connect()
        conn = self.db_manager.conn
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT file_path, content_hash FROM knowledge_documents;")
            rows = cur.fetchall()
            return {row[0]: row[1] for row in rows}

    def upsert_document_chunk(self, doc_data: Dict[str, Any]):
        self.db_manager.connect()
        conn = self.db_manager.conn
        with conn.cursor() as cur:
            query = """
            INSERT INTO knowledge_documents (
                file_path, chunk_index, doc_type, title, description, tags, content, parent_content, raw_frontmatter, content_hash, embedding, updated_at
            ) VALUES (
                %(file_path)s, %(chunk_index)s, %(doc_type)s, %(title)s, %(description)s, %(tags)s, %(content)s, %(parent_content)s, %(raw_frontmatter)s, %(content_hash)s, %(embedding)s, CURRENT_TIMESTAMP
            )
            ON CONFLICT (file_path, chunk_index) DO UPDATE SET
                doc_type = EXCLUDED.doc_type,
                title = EXCLUDED.title,
                description = EXCLUDED.description,
                tags = EXCLUDED.tags,
                content = EXCLUDED.content,
                parent_content = EXCLUDED.parent_content,
                raw_frontmatter = EXCLUDED.raw_frontmatter,
                content_hash = EXCLUDED.content_hash,
                embedding = EXCLUDED.embedding,
                updated_at = CURRENT_TIMESTAMP;
            """
            
            tags = doc_data.get("tags")
            if tags is None:
                tags = []
            elif not isinstance(tags, list):
                tags = [tags]
            
            embedding_str = "[" + ",".join(map(str, doc_data["embedding"])) + "]"
            
            params = {
                "file_path": doc_data["file_path"],
                "chunk_index": doc_data.get("chunk_index", 0),
                "doc_type": doc_data["doc_type"],
                "title": doc_data["title"],
                "description": doc_data.get("description", ""),
                "tags": tags,
                "content": doc_data["content"],
                "parent_content": doc_data.get("parent_content", doc_data["content"]),
                "raw_frontmatter": json.dumps(doc_data.get("raw_frontmatter", {}), default=self._json_serializer),
                "content_hash": doc_data["content_hash"],
                "embedding": embedding_str
            }
            cur.execute(query, params)

    def upsert_document_chunks_batch(self, chunks: List[Dict[str, Any]], batch_size: int = 50):
        if not chunks:
            return
        self.db_manager.connect()
        conn = self.db_manager.conn
        query = """
        INSERT INTO knowledge_documents (
            file_path, chunk_index, doc_type, title, description, tags, content,
            parent_content, raw_frontmatter, content_hash, embedding, updated_at
        ) VALUES %s
        ON CONFLICT (file_path, chunk_index) DO UPDATE SET
            doc_type = EXCLUDED.doc_type,
            title = EXCLUDED.title,
            description = EXCLUDED.description,
            tags = EXCLUDED.tags,
            content = EXCLUDED.content,
            parent_content = EXCLUDED.parent_content,
            raw_frontmatter = EXCLUDED.raw_frontmatter,
            content_hash = EXCLUDED.content_hash,
            embedding = EXCLUDED.embedding,
            updated_at = CURRENT_TIMESTAMP;
        """
        template = "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)"
        
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            values = []
            for doc_data in batch:
                tags = doc_data.get("tags", [])
                if tags is None:
                    tags = []
                elif not isinstance(tags, list):
                    tags = [tags]
                embedding_str = "[" + ",".join(map(str, doc_data["embedding"])) + "]"
                raw_fm = json.dumps(doc_data.get("raw_frontmatter", {}), default=self._json_serializer)
                values.append((
                    doc_data["file_path"],
                    doc_data.get("chunk_index", 0),
                    doc_data["doc_type"],
                    doc_data["title"],
                    doc_data.get("description", ""),
                    tags,
                    doc_data["content"],
                    doc_data.get("parent_content", doc_data["content"]),
                    raw_fm,
                    doc_data["content_hash"],
                    embedding_str
                ))
            with conn.cursor() as cur:
                execute_values(cur, query, values, template=template)

    def insert_edge(self, source_path: str, target_topic: str, weight: float = 1.0):
        self.db_manager.connect()
        conn = self.db_manager.conn
        with conn.cursor() as cur:
            query = """
            INSERT INTO knowledge_edges (source_path, target_topic, weight)
            VALUES (%s, %s, %s)
            ON CONFLICT (source_path, target_topic) DO UPDATE SET weight = EXCLUDED.weight;
            """
            cur.execute(query, (source_path, target_topic, weight))

    def delete_document(self, file_path: str):
        self.db_manager.connect()
        conn = self.db_manager.conn
        with conn.cursor() as cur:
            cur.execute("DELETE FROM knowledge_documents WHERE file_path = %s;", (file_path,))
            cur.execute("DELETE FROM knowledge_edges WHERE source_path = %s;", (file_path,))

    def upsert_topic(self, topic_name: str, category: str, file_path: str):
        self.db_manager.connect()
        conn = self.db_manager.conn
        with conn.cursor() as cur:
            query = """
            INSERT INTO knowledge_topics (topic_name, category, file_path, updated_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (topic_name) DO UPDATE SET
                category = EXCLUDED.category,
                file_path = EXCLUDED.file_path,
                updated_at = CURRENT_TIMESTAMP;
            """
            cur.execute(query, (topic_name, category, file_path))

    def get_topic_by_name(self, topic_name: str) -> Any:
        self.db_manager.connect()
        conn = self.db_manager.conn
        with conn.cursor() as cur:
            query = "SELECT topic_name, category, file_path FROM knowledge_topics WHERE topic_name = %s;"
            cur.execute(query, (topic_name,))
            row = cur.fetchone()
            if row:
                return {
                    "topic_name": row[0],
                    "category": row[1],
                    "file_path": row[2]
                }
            return None
