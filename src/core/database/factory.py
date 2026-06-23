from src.core.config import DB_TYPE
from src.core.database.postgres import PostgresDatabaseManager
from src.core.database.sqlite import SqliteDatabaseManager

def DatabaseManager():
    """
    설정된 DB_TYPE('sqlite' 또는 'postgres')에 따라 
    알맞은 데이터베이스 관리자 구현체 인스턴스를 반환하는 팩토리 함수입니다.
    """
    if DB_TYPE == "sqlite":
        return SqliteDatabaseManager()
    else:
        return PostgresDatabaseManager()
