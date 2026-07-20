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
    async def logout(knowledge_session: Optional[str] = Cookie(default=None)):
        logout_url = "/logged-out"
        remotely_revoked = False
        if knowledge_session:
            store = session_store_factory()
            try:
                logout_url, remotely_revoked = await store.logout(knowledge_session)
                if not remotely_revoked:
                    logger.warning("OAuth token revocation failed during logout")
            except OAuthSessionError as exc:
                logger.warning("OAuth logout failed: error_type=%s", type(exc).__name__)
                store.revoke(knowledge_session)
        response = JSONResponse({
            "success": True,
            "remotely_revoked": remotely_revoked,
            "logout_url": logout_url,
        })
        response.delete_cookie(session_cookie, path="/")
        return response

    @router.get("/logged-out", include_in_schema=False)
    def logged_out():
        return HTMLResponse(
            "<!doctype html><html lang='ko'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>로그아웃 완료</title><body><main>"
            "<h1>로그아웃되었습니다.</h1>"
            "<p>Knowledge와 통합 인증 세션을 종료했습니다.</p>"
            "<a href='/login'>다시 로그인</a>"
            "</main></body></html>"
        )

    return router
