import json
from collections import defaultdict
from typing import List, Dict, Any, Optional

from src.api.decorators import tool_wrapper, with_fresh_user_settings
from src.api.exceptions import (
    WikiBaseException,
    InvalidArgumentException,
    DatabaseException,
)
from src.core.config import EMBEDDING_PROVIDER, EMBEDDING_DIM
from src.core.config import current_user_config
from src.core.database.factory import DatabaseManager
from src.core.logging.audit import log_audit  # 감사 로거 유틸 임포트
from src.indexing.domain.embedding import FakeEmbeddingService, OpenAIEmbeddingService, BGEM3EmbeddingService
from src.retrieval.application.service import WikiSearcher
from src.retrieval.domain.formatter import format_retrieved_documents
from src.retrieval.feedback import SearchFeedbackService
from src.wiki.application.integration import WikiIntegrationManager
from src.settings.inbox import InboxService
from src.learning.application.service import LearningPreparationService
from src.learning.application.session_service import LearningSessionService
from src.learning.domain.feedback import LearningFeedbackPlanner
from src.learning.infrastructure.repository import LearningSessionRepository


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

    try:
        db_manager = DatabaseManager()
        db_manager.connect()
    except Exception as e:
        log_audit("KNOWLEDGE_RETRIEVAL", "FAILED", user_id=user_id, payload={"query": query, "error": f"DB 연결 실패: {e}"})
        raise DatabaseException(f"데이터베이스 연결에 실패했습니다: {e}") from e
        
    try:
        if EMBEDDING_PROVIDER == "openai":
            embedding_service = OpenAIEmbeddingService(dimension=EMBEDDING_DIM)
        elif EMBEDDING_PROVIDER == "bge-m3":
            embedding_service = BGEM3EmbeddingService()
        else:
            embedding_service = FakeEmbeddingService(dimension=EMBEDDING_DIM)
            
        searcher = WikiSearcher(db_manager=db_manager, embedding_service=embedding_service)
        results = searcher.search(query, limit=limit)
        
        try:
            search_id = SearchFeedbackService(db_manager).record_event(owner_id, query, results)
        except Exception as event_error:
            search_id = None
            log_audit("SEARCH_EVENT_RECORD", "FAILED", user_id=owner_id, payload={"error": str(event_error)})

        if not results:
            log_audit("KNOWLEDGE_RETRIEVAL", "SUCCESS", user_id=user_id, payload={"query": query, "results_found": 0})
            suffix = f"\nSearch Event ID: {search_id}" if search_id else ""
            return "지식베이스에서 관련된 문서를 찾지 못했습니다." + suffix
            
        file_paths = [doc["file_path"] for doc in results]
        formatted_result = format_retrieved_documents(results)
            
        # 검색 감사 로그 성공 기록
        log_audit(
            action="KNOWLEDGE_RETRIEVAL",
            status="SUCCESS",
            user_id=user_id,
            payload={"query": query, "limit": limit, "citations": file_paths}
        )
        return (f"Search Event ID: {search_id}\n\n" if search_id else "") + formatted_result
    except DatabaseException as de:
        log_audit("KNOWLEDGE_RETRIEVAL", "FAILED", user_id=user_id, payload={"query": query, "error": str(de)})
        raise
    except Exception as e:
        log_audit("KNOWLEDGE_RETRIEVAL", "FAILED", user_id=user_id, payload={"query": query, "error": str(e)})
        raise WikiBaseException(f"지식베이스 탐색 수행 중 에러 발생: {e}") from e
    finally:
        db_manager.close()


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
    config = current_user_config.get() or {}
    owner_id = config.get("user_id", "SYSTEM")
    if not owner_id or owner_id == "SYSTEM":
        raise InvalidArgumentException("Inbox를 사용하려면 인증된 사용자가 필요합니다.")
    try:
        item = InboxService(owner_id).add_markdown(
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
        log_audit("INBOX_MARKDOWN_CREATE", "SUCCESS", user_id=owner_id, payload={"item_id": item["id"]})
        return item
    except ValueError as exc:
        raise InvalidArgumentException(str(exc)) from exc


@tool_wrapper
@with_fresh_user_settings
def list_wiki_inbox_items(limit: int = 50) -> Dict[str, Any]:
    if limit < 1 or limit > 100:
        raise InvalidArgumentException("limit은 1 이상 100 이하여야 합니다.")
    config = current_user_config.get() or {}
    owner_id = config.get("user_id", "SYSTEM")
    if not owner_id or owner_id == "SYSTEM":
        raise InvalidArgumentException("Inbox를 사용하려면 인증된 사용자가 필요합니다.")
    items = InboxService(owner_id).list_items()[:limit]
    return {"items": items, "count": len(items)}


@tool_wrapper
@with_fresh_user_settings
def read_wiki_inbox_item(item_id: str) -> Dict[str, Any]:
    config = current_user_config.get() or {}
    owner_id = config.get("user_id", "SYSTEM")
    if not owner_id or owner_id == "SYSTEM":
        raise InvalidArgumentException("Inbox를 사용하려면 인증된 사용자가 필요합니다.")
    try:
        return InboxService(owner_id).read_for_learning(item_id)
    except (ValueError, FileNotFoundError) as exc:
        raise InvalidArgumentException("Inbox 항목을 찾을 수 없거나 읽을 수 없습니다.") from exc


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
    return LearningSessionService(LearningSessionRepository(DatabaseManager()))


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
    service = _learning_session_service()
    try:
        return service.start(
            _authenticated_learning_owner(), topic, requested_scope, effective_scope,
            goal, level, duration_minutes, first_question, sources, client_request_id,
        )
    except (ValueError, KeyError) as exc:
        raise InvalidArgumentException(str(exc)) from exc
    finally:
        service.repository.db_manager.close()


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
    service = _learning_session_service()
    try:
        return service.record_attempt(
            _authenticated_learning_owner(), session_id, question_id, answer,
            assessment, confidence, feedback_plan, missing_concepts, misconceptions,
            evidence_refs, next_question, next_question_type, next_evidence_refs,
            client_request_id,
        )
    except (ValueError, KeyError) as exc:
        raise InvalidArgumentException(str(exc)) from exc
    finally:
        service.repository.db_manager.close()


@tool_wrapper
@with_fresh_user_settings
def resume_wiki_learning_session(session_id: Optional[str] = None) -> Dict[str, Any]:
    service = _learning_session_service()
    try:
        return service.resume(_authenticated_learning_owner(), session_id)
    except (ValueError, KeyError) as exc:
        raise InvalidArgumentException(str(exc)) from exc
    finally:
        service.repository.db_manager.close()


@tool_wrapper
@with_fresh_user_settings
def complete_wiki_learning_session(session_id: str, summary: Optional[str] = None) -> Dict[str, Any]:
    service = _learning_session_service()
    try:
        return service.complete(_authenticated_learning_owner(), session_id, summary)
    except (ValueError, KeyError) as exc:
        raise InvalidArgumentException(str(exc)) from exc
    finally:
        service.repository.db_manager.close()


@tool_wrapper
@with_fresh_user_settings
def list_wiki_due_learning_reviews(limit: int = 20) -> Dict[str, Any]:
    service = _learning_session_service()
    try:
        return service.list_due_reviews(_authenticated_learning_owner(), limit)
    except ValueError as exc:
        raise InvalidArgumentException(str(exc)) from exc
    finally:
        service.repository.db_manager.close()


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
    service = _learning_session_service()
    try:
        return service.record_review(
            _authenticated_learning_owner(), review_id, answer, assessment,
            confidence, feedback_plan, client_request_id,
        )
    except (ValueError, KeyError) as exc:
        raise InvalidArgumentException(str(exc)) from exc
    finally:
        service.repository.db_manager.close()


@tool_wrapper
@with_fresh_user_settings
def prepare_wiki_learning_knowledge_candidates(session_id: str) -> Dict[str, Any]:
    service = _learning_session_service()
    try:
        return service.prepare_knowledge_candidates(_authenticated_learning_owner(), session_id)
    except (ValueError, KeyError) as exc:
        raise InvalidArgumentException(str(exc)) from exc
    finally:
        service.repository.db_manager.close()


@tool_wrapper
@with_fresh_user_settings
def stage_wiki_learning_knowledge_candidates(
    session_id: str, candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    service = _learning_session_service()
    try:
        return service.stage_knowledge_candidates(
            _authenticated_learning_owner(), session_id, candidates,
        )
    except (ValueError, KeyError) as exc:
        raise InvalidArgumentException(str(exc)) from exc
    finally:
        service.repository.db_manager.close()


@tool_wrapper
@with_fresh_user_settings
def review_wiki_learning_knowledge_candidate(
    candidate_id: str, approved: bool, note: Optional[str] = None,
) -> Dict[str, Any]:
    service = _learning_session_service()
    try:
        return service.review_knowledge_candidate(
            _authenticated_learning_owner(), candidate_id, approved, note,
        )
    except (ValueError, KeyError) as exc:
        raise InvalidArgumentException(str(exc)) from exc
    finally:
        service.repository.db_manager.close()


@tool_wrapper
@with_fresh_user_settings
def commit_wiki_learning_knowledge_candidate(candidate_id: str) -> Dict[str, Any]:
    owner_id = _authenticated_learning_owner()
    service = _learning_session_service()
    claimed = False
    write_succeeded = False
    try:
        normalized_id = service._uuid(candidate_id, "candidate_id")
        candidate = service.repository.claim_approved_knowledge_candidate(owner_id, normalized_id)
        if candidate["status"] == "committed":
            return {
                "candidate_id": normalized_id, "status": "committed",
                "qa_file_path": candidate.get("qa_file_path"),
                "topic_file_path": candidate.get("topic_file_path"),
                "idempotent_replay": True,
            }
        claimed = True
        response = json.loads(commit_wiki_knowledge(
            title=candidate["title"],
            description=candidate["description"],
            tags=candidate["tags"],
            content=candidate["content"],
            topic_name=candidate.get("topic_name"),
            topic_update_text=candidate.get("topic_update_text"),
            visibility="private",
        ))
        if not response.get("success"):
            raise WikiBaseException(response.get("message") or "승인된 후보의 Knowledge 저장에 실패했습니다.")
        write_succeeded = True
        data = response.get("data") or {}
        receipt = service.repository.mark_knowledge_candidate_committed(
            owner_id, normalized_id, data["qa_file_path"], data.get("topic_file_path"),
        )
        return {"candidate": receipt, "knowledge_commit": data}
    except (ValueError, KeyError) as exc:
        raise InvalidArgumentException(str(exc)) from exc
    finally:
        if claimed and not write_succeeded:
            service.repository.release_knowledge_candidate_claim(owner_id, candidate_id)
        service.repository.db_manager.close()


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
) -> Dict[str, Any]:
    config = current_user_config.get() or {}
    owner_id = config.get("user_id", "SYSTEM")
    service = SearchFeedbackService()
    try:
        return service.submit(
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
        )
    except KeyError as exc:
        raise InvalidArgumentException(str(exc)) from exc
    except ValueError as exc:
        raise InvalidArgumentException(str(exc)) from exc
    finally:
        service.db_manager.close()


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

    try:
        manager = WikiIntegrationManager()
        result = manager.commit_knowledge(
            title=title,
            description=description,
            tags=tags,
            content=content,
            topic_name=topic_name,
            topic_update_text=topic_update_text,
            image_paths=image_paths,
            resource_paths=resource_paths,
            resource_summaries=resource_summaries,
            visibility=visibility
        )

        written_paths = result.get("written_paths", [])
        indexing = {
            "status": "skipped",
            "indexed_files": [],
            "retry_targets": [],
        }
        if written_paths:
            from src.indexing.infrastructure.job_repository import IndexingJobRepository
            queue_db = None
            try:
                queue_db = DatabaseManager()
                job_repository = IndexingJobRepository(queue_db)
                job_repository.enqueue(written_paths)
                indexing = {
                    "status": "queued",
                    "indexed_files": [],
                    "retry_targets": [],
                    "queued_files": written_paths,
                }
            except Exception as queue_error:
                saved_paths = ", ".join(written_paths)
                raise DatabaseException(
                    "지식 파일은 저장되었지만 인덱싱 작업 등록에 실패했습니다. "
                    f"저장된 파일: {saved_paths}. 원인: {queue_error}"
                ) from queue_error
            finally:
                if queue_db is not None:
                    queue_db.close()
        
        # 지식 저널 생성 및 합성 성공 감사 로그 기록
        log_audit(
            action="KNOWLEDGE_COMMIT",
            status="SUCCESS",
            user_id=user_id,
            payload={
                "title": title,
                "qa_path": result["qa_file_path"],
                "topic_name": topic_name,
                "topic_path": result["topic_file_path"],
                "resources_count": len(result["all_resources"])
            }
        )
        
        return {
            "qa_file_path": result["qa_file_path"],
            "topic_file_path": result["topic_file_path"],
            "details": result["details"],
            "indexing": indexing,
        }
    except Exception as e:
        log_audit("KNOWLEDGE_COMMIT", "FAILED", user_id=user_id, payload={"title": title, "error": str(e)})
        raise


@tool_wrapper
@with_fresh_user_settings
def run_wiki_indexing(file_paths: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    로컬 마크다운 파일들의 변경 사항을 감지하여 데이터베이스에 실시간으로 증분 인덱싱(임베딩)합니다.
    """
    from src.indexing.application.service import WikiIndexer

    user_config = current_user_config.get() or {}
    user_id = user_config.get("api_key", "SYSTEM")

    try:
        db_manager = DatabaseManager()
        db_manager.connect()
    except Exception as e:
        log_audit("VECTOR_INDEXING_RUN", "FAILED", user_id=user_id, payload={"error": f"DB 연결 실패: {e}"})
        raise DatabaseException(f"인덱싱 데이터베이스 연결 실패: {e}") from e
        
    try:
        if EMBEDDING_PROVIDER == "openai":
            embedding_service = OpenAIEmbeddingService(dimension=EMBEDDING_DIM)
        elif EMBEDDING_PROVIDER == "bge-m3":
            embedding_service = BGEM3EmbeddingService()
        else:
            embedding_service = FakeEmbeddingService(dimension=EMBEDDING_DIM)
            
        indexer = WikiIndexer(root_dir="", db_manager=db_manager, embedding_service=embedding_service)
        stats = indexer.run_indexing(file_paths=file_paths)
        
        # 인덱싱 완료 감사 로그 성공 기록
        log_audit(
            action="VECTOR_INDEXING_RUN",
            status="SUCCESS",
            user_id=user_id,
            payload={"stats": stats, "file_paths": file_paths}
        )
        return stats
    except Exception as e:
        log_audit("VECTOR_INDEXING_RUN", "FAILED", user_id=user_id, payload={"error": str(e)})
        raise WikiBaseException(f"인덱싱 수행 중 치명적 에러 발생: {e}") from e
    finally:
        db_manager.close()


@tool_wrapper
def retry_wiki_indexing(limit: int = 100, force: bool = True) -> Dict[str, Any]:
    """DB 큐 작업을 소유자별 설정 컨텍스트에서 재처리합니다."""
    if limit < 1 or limit > 1000:
        raise InvalidArgumentException("limit은 1 이상 1000 이하여야 합니다.")

    from src.indexing.infrastructure.job_repository import IndexingJobRepository

    db_manager = DatabaseManager()
    repository = IndexingJobRepository(db_manager)
    try:
        jobs = repository.claim(limit=limit, force=force)
        if not jobs:
            return {"status": "empty", "processed": 0, "jobs": []}

        jobs_by_owner = defaultdict(list)
        for job in jobs:
            jobs_by_owner[job["owner_id"]].append(job["file_path"])

        processed = 0
        results = []
        for owner_id, file_paths in jobs_by_owner.items():
            from src.settings.service import UserSettingsService

            settings_service = UserSettingsService()
            try:
                stored_config = settings_service.get_runtime_config(owner_id)
            finally:
                settings_service.db_manager.close()

            owner_config = {
                "api_key": f"background:{owner_id}",
                "user_id": owner_id,
                **stored_config,
            }
            context_token = current_user_config.set(owner_config)
            try:
                response = json.loads(run_wiki_indexing(file_paths=file_paths))
                if response.get("success"):
                    repository.complete(file_paths, owner_id=owner_id)
                    processed += len(file_paths)
                    results.append({
                        "owner_id": owner_id,
                        "status": "success",
                        "file_paths": file_paths,
                        "stats": response.get("data"),
                    })
                else:
                    error = response.get("message") or "Indexing retry failed"
                    repository.fail(file_paths, error, owner_id=owner_id)
                    results.append({
                        "owner_id": owner_id,
                        "status": "failed",
                        "file_paths": file_paths,
                        "error": error,
                    })
            except Exception as owner_error:
                repository.fail(file_paths, str(owner_error), owner_id=owner_id)
                results.append({
                    "owner_id": owner_id,
                    "status": "failed",
                    "file_paths": file_paths,
                    "error": str(owner_error),
                })
            finally:
                current_user_config.reset(context_token)

        return {
            "status": "success" if processed == len(jobs) else "partial_failure",
            "processed": processed,
            "claimed": len(jobs),
            "results": results,
        }
    except Exception as e:
        raise WikiBaseException(f"인덱싱 재시도 중 오류 발생: {e}") from e
    finally:
        db_manager.close()
