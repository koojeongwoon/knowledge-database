from abc import ABC, abstractmethod


class BaseDatabaseManager(ABC):
    @abstractmethod
    def connect(self):
        """데이터베이스 연결을 수립합니다."""
        pass

    @abstractmethod
    def close(self):
        """데이터베이스 연결을 종료합니다."""
        pass
