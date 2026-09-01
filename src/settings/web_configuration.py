from typing import Awaitable, Callable, Optional, Type

from fastapi import APIRouter, Cookie, Header, HTTPException
from pydantic import BaseModel, Field

from src.settings.openai_oauth import (
    OpenAIOAuthClient,
    OpenAIOAuthDenied,
    OpenAIOAuthError,
    OpenAIOAuthExpired,
    OpenAIOAuthSlowDown,
)


class SettingsPayload(BaseModel):
    llm_auth_type: Optional[str] = Field(default=None, pattern="^(api_key|openai_oauth)$")
    llm_model_name: Optional[str] = Field(default=None, max_length=100)
    openai_api_key: Optional[str] = Field(default=None, max_length=512)
    embedding_api_key: Optional[str] = Field(default=None, max_length=512)
    storage_type: str = Field(default="s3", pattern="^(s3|r2)$")
    s3_endpoint_url: Optional[str] = Field(default=None, max_length=2048)
    s3_bucket_name: Optional[str] = Field(default=None, max_length=255)
    s3_access_key_id: Optional[str] = Field(default=None, max_length=1024)
    s3_secret_access_key: Optional[str] = Field(default=None, max_length=2048)


class SwitchAuthTypePayload(BaseModel):
    llm_auth_type: str = Field(..., pattern="^(api_key|openai_oauth)$")


class DeviceCodePollPayload(BaseModel):
    device_code: str = Field(..., min_length=1)
    user_code: Optional[str] = Field(default=None)


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

    @router.post("/api/settings/switch-auth-type")
    async def switch_auth_type(
        payload: SwitchAuthTypePayload,
        authorization: Optional[str] = Header(default=None),
        knowledge_session: Optional[str] = Cookie(default=None),
    ):
        owner_id = await authenticate(authorization, knowledge_session)
        service = service_factory()
        try:
            return service.switch_llm_auth_type(owner_id, payload.llm_auth_type)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            service.db_manager.close()

    @router.post("/api/settings/openai-oauth/device-code")
    async def start_openai_device_code(
        authorization: Optional[str] = Header(default=None),
        knowledge_session: Optional[str] = Cookie(default=None),
    ):
        await authenticate(authorization, knowledge_session)
        from src.settings.iam_codex_client import IAMCodexClient
        iam_client = IAMCodexClient()
        try:
            return await iam_client.start_device_flow()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"인증 서버(IAM) 연동 실패: {exc}") from exc

    @router.post("/api/settings/openai-oauth/poll")
    async def poll_openai_device_token(
        payload: DeviceCodePollPayload,
        authorization: Optional[str] = Header(default=None),
        knowledge_session: Optional[str] = Cookie(default=None),
    ):
        owner_id = await authenticate(authorization, knowledge_session)
        from src.settings.iam_codex_client import IAMCodexClient
        iam_client = IAMCodexClient()
        try:
            iam_resp = await iam_client.check_device_token(
                device_code=payload.device_code,
                user_code=payload.user_code,
                user_id=owner_id,
            )
            if iam_resp.get("status") == "COMPLETED":
                service = service_factory()
                try:
                    saved = service.switch_llm_auth_type(owner_id, "openai_oauth")
                    return {"status": "complete", "settings": saved}
                finally:
                    service.db_manager.close()
            elif iam_resp.get("status") == "PENDING":
                return {"status": "pending"}
            else:
                return {"status": "pending"}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"인증 서버(IAM) 토큰 확인 실패: {exc}") from exc

    return router
