import json
from typing import List, Dict, Any

from src.api.decorators import tool_wrapper
from src.api.exceptions import (
    WikiBaseException,
    InvalidArgumentException,
    DatabaseException,
)
from src.core.config import EMBEDDING_PROVIDER, EMBEDDING_DIM, WIKI_DIR
from src.core.config import current_user_config
from src.core.database.factory import DatabaseManager
from src.core.logging.audit import log_audit  # 감사 로거 유틸 임포트
from src.indexing.domain.embedding import FakeEmbeddingService, OpenAIEmbeddingService, BGEM3EmbeddingService
from src.retrieval.application.service import WikiSearcher
from src.retrieval.domain.formatter import format_retrieved_documents
from src.wiki.application.integration import WikiIntegrationManager


@tool_wrapper
def retrieve_wiki_knowledge(query: str, limit: int = 5) -> str:
    """
    개인 지식베이스에서 자연어 검색을 수행하고 관련성이 높은 마크다운 지식 조각들을 반환합니다.
    """
    if not query:
        raise InvalidArgumentException("검색 쿼리(query)는 비어 있을 수 없습니다.")

    # 사용자 식별용 Config 추출
    user_config = current_user_config.get() or {}
    user_id = user_config.get("api_key", "SYSTEM")

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
        
        if not results:
            log_audit("KNOWLEDGE_RETRIEVAL", "SUCCESS", user_id=user_id, payload={"query": query, "results_found": 0})
            return "지식베이스에서 관련된 문서를 찾지 못했습니다."
            
        file_paths = [doc["file_path"] for doc in results]
        formatted_result = format_retrieved_documents(results)
            
        # 검색 감사 로그 성공 기록
        log_audit(
            action="KNOWLEDGE_RETRIEVAL",
            status="SUCCESS",
            user_id=user_id,
            payload={"query": query, "limit": limit, "citations": file_paths}
        )
        return formatted_result
    except DatabaseException as de:
        log_audit("KNOWLEDGE_RETRIEVAL", "FAILED", user_id=user_id, payload={"query": query, "error": str(de)})
        raise
    except Exception as e:
        log_audit("KNOWLEDGE_RETRIEVAL", "FAILED", user_id=user_id, payload={"query": query, "error": str(e)})
        raise WikiBaseException(f"지식베이스 탐색 수행 중 에러 발생: {e}") from e
    finally:
        db_manager.close()


@tool_wrapper
def commit_wiki_knowledge(
    title: str, 
    description: str, 
    tags: List[str], 
    content: str, 
    topic_name: str = None, 
    topic_update_text: str = None, 
    image_paths: List[str] = None, 
    resource_paths: List[str] = None, 
    resource_summaries: List[Dict[str, Any]] = None
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
            resource_summaries=resource_summaries
        )
        
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
            "details": result["details"]
        }
    except Exception as e:
        log_audit("KNOWLEDGE_COMMIT", "FAILED", user_id=user_id, payload={"title": title, "error": str(e)})
        raise


@tool_wrapper
def run_wiki_indexing() -> Dict[str, Any]:
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
            
        indexer = WikiIndexer(root_dir=WIKI_DIR, db_manager=db_manager, embedding_service=embedding_service)
        stats = indexer.run_indexing()
        
        # 인덱싱 완료 감사 로그 성공 기록
        log_audit(
            action="VECTOR_INDEXING_RUN",
            status="SUCCESS",
            user_id=user_id,
            payload={"stats": stats}
        )
        return stats
    except Exception as e:
        log_audit("VECTOR_INDEXING_RUN", "FAILED", user_id=user_id, payload={"error": str(e)})
        raise WikiBaseException(f"인덱싱 수행 중 치명적 에러 발생: {e}") from e
    finally:
        db_manager.close()


@tool_wrapper
def check_knowledge_drift() -> str:
    """
    스케줄 관리 디렉토리(.agents/schedules/)를 분석하여 갱신 주기가 도달한 노트들의 목록을 리턴합니다.
    """
    from src.indexing.application.refresher_service import KnowledgeRefresher
    refresher = KnowledgeRefresher(root_dir=WIKI_DIR)
    try:
        targets = refresher.get_expired_targets()
        if not targets:
            return "CHECK_RESULT: NO_EXPIRED_TARGETS"
        return json.dumps(targets, ensure_ascii=False, indent=2)
    except Exception as e:
        raise WikiBaseException(f"Error scanning expired schedules: {e}") from e


@tool_wrapper
def evaluate_knowledge_drift(file_path: str, latest_text: str) -> str:
    """
    특정 노트의 로컬 텍스트와 새로 수집된 최신 정보를 LLM을 통해 비교하여 갱신 괴리가 있는지 판독합니다.
    """
    from src.indexing.application.refresher_service import KnowledgeRefresher
    refresher = KnowledgeRefresher(root_dir=WIKI_DIR)
    try:
        res = refresher.evaluate_drift(rel_path=file_path, latest_text=latest_text)
        return json.dumps(res, ensure_ascii=False, indent=2)
    except Exception as e:
        raise WikiBaseException(f"Error evaluating knowledge drift: {e}") from e


@tool_wrapper
def apply_knowledge_merge(file_path: str) -> str:
    """
    사용자의 승인을 얻은 경우, scratch/에 생성된 임시 갱신안을 원본 지식 마크다운에 병합합니다.
    """
    from src.indexing.application.refresher_service import KnowledgeRefresher
    refresher = KnowledgeRefresher(root_dir=WIKI_DIR)
    try:
        return refresher.apply_merge(rel_path=file_path)
    except Exception as e:
        raise WikiBaseException(f"Error merging knowledge drift: {e}") from e


@tool_wrapper
def update_knowledge_schedule(file_path: str, interval: str, source: str, category: str = "programming") -> str:
    """
    대화를 통해 특정 마크다운 노트의 갱신 주기(refresh_interval) 및 수집 소스(refresh_source)를 변경합니다.
    """
    from src.indexing.application.refresher_service import KnowledgeRefresher
    refresher = KnowledgeRefresher(root_dir=WIKI_DIR)
    try:
        refresher.update_or_create_schedule(rel_path=file_path, interval=interval, source=source, category=category)
        return f"성공: {file_path} 노정의 갱신 주기를 {interval}(소출처: {source}, 범주: {category})으로 변경 완료했습니다."
    except Exception as e:
        raise WikiBaseException(f"Error updating knowledge schedule: {e}") from e
