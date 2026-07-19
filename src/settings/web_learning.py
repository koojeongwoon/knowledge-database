from typing import Awaitable, Callable, Optional

from fastapi import APIRouter, Cookie, Header, HTTPException, Query


def create_learning_router(
    authenticate: Callable[[Optional[str], Optional[str]], Awaitable[str]],
    database_manager_factory: Callable[[], object],
    dashboard_service_factory: Callable[[object], object],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/learning/dashboard")
    async def learning_dashboard(
        days: int = Query(default=30),
        authorization: Optional[str] = Header(default=None),
        knowledge_session: Optional[str] = Cookie(default=None),
    ):
        owner_id = await authenticate(authorization, knowledge_session)
        db_manager = database_manager_factory()
        service = dashboard_service_factory(db_manager)
        try:
            return service.get(owner_id, days)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            db_manager.close()

    return router
