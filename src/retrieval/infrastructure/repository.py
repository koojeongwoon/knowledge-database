from src.retrieval.domain.repository import BaseRetrievalRepository
from src.retrieval.infrastructure.retrieval_repository import PostgresRetrievalRepository

def RetrievalRepository(db_manager) -> BaseRetrievalRepository:
    """
    K8s/PostgreSQL 전용 검색 레포지토리 객체를 반환하는 팩토리 함수.
    (Facade 패턴으로 프로젝트 전체 import 호환성을 제공합니다.)
    """
    return PostgresRetrievalRepository(db_manager)
