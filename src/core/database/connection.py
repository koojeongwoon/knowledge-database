import logging
import uuid
from threading import Lock
from contextlib import contextmanager
from typing import Generator, Any
from psycopg_pool import ConnectionPool
from pgvector.psycopg import register_vector

from src.core.config import DATABASE_URL, DB_MIN_CONNECTIONS, DB_MAX_CONNECTIONS, settings
from src.core.database.base import BaseDatabaseManager

logger = logging.getLogger("database_pool")

# 글로벌 커넥션 풀 선언 (Lazy Initialization)
_connection_pool = None
_pool_lock = Lock()
_VECTOR_REGISTERED_ATTR = "_knowledge_vector_registered"


def _configure_connection(conn) -> None:
    conn.autocommit = True
    if getattr(conn, _VECTOR_REGISTERED_ATTR, False):
        return
    try:
        register_vector(conn)
        setattr(conn, _VECTOR_REGISTERED_ATTR, True)
    except Exception:
        pass


def get_connection_pool() -> ConnectionPool:
    global _connection_pool
    if _connection_pool is None:
        with _pool_lock:
            if _connection_pool is None:
                logger.info("Initializing database pool (min=%s, max=%s)", DB_MIN_CONNECTIONS, DB_MAX_CONNECTIONS)
                _connection_pool = ConnectionPool(
                    conninfo=DATABASE_URL,
                    min_size=DB_MIN_CONNECTIONS,
                    max_size=DB_MAX_CONNECTIONS,
                    timeout=settings.db_pool_timeout_seconds,
                    max_waiting=settings.db_pool_max_waiting,
                    kwargs={"connect_timeout": settings.db_connect_timeout_seconds},
                    check=ConnectionPool.check_connection,
                    configure=_configure_connection,
                    open=True,
                )
    return _connection_pool


class PostgresDatabaseManager(BaseDatabaseManager):
    schema_cache_key = DATABASE_URL

    def __init__(self):
        self.conn = None
        self._from_pool = False

    def connect(self):
        if not self.conn or self.conn.closed:
            self.close()
            try:
                self.conn = get_connection_pool().getconn()
                self._from_pool = True
                _configure_connection(self.conn)
            except Exception as e:
                from src.api.exceptions import DatabaseException
                if self.conn is not None:
                    self.conn.close()
                    self.close()
                logger.warning("Database connection unavailable (%s)", type(e).__name__)
                raise DatabaseException("Database connection unavailable") from e

    def close(self):
        if self.conn is not None:
            try:
                if self._from_pool:
                    # Closed connections must also be returned to release their slot.
                    get_connection_pool().putconn(self.conn)
                else:
                    self.conn.close()
            except Exception:
                logger.warning("Failed to return database connection; closing it")
                self.conn.close()
            finally:
                self.conn = None
                self._from_pool = False

    @contextmanager
    def cursor(self) -> Generator[Any, None, None]:
        self.connect()
        try:
            with self.conn.cursor() as cur:
                yield cur
        except Exception as e:
            from src.api.exceptions import DatabaseException
            logger.error(f"Database query failed: {e}")
            raise DatabaseException(f"Database query failed: {e}") from e
        finally:
            self.close()

    @contextmanager
    def streaming_cursor(self, batch_size: int = 500) -> Generator[Any, None, None]:
        """Keep a named cursor and its transaction alive only while consuming rows."""
        if batch_size <= 0:
            raise ValueError("Streaming batch size must be positive.")
        self.connect()
        try:
            # Named cursors require a transaction even on an autocommit connection.
            with self.conn.transaction():
                with self.conn.cursor(name=f"knowledge_read_{uuid.uuid4().hex}") as cur:
                    cur.itersize = batch_size
                    yield cur
        except Exception as e:
            from src.api.exceptions import DatabaseException
            logger.error(f"Database streaming query failed: {e}")
            raise DatabaseException(f"Database streaming query failed: {e}") from e
        finally:
            self.close()

    @contextmanager
    def transaction(self) -> Generator[Any, None, None]:
        self.connect()
        try:
            with self.conn.transaction():
                with self.conn.cursor() as cur:
                    yield cur
        except Exception as e:
            from src.api.exceptions import DatabaseException
            logger.error(f"Database transaction failed: {e}")
            raise DatabaseException(f"Database transaction failed: {e}") from e
        finally:
            self.close()

    def execute_batch(self, query: str, values: list, template: str = None, page_size: int = 50):
        self.connect()
        from src.api.exceptions import DatabaseException
        
        # VALUES %s 구문을 VALUES (placeholders) 구문으로 변환하여 executemany로 실행
        if not template and values:
            placeholders = ", ".join(["%s"] * len(values[0]))
            template = f"({placeholders})"
            
        single_row_query = query
        if template:
            single_row_query = query.replace("VALUES %s", f"VALUES {template}")
            
        try:
            with self.conn.cursor() as cur:
                # Psycopg 3의 executemany는 내부적으로 파이프라인 모드를 적용하여 매우 빠릅니다.
                cur.executemany(single_row_query, values)
        except Exception as e:
            logger.error(f"Database batch execution failed: {e}")
            raise DatabaseException(f"Database batch execution failed: {e}") from e
        finally:
            self.close()

    def rollback(self):
        if self.conn and not self.conn.closed:
            try:
                self.conn.rollback()
            except Exception as e:
                logger.error(f"Failed to rollback transaction: {e}")
