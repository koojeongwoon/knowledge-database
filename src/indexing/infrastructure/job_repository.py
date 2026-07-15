from typing import Any, Dict, List


class IndexingJobRepository:
    """PostgreSQL을 사용하는 내구성 인덱싱 재시도 큐입니다."""

    MAX_ATTEMPTS = 5

    def __init__(self, db_manager):
        self.db_manager = db_manager

    def _get_owner_id(self) -> str:
        from src.core.config import current_user_config

        config = current_user_config.get() or {}
        return config.get("user_id", "SYSTEM")

    def initialize(self) -> None:
        from src.core.database.migrations import run_database_migrations

        run_database_migrations(self.db_manager)

    def enqueue(self, file_paths: List[str]) -> None:
        if not file_paths:
            return
        self.initialize()
        owner_id = self._get_owner_id()
        with self.db_manager.cursor() as cur:
            cur.executemany("""
                INSERT INTO knowledge_indexing_jobs (
                    owner_id, file_path, status, attempts, last_error,
                    next_retry_at, updated_at
                ) VALUES (%s, %s, 'pending', 0, NULL, NULL, CURRENT_TIMESTAMP)
                ON CONFLICT (owner_id, file_path) DO UPDATE SET
                    status = 'pending',
                    attempts = 0,
                    last_error = NULL,
                    next_retry_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            """, [(owner_id, path) for path in dict.fromkeys(file_paths)])

    def complete(self, file_paths: List[str], owner_id: str = None) -> None:
        if not file_paths:
            return
        owner_id = owner_id or self._get_owner_id()
        with self.db_manager.cursor() as cur:
            cur.execute(
                "DELETE FROM knowledge_indexing_jobs WHERE owner_id = %s AND file_path = ANY(%s);",
                (owner_id, file_paths),
            )

    def start(self, file_paths: List[str], owner_id: str = None) -> None:
        if not file_paths:
            return
        owner_id = owner_id or self._get_owner_id()
        with self.db_manager.cursor() as cur:
            cur.execute("""
                UPDATE knowledge_indexing_jobs
                SET status = 'processing', updated_at = CURRENT_TIMESTAMP
                WHERE owner_id = %s AND file_path = ANY(%s);
            """, (owner_id, file_paths))

    def fail(self, file_paths: List[str], error: str, owner_id: str = None) -> None:
        if not file_paths:
            return
        owner_id = owner_id or self._get_owner_id()
        with self.db_manager.cursor() as cur:
            cur.execute("""
                UPDATE knowledge_indexing_jobs
                SET status = 'failed',
                    attempts = attempts + 1,
                    last_error = %s,
                    next_retry_at = CURRENT_TIMESTAMP +
                        CASE
                            WHEN attempts = 0 THEN INTERVAL '1 minute'
                            WHEN attempts = 1 THEN INTERVAL '5 minutes'
                            WHEN attempts = 2 THEN INTERVAL '30 minutes'
                            ELSE INTERVAL '2 hours'
                        END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE owner_id = %s AND file_path = ANY(%s);
            """, (error[:4000], owner_id, file_paths))

    def claim(self, limit: int = 100, force: bool = False, owner_id: str = None) -> List[Dict[str, str]]:
        self.initialize()
        due_clause = "" if force else "AND (next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP)"
        attempts_clause = "" if force else "AND attempts < %s"
        owner_clause = "AND owner_id = %s" if owner_id else ""
        params: List[Any] = []
        if owner_id:
            params.append(owner_id)
        if not force:
            params.append(self.MAX_ATTEMPTS)
        params.append(limit)

        query = f"""
            WITH claimed AS (
                SELECT id
                FROM knowledge_indexing_jobs
                WHERE (
                      status IN ('pending', 'failed')
                      OR (status = 'processing' AND updated_at <= CURRENT_TIMESTAMP - INTERVAL '15 minutes')
                  )
                  {owner_clause}
                  {due_clause}
                  {attempts_clause}
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT %s
            )
            UPDATE knowledge_indexing_jobs AS jobs
            SET status = 'processing', updated_at = CURRENT_TIMESTAMP
            FROM claimed
            WHERE jobs.id = claimed.id
            RETURNING jobs.owner_id, jobs.file_path;
        """
        with self.db_manager.transaction() as cur:
            cur.execute(query, tuple(params))
            return [
                {"owner_id": row[0], "file_path": row[1]}
                for row in cur.fetchall()
            ]

    def list_jobs(self) -> List[Dict[str, Any]]:
        self.initialize()
        owner_id = self._get_owner_id()
        with self.db_manager.cursor() as cur:
            cur.execute("""
                SELECT file_path, status, attempts, last_error, next_retry_at, updated_at
                FROM knowledge_indexing_jobs
                WHERE owner_id = %s
                ORDER BY updated_at;
            """, (owner_id,))
            return [
                {
                    "file_path": row[0],
                    "status": row[1],
                    "attempts": row[2],
                    "last_error": row[3],
                    "next_retry_at": row[4].isoformat() if row[4] else None,
                    "updated_at": row[5].isoformat() if row[5] else None,
                }
                for row in cur.fetchall()
            ]
