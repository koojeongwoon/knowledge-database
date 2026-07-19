import logging
from typing import Any, Callable, Optional

from fastapi import APIRouter, Cookie, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from src.settings.oauth_session import OAuthSessionError


logger = logging.getLogger("settings_auth")


def create_auth_router(
    session_store_factory: Callable[[], Any],
    session_cookie: str,
) -> APIRouter:
    router = APIRouter()

    @router.get("/login", include_in_schema=False)
    def login():
        try:
            store = session_store_factory()
            state, _, challenge = store.begin_login()
            return RedirectResponse(
                store.oauth_client.authorization_url(state, challenge),
                status_code=302,
            )
        except OAuthSessionError:
            return HTMLResponse("로그인 서비스를 일시적으로 사용할 수 없습니다.", status_code=503)

    @router.get("/callback", include_in_schema=False)
    async def auth_callback(
        code: Optional[str] = Query(default=None),
        state: Optional[str] = Query(default=None),
        error: Optional[str] = Query(default=None),
    ):
        if error or not code or not state:
            return HTMLResponse("로그인이 취소되었거나 올바르지 않은 응답입니다.", status_code=400)
        try:
            store = session_store_factory()
            verifier = store.consume_login(state)
            payload = await store.oauth_client.exchange_code(code, verifier)
            session_id = store.create(payload)
        except OAuthSessionError as exc:
            logger.warning("OAuth callback failed: error_type=%s", type(exc).__name__)
            return HTMLResponse("로그인 세션을 만들지 못했습니다. 다시 시도해 주세요.", status_code=401)
        response = RedirectResponse("/dashboard", status_code=303)
        response.set_cookie(
            session_cookie,
            session_id,
            max_age=store.session_ttl,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
        return response

    @router.post("/logout", include_in_schema=False)
    def logout(knowledge_session: Optional[str] = Cookie(default=None)):
        if knowledge_session:
            session_store_factory().revoke(knowledge_session)
        response = JSONResponse({"success": True})
        response.delete_cookie(session_cookie, path="/")
        return response

    return router
