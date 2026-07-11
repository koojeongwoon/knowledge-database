import os
import sys
from typing import List, Optional

# 프로젝트 루트 디렉토리를 Python Path에 추가하여 절대 import 호환성 확보
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from mcp.server.fastmcp import FastMCP
from src.api.agent_tool import retrieve_wiki_knowledge, commit_wiki_knowledge, run_wiki_indexing

# FastMCP 서버 이름: LLM-Wiki
mcp = FastMCP("LLM-Wiki", host="0.0.0.0")

@mcp.tool(name="search_wiki_knowledge")
def search_wiki_knowledge(query: str, limit: int = 5) -> str:
    """
    개인 지식베이스(옵시디언 위키)에서 자연어 검색을 수행하고 
    관련성이 높은 마크다운 지식 조각 및 인용 정보를 반환합니다.
    """
    return retrieve_wiki_knowledge(query, limit)

@mcp.tool(name="commit_new_knowledge")
def commit_new_knowledge(
    title: str, 
    description: str, 
    tags: List[str], 
    content: str, 
    topic_name: Optional[str] = None, 
    topic_update_text: Optional[str] = None
) -> str:
    """
    새롭게 습득하거나 정리된 지식을 로컬 QA 저널(qa/)에 파일로 기록하고,
    선택적으로 기존 공통 개념 토픽(topics/) 문서에 누적 합성합니다.
    """
    return commit_wiki_knowledge(
        title=title,
        description=description,
        tags=tags,
        content=content,
        topic_name=topic_name,
        topic_update_text=topic_update_text
    )

@mcp.tool(name="run_database_indexing")
def run_database_indexing() -> str:
    """
    로컬 마크다운 파일들의 변경 사항을 감지하여 
    데이터베이스(pgvector/SQLite)에 실시간으로 증분 인덱싱(임베딩)합니다.
    """
    return run_wiki_indexing()

# -----------------------------------------------------------------------------
# Pure ASGI Middleware + SSE App (BaseHTTPMiddleware 사용하지 않음)
# -----------------------------------------------------------------------------
import json
import httpx
from starlette.applications import Starlette
from starlette.routing import Mount
from src.core.config import current_user_config

# 중앙 인증 서버 검증 URL (환경 변수로 관리)
AUTH_SERVER_URL = os.getenv("AUTH_SERVER_URL")

# FastMCP의 내부 Starlette Streamable HTTP App 획득
mcp_http_app = mcp.streamable_http_app()


async def _validate_api_key(token: str) -> dict:
    """중앙 인증 서버(Auth Server)에 API Key의 유효성을 실시간으로 확인합니다."""
    if not AUTH_SERVER_URL:
        return {"valid": True, "api_key": token}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{AUTH_SERVER_URL}/api/auth/validate-key",
                json={"api_key": token}
            )
            if response.status_code != 200:
                return None
            result = response.json()
            if not result.get("valid"):
                return None
            return result
    except Exception:
        return None


def _extract_user_config(headers: dict) -> dict:
    """HTTP 헤더에서 사용자별 설정 정보를 추출합니다."""
    auth_header = headers.get("authorization", "")
    api_token = ""
    if auth_header.startswith("Bearer "):
        api_token = auth_header.split(" ", 1)[1]

    return {
        "api_key": api_token,
        "openai_api_key": headers.get("x-openai-api-key"),
        "storage": {
            "storage_type": headers.get("x-storage-type", "local"),
            "s3_endpoint_url": headers.get("x-s3-endpoint-url"),
            "s3_access_key_id": headers.get("x-s3-access-key-id"),
            "s3_secret_access_key": headers.get("x-s3-secret-access-key"),
            "s3_bucket_name": headers.get("x-s3-bucket-name"),
        }
    }


async def _send_json_error(send, status: int, detail: str):
    """ASGI send를 통해 JSON 에러 응답을 직접 전송합니다."""
    body = json.dumps({"detail": detail}).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            [b"content-type", b"application/json"],
            [b"content-length", str(len(body)).encode()],
        ],
    })
    await send({
        "type": "http.response.body",
        "body": body,
    })


class MCPAuthMiddleware:
    """
    순수 ASGI 미들웨어 — BaseHTTPMiddleware를 사용하지 않으므로
    SSE StreamingResponse가 버퍼링되거나 파괴되지 않습니다.

    역할:
    1. Authorization 헤더 검증 (인증 서버 위임)
    2. 사용자별 설정을 ContextVar에 주입
    3. SSL Offloading 시 scheme을 https로 강제 전환
    4. 사용자별 설정을 ContextVar에 주입하여 멀티테넌트 지원
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            # WebSocket 등 비-HTTP 요청은 그냥 통과
            await self.app(scope, receive, send)
            return

        # 헤더를 딕셔너리로 변환 (소문자 키)
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }

        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()

        # ─── 인증 검증 ───
        if AUTH_SERVER_URL and path in ("/mcp",):
            auth_header = headers.get("authorization", "")
            if not auth_header.startswith("Bearer "):
                await _send_json_error(send, 401, "Missing or invalid Authorization header")
                return

            token = auth_header.split(" ", 1)[1]
            result = await _validate_api_key(token)
            if result is None:
                await _send_json_error(send, 401, "Unauthorized or invalid API Key")
                return


        # ─── SSL Offloading 환경 ───
        if AUTH_SERVER_URL:
            scope["scheme"] = "https"

        # ─── 사용자 설정 ContextVar 주입 ───
        user_config = _extract_user_config(headers)
        token_val = current_user_config.set(user_config)
        try:
            await self.app(scope, receive, send)
        finally:
            current_user_config.reset(token_val)


# Streamable HTTP App에 순수 ASGI 미들웨어를 감싸서 최종 app 생성
app = MCPAuthMiddleware(mcp_http_app)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
