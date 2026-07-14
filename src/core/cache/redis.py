import logging
from typing import Optional

import redis

from src.core.cache.base import BaseCacheManager

logger = logging.getLogger("cache_manager")


class RedisCacheManager(BaseCacheManager):
    """RedisTemplate 구조를 구현한 Redis 구체 캐시 매니저 (예외 은닉 래핑).

    Args:
        host:             Redis 서버 호스트
        port:             Redis 서버 포트
        password:         인증 비밀번호 (없으면 None)
        socket_timeout:   명령 타임아웃(초) — 커넥션 장애 시 블로킹 방지
        socket_connect_timeout: 초기 연결 타임아웃(초)
        socket_keepalive: TCP Keep-Alive 활성화 여부
    """

    def __init__(
        self,
        host: str,
        port: int,
        password: Optional[str] = None,
        socket_timeout: float = 1.0,
        socket_connect_timeout: float = 2.0,
        socket_keepalive: bool = True,
    ):
        self.client = redis.Redis(
            host=host,
            port=port,
            password=password or None,
            db=0,
            decode_responses=True,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_connect_timeout,
            socket_keepalive=socket_keepalive,
        )

    # ── 기본 캐시 오퍼레이션 ──────────────────────────────────────────────

    def get(self, key: str) -> Optional[str]:
        try:
            return self.client.get(key)
        except redis.RedisError as e:
            # Graceful Fallback: 예외를 상위로 전파하지 않고 내부 로깅 후 None 반환
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

    # ── 헬스체크 ─────────────────────────────────────────────────────────

    def ping(self) -> bool:
        """Redis 연결 상태를 점검합니다. 정상이면 True, 장애 시 False를 반환합니다."""
        try:
            return self.client.ping()
        except redis.RedisError as e:
            logger.warning(f"[Cache Warning] Redis ping failed: {e}")
            return False
