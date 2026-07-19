from dataclasses import dataclass
from typing import Any, Callable

from src.api.exceptions import DatabaseException, WikiBaseException


@dataclass(frozen=True)
class RetrievalApiHandler:
    database_factory: Callable[[], Any]
    embedding_factory: Callable[[], Any]
    searcher_factory: Callable[[Any, Any], Any]
    feedback_factory: Callable[[Any], Any]
    formatter: Callable[[list[dict]], str]
    audit: Callable[..., None]

    def search(self, query: str, limit: int, user_id: str, owner_id: str) -> str:
        try:
            database = self.database_factory()
            database.connect()
        except Exception as exc:
            self.audit("KNOWLEDGE_RETRIEVAL", "FAILED", user_id=user_id,
                       payload={"query": query, "error": f"DB 연결 실패: {exc}"})
            raise DatabaseException(f"데이터베이스 연결에 실패했습니다: {exc}") from exc

        try:
            results = self.searcher_factory(database, self.embedding_factory()).search(query, limit=limit)
            try:
                search_id = self.feedback_factory(database).record_event(owner_id, query, results)
            except Exception as event_error:
                search_id = None
                self.audit("SEARCH_EVENT_RECORD", "FAILED", user_id=owner_id,
                           payload={"error": str(event_error)})

            if not results:
                self.audit("KNOWLEDGE_RETRIEVAL", "SUCCESS", user_id=user_id,
                           payload={"query": query, "results_found": 0})
                suffix = f"\nSearch Event ID: {search_id}" if search_id else ""
                return "지식베이스에서 관련된 문서를 찾지 못했습니다." + suffix

            file_paths = [document["file_path"] for document in results]
            self.audit("KNOWLEDGE_RETRIEVAL", "SUCCESS", user_id=user_id,
                       payload={"query": query, "limit": limit, "citations": file_paths})
            prefix = f"Search Event ID: {search_id}\n\n" if search_id else ""
            return prefix + self.formatter(results)
        except DatabaseException as exc:
            self.audit("KNOWLEDGE_RETRIEVAL", "FAILED", user_id=user_id,
                       payload={"query": query, "error": str(exc)})
            raise
        except Exception as exc:
            self.audit("KNOWLEDGE_RETRIEVAL", "FAILED", user_id=user_id,
                       payload={"query": query, "error": str(exc)})
            raise WikiBaseException(f"지식베이스 탐색 수행 중 에러 발생: {exc}") from exc
        finally:
            database.close()
