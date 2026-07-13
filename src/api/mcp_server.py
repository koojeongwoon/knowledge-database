import os
import sys
from typing import List, Optional

# 프로젝트 루트 디렉토리를 Python Path에 추가하여 절대 import 호환성 확보
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from mcp.server.fastmcp import FastMCP
from src.api.agent_tool import retrieve_wiki_knowledge, commit_wiki_knowledge, run_wiki_indexing
from src.api.middleware import MCPAuthMiddleware

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
    topic_update_text: Optional[str] = None,
    visibility: Optional[str] = "private"
) -> str:
    """
    새롭게 습득하거나 정리된 지식을 로컬 QA 저널(qa/)에 파일로 기록하고,
    선택적으로 기존 공통 개념 토픽(topics/) 문서에 누적 합성합니다.
    """
    return cast(
        str,
        commit_wiki_knowledge(
            title=title,
            description=description,
            tags=tags,
            content=content,
            topic_name=topic_name,
            topic_update_text=topic_update_text,
            visibility=visibility
        )
    )

@mcp.tool(name="run_database_indexing")
def run_database_indexing() -> str:
    """
    로컬 마크다운 파일들의 변경 사항을 감지하여 
    데이터베이스(pgvector/SQLite)에 실시간으로 증분 인덱싱(임베딩)합니다.
    """
    return run_wiki_indexing()

# FastMCP의 내부 Starlette Streamable HTTP App 획득
mcp_http_app = mcp.streamable_http_app()

# Streamable HTTP App에 순수 ASGI 미들웨어를 감싸서 최종 app 생성
app = MCPAuthMiddleware(mcp_http_app)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
