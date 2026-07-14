"""
캐시 팩토리 모듈.

각 Redis 인스턴스(Wiki Cache / App Cache / Stream)를 생성하는 팩토리 함수를 제공합니다.
모든 연결 파라미터는 config.py에서 중앙 관리됩니다.

사용 예시:
    from src.core.cache.factory import WikiCacheManager, StreamRedisClient

    cache = WikiCacheManager()     # 지식베이스 전용 캐시
    r = StreamRedisClient()        # Redis Streams 전용 클라이언트 (raw redis.Redis)
"""

import redis

from src.core.cache.base import BaseCacheManager
from src.core.cache.redis import RedisCacheManager
from src.core.config import (
    REDIS_WIKI_HOST,
    REDIS_WIKI_PORT,
    REDIS_WIKI_PASSWORD,
    REDIS_CACHE_HOST,
    REDIS_CACHE_PORT,
    REDIS_CACHE_PASSWORD,
    REDIS_STREAM_HOST,
    REDIS_STREAM_PORT,
    REDIS_STREAM_PASSWORD,
)


def WikiCacheManager() -> BaseCacheManager:
    """지식베이스 전용 Redis 캐시 매니저를 생성합니다.

    인스턴스: redis-wiki-cache-service (infra 네임스페이스)
    정책: allkeys-lru / AOF off / maxmemory 50mb
    용도: API Key 토큰 검증 결과 캐싱
    """
    return RedisCacheManager(
        host=REDIS_WIKI_HOST,
        port=REDIS_WIKI_PORT,
        password=REDIS_WIKI_PASSWORD or None,
    )


def AppCacheManager() -> BaseCacheManager:
    """공용 애플리케이션 Redis 캐시 매니저를 생성합니다.

    인스턴스: redis-cache-service (infra 네임스페이스)
    정책: allkeys-lru / AOF off / maxmemory 100mb
    용도: 세션 공유, 일반 임시 캐싱
    """
    return RedisCacheManager(
        host=REDIS_CACHE_HOST,
        port=REDIS_CACHE_PORT,
        password=REDIS_CACHE_PASSWORD or None,
    )


def StreamRedisClient() -> redis.Redis:
    """Redis Streams 전용 클라이언트(raw)를 생성합니다.

    인스턴스: redis-stream-service (infra 네임스페이스)
    정책: noeviction / AOF on / maxmemory 100mb
    용도: 아웃박스 비동기 이벤트 큐 (xreadgroup / xadd / xack)

    Returns:
        redis.Redis: Streams API를 직접 사용하는 저수준 클라이언트
    """
    return redis.Redis(
        host=REDIS_STREAM_HOST,
        port=REDIS_STREAM_PORT,
        password=REDIS_STREAM_PASSWORD or None,
        db=0,
        decode_responses=True,
        socket_timeout=5.0,           # Streams block 폴링 지연을 고려한 여유 타임아웃
        socket_connect_timeout=2.0,
        socket_keepalive=True,
    )


# ── 하위 호환 alias (기존 코드가 CacheManager()를 임포트하는 경우 지원) ─────────
CacheManager = WikiCacheManager
