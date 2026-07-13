import psycopg
import time
import logging
from contextlib import contextmanager
from typing import Generator, Any
from psycopg_pool import ConnectionPool, PoolTimeout
from pgvector.psycopg import register_vector

from src.core.config import DATABASE_URL, DB_MIN_CONNECTIONS, DB_MAX_CONNECTIONS
from src.core.database.base import BaseDatabaseManager

logger = logging.getLogger("database_pool")

# 글로벌 커넥션 풀 선언 (Lazy Initialization)
_connection_pool = None

def get_connection_pool() -> ConnectionPool:
    global _connection_pool
    if _connection_pool is None:
        logger.info(f"Initializing psycopg_pool.ConnectionPool (min={DB_MIN_CONNECTIONS}, max={DB_MAX_CONNECTIONS})")
        _connection_pool = ConnectionPool(
            conninfo=DATABASE_URL,
            min_size=DB_MIN_CONNECTIONS,
            max_size=DB_MAX_CONNECTIONS,
            timeout=3.0,
            open=True
        )
    return _connection_pool


class PostgresDatabaseManager(BaseDatabaseManager):
    def __init__(self):
        self.conn = None
        self._from_pool = False

    def _is_connection_alive(self, conn) -> bool:
        """
        [Connection Test Query (SELECT 1) 구현]
        커넥션이 물리적으로 연결이 끊겼거나 방화벽에 의해 죽었는지 검증합니다.
        """
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
            return True
        except Exception:
            return False

    def connect(self):
        if not self.conn or self.conn.closed:
            try:
                pool = get_connection_pool()
                # 1. 커넥션 풀에서 커넥션 획득 (내장 대기 시간 3초 활용)
                self.conn = pool.getconn()
                self.conn.autocommit = True
                self._from_pool = True
                
                # 2. 대여받은 커넥션 생존 검증 (Test-On-Borrow)
                if not self._is_connection_alive(self.conn):
                    logger.warning("Borrowed connection was dead. Evicting and creating a new one.")
                    pool.putconn(self.conn)
                    self.conn = pool.getconn()
                    self.conn.autocommit = True
                
                # pgvector 데이터 타입 등록
                register_vector(self.conn)
            except Exception as e:
                logger.error(f"Failed to lease connection from pool ({e}). Falling back to direct connection.")
                # 풀 에러 발생 시 최후의 보루로 다이렉트 단일 연결로 폴백
                self.conn = psycopg.connect(DATABASE_URL)
                self.conn.autocommit = True
                self._from_pool = False
                try:
                    register_vector(self.conn)
                except Exception:
                    pass

    def close(self):
        if self.conn and not self.conn.closed:
            if self._from_pool:
                try:
                    pool = get_connection_pool()
                    # 풀에 다시 커넥션 반납
                    pool.putconn(self.conn)
                except Exception as e:
                    logger.error(f"Failed to return connection to pool ({e}). Closing physically.")
                    self.conn.close()
            else:
                self.conn.close()
            self.conn = None

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

    def rollback(self):
        if self.conn and not self.conn.closed:
            try:
                self.conn.rollback()
            except Exception as e:
                logger.error(f"Failed to rollback transaction: {e}")
