import json
import logging
import os
import hashlib
import base64
from datetime import datetime, timezone

import httpx

from src.core.cache.factory import WikiCacheManager
from src.core.config import current_user_config
from src.core.logging.audit import log_audit

# 추상화된 공용 캐시 매니저 획득 (Wiki Cache 인스턴스 연동)
cache_manager = WikiCacheManager()

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

def _hash_api_key(plain_key: str) -> str:
    """평문 API Key를 지식베이스 저장 형식으로 SHA-256 해싱합니다."""
    hasher = hashlib.sha256()
    hasher.update(plain_key.encode('utf-8'))
    return base64.b64encode(hasher.digest()).decode('utf-8')

def _validate_api_key_from_db(plain_key: str) -> dict:
    """지식베이스 소유의 API Key 테이블에서 실존 여부와 만료일을 검사합니다."""
    key_hash = _hash_api_key(plain_key)
    try:
        from src.core.database.factory import DatabaseManager
        with DatabaseManager().cursor() as cur:
            cur.execute("""
                SELECT user_id, expires_at 
                FROM knowledge_api_keys 
                WHERE api_key_hash = %s;
            """, (key_hash,))
            row = cur.fetchone()
            
            if row:
                user_id, expires_at = row
                # 만료일자가 없거나 현재 시간보다 뒤에 있어야 유효
                if expires_at is None or expires_at > datetime.now(timezone.utc):
                    return {
                        "valid": True, 
                        "api_key": plain_key, 
                        "user_id": user_id,
                        "expires_at": expires_at.isoformat() if expires_at else None
                    }
    except Exception as e:
        logger.error(f"Failed to validate API Key in local DB: {e}")
    return None

async def _validate_api_key_cached(token: str) -> dict:
    """지식베이스 전용 Redis 인스턴스에 토큰 유효성 검증 결과를 캐싱합니다."""
    # 1. 보안성 향상을 위해 평문 API Key를 해싱한 값을 캐시 키로 일원화
    key_hash = _hash_api_key(token)
    cache_key = f"auth:token:hash:{key_hash}"
    
    # 2. 추상화 레이어를 통해 캐시 조회 (컨슈머가 CREATED 이벤트 시점에 미리 밀어넣었거나 기 캐싱된 결과)
    cached_result = cache_manager.get(cache_key)
    if cached_result:
        try:
            result = json.loads(cached_result)
            
            # 2.1 캐시 만료 시간 2중 체크 (이중 방어벽)
            expires_at_str = result.get("expires_at")
            if expires_at_str:
                expires_dt = datetime.fromisoformat(expires_at_str)
                if expires_dt <= datetime.now(timezone.utc):
                    log_audit("AUTHENTICATE_EXPIRED", "FAILED", user_id=result.get("user_id", token))
                    # 만료되었을 경우 캐시 즉시 제거
                    cache_manager.delete(cache_key)
                    return None
                    
            log_audit("AUTHENTICATE_BY_CACHE", "SUCCESS", user_id=result.get("user_id", token))
            return result
        except json.JSONDecodeError:
            pass

    # 3. 캐시 미스 시 로컬 DB 조회
    result = _validate_api_key_from_db(token)
    if result:
        # 3.1 남은 유효 시간을 계산하여 Redis TTL로 설정
        expires_at_str = result.get("expires_at")
        ttl = 86400  # 기본값 24시간
        if expires_at_str:
            expires_dt = datetime.fromisoformat(expires_at_str)
            remaining_seconds = int((expires_dt - datetime.now(timezone.utc)).total_seconds())
            if remaining_seconds <= 0:
                log_audit("AUTHENTICATE_EXPIRED", "FAILED", user_id=result["user_id"])
                return None
            ttl = min(86400, remaining_seconds)
            
        # 인증 성공 시 캐시 매니저에 정밀 TTL로 보관 (폐기 시 즉각 Evict되므로 일관성 100% 보장)
        cache_manager.set(cache_key, json.dumps(result), ttl=ttl)
        log_audit("AUTHENTICATE_LOCAL_DB", "SUCCESS", user_id=result["user_id"])
        return result
        
    log_audit("AUTHENTICATE", "FAILED", user_id=token, payload={"reason": "Invalid or expired API Key"})
    return None

def _extract_user_config(headers: dict) -> dict:
    """HTTP 헤더에서는 인증 토큰만 추출합니다."""
    auth_header = headers.get("authorization", "")
    token = auth_header.split(" ", 1)[1] if auth_header.startswith("Bearer ") else auth_header
    
    return {
        "api_key": token,
    }


def _request_user_config(headers: dict, user_id: str) -> dict:
    """인증 사용자는 토큰 식별자만 유지하고 자격증명 헤더는 신뢰하지 않습니다."""
    header_config = _extract_user_config(headers)
    if user_id != "SYSTEM":
        return {
            "api_key": header_config.get("api_key"),
            "user_id": user_id,
        }
    header_config["user_id"] = user_id
    return header_config

class MCPAuthMiddleware:
    """
    HTTP/SSE 프로토콜을 통과하기 전에 요청을 사전 가로채는 ASGI 미들웨어.
    역할:
    1. Authorization 헤더 검증 (로컬 DB 조회를 통환 완벽한 격리 무중단)
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
        validated_user_id = "SYSTEM"
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
            validated_user_id = result.get("user_id", "SYSTEM")

        # ─── SSL Offloading (X-Forwarded-Proto에 따른 지능형 판단) ───
        forwarded_proto = headers.get("x-forwarded-proto", "http")
        if forwarded_proto == "https" or AUTH_SERVER_URL:
            scope["scheme"] = "https"

        # ─── 사용자 설정 ContextVar 주입 ───
        user_config = _request_user_config(headers, validated_user_id)

        # 프론트에서 저장한 설정을 우선 사용하고 미등록 사용자는 기존 헤더 설정을 유지합니다.
        if validated_user_id != "SYSTEM":
            try:
                from src.settings.service import UserSettingsService
                settings_service = UserSettingsService()
                try:
                    stored_config = settings_service.get_runtime_config(validated_user_id)
                    if stored_config:
                        user_config.update(stored_config)
                finally:
                    settings_service.db_manager.close()
            except Exception as e:
                logger.warning(f"Failed to load stored user settings for {validated_user_id}: {e}")
        
        token_val = current_user_config.set(user_config)
        try:
            await self.app(scope, receive, send)
        finally:
            current_user_config.reset(token_val)
