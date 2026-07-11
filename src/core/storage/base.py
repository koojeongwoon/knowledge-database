from abc import ABC, abstractmethod
from typing import List

class BaseStorageManager(ABC):
    @abstractmethod
    def exists(self, path: str) -> bool:
        """파일 또는 경로가 존재하는지 확인합니다."""
        pass

    @abstractmethod
    def read_text(self, path: str) -> str:
        """지정된 경로의 파일을 텍스트로 읽어옵니다."""
        pass

    @abstractmethod
    def write_text(self, path: str, content: str) -> None:
        """지정된 경로에 텍스트 내용을 저장합니다."""
        pass

    @abstractmethod
    def list_files(self, target_dir: str, pattern: str = "*.md") -> List[str]:
        """지정된 디렉토리 내의 파일 목록을 상대 경로로 리턴합니다."""
        pass

    @abstractmethod
    def copy_file(self, src_path: str, dest_path: str) -> None:
        """로컬 파일 혹은 스토리지 간 파일을 복사합니다."""
        pass

    @abstractmethod
    def delete_file(self, path: str) -> None:
        """지정된 파일을 스토리지에서 삭제합니다."""
        pass

    @abstractmethod
    def makedirs(self, path: str) -> None:
        """가상 혹은 물리적 디렉토리 경로를 생성합니다."""
        pass
