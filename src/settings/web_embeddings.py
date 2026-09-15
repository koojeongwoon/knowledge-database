"""First authenticated Knowledge result-only embedding path."""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Cookie, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from src.indexing.infrastructure.broker_embedding import BrokerEmbeddingError, BrokerEmbeddingService
from src.settings.oauth_session import OAuthSessionError


class EmbeddingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: UUID
    credential_version: int = Field(ge=1)
    input: list[str] = Field(min_length=1, max_length=100)
    dimensions: int = Field(default=1536, ge=1, le=1536)


def create_embedding_router(session_store_factory, embedding_factory=BrokerEmbeddingService):
    router = APIRouter()

    @router.post("/api/settings/embeddings")
    async def create_embeddings(payload: EmbeddingPayload, knowledge_session: Optional[str] = Cookie(default=None)):
        if not knowledge_session:
            raise HTTPException(401, "IAM login session is required")
        try:
            tokens = await session_store_factory().resolve(knowledge_session)
        except OAuthSessionError:
            raise HTTPException(401, "IAM login session expired") from None
        service = embedding_factory(
            dimension=payload.dimensions, connection_id=str(payload.connection_id),
            credential_version=payload.credential_version,
            subject_token_supplier=lambda: tokens.access_token,
        )
        try:
            vectors = await run_in_threadpool(service.embed_batch, payload.input)
        except BrokerEmbeddingError as exc:
            raise HTTPException(exc.status_code, "Broker embedding request failed") from None
        return {"embeddings": vectors, "dimensions": payload.dimensions}

    return router
