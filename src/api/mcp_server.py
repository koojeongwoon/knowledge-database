import os
import sys
from typing import Any, Dict, List, Optional, cast


# 프로젝트 루트 디렉토리를 Python Path에 추가하여 절대 import 호환성 확보
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from mcp.server.fastmcp import FastMCP
from src.api.agent_tool import (
    retrieve_wiki_knowledge,
    commit_wiki_knowledge,
    run_wiki_indexing,
    submit_wiki_search_feedback,
    create_wiki_inbox_markdown,
    list_wiki_inbox_items,
    read_wiki_inbox_item,
    prepare_wiki_learning_session,
    plan_wiki_learning_feedback,
    start_wiki_learning_session,
    record_wiki_learning_attempt,
    resume_wiki_learning_session,
    complete_wiki_learning_session,
    list_wiki_due_learning_reviews,
    record_wiki_learning_review,
    prepare_wiki_learning_knowledge_candidates,
    stage_wiki_learning_knowledge_candidates,
    review_wiki_learning_knowledge_candidate,
    commit_wiki_learning_knowledge_candidate,
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
    3. 'commit_new_knowledge'는 파일을 저장하고 비동기 인덱싱 작업을 등록합니다. 외부에서 직접 수정한 파일을
       즉시 반영해야 할 때만 'run_database_indexing'을 호출하고, 대상 파일 경로만 전달하십시오.
    4. 사용자가 파일, 이미지, 링크의 내용을 Inbox에 저장해 달라고 명시하면 실제 원문을 읽은 뒤 Markdown으로 구조화하고
       사용자 확인 후 'create_inbox_markdown'을 호출하십시오. 링크는 URL만 등록하지 말고 source_kind='external_link'와
       original_url을 포함한 Markdown 파생본으로 저장하십시오. 원문을 읽지 못했다면 저장 성공을 주장하지 마십시오.
    5. 사용자가 별도 범위를 지정하지 않고 학습하자고 하면 scope='combined'로 'prepare_learning_session'을 호출하십시오.
       반환된 first_question 하나만 먼저 제시하고, 사용자가 답하기 전에는 근거와 정답 내용을 노출하지 마십시오.
    6. 사용자의 답변 의미 평가는 현재 대화의 클라이언트 LLM이 근거에 기반해 수행합니다. 판정 뒤
       'plan_learning_feedback'을 호출해 힌트, 재답변, 다음 문제와 복습 우선순위를 정규화하십시오.
    7. 사용자가 학습을 이어서 하거나 기록하기를 원하면 준비된 학습 팩으로 'start_learning_session'을 호출하십시오.
       각 답변과 클라이언트 판정은 'record_learning_attempt'에 저장하고, 다른 대화에서는
       'resume_learning_session'으로 이어가며 종료 시 'complete_learning_session'을 호출하십시오.
    8. 사용자가 복습하자고 하면 'list_due_learning_reviews'로 기한이 된 항목을 조회해 한 문제씩 제시하십시오.
       답변은 현재 대화의 클라이언트 LLM이 판정하고 'plan_learning_feedback'으로 정규화한 뒤
       'record_learning_review'에 저장해 다음 복습일을 조정하십시오.
    9. 학습 결과를 Knowledge로 만들 때는 'prepare_learning_knowledge_candidates'의 기록을 바탕으로 후보 초안을 만들고
       'stage_learning_knowledge_candidates'에 저장하십시오. 후보를 사용자에게 하나씩 보여준 뒤 명시적인 선택에만
       'review_learning_knowledge_candidate'를 호출하십시오. approved=true가 된 후보만
       'commit_learning_knowledge_candidate'로 저장할 수 있으며 conflict나 correction을 기존 문서에 자동 덮어쓰지 마십시오.
    """
)

@mcp.tool(name="search_wiki_knowledge")
def search_wiki_knowledge(query: str, limit: int = 5) -> str:
    """
    개인 지식베이스(옵시디언 위키)에서 자연어 검색을 수행하고 
    관련성이 높은 마크다운 지식 조각 및 인용 정보를 반환합니다.
    """
    return retrieve_wiki_knowledge(query, limit)


@mcp.tool(name="create_inbox_markdown")
def create_inbox_markdown(
    title: str,
    content: str,
    source_kind: str = "user_text",
    original_filename: Optional[str] = None,
    original_url: Optional[str] = None,
    media_type: Optional[str] = None,
    extraction_complete: bool = True,
    warnings: Optional[List[str]] = None,
    note: Optional[str] = None,
) -> str:
    """사용자가 승인한 파일·이미지·링크 해석 결과를 미검증 Markdown 자료로 Inbox에 저장합니다."""
    return cast(str, create_wiki_inbox_markdown(
        title=title,
        content=content,
        source_kind=source_kind,
        original_filename=original_filename,
        original_url=original_url,
        media_type=media_type,
        extraction_complete=extraction_complete,
        warnings=warnings,
        note=note,
    ))


@mcp.tool(name="list_inbox_items")
def list_inbox_items(limit: int = 50) -> str:
    """인증된 사용자의 Inbox 항목을 최신순으로 조회합니다."""
    return cast(str, list_wiki_inbox_items(limit=limit))


@mcp.tool(name="read_inbox_item")
def read_inbox_item(item_id: str) -> str:
    """Inbox 항목의 메타데이터와 학습 가능한 텍스트를 조회합니다."""
    return cast(str, read_wiki_inbox_item(item_id=item_id))


@mcp.tool(name="prepare_learning_session")
def prepare_learning_session(
    topic: str,
    scope: str = "combined",
    goal: str = "understand",
    level: str = "practical",
    duration_minutes: int = 20,
    inbox_item_ids: Optional[List[str]] = None,
    knowledge_limit: int = 5,
) -> str:
    """Inbox와 Knowledge를 결합한 stateless 학습 팩과 첫 진단 질문을 준비합니다."""
    return cast(str, prepare_wiki_learning_session(
        topic=topic,
        scope=scope,
        goal=goal,
        level=level,
        duration_minutes=duration_minutes,
        inbox_item_ids=inbox_item_ids,
        knowledge_limit=knowledge_limit,
    ))


@mcp.tool(name="plan_learning_feedback")
def plan_learning_feedback(
    assessment: str,
    confidence: str = "medium",
    missing_concepts: Optional[List[str]] = None,
    misconceptions: Optional[List[str]] = None,
    evidence_refs: Optional[List[str]] = None,
    hint: Optional[str] = None,
    next_question: Optional[str] = None,
) -> str:
    """클라이언트 LLM의 학습 판정을 검증하고 피드백·재질문·복습 계획으로 정규화합니다."""
    return cast(str, plan_wiki_learning_feedback(
        assessment=assessment,
        confidence=confidence,
        missing_concepts=missing_concepts,
        misconceptions=misconceptions,
        evidence_refs=evidence_refs,
        hint=hint,
        next_question=next_question,
    ))


@mcp.tool(name="start_learning_session")
def start_learning_session(
    topic: str,
    requested_scope: str,
    effective_scope: str,
    goal: str,
    level: str,
    duration_minutes: int,
    first_question: str,
    sources: Optional[List[Dict[str, Any]]] = None,
    client_request_id: Optional[str] = None,
) -> str:
    """준비된 학습 팩의 설정·첫 질문·출처 참조를 영속 세션으로 시작합니다."""
    return cast(str, start_wiki_learning_session(
        topic=topic, requested_scope=requested_scope, effective_scope=effective_scope,
        goal=goal, level=level, duration_minutes=duration_minutes,
        first_question=first_question, sources=sources, client_request_id=client_request_id,
    ))


@mcp.tool(name="record_learning_attempt")
def record_learning_attempt(
    session_id: str,
    question_id: str,
    answer: str,
    assessment: str,
    confidence: str,
    feedback_plan: Dict[str, Any],
    missing_concepts: Optional[List[str]] = None,
    misconceptions: Optional[List[str]] = None,
    evidence_refs: Optional[List[str]] = None,
    next_question: Optional[str] = None,
    next_question_type: str = "retrieval",
    next_evidence_refs: Optional[List[str]] = None,
    client_request_id: Optional[str] = None,
) -> str:
    """사용자 답변과 클라이언트 LLM 판정, 피드백 계획 및 선택적 다음 질문을 저장합니다."""
    return cast(str, record_wiki_learning_attempt(
        session_id=session_id, question_id=question_id, answer=answer,
        assessment=assessment, confidence=confidence, feedback_plan=feedback_plan,
        missing_concepts=missing_concepts, misconceptions=misconceptions,
        evidence_refs=evidence_refs, next_question=next_question,
        next_question_type=next_question_type, next_evidence_refs=next_evidence_refs,
        client_request_id=client_request_id,
    ))


@mcp.tool(name="resume_learning_session")
def resume_learning_session(session_id: Optional[str] = None) -> str:
    """지정한 세션 또는 가장 최근의 활성 학습 세션을 출처·질문·최근 답변과 함께 불러옵니다."""
    return cast(str, resume_wiki_learning_session(session_id=session_id))


@mcp.tool(name="complete_learning_session")
def complete_learning_session(session_id: str, summary: Optional[str] = None) -> str:
    """활성 학습 세션을 완료 처리하고 저장된 질문·답변 수를 반환합니다."""
    return cast(str, complete_wiki_learning_session(session_id=session_id, summary=summary))


@mcp.tool(name="list_due_learning_reviews")
def list_due_learning_reviews(limit: int = 20) -> str:
    """현재 시각까지 복습 기한이 된 항목을 우선순위와 기한 순으로 조회합니다."""
    return cast(str, list_wiki_due_learning_reviews(limit=limit))


@mcp.tool(name="record_learning_review")
def record_learning_review(
    review_id: str,
    answer: str,
    assessment: str,
    confidence: str,
    feedback_plan: Dict[str, Any],
    client_request_id: Optional[str] = None,
) -> str:
    """복습 답변과 클라이언트 판정을 기록하고 다음 복습 간격과 예정일을 조정합니다."""
    return cast(str, record_wiki_learning_review(
        review_id=review_id, answer=answer, assessment=assessment,
        confidence=confidence, feedback_plan=feedback_plan,
        client_request_id=client_request_id,
    ))


@mcp.tool(name="prepare_learning_knowledge_candidates")
def prepare_learning_knowledge_candidates(session_id: str) -> str:
    """학습 세션의 출처·답변·판정을 재지식화 후보 초안용 stateless 팩으로 반환합니다."""
    return cast(str, prepare_wiki_learning_knowledge_candidates(session_id=session_id))


@mcp.tool(name="stage_learning_knowledge_candidates")
def stage_learning_knowledge_candidates(
    session_id: str, candidates: List[Dict[str, Any]],
) -> str:
    """클라이언트 LLM이 만든 재지식화 초안을 사용자 승인 전 pending 후보로 저장합니다."""
    return cast(str, stage_wiki_learning_knowledge_candidates(
        session_id=session_id, candidates=candidates,
    ))


@mcp.tool(name="review_learning_knowledge_candidate")
def review_learning_knowledge_candidate(
    candidate_id: str, approved: bool, note: Optional[str] = None,
) -> str:
    """사용자의 개별 선택에 따라 pending 후보 하나를 승인하거나 거절합니다."""
    return cast(str, review_wiki_learning_knowledge_candidate(
        candidate_id=candidate_id, approved=approved, note=note,
    ))


@mcp.tool(name="commit_learning_knowledge_candidate")
def commit_learning_knowledge_candidate(candidate_id: str) -> str:
    """이미 승인된 후보 하나를 기존 Knowledge 커밋 경로로 저장하고 인덱싱 큐에 등록합니다."""
    return cast(str, commit_wiki_learning_knowledge_candidate(candidate_id=candidate_id))


@mcp.tool(name="submit_search_feedback")
def submit_search_feedback(
    search_id: str,
    relevant_paths: Optional[List[str]] = None,
    irrelevant_paths: Optional[List[str]] = None,
    expected_no_answer: bool = False,
    missing_answer_path: Optional[str] = None,
    notes: Optional[str] = None,
    partially_relevant_paths: Optional[List[str]] = None,
    satisfaction: Optional[str] = None,
    failure_reasons: Optional[List[str]] = None,
    result_feedback: Optional[List[Dict[str, Any]]] = None,
    expected_relations: Optional[List[Dict[str, str]]] = None,
    expected_graph_paths: Optional[List[List[str]]] = None,
    forbidden_paths: Optional[List[str]] = None,
    expected_rule_types: Optional[List[str]] = None,
    ontology_notes: Optional[str] = None,
) -> str:
    """검색 결과에 대해 사용자가 직접 판정한 정답/오답/no-answer 라벨을 저장합니다."""
    return cast(str, submit_wiki_search_feedback(
        search_id=search_id,
        relevant_paths=relevant_paths or [],
        irrelevant_paths=irrelevant_paths or [],
        expected_no_answer=expected_no_answer,
        missing_answer_path=missing_answer_path,
        notes=notes,
        partially_relevant_paths=partially_relevant_paths or [],
        satisfaction=satisfaction,
        failure_reasons=failure_reasons or [],
        result_feedback=result_feedback or [],
        expected_relations=expected_relations or [],
        expected_graph_paths=expected_graph_paths or [],
        forbidden_paths=forbidden_paths or [],
        expected_rule_types=expected_rule_types or [],
        ontology_notes=ontology_notes,
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
    실제로 작성한 마크다운 파일만 비동기 인덱싱 큐에 등록합니다.
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

    외부에서 직접 수정한 마크다운 파일을 즉시 반영해야 할 때
    해당 파일 경로만 file_paths로 전달합니다.
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
