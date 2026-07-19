from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from src.api.exceptions import InvalidArgumentException


@dataclass(frozen=True)
class BaselineApiHandler:
    context_factory: Callable[[], tuple[str, Any, Any]]
    searcher_factory: Callable[[Any, Any, str], Any]
    embedding_factory: Callable[[], Any]
    formatter: Callable[[list[dict]], str]
    audit: Callable[..., None]

    def prepare(self, **command) -> Dict[str, Any]:
        owner, database, service = self.context_factory()
        try:
            result = service.prepare(**command)
            self.audit("KNOWLEDGE_BASELINE_PREPARE", "SUCCESS", user_id=owner,
                       payload={"draft_id": result["draft_id"], "version": result["version"]})
            return result
        except ValueError as exc:
            raise InvalidArgumentException(str(exc)) from exc
        finally:
            database.close()

    def confirm(self, draft_id: str) -> Dict[str, Any]:
        owner, database, service = self.context_factory()
        try:
            result = service.confirm(draft_id)
            self.audit("KNOWLEDGE_BASELINE_CONFIRM", "SUCCESS", user_id=owner,
                       payload={"release_id": result["release_id"], "version": result["version"]})
            return result
        except ValueError as exc:
            raise InvalidArgumentException(str(exc)) from exc
        finally:
            database.close()

    def search(self, owner: str, database: Any, query: str, release_id: str, limit: int) -> str:
        try:
            results = self.searcher_factory(database, self.embedding_factory(), owner).search(query, release_id, limit)
            if not results:
                return "지정한 기준본에서 관련 문서를 찾지 못했습니다. 일반 지식으로 자동 전환하지 않았습니다."
            return self.formatter(results)
        except ValueError as exc:
            raise InvalidArgumentException(str(exc)) from exc
        finally:
            database.close()


@dataclass(frozen=True)
class InboxApiHandler:
    service_factory: Callable[[str], Any]
    audit: Callable[..., None]

    def create(self, owner: str, **command) -> Dict[str, Any]:
        try:
            item = self.service_factory(owner).add_markdown(**command)
            self.audit("INBOX_MARKDOWN_CREATE", "SUCCESS", user_id=owner, payload={"item_id": item["id"]})
            return item
        except ValueError as exc:
            raise InvalidArgumentException(str(exc)) from exc

    def list(self, owner: str, limit: int) -> Dict[str, Any]:
        items = self.service_factory(owner).list_items()[:limit]
        return {"items": items, "count": len(items)}

    def read(self, owner: str, item_id: str) -> Dict[str, Any]:
        try:
            return self.service_factory(owner).read_for_learning(item_id)
        except (ValueError, FileNotFoundError) as exc:
            raise InvalidArgumentException("Inbox 항목을 찾을 수 없거나 읽을 수 없습니다.") from exc


@dataclass(frozen=True)
class SearchFeedbackApiHandler:
    service_factory: Callable[[], Any]

    def submit(self, owner_id: str, **feedback) -> Dict[str, Any]:
        service = self.service_factory()
        try:
            return service.submit(owner_id=owner_id, **feedback)
        except (KeyError, ValueError) as exc:
            raise InvalidArgumentException(str(exc)) from exc
        finally:
            service.db_manager.close()
