from pathlib import Path
import logging
import os
from typing import List, Optional

import jwt
from fastapi import Cookie, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from src.api.middleware import _validate_api_key_cached
from src.api_keys.auth import verify_auth_token
from src.api_keys.service import ApiKeyService
from src.settings.service import SettingsEncryptionError, UserSettingsService
from src.retrieval.feedback import SearchFeedbackService
from src.settings.oauth_session import (
    OAuthSessionError,
    session_store,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
settings_app = FastAPI(title="LLM-Wiki Settings", docs_url=None, redoc_url=None)
SESSION_COOKIE = "knowledge_session"
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


class SearchResultFeedbackPayload(BaseModel):
    file_path: str = Field(min_length=1, max_length=512)
    relevance_grade: int = Field(ge=0, le=3)
    issue_reasons: List[str] = Field(default_factory=list, max_length=10)
    preferred_replacement_path: Optional[str] = Field(default=None, max_length=512)
    relation_helpful: Optional[bool] = None
    notes: Optional[str] = Field(default=None, max_length=1000)


class SearchFeedbackPayload(BaseModel):
    relevant_paths: List[str] = Field(default_factory=list, max_length=20)
    partially_relevant_paths: List[str] = Field(default_factory=list, max_length=20)
    irrelevant_paths: List[str] = Field(default_factory=list, max_length=20)
    satisfaction: Optional[str] = Field(default=None, pattern="^(satisfied|partial|dissatisfied)$")
    failure_reasons: List[str] = Field(default_factory=list, max_length=10)
    expected_no_answer: bool = False
    missing_answer_path: Optional[str] = Field(default=None, max_length=512)
    notes: Optional[str] = Field(default=None, max_length=2000)
    result_feedback: List[SearchResultFeedbackPayload] = Field(default_factory=list, max_length=30)


class SearchBehaviorPayload(BaseModel):
    action: str = Field(pattern="^(open|copy|cite|follow_graph|reformulate|abandon)$")
    file_path: Optional[str] = Field(default=None, max_length=512)
    position: Optional[int] = Field(default=None, ge=1, le=1000)


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


@settings_app.get("/settings", response_class=HTMLResponse, include_in_schema=False)
async def settings_page(knowledge_session: Optional[str] = Cookie(default=None)):
    return await _protected_page("index.html", knowledge_session)


@settings_app.get("/settings/edit", response_class=HTMLResponse, include_in_schema=False)
async def settings_edit_page(knowledge_session: Optional[str] = Cookie(default=None)):
    return await _protected_page("edit.html", knowledge_session)


async def _protected_page(filename: str, knowledge_session: Optional[str]):
    if not knowledge_session:
        return RedirectResponse("/login", status_code=302)
    try:
        await session_store().resolve(knowledge_session)
    except OAuthSessionError:
        response = RedirectResponse("/login", status_code=302)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response
    return HTMLResponse((STATIC_DIR / filename).read_text(encoding="utf-8"))


@settings_app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_page(knowledge_session: Optional[str] = Cookie(default=None)):
    return await _protected_page("dashboard.html", knowledge_session)


@settings_app.get("/search-feedback", response_class=HTMLResponse, include_in_schema=False)
async def search_feedback_page(knowledge_session: Optional[str] = Cookie(default=None)):
    return await _protected_page("feedback.html", knowledge_session)


@settings_app.get("/search-feedback/{search_id}", response_class=HTMLResponse, include_in_schema=False)
async def search_graph_page(search_id: str, knowledge_session: Optional[str] = Cookie(default=None)):
    return await _protected_page("search-graph.html", knowledge_session)


@settings_app.get("/login", include_in_schema=False)
def login():
    try:
        state, _, challenge = session_store().begin_login()
        return RedirectResponse(
            session_store().oauth_client.authorization_url(state, challenge),
            status_code=302,
        )
    except OAuthSessionError:
        return HTMLResponse("로그인 서비스를 일시적으로 사용할 수 없습니다.", status_code=503)


@settings_app.get("/callback", include_in_schema=False)
async def auth_callback(
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
):
    if error or not code or not state:
        return HTMLResponse("로그인이 취소되었거나 올바르지 않은 응답입니다.", status_code=400)
    try:
        verifier = session_store().consume_login(state)
        payload = await session_store().oauth_client.exchange_code(code, verifier)
        session_id = session_store().create(payload)
    except OAuthSessionError as exc:
        logger.warning("OAuth callback failed: error_type=%s", type(exc).__name__)
        return HTMLResponse("로그인 세션을 만들지 못했습니다. 다시 시도해 주세요.", status_code=401)
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        max_age=session_store().session_ttl,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return response


@settings_app.post("/logout", include_in_schema=False)
def logout(knowledge_session: Optional[str] = Cookie(default=None)):
    if knowledge_session:
        session_store().revoke(knowledge_session)
    response = JSONResponse({"success": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@settings_app.get("/settings/assets/settings.css", include_in_schema=False)
def settings_css():
    return Response((STATIC_DIR / "settings.css").read_text(encoding="utf-8"), media_type="text/css")


@settings_app.get("/settings/assets/settings.js", include_in_schema=False)
def settings_js():
    return Response((STATIC_DIR / "settings.js").read_text(encoding="utf-8"), media_type="application/javascript")


@settings_app.get("/settings/assets/edit.js", include_in_schema=False)
def settings_edit_js():
    return Response((STATIC_DIR / "edit.js").read_text(encoding="utf-8"), media_type="application/javascript")


@settings_app.get("/settings/assets/settings-view.css", include_in_schema=False)
def settings_view_css():
    return Response((STATIC_DIR / "settings-view.css").read_text(encoding="utf-8"), media_type="text/css")


@settings_app.get("/settings/assets/feedback.js", include_in_schema=False)
def feedback_js():
    return Response((STATIC_DIR / "feedback.js").read_text(encoding="utf-8"), media_type="application/javascript")


@settings_app.get("/settings/assets/feedback-links.js", include_in_schema=False)
def feedback_links_js():
    return Response((STATIC_DIR / "feedback-links.js").read_text(encoding="utf-8"), media_type="application/javascript")


@settings_app.get("/settings/assets/feedback.css", include_in_schema=False)
def feedback_css():
    return Response((STATIC_DIR / "feedback.css").read_text(encoding="utf-8"), media_type="text/css")


@settings_app.get("/settings/assets/search-graph.js", include_in_schema=False)
def search_graph_js():
    return Response((STATIC_DIR / "search-graph.js").read_text(encoding="utf-8"), media_type="application/javascript")


@settings_app.get("/settings/assets/search-graph.css", include_in_schema=False)
def search_graph_css():
    return Response((STATIC_DIR / "search-graph.css").read_text(encoding="utf-8"), media_type="text/css")


@settings_app.get("/settings/assets/cytoscape-3.34.0.min.js", include_in_schema=False)
def cytoscape_js():
    return Response((STATIC_DIR / "cytoscape-3.34.0.min.js").read_bytes(), media_type="application/javascript")


@settings_app.get("/settings/assets/dashboard.js", include_in_schema=False)
def dashboard_js():
    return Response((STATIC_DIR / "dashboard.js").read_text(encoding="utf-8"), media_type="application/javascript")


@settings_app.get("/settings/assets/dashboard.css", include_in_schema=False)
def dashboard_css():
    return Response((STATIC_DIR / "dashboard.css").read_text(encoding="utf-8"), media_type="text/css")


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
async def list_api_keys(authorization: Optional[str] = Header(default=None), knowledge_session: Optional[str] = Cookie(default=None)):
    auth_id = await _authenticated_auth_id(authorization, knowledge_session)
    return ApiKeyService().list_for_user(auth_id)


@settings_app.post("/api/keys", status_code=status.HTTP_201_CREATED)
async def create_api_key(payload: CreateApiKeyPayload, authorization: Optional[str] = Header(default=None), knowledge_session: Optional[str] = Cookie(default=None)):
    auth_id = await _authenticated_auth_id(authorization, knowledge_session)
    return ApiKeyService().create(auth_id, payload.key_name, payload.validity_days)


@settings_app.delete("/api/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(key_id: str, authorization: Optional[str] = Header(default=None), knowledge_session: Optional[str] = Cookie(default=None)):
    auth_id = await _authenticated_auth_id(authorization, knowledge_session)
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


@settings_app.get("/api/search-feedback/{search_id}/graph")
async def search_feedback_graph(
    search_id: str,
    authorization: Optional[str] = Header(default=None),
    knowledge_session: Optional[str] = Cookie(default=None),
):
    owner_id = await _authenticated_user(authorization, knowledge_session)
    service = SearchFeedbackService()
    try:
        return service.graph_for_event(owner_id, search_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
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


@settings_app.post("/api/search-feedback/{search_id}/behavior", status_code=status.HTTP_201_CREATED)
async def save_search_behavior(
    search_id: str,
    payload: SearchBehaviorPayload,
    authorization: Optional[str] = Header(default=None),
    knowledge_session: Optional[str] = Cookie(default=None),
):
    owner_id = await _authenticated_user(authorization, knowledge_session)
    service = SearchFeedbackService()
    try:
        return service.record_behavior(owner_id, search_id, **payload.model_dump())
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
            path in ("/dashboard", "/settings", "/search-feedback") or path.startswith("/search-feedback/") or path.startswith("/settings/") or
            path.startswith("/api/settings") or path.startswith("/api/keys") or
            path.startswith("/api/search-feedback") or
            path in ("/callback", "/login", "/logout")
        ):
            await self._not_found(scope, receive, send)
            return

        web_path = (path in ("/", "/health", "/dashboard", "/settings", "/search-feedback", "/callback", "/login", "/logout") or path.startswith("/search-feedback/") or
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
