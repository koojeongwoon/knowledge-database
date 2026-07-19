from typing import Awaitable, Callable, Optional

from fastapi import APIRouter, Cookie, Header, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field


class CreateApiKeyPayload(BaseModel):
    key_name: str = Field(min_length=1, max_length=100)
    validity_days: int = Field(default=365, ge=1, le=3650)


def create_api_key_router(
    authenticate_auth_id: Callable[[Optional[str], Optional[str]], Awaitable[str]],
    service_factory: Callable[[], object],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/keys")
    async def list_api_keys(
        authorization: Optional[str] = Header(default=None),
        knowledge_session: Optional[str] = Cookie(default=None),
    ):
        auth_id = await authenticate_auth_id(authorization, knowledge_session)
        return service_factory().list_for_user(auth_id)

    @router.post("/api/keys", status_code=status.HTTP_201_CREATED)
    async def create_api_key(
        payload: CreateApiKeyPayload,
        authorization: Optional[str] = Header(default=None),
        knowledge_session: Optional[str] = Cookie(default=None),
    ):
        auth_id = await authenticate_auth_id(authorization, knowledge_session)
        return service_factory().create(auth_id, payload.key_name, payload.validity_days)

    @router.delete("/api/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def revoke_api_key(
        key_id: str,
        authorization: Optional[str] = Header(default=None),
        knowledge_session: Optional[str] = Cookie(default=None),
    ):
        auth_id = await authenticate_auth_id(authorization, knowledge_session)
        if not service_factory().revoke(auth_id, key_id):
            raise HTTPException(status_code=404, detail="API Key를 찾을 수 없습니다.")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
