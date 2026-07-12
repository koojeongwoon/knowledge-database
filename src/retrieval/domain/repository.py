from abc import ABC, abstractmethod
from typing import Dict, List, Any

class BaseRetrievalRepository(ABC):
    """지식 검색 및 RAG 조회를 담당하는 도메인 인프라 인터페이스 계약"""
    @abstractmethod
    def keyword_search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def similarity_search(self, query_embedding: List[float], limit: int = 5) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def get_connected_documents(self, file_paths: List[str], limit: int = 3) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def increment_citation_count(self, file_paths: List[str]) -> None:
        """RAG 검색 결과로 인용된 문서들의 인용 횟수를 1 증가시킵니다."""
        pass
