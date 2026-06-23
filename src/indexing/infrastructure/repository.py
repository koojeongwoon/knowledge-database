from abc import ABC, abstractmethod
from typing import Dict, List, Any
import json
import psycopg2
from psycopg2.extras import execute_values
from pgvector.psycopg2 import register_vector
from src.core.config import DB_TYPE, EMBEDDING_DIM

class BaseIndexingRepository(ABC):
    @abstractmethod
    def initialize_db(self) -> None:
        pass

    @abstractmethod
    def get_all_file_hashes(self) -> Dict[str, str]:
        pass

    @abstractmethod
    def upsert_document_chunk(self, doc_data: Dict[str, Any]) -> None:
        pass

    @abstractmethod
    def upsert_document_chunks_batch(self, chunks: List[Dict[str, Any]], batch_size: int = 50) -> None:
        pass

    @abstractmethod
    def insert_edge(self, source_path: str, target_topic: str, weight: float = 1.0) -> None:
        pass

    @abstractmethod
    def delete_document(self, file_path: str) -> None:
        pass


class PostgresIndexingRepository(BaseIndexingRepository):
    def __init__(self, db_manager):
        self.db_manager = db_manager

    def _json_serializer(self, obj):
        import datetime
        if isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        raise TypeError(f"Type {type(obj)} not serializable")

    def initialize_db(self):
        # 1. pgvector 확장을 먼저 활성화하기 위해 일반 연결 생성
        self.db_manager.connect()
        conn = self.db_manager.conn
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            
        # 2. 연결을 갱신하여 register_vector가 활성화된 vector 타입을 인지하도록 함
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


class SqliteIndexingRepository(BaseIndexingRepository):
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
        cursor = conn.cursor()
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
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_path TEXT NOT NULL,
            target_topic TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_path, target_topic)
        );
        """)
        try:
            cursor.execute("ALTER TABLE knowledge_edges ADD COLUMN weight REAL NOT NULL DEFAULT 1.0;")
        except Exception:
            pass

        cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_documents_fts USING fts5(
            file_path UNINDEXED,
            chunk_index UNINDEXED,
            title,
            content
        );
        """)
        conn.commit()

    def get_all_file_hashes(self) -> Dict[str, str]:
        self.db_manager.connect()
        conn = self.db_manager.conn
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT file_path, content_hash FROM knowledge_documents;")
        rows = cursor.fetchall()
        return {row["file_path"]: row["content_hash"] for row in rows}

    def upsert_document_chunk(self, doc_data: Dict[str, Any]):
        self.upsert_document_chunks_batch([doc_data])

    def upsert_document_chunks_batch(self, chunks: List[Dict[str, Any]], batch_size: int = 50):
        if not chunks:
            return
        self.db_manager.connect()
        conn = self.db_manager.conn
        cursor = conn.cursor()
        
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            for doc_data in batch:
                tags = doc_data.get("tags", [])
                tags_str = ",".join(tags) if isinstance(tags, list) else str(tags) if tags else ""
                embedding_str = json.dumps(doc_data["embedding"])
                raw_fm = json.dumps(doc_data.get("raw_frontmatter", {}), default=self._json_serializer)
                
                cursor.execute("""
                INSERT INTO knowledge_documents (
                    file_path, chunk_index, doc_type, title, description, tags, content, parent_content, raw_frontmatter, content_hash, embedding
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_path, chunk_index) DO UPDATE SET
                    doc_type=excluded.doc_type,
                    title=excluded.title,
                    description=excluded.description,
                    tags=excluded.tags,
                    content=excluded.content,
                    parent_content=excluded.parent_content,
                    raw_frontmatter=excluded.raw_frontmatter,
                    content_hash=excluded.content_hash,
                    embedding=excluded.embedding;
                """, (
                    doc_data["file_path"],
                    doc_data.get("chunk_index", 0),
                    doc_data["doc_type"],
                    doc_data["title"],
                    doc_data.get("description", ""),
                    tags_str,
                    doc_data["content"],
                    doc_data.get("parent_content", doc_data["content"]),
                    raw_fm,
                    doc_data["content_hash"],
                    embedding_str
                ))
                
                cursor.execute("DELETE FROM knowledge_documents_fts WHERE file_path = ? AND chunk_index = ?;", (doc_data["file_path"], doc_data.get("chunk_index", 0)))
                cursor.execute("""
                INSERT INTO knowledge_documents_fts (file_path, chunk_index, title, content)
                VALUES (?, ?, ?, ?);
                """, (doc_data["file_path"], doc_data.get("chunk_index", 0), doc_data["title"], doc_data["content"]))
                
        conn.commit()

    def insert_edge(self, source_path: str, target_topic: str, weight: float = 1.0):
        self.db_manager.connect()
        conn = self.db_manager.conn
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO knowledge_edges (source_path, target_topic, weight)
        VALUES (?, ?, ?)
        ON CONFLICT(source_path, target_topic) DO UPDATE SET weight=excluded.weight;
        """, (source_path, target_topic, weight))
        conn.commit()

    def delete_document(self, file_path: str):
        self.db_manager.connect()
        conn = self.db_manager.conn
        cursor = conn.cursor()
        cursor.execute("DELETE FROM knowledge_documents WHERE file_path = ?;", (file_path,))
        cursor.execute("DELETE FROM knowledge_documents_fts WHERE file_path = ?;", (file_path,))
        cursor.execute("DELETE FROM knowledge_edges WHERE source_path = ?;", (file_path,))
        conn.commit()


def IndexingRepository(db_manager) -> BaseIndexingRepository:
    if DB_TYPE == "sqlite":
        return SqliteIndexingRepository(db_manager)
    else:
        return PostgresIndexingRepository(db_manager)
