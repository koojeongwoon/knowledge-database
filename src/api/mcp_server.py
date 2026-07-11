import os
import sys
from typing import List, Optional

import boto3
from botocore.config import Config
from fastapi import FastAPI, Request, Depends, HTTPException, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

# 프로젝트 루트 디렉토리를 Python Path에 추가하여 절대 import 호환성 확보
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from mcp.server.fastmcp import FastMCP
from src.api.agent_tool import retrieve_wiki_knowledge, commit_wiki_knowledge, run_wiki_indexing

# FastMCP 서버 이름: LLM-Wiki
mcp = FastMCP("LLM-Wiki")

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
# FastAPI Wrapper & Auth Server Integration (Stateless Multi-tenant Architecture)
# -----------------------------------------------------------------------------
import httpx

app = FastAPI(title="LLM-Wiki MCP SSE Server")

# 중앙 인증 서버 검증 URL (환경 변수로 관리)
AUTH_SERVER_URL = os.getenv("AUTH_SERVER_URL")

async def validate_api_key_against_auth_server(token: str) -> dict:
    """중앙 인증 서버(Auth Server)에 API Key의 유효성을 실시간으로 확인합니다."""
    if not AUTH_SERVER_URL:
        # 인증 서버 주소가 지정되지 않은 로컬 단독 가동 시에는 검증을 스킵하고 기본 로컬 설정을 쓰도록 처리
        return {"valid": True, "api_key": token}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{AUTH_SERVER_URL}/api/auth/validate-key",
                json={"api_key": token}
            )
            
            if response.status_code != 200:
                raise ValueError("Invalid API Key response")
                
            result = response.json()
            if not result.get("valid"):
                raise ValueError("Unauthorized or deactivated API Key")
                
            return result
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Authentication service temporarily unavailable: {exc}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=401,
            detail=f"Authentication failed: {str(e)}"
        )

async def verify_mcp_access(authorization: Optional[str] = Header(None)):
    """
    HTTP Authorization 헤더를 추출하여 중앙 인증 서버의 API Key 검증을 위임합니다.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization Header")
    
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization format. Use 'Bearer <token>'")
        
    token = authorization.split(" ")[1]
    return await validate_api_key_against_auth_server(token)


# FastMCP의 내부 Starlette SSE App 획득
mcp_sse_app = mcp.sse_app()

from src.core.config import current_user_config

@app.get("/sse")
async def handle_sse(request: Request, user = Depends(verify_mcp_access)):
    """SseServerTransport 연결 수립 엔드포인트"""
    auth_header = request.headers.get("Authorization", "")
    api_token = ""
    if auth_header and auth_header.startswith("Bearer "):
        api_token = auth_header.split(" ")[1]
        
    # 헤더에서 직접 실어 보낸 개인 키 및 스토리지 정보 획득
    openai_key = request.headers.get("X-OpenAI-API-Key")
    storage_type = request.headers.get("X-Storage-Type", "local")
    s3_endpoint = request.headers.get("X-S3-Endpoint-URL")
    s3_access = request.headers.get("X-S3-Access-Key-ID")
    s3_secret = request.headers.get("X-S3-Secret-Access-Key")
    s3_bucket = request.headers.get("X-S3-Bucket-Name")
    
    user_config = {
        "api_key": api_token,
        "openai_api_key": openai_key,
        "storage": {
            "storage_type": storage_type,
            "s3_endpoint_url": s3_endpoint,
            "s3_access_key_id": s3_access,
            "s3_secret_access_key": s3_secret,
            "s3_bucket_name": s3_bucket
        }
    }
    
    token_val = current_user_config.set(user_config)
    try:
        await mcp_sse_app(request.scope, request.receive, request._send)
    finally:
        current_user_config.reset(token_val)

@app.post("/messages")
async def handle_messages(request: Request, user = Depends(verify_mcp_access)):
    """MCP 프로토콜 메시지 전송 엔드포인트"""
    auth_header = request.headers.get("Authorization", "")
    api_token = ""
    if auth_header and auth_header.startswith("Bearer "):
        api_token = auth_header.split(" ")[1]
        
    # 동일하게 헤더에서 개인 설정 정보 획득
    openai_key = request.headers.get("X-OpenAI-API-Key")
    storage_type = request.headers.get("X-Storage-Type", "local")
    s3_endpoint = request.headers.get("X-S3-Endpoint-URL")
    s3_access = request.headers.get("X-S3-Access-Key-ID")
    s3_secret = request.headers.get("X-S3-Secret-Access-Key")
    s3_bucket = request.headers.get("X-S3-Bucket-Name")
    
    user_config = {
        "api_key": api_token,
        "openai_api_key": openai_key,
        "storage": {
            "storage_type": storage_type,
            "s3_endpoint_url": s3_endpoint,
            "s3_access_key_id": s3_access,
            "s3_secret_access_key": s3_secret,
            "s3_bucket_name": s3_bucket
        }
    }
    
    token_val = current_user_config.set(user_config)
    try:
        await mcp_sse_app(request.scope, request.receive, request._send)
    finally:
        current_user_config.reset(token_val)

if __name__ == "__main__":
    import uvicorn
    # 기본 포트 8000번으로 원격 수신 대기 시작
    uvicorn.run(app, host="0.0.0.0", port=8000)
