from pathlib import Path
import logging
import os
import time
from typing import List, Optional

import jwt
from fastapi import Cookie, FastAPI, Header, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from src.api.middleware import _validate_api_key_cached
from src.api_keys.auth import verify_auth_token
from src.api_keys.service import ApiKeyService
from src.settings.service import SettingsEncryptionError, UserSettingsService
from src.retrieval.feedback import SearchFeedbackService

STATIC_DIR = Path(__file__).resolve().parent / "static"
settings_app = FastAPI(title="LLM-Wiki Settings", docs_url=None, redoc_url=None)
SESSION_COOKIE = "knowledge_session"
LOGIN_URL = os.getenv(
    "KNOWLEDGE_LOGIN_URL",
    "https://auth.snappytory.com/portal/tenants/knowledge/login",
)
logger = logging.getLogger("settings_auth")


class SettingsPayload(BaseModel):
    openai_api_key: Optional[str] = Field(default=None, max_length=512)
    storage_type: str = Field(default="s3", pattern="^(s3|r2)$")
    s3_endpoint_url: Optional[str] = Field(default=None, max_length=2048)
    s3_bucket_name: Optional[str] = Field(default=None, max_length=255)
    s3_access_key_id: Optional[str] = Field(default=None, max_length=1024)
    s3_secret_access_key: Optional[str] = Field(default=None, max_length=2048)


class CreateApiKeyPayload(BaseModel):
    key_name: str = Field(min_length=1, max_length=100)
    validity_days: int = Field(default=365, ge=1, le=3650)


class SessionPayload(BaseModel):
    access_token: str = Field(min_length=20, max_length=8192)


class SearchFeedbackPayload(BaseModel):
    relevant_paths: List[str] = Field(default_factory=list, max_length=20)
    irrelevant_paths: List[str] = Field(default_factory=list, max_length=20)
    expected_no_answer: bool = False
    missing_answer_path: Optional[str] = Field(default=None, max_length=512)
    notes: Optional[str] = Field(default=None, max_length=2000)


async def _authenticated_user(authorization: Optional[str], session_token: Optional[str] = None) -> str:
    if session_token:
        try:
            claims = verify_auth_token(session_token)
            return ApiKeyService().get_or_create_user(claims["sub"])
        except jwt.PyJWTError as exc:
            raise HTTPException(status_code=401, detail="로그인 세션이 만료되었습니다.") from exc
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    result = await _validate_api_key_cached(authorization.split(" ", 1)[1].strip())
    if not result:
        raise HTTPException(status_code=401, detail="유효하지 않거나 만료된 API Key입니다.")
    return result.get("user_id", "SYSTEM")


def _authenticated_auth_id(authorization: Optional[str], session_token: Optional[str] = None) -> str:
    token = session_token
    if not token and authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="인증서버 로그인 토큰이 필요합니다.")
    try:
        claims = verify_auth_token(token)
        return claims["sub"]
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="유효하지 않은 인증서버 로그인 토큰입니다.") from exc


@settings_app.get("/", include_in_schema=False)
def root():
    return {"service": "LLM-Wiki MCP Server", "status": "ok",
            "mcp_endpoint": "/mcp", "settings_url": "/settings"}


@settings_app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}


@settings_app.get("/settings", response_class=HTMLResponse, include_in_schema=False)
def settings_page(knowledge_session: Optional[str] = Cookie(default=None)):
    if not knowledge_session:
        return RedirectResponse(LOGIN_URL, status_code=302)
    try:
        verify_auth_token(knowledge_session)
    except jwt.PyJWTError:
        response = RedirectResponse(LOGIN_URL, status_code=302)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@settings_app.get("/callback", response_class=HTMLResponse, include_in_schema=False)
def auth_callback():
    return HTMLResponse((STATIC_DIR / "callback.html").read_text(encoding="utf-8"))


@settings_app.get("/login", include_in_schema=False)
def login():
    return RedirectResponse(LOGIN_URL, status_code=302)


@settings_app.post("/api/session", include_in_schema=False)
def create_session(payload: SessionPayload):
    try:
        claims = verify_auth_token(payload.access_token)
    except jwt.PyJWTError as exc:
        # 토큰이나 claim은 기록하지 않고 실패 유형만 남긴다.
        logger.warning("Login token verification failed: error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=401, detail="유효하지 않은 로그인 토큰입니다.") from exc
    max_age = max(1, min(int(claims.get("exp", 0)) - int(time.time()), 86400))
    response = JSONResponse({"success": True})
    response.set_cookie(
        SESSION_COOKIE,
        payload.access_token,
        max_age=max_age,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return response


@settings_app.post("/logout", include_in_schema=False)
def logout():
    response = JSONResponse({"success": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@settings_app.get("/settings/assets/settings.css", include_in_schema=False)
def settings_css():
    return Response((STATIC_DIR / "settings.css").read_text(encoding="utf-8"), media_type="text/css")


@settings_app.get("/settings/assets/settings.js", include_in_schema=False)
def settings_js():
    return Response((STATIC_DIR / "settings.js").read_text(encoding="utf-8"), media_type="application/javascript")


@settings_app.get("/api/settings")
async def read_settings(
    authorization: Optional[str] = Header(default=None),
    knowledge_session: Optional[str] = Cookie(default=None),
):
    owner_id = await _authenticated_user(authorization, knowledge_session)
    service = UserSettingsService()
    try:
        return service.get_public(owner_id)
    finally:
        service.db_manager.close()


@settings_app.put("/api/settings")
async def save_settings(
    payload: SettingsPayload,
    authorization: Optional[str] = Header(default=None),
    knowledge_session: Optional[str] = Cookie(default=None),
):
    owner_id = await _authenticated_user(authorization, knowledge_session)
    if payload.storage_type in ("s3", "r2") and (not payload.s3_endpoint_url or not payload.s3_bucket_name):
        raise HTTPException(status_code=422, detail="S3/R2 Endpoint와 Bucket은 필수입니다.")
    service = UserSettingsService()
    try:
        existing = service.get_public(owner_id)
        if payload.storage_type in ("s3", "r2"):
            if not payload.s3_access_key_id and not existing["s3_access_key_configured"]:
                raise HTTPException(status_code=422, detail="Access Key ID가 필요합니다.")
            if not payload.s3_secret_access_key and not existing["s3_secret_key_configured"]:
                raise HTTPException(status_code=422, detail="Secret Access Key가 필요합니다.")
        saved = service.save(owner_id, payload.model_dump())
        from src.core.storage.factory import invalidate_storage_cache
        invalidate_storage_cache(owner_id)
        return saved
    except SettingsEncryptionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        service.db_manager.close()


@settings_app.get("/api/keys")
def list_api_keys(authorization: Optional[str] = Header(default=None), knowledge_session: Optional[str] = Cookie(default=None)):
    auth_id = _authenticated_auth_id(authorization, knowledge_session)
    return ApiKeyService().list_for_user(auth_id)


@settings_app.post("/api/keys", status_code=status.HTTP_201_CREATED)
def create_api_key(payload: CreateApiKeyPayload, authorization: Optional[str] = Header(default=None), knowledge_session: Optional[str] = Cookie(default=None)):
    auth_id = _authenticated_auth_id(authorization, knowledge_session)
    return ApiKeyService().create(auth_id, payload.key_name, payload.validity_days)


@settings_app.delete("/api/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_key(key_id: str, authorization: Optional[str] = Header(default=None), knowledge_session: Optional[str] = Cookie(default=None)):
    auth_id = _authenticated_auth_id(authorization, knowledge_session)
    if not ApiKeyService().revoke(auth_id, key_id):
        raise HTTPException(status_code=404, detail="API Key를 찾을 수 없습니다.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@settings_app.get("/api/search-feedback/events")
async def recent_search_events(
    limit: int = 30,
    authorization: Optional[str] = Header(default=None),
    knowledge_session: Optional[str] = Cookie(default=None),
):
    owner_id = await _authenticated_user(authorization, knowledge_session)
    service = SearchFeedbackService()
    try:
        return {"events": service.list_recent(owner_id, limit)}
    finally:
        service.db_manager.close()


@settings_app.put("/api/search-feedback/{search_id}")
async def save_search_feedback(
    search_id: str,
    payload: SearchFeedbackPayload,
    authorization: Optional[str] = Header(default=None),
    knowledge_session: Optional[str] = Cookie(default=None),
):
    owner_id = await _authenticated_user(authorization, knowledge_session)
    service = SearchFeedbackService()
    try:
        return service.submit(owner_id, search_id, **payload.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        service.db_manager.close()


class SettingsPathDispatcher:
    """웹 설정 경로만 FastAPI로 보내고 나머지는 기존 MCP 앱에 보냅니다."""
    def __init__(self, web_app, mcp_app):
        self.web_app = web_app
        self.mcp_app = mcp_app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        host = headers.get("host", "").split(":", 1)[0].lower()
        settings_host = os.getenv("SETTINGS_PUBLIC_HOST", "").lower()
        mcp_host = os.getenv("MCP_PUBLIC_HOST", "").lower()

        if settings_host and host == settings_host and path.startswith("/mcp"):
            await self._not_found(scope, receive, send)
            return
        if mcp_host and host == mcp_host and (
            path == "/settings" or path.startswith("/settings/") or
            path.startswith("/api/settings") or path.startswith("/api/keys") or
            path.startswith("/api/search-feedback") or
            path in ("/callback", "/login", "/logout", "/api/session")
        ):
            await self._not_found(scope, receive, send)
            return

        web_path = (path in ("/", "/health", "/settings", "/callback", "/login", "/logout", "/api/session") or
                    path.startswith("/settings/") or path.startswith("/api/settings"))
        web_path = web_path or path.startswith("/api/keys")
        web_path = web_path or path.startswith("/api/search-feedback")
        await (self.web_app if web_path else self.mcp_app)(scope, receive, send)

    async def _not_found(self, scope, receive, send):
        response = Response(
            content='{"detail":"Not Found"}',
            status_code=404,
            media_type="application/json",
        )
        await response(scope, receive, send)
