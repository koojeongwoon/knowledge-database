from src.indexing.domain.repository import BaseIndexingRepository
from src.indexing.infrastructure.postgres import PostgresIndexingRepository

def IndexingRepository(db_manager) -> BaseIndexingRepository:
    """
    K8s/PostgreSQL 전용 인덱싱 레포지토리 객체를 반환하는 팩토리 함수.
    (Facade 패턴으로 프로젝트 전체 import 호환성을 제공합니다.)
    """
    return PostgresIndexingRepository(db_manager)
