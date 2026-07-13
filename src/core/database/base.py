from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Generator, Any


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
    @contextmanager
    def cursor(self) -> Generator[Any, None, None]:
        """커서를 빌려주고 자동으로 닫고 트랜잭션 커밋/롤백 및 예외 래핑을 관리하는 컨텍스트 매니저입니다."""
        pass

    @abstractmethod
    def execute_batch(self, query: str, values: list, template: str = None, page_size: int = 50):
        """배치 삽입 처리를 추상화하여 하위 드라이버 종속성을 제거합니다."""
        pass

    @abstractmethod
    def rollback(self):
        """현재 트랜잭션을 롤백합니다."""
        pass
