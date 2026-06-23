import psycopg2
from psycopg2.extras import execute_values
from pgvector.psycopg2 import register_vector
from typing import List, Dict, Any, Optional
import json

from src.core.config import DATABASE_URL, EMBEDDING_DIM
from src.infrastructure.base import BaseDatabaseManager

class PostgresDatabaseManager(BaseDatabaseManager):
    def __init__(self):
        self.conn = None

    def connect(self):
        if not self.conn or self.conn.closed:
            self.conn = psycopg2.connect(DATABASE_URL)
            self.conn.autocommit = True
            try:
                # pgvector 데이터 타입 등록 (DB에 vector 확장이 설치되어 있어야 성공)
                register_vector(self.conn)
            except Exception:
                # 확장이 아직 설치되지 않은 경우 예외가 발생하므로 임시 무시
                pass

    def close(self):
        if self.conn and not self.conn.closed:
            self.conn.close()

    def initialize_db(self):
        """
        데이터베이스 및 테이블 초기화, pgvector 확장 활성화 및 HNSW 인덱스 생성
        """
        # 1. pgvector 확장을 먼저 활성화하기 위해 일반 연결 생성
        if not self.conn or self.conn.closed:
            self.conn = psycopg2.connect(DATABASE_URL)
            self.conn.autocommit = True
            
        with self.conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            
        # 2. 연결을 닫았다가 다시 연결하여 register_vector가 활성화된 vector 타입을 인지하도록 갱신
        self.close()
        self.connect()
        
        # 3. 테이블 생성 (이미 존재하면 스킵하여 증분 인덱싱 데이터 보존)
        with self.conn.cursor() as cur:
            
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
            
            # 4. knowledge_edges 테이블 생성
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
            
            # 기존 테이블 대응을 위해 weight 컬럼 추가
            cur.execute("ALTER TABLE knowledge_edges ADD COLUMN IF NOT EXISTS weight REAL NOT NULL DEFAULT 1.0;")
            
            # HNSW 인덱스는 pgvector 0.5.0 이상에서 지원.
            try:
                cur.execute("""
                CREATE INDEX IF NOT EXISTS knowledge_documents_embedding_idx 
                ON knowledge_documents USING hnsw (embedding vector_cosine_ops);
                """)
            except psycopg2.DatabaseError as e:
                self.conn.rollback()
                print(f"Warning: HNSW index creation failed ({e}). Attempting IVFFlat index...")
                try:
                    cur.execute("""
                    CREATE INDEX IF NOT EXISTS knowledge_documents_embedding_idx 
                    ON knowledge_documents USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
                    """)
                except Exception as ex:
                    self.conn.rollback()
                    print(f"Warning: Index creation failed. Similarity search will use sequential scan. ({ex})")

            # GIN 인덱스: 키워드 Full-Text Search 가속 (RRF 하이브리드 검색용)
            try:
                cur.execute("""
                CREATE INDEX IF NOT EXISTS knowledge_documents_fts_idx
                ON knowledge_documents USING gin (
                    to_tsvector('simple', coalesce(content, '') || ' ' || coalesce(title, ''))
                );
                """)
            except psycopg2.DatabaseError as e:
                self.conn.rollback()
                print(f"Warning: Full-text search GIN index creation failed ({e}). Keyword search will use sequential scan.")


    def get_all_file_hashes(self) -> Dict[str, str]:
        """
        DB에 저장된 모든 파일의 상대경로(file_path)와 content_hash를 리턴합니다.
        여러 청크 중 chunk_index = 0인 대표값만 추출하거나 DISTINCT하여 해시맵을 구성합니다.
        """
        self.connect()
        with self.conn.cursor() as cur:
            cur.execute("SELECT DISTINCT file_path, content_hash FROM knowledge_documents;")
            rows = cur.fetchall()
            return {row[0]: row[1] for row in rows}

    def _json_serializer(self, obj):
        import datetime
        if isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        raise TypeError(f"Type {type(obj)} not serializable")

    def upsert_document_chunk(self, doc_data: Dict[str, Any]):
        """
        분할된 개별 청크를 DB에 추가하거나 갱신합니다.
        """
        self.connect()
        with self.conn.cursor() as cur:
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
        """
        청크 리스트를 batch_size 단위로 나누어 일괄 삽입(UPSERT)합니다.
        대량의 개별 INSERT 대신 execute_values를 사용하여 DB 라운드트립을 최소화합니다.
        """
        if not chunks:
            return

        self.connect()

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
                raw_fm = json.dumps(
                    doc_data.get("raw_frontmatter", {}),
                    default=self._json_serializer
                )

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

            with self.conn.cursor() as cur:
                execute_values(cur, query, values, template=template)

    def keyword_search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        PostgreSQL Full-Text Search를 사용하여 키워드 기반 문서를 검색합니다.
        'simple' 설정을 사용하여 한국어를 공백 기준으로 토큰화합니다.
        RRF 하이브리드 검색의 두 번째 경로로 사용됩니다.
        """
        import re

        self.connect()

        # 쿼리에서 단어 추출 후 OR 기반 tsquery 구성 (넓은 Recall 확보)
        words = re.findall(r'\w+', query, re.UNICODE)
        words = [w for w in words if len(w) > 1]

        if not words:
            return []

        tsquery_str = " | ".join(words)

        with self.conn.cursor() as cur:
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

    def insert_edge(self, source_path: str, target_topic: str, weight: float = 1.0):
        """
        문서 간의 위키링크 방향성 연결(Edge) 및 가중치(weight)를 데이터베이스에 저장합니다.
        """
        self.connect()
        with self.conn.cursor() as cur:
            query = """
            INSERT INTO knowledge_edges (source_path, target_topic, weight)
            VALUES (%s, %s, %s)
            ON CONFLICT (source_path, target_topic) DO UPDATE SET weight = EXCLUDED.weight;
            """
            cur.execute(query, (source_path, target_topic, weight))

    def delete_document(self, file_path: str):
        """
        특정 파일 경로의 모든 청크 및 소스 엣지 정보를 일괄 삭제합니다.
        """
        self.connect()
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM knowledge_documents WHERE file_path = %s;", (file_path,))
            cur.execute("DELETE FROM knowledge_edges WHERE source_path = %s;", (file_path,))

    def similarity_search(self, query_embedding: List[float], limit: int = 5) -> List[Dict[str, Any]]:
        """
        코사인 유사도 기준 상위 K개 청크를 검색하여 자식 정보와 함께 부모 단락 정보를 반환합니다.
        """
        self.connect()
        embedding_str = "[" + ",".join(map(str, query_embedding)) + "]"
        
        with self.conn.cursor() as cur:
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
                # JSON 문자열인 경우 역직렬화
                if isinstance(doc["raw_frontmatter"], str):
                    doc["raw_frontmatter"] = json.loads(doc["raw_frontmatter"])
                results.append(doc)
            return results

    def get_connected_documents(self, file_paths: List[str], limit: int = 3) -> List[Dict[str, Any]]:
        """
        주어진 파일들과 연결된 타겟 토픽들의 대표 문서(chunk_index = 0) 본문을 조회하며,
        연결 가중치(weight)를 함께 반환합니다.
        """
        if not file_paths:
            return []
            
        import os
        self.connect()
        with self.conn.cursor() as cur:
            # 1. 엣지 조회를 통해 1촌 연결된 토픽들 및 가중치 리스트업
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
                
            # 토픽명 -> 가중치 매핑 딕셔너리 생성
            topic_to_weight = {row[0].lower(): row[1] for row in edges_rows}
            topics_lower = list(topic_to_weight.keys())
                
            # 2. 토픽명과 매칭되는 문서들의 대표 청크(index=0) 내용 가져오기
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
                
                # 가중치 매핑 매칭
                weight = 1.0
                if t_title in topic_to_weight:
                    weight = topic_to_weight[t_title]
                elif t_filename in topic_to_weight:
                    weight = topic_to_weight[t_filename]
                    
                doc["edge_weight"] = weight
                results.append(doc)
            return results
