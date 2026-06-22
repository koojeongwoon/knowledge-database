from abc import ABC, abstractmethod
from typing import List, Dict, Any

class BaseDatabaseManager(ABC):
    @abstractmethod
    def connect(self):
        """데이터베이스 연결을 수립합니다."""
        pass

    @abstractmethod
    def close(self):
        """데이터베이스 연결을 종료합니다."""
        pass

    @abstractmethod
    def initialize_db(self):
        """데이터베이스 테이블 및 인덱스를 초기화합니다."""
        pass

    @abstractmethod
    def get_all_file_hashes(self) -> Dict[str, str]:
        """DB에 저장된 모든 파일의 상대경로(file_path)와 content_hash 매핑을 반환합니다."""
        pass

    @abstractmethod
    def upsert_document_chunk(self, doc_data: Dict[str, Any]):
        """단일 문서 청크를 업서트(추가/갱신)합니다."""
        pass

    @abstractmethod
    def upsert_document_chunks_batch(self, chunks: List[Dict[str, Any]], batch_size: int = 50):
        """문서 청크 리스트를 배치로 일괄 업서트합니다."""
        pass

    @abstractmethod
    def keyword_search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """텍스트 질의어 기반 키워드 검색을 수행하여 일치하는 문서 리스트를 반환합니다."""
        pass

    @abstractmethod
    def similarity_search(self, query_embedding: List[float], limit: int = 5) -> List[Dict[str, Any]]:
        """벡터 유사도 기반 검색을 수행하여 상위 K개 문서 리스트를 반환합니다."""
        pass

    @abstractmethod
    def insert_edge(self, source_path: str, target_topic: str):
        """문서 간의 위키링크 연결(Edge) 관계를 저장합니다."""
        pass

    @abstractmethod
    def delete_document(self, file_path: str):
        """특정 파일 경로의 모든 청크 및 소스 엣지 정보를 일괄 삭제합니다."""
        pass

    @abstractmethod
    def get_connected_documents(self, file_paths: List[str], limit: int = 3) -> List[Dict[str, Any]]:
        """지정된 파일 경로들과 연결된 연관 주제 문서 리스트를 조회합니다."""
        pass
