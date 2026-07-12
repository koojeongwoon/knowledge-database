import os

from src.core.cache.base import BaseCacheManager
from src.core.cache.redis import RedisCacheManager


def CacheManager() -> BaseCacheManager:
    """
    공용 캐시 인프라 객체를 생성하여 반환하는 팩토리 함수.
    (기본 호스트 도메인을 infra 네임스페이스로 동기화합니다.)
    """
    host = os.getenv("REDIS_WIKI_HOST", "redis-wiki-cache-service.infra.svc.cluster.local")
    port = int(os.getenv("REDIS_WIKI_PORT", 6379))
    return RedisCacheManager(host=host, port=port)
