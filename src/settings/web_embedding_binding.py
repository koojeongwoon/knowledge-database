from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Cookie, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from src.api_keys.service import ApiKeyService
from src.settings.embedding_binding import EmbeddingBindingRepository, BindingBusyError
from src.settings.broker_delegation_client import BrokerDelegationClient, KNOWLEDGE_WORKLOADS
from src.settings.oauth_session import OAuthSessionError
from src.indexing.infrastructure.broker_embedding import BrokerEmbeddingError


class BindingRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    connection_id: UUID
    credential_version: int = Field(ge=1)
    validity_days: int = Field(default=30,ge=1,le=30)


def create_embedding_binding_router(session_store_factory, repository_factory=EmbeddingBindingRepository,
                                    client_factory=BrokerDelegationClient, identity_factory=ApiKeyService):
    router = APIRouter()

    async def authenticate(cookie):
        if not cookie:
            raise HTTPException(401,'IAM login session is required')
        try:
            tokens = await session_store_factory().resolve(cookie)
        except OAuthSessionError:
            raise HTTPException(401,'IAM login session expired') from None
        identity = identity_factory()
        try:
            owner = identity.get_or_create_user(tokens.auth_id)
        finally:
            identity.db_manager.close()
        return tokens, owner

    def same_origin(request):
        # Cookie-authorized persistent delegation must not be created by another origin.
        origin = request.headers.get('origin')
        if origin != str(request.base_url).rstrip('/'):
            raise HTTPException(403,'Same-origin request required')

    @router.get('/api/settings/embedding-binding')
    async def status(knowledge_session: Optional[str] = Cookie(default=None)):
        _, owner = await authenticate(knowledge_session)
        return repository_factory().get(owner)

    @router.put('/api/settings/embedding-binding')
    async def bind(body: BindingRequest, request: Request, knowledge_session: Optional[str] = Cookie(default=None)):
        same_origin(request)
        tokens, owner = await authenticate(knowledge_session)
        repo, client = repository_factory(), client_factory()
        try:
            with repo.lock(owner):
                existing = repo.get(owner)
                # Rebinding invalidates the old grant first, avoiding orphaned live authorizations.
                if existing:
                    await client.request(tokens.access_token,'/v1/execution-grants/'+existing['grant_id']+'/revoke')
                grant = await client.request(tokens.access_token,'/v1/execution-grants',{
                    'connection_id':str(body.connection_id),'credential_version':body.credential_version,
                    'workloads':KNOWLEDGE_WORKLOADS,'validity_days':body.validity_days,
                })
                try:
                    repo.save(owner,grant)
                except Exception:
                    await client.request(tokens.access_token,'/v1/execution-grants/'+grant['id']+'/revoke')
                    raise
                return repo.get(owner)
        except BrokerEmbeddingError as exc:
            raise HTTPException(exc.status_code,'Broker delegation request failed') from None
        except BindingBusyError:
            raise HTTPException(409,'Embedding binding is being changed; retry after completion') from None

    @router.delete('/api/settings/embedding-binding')
    async def revoke(request: Request, knowledge_session: Optional[str] = Cookie(default=None)):
        same_origin(request)
        tokens, owner = await authenticate(knowledge_session)
        repo = repository_factory()
        try:
            with repo.lock(owner):
                binding = repo.get(owner)
                if binding:
                    await client_factory().request(tokens.access_token,'/v1/execution-grants/'+binding['grant_id']+'/revoke')
                    repo.delete(owner)
        except BrokerEmbeddingError as exc:
            raise HTTPException(exc.status_code,'Broker delegation revoke failed') from None
        except BindingBusyError:
            raise HTTPException(409,'Embedding binding is being changed; retry after completion') from None
        return {'revoked':True}

    return router
