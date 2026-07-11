from src.core.database.postgres import PostgresDatabaseManager

def DatabaseManager():
    """
    PostgreSQL 데이터베이스 관리자 구현체 인스턴스를 반환하는 팩토리 함수입니다.
    """
    return PostgresDatabaseManager()
