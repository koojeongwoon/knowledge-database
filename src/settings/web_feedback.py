from typing import Awaitable, Callable, List, Optional

from fastapi import APIRouter, Cookie, Header, HTTPException, status
from pydantic import BaseModel, Field


class SearchResultFeedbackPayload(BaseModel):
    file_path: str = Field(min_length=1, max_length=512)
    relevance_grade: int = Field(ge=0, le=3)
    issue_reasons: List[str] = Field(default_factory=list, max_length=10)
    preferred_replacement_path: Optional[str] = Field(default=None, max_length=512)
    relation_helpful: Optional[bool] = None
    ontology_context_grade: Optional[int] = Field(default=None, ge=0, le=3)
    relation_path_correct: Optional[bool] = None
    rule_application_correct: Optional[bool] = None
    notes: Optional[str] = Field(default=None, max_length=1000)


class ExpectedRelationPayload(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    predicate: str = Field(min_length=1, max_length=40)
    object: str = Field(min_length=1, max_length=300)


class SearchFeedbackPayload(BaseModel):
    relevant_paths: List[str] = Field(default_factory=list, max_length=20)
    partially_relevant_paths: List[str] = Field(default_factory=list, max_length=20)
    irrelevant_paths: List[str] = Field(default_factory=list, max_length=20)
    satisfaction: Optional[str] = Field(default=None, pattern="^(satisfied|partial|dissatisfied)$")
    failure_reasons: List[str] = Field(default_factory=list, max_length=10)
    expected_no_answer: bool = False
    missing_answer_path: Optional[str] = Field(default=None, max_length=512)
    notes: Optional[str] = Field(default=None, max_length=2000)
    expected_relations: List[ExpectedRelationPayload] = Field(default_factory=list, max_length=20)
    expected_graph_paths: List[List[str]] = Field(default_factory=list, max_length=20)
    forbidden_paths: List[str] = Field(default_factory=list, max_length=20)
    expected_rule_types: List[str] = Field(default_factory=list, max_length=10)
    ontology_notes: Optional[str] = Field(default=None, max_length=2000)
    result_feedback: List[SearchResultFeedbackPayload] = Field(default_factory=list, max_length=30)


class SearchBehaviorPayload(BaseModel):
    action: str = Field(pattern="^(open|copy|cite|follow_graph|reformulate|abandon)$")
    file_path: Optional[str] = Field(default=None, max_length=512)
    position: Optional[int] = Field(default=None, ge=1, le=1000)


def create_feedback_router(
    authenticate: Callable[[Optional[str], Optional[str]], Awaitable[str]],
    service_factory: Callable[[], object],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/search-feedback/events")
    async def recent_search_events(
        limit: int = 30,
        authorization: Optional[str] = Header(default=None),
        knowledge_session: Optional[str] = Cookie(default=None),
    ):
        owner_id = await authenticate(authorization, knowledge_session)
        service = service_factory()
        try:
            return {"events": service.list_recent(owner_id, limit)}
        finally:
            service.db_manager.close()

    @router.get("/api/search-feedback/{search_id}/graph")
    async def search_feedback_graph(
        search_id: str,
        authorization: Optional[str] = Header(default=None),
        knowledge_session: Optional[str] = Cookie(default=None),
    ):
        owner_id = await authenticate(authorization, knowledge_session)
        service = service_factory()
        try:
            return service.graph_for_event(owner_id, search_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            service.db_manager.close()

    @router.put("/api/search-feedback/{search_id}")
    async def save_search_feedback(
        search_id: str,
        payload: SearchFeedbackPayload,
        authorization: Optional[str] = Header(default=None),
        knowledge_session: Optional[str] = Cookie(default=None),
    ):
        owner_id = await authenticate(authorization, knowledge_session)
        service = service_factory()
        try:
            return service.submit(owner_id, search_id, **payload.model_dump())
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            service.db_manager.close()

    @router.post("/api/search-feedback/{search_id}/behavior", status_code=status.HTTP_201_CREATED)
    async def save_search_behavior(
        search_id: str,
        payload: SearchBehaviorPayload,
        authorization: Optional[str] = Header(default=None),
        knowledge_session: Optional[str] = Cookie(default=None),
    ):
        owner_id = await authenticate(authorization, knowledge_session)
        service = service_factory()
        try:
            return service.record_behavior(owner_id, search_id, **payload.model_dump())
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            service.db_manager.close()

    return router
