from pathlib import Path
from typing import Optional

import jwt
from fastapi import FastAPI, HTTPException

from src.api.middleware import _validate_api_key_cached
from src.api_keys.auth import verify_auth_token
from src.api_keys.service import ApiKeyService
from src.settings.service import SettingsEncryptionError, UserSettingsService
from src.settings.documents import DocumentBrowserService
from src.settings.inbox import InboxService, MAX_UPLOAD_BYTES
from src.retrieval.feedback import SearchFeedbackService
from src.core.database.factory import DatabaseManager
from src.learning.application.dashboard import LearningDashboardService
from src.learning.infrastructure.dashboard_repository import LearningDashboardRepository
from src.settings.oauth_session import (
    OAuthSessionError,
    session_store,
)
from src.settings.web_auth import create_auth_router
from src.settings.web_api_keys import create_api_key_router
from src.settings.web_configuration import create_configuration_router
from src.settings.web_content import create_content_router
from src.settings.web_feedback import create_feedback_router
from src.settings.web_learning import create_learning_router
from src.settings.web_pages import create_page_router
from src.settings.web_dispatcher import SettingsPathDispatcher
from src.core.storage.factory import invalidate_storage_cache

STATIC_DIR = Path(__file__).resolve().parent / "static"
settings_app = FastAPI(title="LLM-Wiki Settings", docs_url=None, redoc_url=None)
SESSION_COOKIE = "knowledge_session"


async def _authenticated_user(authorization: Optional[str], session_token: Optional[str] = None) -> str:
    if session_token:
        try:
            token_set = await session_store().resolve(session_token)
            return ApiKeyService().get_or_create_user(token_set.auth_id)
        except OAuthSessionError as exc:
            raise HTTPException(status_code=401, detail="로그인 세션이 만료되었습니다.") from exc
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    result = await _validate_api_key_cached(authorization.split(" ", 1)[1].strip())
    if not result:
        raise HTTPException(status_code=401, detail="유효하지 않거나 만료된 API Key입니다.")
    return result.get("user_id", "SYSTEM")


async def _authenticated_auth_id(authorization: Optional[str], session_token: Optional[str] = None) -> str:
    if session_token:
        try:
            return (await session_store().resolve(session_token)).auth_id
        except OAuthSessionError as exc:
            raise HTTPException(status_code=401, detail="로그인 세션이 만료되었습니다.") from exc
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="인증서버 로그인 토큰이 필요합니다.")
    try:
        claims = verify_auth_token(authorization.split(" ", 1)[1].strip())
        return claims["sub"]
    except (jwt.PyJWTError, KeyError) as exc:
        raise HTTPException(status_code=401, detail="유효하지 않은 인증서버 로그인 토큰입니다.") from exc


@settings_app.get("/", include_in_schema=False)
def root():
    return {"service": "LLM-Wiki MCP Server", "status": "ok",
            "mcp_endpoint": "/mcp", "settings_url": "/settings"}


@settings_app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}


settings_app.include_router(create_auth_router(lambda: session_store(), SESSION_COOKIE))
settings_app.include_router(create_page_router(STATIC_DIR, lambda: session_store(), SESSION_COOKIE))
settings_app.include_router(create_content_router(
    lambda authorization, session_token: _authenticated_user(authorization, session_token),
    lambda owner_id: InboxService(owner_id),
    lambda owner_id: DocumentBrowserService(owner_id),
    MAX_UPLOAD_BYTES,
))
settings_app.include_router(create_learning_router(
    lambda authorization, session_token: _authenticated_user(authorization, session_token),
    lambda: DatabaseManager(),
    lambda db_manager: LearningDashboardService(LearningDashboardRepository(db_manager)),
))
settings_app.include_router(create_feedback_router(
    lambda authorization, session_token: _authenticated_user(authorization, session_token),
    lambda: SearchFeedbackService(),
))
settings_app.include_router(create_configuration_router(
    lambda authorization, session_token: _authenticated_user(authorization, session_token),
    lambda: UserSettingsService(),
    lambda owner_id: invalidate_storage_cache(owner_id),
    SettingsEncryptionError,
))
settings_app.include_router(create_api_key_router(
    lambda authorization, session_token: _authenticated_auth_id(authorization, session_token),
    lambda: ApiKeyService(),
))
