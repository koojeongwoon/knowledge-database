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
def submit_wiki_search_feedback(
    search_id: str,
    relevant_paths: List[str] = None,
    irrelevant_paths: List[str] = None,
    expected_no_answer: bool = False,
    missing_answer_path: Optional[str] = None,
    notes: Optional[str] = None,
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
            queue_db = DatabaseManager()
            from src.indexing.infrastructure.job_repository import IndexingJobRepository
            job_repository = IndexingJobRepository(queue_db)
            try:
                job_repository.enqueue(written_paths)
                job_repository.start(written_paths)
                indexing_response = json.loads(run_wiki_indexing(file_paths=written_paths))
                if indexing_response.get("success"):
                    job_repository.complete(written_paths)
                    indexing = {
                        "status": "success",
                        "indexed_files": written_paths,
                        "retry_targets": [],
                        "stats": indexing_response.get("data"),
                    }
                else:
                    error = indexing_response.get("message") or "Indexing failed"
                    job_repository.fail(written_paths, error)
                    indexing = {
                        "status": "failed",
                        "indexed_files": [],
                        "retry_targets": written_paths,
                        "error": error,
                    }
            except Exception as indexing_error:
                try:
                    job_repository.fail(written_paths, str(indexing_error))
                except Exception:
                    pass
                indexing = {
                    "status": "failed",
                    "indexed_files": [],
                    "retry_targets": written_paths,
                    "error": str(indexing_error),
                }
            finally:
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
