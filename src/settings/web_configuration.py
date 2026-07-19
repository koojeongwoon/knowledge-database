from typing import Awaitable, Callable, Optional, Type

from fastapi import APIRouter, Cookie, Header, HTTPException
from pydantic import BaseModel, Field


class SettingsPayload(BaseModel):
    openai_api_key: Optional[str] = Field(default=None, max_length=512)
    storage_type: str = Field(default="s3", pattern="^(s3|r2)$")
    s3_endpoint_url: Optional[str] = Field(default=None, max_length=2048)
    s3_bucket_name: Optional[str] = Field(default=None, max_length=255)
    s3_access_key_id: Optional[str] = Field(default=None, max_length=1024)
    s3_secret_access_key: Optional[str] = Field(default=None, max_length=2048)


def create_configuration_router(
    authenticate: Callable[[Optional[str], Optional[str]], Awaitable[str]],
    service_factory: Callable[[], object],
    invalidate_storage_cache: Callable[[str], None],
    encryption_error: Type[Exception],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/settings")
    async def read_settings(
        authorization: Optional[str] = Header(default=None),
        knowledge_session: Optional[str] = Cookie(default=None),
    ):
        owner_id = await authenticate(authorization, knowledge_session)
        service = service_factory()
        try:
            return service.get_public(owner_id)
        finally:
            service.db_manager.close()

    @router.put("/api/settings")
    async def save_settings(
        payload: SettingsPayload,
        authorization: Optional[str] = Header(default=None),
        knowledge_session: Optional[str] = Cookie(default=None),
    ):
        owner_id = await authenticate(authorization, knowledge_session)
        if not payload.s3_endpoint_url or not payload.s3_bucket_name:
            raise HTTPException(status_code=422, detail="S3/R2 Endpoint와 Bucket은 필수입니다.")
        service = service_factory()
        try:
            existing = service.get_public(owner_id)
            if not payload.s3_access_key_id and not existing["s3_access_key_configured"]:
                raise HTTPException(status_code=422, detail="Access Key ID가 필요합니다.")
            if not payload.s3_secret_access_key and not existing["s3_secret_key_configured"]:
                raise HTTPException(status_code=422, detail="Secret Access Key가 필요합니다.")
            saved = service.save(owner_id, payload.model_dump())
            invalidate_storage_cache(owner_id)
            return saved
        except encryption_error as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        finally:
            service.db_manager.close()

    return router
