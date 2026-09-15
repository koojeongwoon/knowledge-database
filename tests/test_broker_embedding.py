import base64,json
from uuid import uuid4
from unittest.mock import AsyncMock
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.indexing.infrastructure.broker_embedding import WorkloadBrokerEmbeddingService, BrokerEmbeddingError
from src.settings.web_embeddings import create_embedding_router


@pytest.fixture
def environment(monkeypatch, tmp_path):
    token = tmp_path / 'token'
    token.write_text('projected-workload')
    monkeypatch.setenv('BROKER_WORKLOAD_TOKEN_FILE', str(token))
    monkeypatch.setenv('IAM_SERVER_URL', 'https://iam.example')
    monkeypatch.setenv('CREDENTIAL_BROKER_URL', 'https://broker.example')
    monkeypatch.setenv('KNOWLEDGE_CLIENT_SECRET', 'service-client-secret')
    return token


def service(transport=None, **kwargs):
    return WorkloadBrokerEmbeddingService('owner',dimension=1,
        identity_repository=SimpleNamespace(subject_for_owner=lambda _:'verified-sub'),transport=transport,**kwargs)


@pytest.mark.parametrize('data',[
    [{'index':0,'embedding':[.1,.2]}],
    [{'index':1,'embedding':[.1]}],
    [{'index':0,'embedding':[float('nan')]}],
    [],
])
def test_invalid_provider_vectors_are_rejected(environment,data):
    transport=httpx.MockTransport(lambda _:httpx.Response(200,headers={'x-broker-response-source':'upstream'},
        content=json.dumps({'data':data}).encode()))
    with pytest.raises(BrokerEmbeddingError):service(transport).embed_text('test')


def test_no_owner_mapping_never_uses_raw_key_or_provider(environment,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','must-not-use')
    def missing(_):raise ValueError('No IAM user identity')
    with pytest.raises(ValueError):
        WorkloadBrokerEmbeddingService('owner',identity_repository=SimpleNamespace(subject_for_owner=missing))


def test_broker_failure_is_sanitized_without_retry(environment):
    calls=[]
    def handler(req):
        calls.append(req)
        return httpx.Response(409,text='secret-must-not-leak')
    with pytest.raises(BrokerEmbeddingError) as caught:
        service(httpx.MockTransport(handler)).embed_text('text')
    assert caught.value.status_code==409 and 'secret' not in str(caught.value)
    assert len(calls)==1


def test_session_endpoint_passes_proof_without_loading_credential_settings():
    authenticate=AsyncMock(return_value='verified-owner')
    def factory(**kwargs):
        assert kwargs['owner_id']=='verified-owner'
        assert kwargs['credential_version']==1 and kwargs['connection_id']==body['connection_id']
        return SimpleNamespace(embed_batch=lambda texts:[[0.1] for _ in texts])
    app=FastAPI();app.include_router(create_embedding_router(authenticate,factory))
    with TestClient(app) as client:
        body=dict(connection_id=str(uuid4()),credential_version=1,input=['test'],dimensions=1)
        assert client.post('/api/settings/embeddings',json=body).status_code==401
        client.cookies.set('knowledge_session','session')
        response=client.post('/api/settings/embeddings',json=body)
        assert response.json()=={'embeddings':[[0.1]],'dimensions':1}
        assert 'proof' not in response.text


def test_session_endpoint_preserves_connection_denial_status():
    authenticate = AsyncMock(return_value='verified-owner')
    def fail(texts):
        raise BrokerEmbeddingError('Broker embedding request failed (HTTP 409)', status_code=409)
    app = FastAPI()
    app.include_router(create_embedding_router(
        authenticate, lambda **kwargs: SimpleNamespace(embed_batch=fail),
    ))
    with TestClient(app) as client:
        client.cookies.set('knowledge_session', 'session')
        response = client.post('/api/settings/embeddings', json={
            'connection_id': str(uuid4()), 'credential_version': 3, 'input': ['text'],
        })
        assert response.status_code == 409
        assert response.json() == {'detail': 'Broker embedding request failed'}
