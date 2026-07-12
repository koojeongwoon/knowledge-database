import json
import logging
import os

import httpx

from src.core.cache.factory import CacheManager
from src.core.config import current_user_config
from src.core.logging.audit import log_audit

# 지식베이스(Wiki) 전용으로 새로 배포될 Redis 캐시 서비스 정보 연동 (infra 네임스페이스 타겟팅)
REDIS_HOST = os.getenv("REDIS_WIKI_HOST", "redis-wiki-cache-service.infra.svc.cluster.local")
REDIS_PORT = int(os.getenv("REDIS_WIKI_PORT", 6379))

# 추상화된 공용 캐시 매니저 획득 (디펜던시 격리)
cache_manager = CacheManager()

logger = logging.getLogger("security_middleware")
AUTH_SERVER_URL = os.getenv("AUTH_SERVER_URL")

async def _send_json_error(send, status: int, detail: str):
    """클라이언트에 에러 코드와 세부 정보를 담아 즉시 JSON 응답을 전송합니다."""
    body = json.dumps({"detail": detail}).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ]
    })
    await send({
        "type": "http.response.body",
        "body": body,
    })

async def _validate_api_key_cached(token: str) -> dict:
    """지식베이스 전용 Redis 인스턴스에 토큰 유효성 검증 결과를 캐싱합니다."""
    cache_key = f"auth:token:{token}"
    
    # 1. 추상화 레이어를 통해 캐시 조회 (하부 Redis 연동 에러는 내부에서 캡슐화 처리됨)
    cached_result = cache_manager.get(cache_key)
    if cached_result:
        try:
            result = json.loads(cached_result)
            # 캐시 히트 성공 감사 로그 기록
            log_audit("AUTHENTICATE_BY_CACHE", "SUCCESS", user_id=token)
            return result
        except json.JSONDecodeError:
            pass

    # 2. 캐시 미스 시 외부 인증 서버 호출
    if not AUTH_SERVER_URL:
        log_audit("AUTHENTICATE_BYPASS", "SUCCESS", user_id=token)
        return {"valid": True, "api_key": token}
        
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.post(
                f"{AUTH_SERVER_URL}/api/auth/validate-key",
                json={"api_key": token}
            )
            if response.status_code == 200:
                result = response.json()
                if result.get("valid"):
                    # 3. 인증 성공 시 캐시 매니저에 5분(TTL 300초)간 보관
                    cache_manager.set(cache_key, json.dumps(result), ttl=300)
                    log_audit("AUTHENTICATE_BY_AUTH_SERVER", "SUCCESS", user_id=token)
                    return result
    except Exception as e:
        logger.error(f"Authentication Server Connection Timeout/Error: {e}")
        
    log_audit("AUTHENTICATE", "FAILED", user_id=token, payload={"reason": "Invalid token or auth server offline"})
    return None

def _extract_user_config(headers: dict) -> dict:
    """HTTP 헤더에서 사용자별 설정 정보를 추출합니다 (factory.py 규격과 완벽히 호환되는 중첩 구조 복구)."""
    auth_header = headers.get("authorization", "")
    token = auth_header.split(" ", 1)[1] if auth_header.startswith("Bearer ") else auth_header
    
    return {
        "api_key": token,
        "openai_api_key": headers.get("x-openai-api-key"),
        "anthropic_api_key": headers.get("x-anthropic-api-key"),
        "gemini_api_key": headers.get("x-gemini-api-key"),
        # factory.py 스펙과 호환되도록 중첩 딕셔너리로 다시 묶음
        "storage": {
            "storage_type": headers.get("x-storage-type", "local"),
            "s3_endpoint_url": headers.get("x-s3-endpoint-url"),
            "s3_bucket_name": headers.get("x-s3-bucket-name"),
            "s3_access_key_id": headers.get("x-s3-access-key-id"),
            "s3_secret_access_key": headers.get("x-s3-secret-access-key"),
        },
        "wiki_dir": headers.get("x-wiki-dir", "/app/wiki")
    }

class MCPAuthMiddleware:
    """
    HTTP/SSE 프로토콜을 통과하기 전에 요청을 사전 가로채는 순수 ASGI 미들웨어.
    역할:
    1. Authorization 헤더 검증 (인메모리 캐시 TTL 검증 연동)
    2. 사용자별 설정을 ContextVar에 주입하여 멀티테넌트 지원
    3. X-Forwarded-Proto 헤더 기반 유연한 SSL 스킴 오프로딩
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }

        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()

        # ─── 인증 검증 (POST /mcp 실제 요청에 대해서만 수행) ───
        if AUTH_SERVER_URL and path in ("/mcp",) and method == "POST":
            auth_header = headers.get("authorization", "")
            if not auth_header.startswith("Bearer "):
                await _send_json_error(send, 401, "Missing or invalid Authorization header")
                return

            token = auth_header.split(" ", 1)[1]
            result = await _validate_api_key_cached(token)
            if result is None:
                await _send_json_error(send, 401, "Unauthorized or invalid API Key")
                return

        # ─── SSL Offloading (X-Forwarded-Proto에 따른 지능형 판단) ───
        forwarded_proto = headers.get("x-forwarded-proto", "http")
        if forwarded_proto == "https" or AUTH_SERVER_URL:
            scope["scheme"] = "https"

        # ─── 사용자 설정 ContextVar 주입 ───
        user_config = _extract_user_config(headers)
        token_val = current_user_config.set(user_config)
        try:
            await self.app(scope, receive, send)
        finally:
            current_user_config.reset(token_val)
