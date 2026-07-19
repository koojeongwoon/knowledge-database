from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from src.settings.oauth_session import OAuthSessionError


def create_page_router(
    static_dir: Path,
    session_store_factory: Callable[[], Any],
    session_cookie: str,
) -> APIRouter:
    router = APIRouter()

    async def protected_page(filename: str, session_token: Optional[str]):
        if not session_token:
            return RedirectResponse("/login", status_code=302)
        try:
            await session_store_factory().resolve(session_token)
        except OAuthSessionError:
            response = RedirectResponse("/login", status_code=302)
            response.delete_cookie(session_cookie, path="/")
            return response
        return HTMLResponse((static_dir / filename).read_text(encoding="utf-8"))

    def page_route(filename: str):
        async def route(knowledge_session: Optional[str] = Cookie(default=None)):
            return await protected_page(filename, knowledge_session)
        return route

    for path, filename in {
        "/settings": "index.html", "/settings/edit": "edit.html", "/dashboard": "dashboard.html",
        "/documents": "documents.html", "/inbox": "inbox.html", "/learning": "learning.html",
        "/search-feedback": "feedback.html", "/search-feedback/{search_id}": "search-graph.html",
    }.items():
        router.add_api_route(path, page_route(filename), methods=["GET"], response_class=HTMLResponse,
                             include_in_schema=False)

    def asset_route(filename: str, media_type: str, binary: bool = False):
        def route():
            path = static_dir / filename
            content = path.read_bytes() if binary else path.read_text(encoding="utf-8")
            return Response(content, media_type=media_type)
        return route

    assets = {
        "settings.css": "text/css", "settings.js": "application/javascript",
        "edit.js": "application/javascript", "settings-view.css": "text/css",
        "feedback.js": "application/javascript", "feedback-links.js": "application/javascript",
        "feedback.css": "text/css", "search-graph.js": "application/javascript",
        "search-graph.css": "text/css", "cytoscape-3.34.0.min.js": "application/javascript",
        "dashboard.js": "application/javascript", "dashboard.css": "text/css",
        "documents.js": "application/javascript", "documents.css": "text/css",
        "inbox.js": "application/javascript", "inbox.css": "text/css",
        "learning.js": "application/javascript", "learning.css": "text/css",
    }
    for filename, media_type in assets.items():
        router.add_api_route(
            f"/settings/assets/{filename}",
            asset_route(filename, media_type, binary=filename == "cytoscape-3.34.0.min.js"),
            methods=["GET"], include_in_schema=False,
        )
    return router
