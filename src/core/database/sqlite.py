import sqlite3
import json
import re
from typing import List, Dict, Any
from src.core.config import DB_NAME
from src.core.database.base import BaseDatabaseManager

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


