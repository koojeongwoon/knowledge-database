import logging
from typing import Optional

import redis
from src.core.cache.base import BaseCacheManager

logger = logging.getLogger("cache_manager")

class RedisCacheManager(BaseCacheManager):
    """RedisTemplate 구조를 구현한 Redis 구체 캐시 매니저 (예외 은닉 래핑)"""
    def __init__(self, host: str, port: int, password: Optional[str] = None):
        self.client = redis.Redis(
            host=host,
            port=port,
            password=password,
            db=0,
            decode_responses=True,
            socket_timeout=1.0  # 연결 장애 시 지연을 방지하기 위한 타임아웃 제한
        )

    def get(self, key: str) -> Optional[str]:
        try:
            return self.client.get(key)
        except redis.RedisError as e:
            # 예외를 상위로 전파하지 않고 내부에서 로깅 후 삼킴 (Graceful Fallback 보장)
            logger.warning(f"[Cache Warning] Redis get failed (Key: {key}): {e}")
            return None

    def set(self, key: str, value: str, ttl: int = 300) -> bool:
        try:
            self.client.setex(key, ttl, value)
            return True
        except redis.RedisError as e:
            logger.warning(f"[Cache Warning] Redis set failed (Key: {key}): {e}")
            return False

    def delete(self, key: str) -> bool:
        try:
            self.client.delete(key)
            return True
        except redis.RedisError as e:
            logger.warning(f"[Cache Warning] Redis delete failed (Key: {key}): {e}")
            return False
