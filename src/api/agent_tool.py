import json
from typing import List, Dict, Any, Optional

from src.api.decorators import tool_wrapper, with_fresh_user_settings
from src.api.exceptions import (
    WikiBaseException,
    InvalidArgumentException,
    DatabaseException,
)
from src.api.handlers.learning import LearningCandidateCommitHandler, LearningSessionApiHandler
from src.api.handlers.knowledge import KnowledgeCommitApiHandler
from src.api.handlers.indexing import IndexingRetryApiHandler, IndexingRunApiHandler
from src.api.handlers.retrieval import RetrievalApiHandler
from src.api.handlers.support import BaselineApiHandler, InboxApiHandler, SearchFeedbackApiHandler
from src.core.config import EMBEDDING_PROVIDER, EMBEDDING_DIM
from src.core.config import current_user_config
from src.core.database.factory import DatabaseManager
from src.core.logging.audit import log_audit  # 감사 로거 유틸 임포트
from src.indexing.composition import create_wiki_indexer
from src.indexing.domain.embedding import FakeEmbeddingService, OpenAIEmbeddingService, BGEM3EmbeddingService
from src.retrieval.composition import create_wiki_searcher
from src.retrieval.domain.formatter import format_retrieved_documents
from src.retrieval.feedback import SearchFeedbackService
from src.wiki.composition import create_knowledge_commit_runtime
from src.settings.inbox import InboxService
from src.learning.application.service import LearningPreparationService
from src.learning.application.session_service import LearningSessionService
from src.learning.composition import create_learning_session_service
from src.learning.domain.feedback import LearningFeedbackPlanner
from src.baselines.search import BaselineSearcher
from src.baselines.composition import create_baseline_service


@tool_wrapper
@with_fresh_user_settings
def retrieve_wiki_knowledge(query: str, limit: int = 5) -> str:
    """
    개인 지식베이스에서 자연어 검색을 수행하고 관련성이 높은 마크다운 지식 조각들을 반환합니다.
    """
    if not query:
        raise InvalidArgumentException("검색 쿼리(query)는 비어 있을 수 없습니다.")

    # 사용자 식별용 Config 추출
    user_config = current_user_config.get() or {}
    user_id = user_config.get("api_key", "SYSTEM")
    owner_id = user_config.get("user_id", "SYSTEM")

    return RetrievalApiHandler(
        database_factory=DatabaseManager,
        embedding_factory=_embedding_service,
        searcher_factory=create_wiki_searcher,
        feedback_factory=SearchFeedbackService,
        formatter=format_retrieved_documents,
        audit=log_audit,
    ).search(query, limit, user_id, owner_id)


def _embedding_service():
    if EMBEDDING_PROVIDER == "openai":
        return OpenAIEmbeddingService(dimension=EMBEDDING_DIM)
    if EMBEDDING_PROVIDER == "bge-m3":
        return BGEM3EmbeddingService()
    return FakeEmbeddingService(dimension=EMBEDDING_DIM)


def _baseline_context():
    config = current_user_config.get() or {}
    owner_id = config.get("user_id", "SYSTEM")
    if not owner_id or owner_id == "SYSTEM":
        raise InvalidArgumentException("기준본은 인증된 사용자만 사용할 수 있습니다.")
    db_manager = DatabaseManager()
    return owner_id, db_manager, create_baseline_service(owner_id, db_manager)


def _baseline_handler() -> BaselineApiHandler:
    return BaselineApiHandler(_baseline_context, BaselineSearcher, _embedding_service,
                              format_retrieved_documents, log_audit)


def _authenticated_owner(message: str) -> str:
    owner_id = (current_user_config.get() or {}).get("user_id", "SYSTEM")
    if not owner_id or owner_id == "SYSTEM":
        raise InvalidArgumentException(message)
    return owner_id


@tool_wrapper
@with_fresh_user_settings
def prepare_wiki_knowledge_baseline(
    name: str, version: str, purpose: str, source_paths: List[str],
    base_release_id: Optional[str] = None,
) -> Dict[str, Any]:
    return _baseline_handler().prepare(name=name, version=version, purpose=purpose,
                                       source_paths=source_paths, base_release_id=base_release_id)


@tool_wrapper
@with_fresh_user_settings
def confirm_wiki_knowledge_baseline(draft_id: str) -> Dict[str, Any]:
    return _baseline_handler().confirm(draft_id)


@tool_wrapper
@with_fresh_user_settings
def search_wiki_knowledge_baseline(query: str, release_id: str, limit: int = 5) -> str:
    if not query.strip():
        raise InvalidArgumentException("검색 쿼리(query)는 비어 있을 수 없습니다.")
    if limit < 1 or limit > 20:
        raise InvalidArgumentException("limit은 1 이상 20 이하여야 합니다.")
    owner_id = _authenticated_owner("기준본은 인증된 사용자만 사용할 수 있습니다.")
    return _baseline_handler().search(owner_id, DatabaseManager(), query, release_id, limit)


@tool_wrapper
@with_fresh_user_settings
def create_wiki_inbox_markdown(
    title: str,
    content: str,
    source_kind: str = "user_text",
    original_filename: Optional[str] = None,
    original_url: Optional[str] = None,
    media_type: Optional[str] = None,
    extraction_complete: bool = True,
    warnings: Optional[List[str]] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    owner_id = _authenticated_owner("Inbox를 사용하려면 인증된 사용자가 필요합니다.")
    return InboxApiHandler(InboxService, log_audit).create(owner_id,
            title=title,
            content=content,
            source_kind=source_kind,
            original_filename=original_filename,
            original_url=original_url,
            media_type=media_type,
            extraction_complete=extraction_complete,
            warnings=warnings,
            note=note,
        )


@tool_wrapper
@with_fresh_user_settings
def list_wiki_inbox_items(limit: int = 50) -> Dict[str, Any]:
    if limit < 1 or limit > 100:
        raise InvalidArgumentException("limit은 1 이상 100 이하여야 합니다.")
    owner_id = _authenticated_owner("Inbox를 사용하려면 인증된 사용자가 필요합니다.")
    return InboxApiHandler(InboxService, log_audit).list(owner_id, limit)


@tool_wrapper
@with_fresh_user_settings
def read_wiki_inbox_item(item_id: str) -> Dict[str, Any]:
    owner_id = _authenticated_owner("Inbox를 사용하려면 인증된 사용자가 필요합니다.")
    return InboxApiHandler(InboxService, log_audit).read(owner_id, item_id)


@tool_wrapper
@with_fresh_user_settings
def prepare_wiki_learning_session(
    topic: str,
    scope: str = "combined",
    goal: str = "understand",
    level: str = "practical",
    duration_minutes: int = 20,
    inbox_item_ids: Optional[List[str]] = None,
    knowledge_limit: int = 5,
) -> Dict[str, Any]:
    config = current_user_config.get() or {}
    owner_id = config.get("user_id", "SYSTEM")
    if not owner_id or owner_id == "SYSTEM":
        raise InvalidArgumentException("학습 모드를 사용하려면 인증된 사용자가 필요합니다.")

    def knowledge_search(query: str, limit: int) -> str:
        response = json.loads(retrieve_wiki_knowledge(query, limit))
        if not response.get("success"):
            raise WikiBaseException(response.get("message") or "Knowledge 검색에 실패했습니다.")
        return str(response.get("data") or "")

    try:
        return LearningPreparationService(
            inbox_service=InboxService(owner_id),
            knowledge_search=knowledge_search,
        ).prepare(
            topic=topic,
            scope=scope,
            goal=goal,
            level=level,
            duration_minutes=duration_minutes,
            inbox_item_ids=inbox_item_ids,
            knowledge_limit=knowledge_limit,
        )
    except ValueError as exc:
        raise InvalidArgumentException(str(exc)) from exc


@tool_wrapper
def plan_wiki_learning_feedback(
    assessment: str,
    confidence: str = "medium",
    missing_concepts: Optional[List[str]] = None,
    misconceptions: Optional[List[str]] = None,
    evidence_refs: Optional[List[str]] = None,
    hint: Optional[str] = None,
    next_question: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        return LearningFeedbackPlanner().plan(
            assessment=assessment,
            confidence=confidence,
            missing_concepts=missing_concepts,
            misconceptions=misconceptions,
            evidence_refs=evidence_refs,
            hint=hint,
            next_question=next_question,
        )
    except ValueError as exc:
        raise InvalidArgumentException(str(exc)) from exc


def _learning_session_service() -> LearningSessionService:
    return create_learning_session_service()


def _learning_api_handler() -> LearningSessionApiHandler:
    return LearningSessionApiHandler(service_factory=_learning_session_service)


def _authenticated_learning_owner() -> str:
    owner_id = (current_user_config.get() or {}).get("user_id", "SYSTEM")
    if not owner_id or owner_id == "SYSTEM":
        raise InvalidArgumentException("학습 세션을 사용하려면 인증된 사용자가 필요합니다.")
    return owner_id


@tool_wrapper
@with_fresh_user_settings
def start_wiki_learning_session(
    topic: str,
    requested_scope: str,
    effective_scope: str,
    goal: str,
    level: str,
    duration_minutes: int,
    first_question: str,
    sources: Optional[List[Dict[str, Any]]] = None,
    client_request_id: Optional[str] = None,
) -> Dict[str, Any]:
    return _learning_api_handler().start(
        _authenticated_learning_owner(), topic, requested_scope, effective_scope,
        goal, level, duration_minutes, first_question, sources, client_request_id,
    )


@tool_wrapper
@with_fresh_user_settings
def record_wiki_learning_attempt(
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
) -> Dict[str, Any]:
    return _learning_api_handler().record_attempt(
        _authenticated_learning_owner(), session_id, question_id, answer,
        assessment, confidence, feedback_plan, missing_concepts, misconceptions,
        evidence_refs, next_question, next_question_type, next_evidence_refs, client_request_id,
    )


@tool_wrapper
@with_fresh_user_settings
def resume_wiki_learning_session(session_id: Optional[str] = None) -> Dict[str, Any]:
    return _learning_api_handler().resume(_authenticated_learning_owner(), session_id)


@tool_wrapper
@with_fresh_user_settings
def complete_wiki_learning_session(session_id: str, summary: Optional[str] = None) -> Dict[str, Any]:
    return _learning_api_handler().complete(_authenticated_learning_owner(), session_id, summary)


@tool_wrapper
@with_fresh_user_settings
def list_wiki_due_learning_reviews(limit: int = 20) -> Dict[str, Any]:
    return _learning_api_handler().list_due_reviews(_authenticated_learning_owner(), limit)


@tool_wrapper
@with_fresh_user_settings
def record_wiki_learning_review(
    review_id: str,
    answer: str,
    assessment: str,
    confidence: str,
    feedback_plan: Dict[str, Any],
    client_request_id: Optional[str] = None,
) -> Dict[str, Any]:
    return _learning_api_handler().record_review(
        _authenticated_learning_owner(), review_id, answer, assessment,
        confidence, feedback_plan, client_request_id,
    )


@tool_wrapper
@with_fresh_user_settings
def prepare_wiki_learning_knowledge_candidates(session_id: str) -> Dict[str, Any]:
    return _learning_api_handler().prepare_candidates(_authenticated_learning_owner(), session_id)


@tool_wrapper
@with_fresh_user_settings
def stage_wiki_learning_knowledge_candidates(
    session_id: str, candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return _learning_api_handler().stage_candidates(
        _authenticated_learning_owner(), session_id, candidates,
    )


@tool_wrapper
@with_fresh_user_settings
def review_wiki_learning_knowledge_candidate(
    candidate_id: str, approved: bool, note: Optional[str] = None,
) -> Dict[str, Any]:
    return _learning_api_handler().review_candidate(
        _authenticated_learning_owner(), candidate_id, approved, note,
    )


@tool_wrapper
@with_fresh_user_settings
def commit_wiki_learning_knowledge_candidate(candidate_id: str) -> Dict[str, Any]:
    return LearningCandidateCommitHandler(
        service_factory=_learning_session_service,
        commit_knowledge=commit_wiki_knowledge,
    ).commit(_authenticated_learning_owner(), candidate_id)


@tool_wrapper
@with_fresh_user_settings
def submit_wiki_search_feedback(
    search_id: str,
    relevant_paths: List[str] = None,
    irrelevant_paths: List[str] = None,
    expected_no_answer: bool = False,
    missing_answer_path: Optional[str] = None,
    notes: Optional[str] = None,
    partially_relevant_paths: List[str] = None,
    satisfaction: Optional[str] = None,
    failure_reasons: List[str] = None,
    result_feedback: List[Dict[str, Any]] = None,
    expected_relations: List[Dict[str, str]] = None,
    expected_graph_paths: List[List[str]] = None,
    forbidden_paths: List[str] = None,
    expected_rule_types: List[str] = None,
    ontology_notes: Optional[str] = None,
) -> Dict[str, Any]:
    config = current_user_config.get() or {}
    owner_id = config.get("user_id", "SYSTEM")
    return SearchFeedbackApiHandler(SearchFeedbackService).submit(
            owner_id=owner_id,
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
        )


@tool_wrapper
@with_fresh_user_settings
def commit_wiki_knowledge(
    title: str, 
    description: str, 
    tags: List[str], 
    content: str, 
    topic_name: str = None, 
    topic_update_text: str = None, 
    image_paths: List[str] = None, 
    resource_paths: List[str] = None, 
    resource_summaries: List[Dict[str, Any]] = None,
    visibility: str = "private"
) -> Dict[str, Any]:
    """
    새로운 지식을 qa/ 저널 마크다운 문서로 영속화하고, 선택적으로 주제별 토픽(topics/) 문서를 누적 업데이트합니다.
    """
    if not title:
        raise InvalidArgumentException("지식 제목(title)은 필수 입력 항목입니다.")
    if not content:
        raise InvalidArgumentException("지식 본문(content)은 필수 입력 항목입니다.")

    user_config = current_user_config.get() or {}
    user_id = user_config.get("api_key", "SYSTEM")

    return KnowledgeCommitApiHandler(
        runtime_factory=create_knowledge_commit_runtime,
        audit=log_audit,
    ).commit(
        user_id=user_id, title=title, description=description, tags=tags, content=content,
        topic_name=topic_name, topic_update_text=topic_update_text, image_paths=image_paths,
        resource_paths=resource_paths, resource_summaries=resource_summaries, visibility=visibility,
    )


@tool_wrapper
@with_fresh_user_settings
def run_wiki_indexing(file_paths: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    로컬 마크다운 파일들의 변경 사항을 감지하여 데이터베이스에 실시간으로 증분 인덱싱(임베딩)합니다.
    """
    user_config = current_user_config.get() or {}
    user_id = user_config.get("api_key", "SYSTEM")

    return IndexingRunApiHandler(
        database_factory=DatabaseManager,
        embedding_factory=_embedding_service,
        indexer_factory=create_wiki_indexer,
        audit=log_audit,
    ).run(file_paths, user_id)


@tool_wrapper
def retry_wiki_indexing(limit: int = 100, force: bool = True) -> Dict[str, Any]:
    """DB 큐 작업을 소유자별 설정 컨텍스트에서 재처리합니다."""
    if limit < 1 or limit > 1000:
        raise InvalidArgumentException("limit은 1 이상 1000 이하여야 합니다.")

    from src.indexing.infrastructure.job_repository import IndexingJobRepository

    from src.settings.service import UserSettingsService

    return IndexingRetryApiHandler(
        database_factory=DatabaseManager,
        repository_factory=IndexingJobRepository,
        settings_factory=UserSettingsService,
        run_indexing=run_wiki_indexing,
        config_context=current_user_config,
    ).retry(limit, force)
