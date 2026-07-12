from abc import ABC, abstractmethod
from typing import Optional

class BaseCacheManager(ABC):
    """스프링 CacheManager 인터페이스에 대응하는 최상위 캐시 추상화 클래스"""
    @abstractmethod
    def get(self, key: str) -> Optional[str]:
        pass

    @abstractmethod
    def set(self, key: str, value: str, ttl: int = 300) -> bool:
        pass

    @abstractmethod
    def delete(self, key: str) -> bool:
        pass
