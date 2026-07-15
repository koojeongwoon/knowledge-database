from abc import ABC, abstractmethod
from typing import Dict, List, Any

class BaseIndexingRepository(ABC):
    """지식 인덱싱 저장을 담당하는 도메인 인프라 인터페이스 계약"""
    @abstractmethod
    def initialize_db(self) -> None:
        pass

    @abstractmethod
    def get_all_file_hashes(self) -> Dict[str, str]:
        pass

    @abstractmethod
    def get_file_hashes(self, file_paths: List[str]) -> Dict[str, str]:
        """요청된 파일들의 현재 인덱스 해시만 조회합니다."""
        pass

    @abstractmethod
    def upsert_document_chunk(self, doc_data: Dict[str, Any]) -> None:
        pass

    @abstractmethod
    def upsert_document_chunks_batch(self, chunks: List[Dict[str, Any]], batch_size: int = 50) -> None:
        pass

    @abstractmethod
    def insert_edge(self, source_path: str, target_topic: str, weight: float = 1.0) -> None:
        pass

    @abstractmethod
    def delete_document(self, file_path: str) -> None:
        pass

    @abstractmethod
    def upsert_topic(self, topic_name: str, category: str, file_path: str) -> None:
        pass

    @abstractmethod
    def get_topic_by_name(self, topic_name: str) -> Any:
        pass

    @abstractmethod
    def get_document_chunks(self, file_path: str) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def replace_document(
        self,
        file_path: str,
        chunks: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
    ) -> None:
        """문서 청크와 엣지를 하나의 트랜잭션에서 원자적으로 교체합니다."""
        pass
