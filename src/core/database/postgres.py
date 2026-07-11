import psycopg2
from pgvector.psycopg2 import register_vector

from src.core.config import DATABASE_URL
from src.core.database.base import BaseDatabaseManager


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
