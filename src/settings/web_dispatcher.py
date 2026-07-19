import os

from fastapi.responses import Response


WEB_EXACT_PATHS = frozenset({
    "/", "/health", "/dashboard", "/documents", "/inbox", "/learning",
    "/settings", "/search-feedback", "/callback", "/login", "/logout",
})
WEB_PATH_PREFIXES = (
    "/search-feedback/", "/settings/", "/api/settings", "/api/keys",
    "/api/documents", "/api/inbox", "/api/learning", "/api/search-feedback",
)


def is_web_path(path: str) -> bool:
    return path in WEB_EXACT_PATHS or path.startswith(WEB_PATH_PREFIXES)


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
        if mcp_host and host == mcp_host and is_web_path(path) and path not in {"/", "/health"}:
            await self._not_found(scope, receive, send)
            return

        app = self.web_app if is_web_path(path) else self.mcp_app
        await app(scope, receive, send)

    async def _not_found(self, scope, receive, send):
        response = Response(
            content='{"detail":"Not Found"}',
            status_code=404,
            media_type="application/json",
        )
        await response(scope, receive, send)
