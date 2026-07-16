import os
import sys
from typing import List, Optional, cast


# 프로젝트 루트 디렉토리를 Python Path에 추가하여 절대 import 호환성 확보
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from mcp.server.fastmcp import FastMCP
from src.api.agent_tool import (
    retrieve_wiki_knowledge,
    commit_wiki_knowledge,
    run_wiki_indexing,
    submit_wiki_search_feedback,
)
from src.api.middleware import MCPAuthMiddleware
from src.core.database.factory import DatabaseManager
from src.core.database.migrations import run_database_migrations
from src.settings.web import SettingsPathDispatcher, settings_app

database_manager = DatabaseManager()
try:
    run_database_migrations(database_manager)
finally:
    database_manager.close()

# FastMCP 서버 이름: LLM-Wiki
mcp = FastMCP(
    "LLM-Wiki",
    host="0.0.0.0",
    instructions="""
    [지식베이스(LLM-Wiki) 사용 지침]
    1. 사용자가 개인적인 메모, 업무, 기획, 아이디어, 과거 대화 이력, 혹은 기술적 질문 등
       개인 지식베이스 내의 정보 조회가 필요한 모든 질문을 하면,
       답변하기 전에 반드시 'search_wiki_knowledge' 도구를 실행하여 관련 정보를 먼저 검색하십시오.
    2. 새로운 지식, 노하우, 업무 규칙 등이 도출되거나 사용자가 지식 기록을 원하면 'commit_new_knowledge'를 사용하십시오.
    3. 'commit_new_knowledge'는 작성한 파일을 자동 인덱싱합니다. 자동 인덱싱 실패 재시도나 외부에서 직접 수정한 파일에만
       'run_database_indexing'을 호출하고, 대상 파일 경로만 전달하십시오.
    """
)

@mcp.tool(name="search_wiki_knowledge")
def search_wiki_knowledge(query: str, limit: int = 5) -> str:
    """
    개인 지식베이스(옵시디언 위키)에서 자연어 검색을 수행하고 
    관련성이 높은 마크다운 지식 조각 및 인용 정보를 반환합니다.
    """
    return retrieve_wiki_knowledge(query, limit)


@mcp.tool(name="submit_search_feedback")
def submit_search_feedback(
    search_id: str,
    relevant_paths: Optional[List[str]] = None,
    irrelevant_paths: Optional[List[str]] = None,
    expected_no_answer: bool = False,
    missing_answer_path: Optional[str] = None,
    notes: Optional[str] = None,
) -> str:
    """검색 결과에 대해 사용자가 직접 판정한 정답/오답/no-answer 라벨을 저장합니다."""
    return cast(str, submit_wiki_search_feedback(
        search_id=search_id,
        relevant_paths=relevant_paths or [],
        irrelevant_paths=irrelevant_paths or [],
        expected_no_answer=expected_no_answer,
        missing_answer_path=missing_answer_path,
        notes=notes,
    ))

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
    선택적으로 기존 공통 개념 토픽(topics/) 문서에 누적 합성한 뒤
    실제로 작성한 마크다운 파일만 자동 인덱싱합니다.
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
def run_database_indexing(file_paths: List[str]) -> str:
    """
    지정한 마크다운 파일들의 변경 사항만 감지하여
    데이터베이스에 실시간으로 증분 인덱싱(임베딩)합니다.

    자동 인덱싱 실패 시 반환된 retry_targets 또는 외부에서 직접 수정한
    마크다운 파일 경로만 file_paths로 전달합니다.
    """
    return run_wiki_indexing(file_paths=file_paths)

# FastMCP의 내부 Starlette Streamable HTTP App 획득
mcp_http_app = mcp.streamable_http_app()

# Streamable HTTP App에 순수 ASGI 미들웨어를 감싸서 최종 app 생성
mcp_app = MCPAuthMiddleware(mcp_http_app)
app = SettingsPathDispatcher(settings_app, mcp_app)

# Redis Stream 기반의 회원가입 비동기 이벤트 컨슈머 데몬 시작
try:
    from src.core.event.consumer import UserSignupEventConsumer
    event_consumer = UserSignupEventConsumer()
    event_consumer.start()
except Exception as e:
    import logging
    logging.getLogger("mcp_server").error(f"Failed to start UserSignupEventConsumer: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
