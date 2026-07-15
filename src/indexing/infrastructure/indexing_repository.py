import json
from typing import Dict, List, Any

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

    def _get_owner_id(self) -> str:
        from src.core.config import current_user_config
        config = current_user_config.get() or {}
        return config.get("user_id", "SYSTEM")

    def initialize_db(self):
        from src.core.database.migrations import run_database_migrations

        run_database_migrations(self.db_manager)

    def get_all_file_hashes(self) -> Dict[str, str]:
        owner_id = self._get_owner_id()
        with self.db_manager.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT file_path, content_hash FROM knowledge_documents WHERE owner_id = %s;",
                (owner_id,)
            )
            rows = cur.fetchall()
            return {row[0]: row[1] for row in rows}

    def get_file_hashes(self, file_paths: List[str]) -> Dict[str, str]:
        if not file_paths:
            return {}
        owner_id = self._get_owner_id()
        with self.db_manager.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT file_path, content_hash
                FROM knowledge_documents
                WHERE owner_id = %s AND file_path = ANY(%s);
                """,
                (owner_id, file_paths)
            )
            rows = cur.fetchall()
            return {row[0]: row[1] for row in rows}

    def upsert_document_chunk(self, doc_data: Dict[str, Any]):
        owner_id = self._get_owner_id()
        visibility = doc_data.get("visibility", "public")
        with self.db_manager.cursor() as cur:
            query = """
            INSERT INTO knowledge_documents (
                file_path, chunk_index, doc_type, title, description, tags, content, parent_content, raw_frontmatter, content_hash, embedding, owner_id, visibility, updated_at
            ) VALUES (
                %(file_path)s, %(chunk_index)s, %(doc_type)s, %(title)s, %(description)s, %(tags)s, %(content)s, %(parent_content)s, %(raw_frontmatter)s, %(content_hash)s, %(embedding)s, %(owner_id)s, %(visibility)s, CURRENT_TIMESTAMP
            )
            ON CONFLICT (owner_id, file_path, chunk_index) DO UPDATE SET
                doc_type = EXCLUDED.doc_type,
                title = EXCLUDED.title,
                description = EXCLUDED.description,
                tags = EXCLUDED.tags,
                content = EXCLUDED.content,
                parent_content = EXCLUDED.parent_content,
                raw_frontmatter = EXCLUDED.raw_frontmatter,
                content_hash = EXCLUDED.content_hash,
                embedding = EXCLUDED.embedding,
                visibility = EXCLUDED.visibility,
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
                "embedding": embedding_str,
                "owner_id": owner_id,
                "visibility": visibility
            }
            cur.execute(query, params)

    def upsert_document_chunks_batch(self, chunks: List[Dict[str, Any]], batch_size: int = 50):
        if not chunks:
            return
        owner_id = self._get_owner_id()
        query = """
        INSERT INTO knowledge_documents (
            file_path, chunk_index, doc_type, title, description, tags, content,
            parent_content, raw_frontmatter, content_hash, embedding, owner_id, visibility, updated_at
        ) VALUES %s
        ON CONFLICT (owner_id, file_path, chunk_index) DO UPDATE SET
            doc_type = EXCLUDED.doc_type,
            title = EXCLUDED.title,
            description = EXCLUDED.description,
            tags = EXCLUDED.tags,
            content = EXCLUDED.content,
            parent_content = EXCLUDED.parent_content,
            raw_frontmatter = EXCLUDED.raw_frontmatter,
            content_hash = EXCLUDED.content_hash,
            embedding = EXCLUDED.embedding,
            visibility = EXCLUDED.visibility,
            updated_at = CURRENT_TIMESTAMP;
        """
        template = "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)"
        
        values = []
        for doc_data in chunks:
            tags = doc_data.get("tags", [])
            if tags is None:
                tags = []
            elif not isinstance(tags, list):
                tags = [tags]
            embedding_str = "[" + ",".join(map(str, doc_data["embedding"])) + "]"
            raw_fm = json.dumps(doc_data.get("raw_frontmatter", {}), default=self._json_serializer)
            visibility = doc_data.get("visibility", "public")
            if visibility not in ("public", "private"):
                visibility = "public"
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
                embedding_str,
                owner_id,
                visibility
            ))
        self.db_manager.execute_batch(query, values, template=template, page_size=batch_size)

    def insert_edge(self, source_path: str, target_topic: str, weight: float = 1.0):
        owner_id = self._get_owner_id()
        with self.db_manager.cursor() as cur:
            query = """
            INSERT INTO knowledge_edges (source_path, target_topic, weight, owner_id)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (owner_id, source_path, target_topic) DO UPDATE SET weight = EXCLUDED.weight;
            """
            cur.execute(query, (source_path, target_topic, weight, owner_id))

    def delete_document(self, file_path: str):
        owner_id = self._get_owner_id()
        with self.db_manager.cursor() as cur:
            cur.execute("DELETE FROM knowledge_documents WHERE file_path = %s AND owner_id = %s;", (file_path, owner_id))
            cur.execute("DELETE FROM knowledge_edges WHERE source_path = %s AND owner_id = %s;", (file_path, owner_id))

    def upsert_topic(self, topic_name: str, category: str, file_path: str):
        owner_id = self._get_owner_id()
        with self.db_manager.cursor() as cur:
            query = """
            INSERT INTO knowledge_topics (topic_name, category, file_path, owner_id, updated_at)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (owner_id, topic_name) DO UPDATE SET
                category = EXCLUDED.category,
                file_path = EXCLUDED.file_path,
                updated_at = CURRENT_TIMESTAMP;
            """
            cur.execute(query, (topic_name, category, file_path, owner_id))

    def get_topic_by_name(self, topic_name: str) -> Any:
        owner_id = self._get_owner_id()
        with self.db_manager.cursor() as cur:
            query = "SELECT topic_name, category, file_path FROM knowledge_topics WHERE topic_name = %s AND owner_id = %s;"
            cur.execute(query, (topic_name, owner_id))
            row = cur.fetchone()
            if row:
                return {
                    "topic_name": row[0],
                    "category": row[1],
                    "file_path": row[2]
                }
            return None

    def get_document_chunks(self, file_path: str) -> List[Dict[str, Any]]:
        owner_id = self._get_owner_id()
        with self.db_manager.cursor() as cur:
            cur.execute(
                "SELECT chunk_index, content, embedding FROM knowledge_documents WHERE file_path = %s AND owner_id = %s ORDER BY chunk_index ASC;",
                (file_path, owner_id)
            )
            rows = cur.fetchall()
            chunks = []
            for row in rows:
                idx, content, emb = row
                # Handle possible string representation of postgres vector type
                if isinstance(emb, str):
                    try:
                        emb = json.loads(emb)
                    except Exception:
                        # Strip bracket representation if needed e.g. [0.1, 0.2]
                        if emb.startswith('[') and emb.endswith(']'):
                            emb = [float(x) for x in emb[1:-1].split(',')]
                elif hasattr(emb, 'tolist'):
                    emb = emb.tolist()
                chunks.append({
                    "chunk_index": idx,
                    "content": content,
                    "embedding": emb
                })
            return chunks

    def replace_document(
        self,
        file_path: str,
        chunks: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
    ) -> None:
        owner_id = self._get_owner_id()
        chunk_query = """
        INSERT INTO knowledge_documents (
            file_path, chunk_index, doc_type, title, description, tags, content,
            parent_content, raw_frontmatter, content_hash, embedding, owner_id,
            visibility, updated_at
        ) VALUES (
            %(file_path)s, %(chunk_index)s, %(doc_type)s, %(title)s,
            %(description)s, %(tags)s, %(content)s, %(parent_content)s,
            %(raw_frontmatter)s, %(content_hash)s, %(embedding)s, %(owner_id)s,
            %(visibility)s, CURRENT_TIMESTAMP
        );
        """
        edge_query = """
        INSERT INTO knowledge_edges (source_path, target_topic, weight, owner_id)
        VALUES (%s, %s, %s, %s);
        """

        chunk_params = []
        for doc_data in chunks:
            tags = doc_data.get("tags", [])
            if tags is None:
                tags = []
            elif not isinstance(tags, list):
                tags = [tags]
            visibility = doc_data.get("visibility", "public")
            if visibility not in ("public", "private"):
                visibility = "public"
            chunk_params.append({
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
                "embedding": "[" + ",".join(map(str, doc_data["embedding"])) + "]",
                "owner_id": owner_id,
                "visibility": visibility,
            })

        with self.db_manager.transaction() as cur:
            cur.execute(
                "DELETE FROM knowledge_documents WHERE file_path = %s AND owner_id = %s;",
                (file_path, owner_id),
            )
            cur.execute(
                "DELETE FROM knowledge_edges WHERE source_path = %s AND owner_id = %s;",
                (file_path, owner_id),
            )
            if chunk_params:
                cur.executemany(chunk_query, chunk_params)
            if edges:
                cur.executemany(
                    edge_query,
                    [
                        (edge["source_path"], edge["target_topic"], edge["weight"], owner_id)
                        for edge in edges
                    ],
                )
