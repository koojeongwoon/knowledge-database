import json
from uuid import uuid4
from unittest.mock import AsyncMock
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.indexing.infrastructure.broker_embedding import BrokerEmbeddingService, BrokerEmbeddingError
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


def test_exchange_execution_and_workload_rotation(environment):
    calls=[]
    def handler(req):
        calls.append(req)
        if req.url.host == 'iam.example':
            assert b'subject_token=user-iam-token' in req.content
            assert req.url.path == '/api/auth/oauth2/token'
            assert b'client_id=credential-broker' in req.content
            assert b'requested_target_tenant=ten_9664c024babc4110' in req.content
            return httpx.Response(200,json={'access_token':'broker-audience-token'})
        assert req.headers['authorization']=='Bearer broker-audience-token'
        assert req.headers['x-workload-authorization']==f'Bearer {environment.read_text()}'
        body=json.loads(req.content)
        assert 'secret' not in body and 'tenant_id' not in body and 'user_id' not in body
        return httpx.Response(200,json={'success':True,'data':{'embeddings':[[0.1]]}})
    service=BrokerEmbeddingService(dimension=1,connection_id=str(uuid4()),subject_token_supplier=lambda:'user-iam-token',transport=httpx.MockTransport(handler))
    assert service.embed_text('test')==[0.1]
    environment.write_text('rotated-workload')
    assert service.embed_text('test')==[0.1]
    assert len(calls)==4


def test_no_iam_context_never_uses_raw_key_or_provider(environment, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','must-not-use')
    service=BrokerEmbeddingService(connection_id=str(uuid4()))
    with pytest.raises(BrokerEmbeddingError,match='IAM user token'):
        service.embed_text('test')


def test_broker_failure_is_sanitized_without_retry(environment):
    calls=[]
    def handler(req):
        calls.append(req)
        if req.url.host=='iam.example':
            return httpx.Response(200,json={'access_token':'token'})
        return httpx.Response(409,text='secret-must-not-leak')
    service=BrokerEmbeddingService(connection_id=str(uuid4()),subject_token_supplier=lambda:'user-token',transport=httpx.MockTransport(handler))
    with pytest.raises(BrokerEmbeddingError, match='HTTP 409') as caught:
        service.embed_text('text')
    assert 'secret' not in str(caught.value)
    assert len(calls)==2


def test_session_endpoint_passes_proof_without_loading_credential_settings():
    store=SimpleNamespace(resolve=AsyncMock(return_value=SimpleNamespace(access_token='user-proof')))
    def factory(**kwargs):
        assert kwargs['subject_token_supplier']()=='user-proof'
        return SimpleNamespace(embed_batch=lambda texts:[[0.1] for _ in texts])
    app=FastAPI();app.include_router(create_embedding_router(lambda:store,factory))
    with TestClient(app) as client:
        body=dict(connection_id=str(uuid4()),credential_version=1,input=['test'],dimensions=1)
        assert client.post('/api/settings/embeddings',json=body).status_code==401
        client.cookies.set('knowledge_session','session')
        response=client.post('/api/settings/embeddings',json=body)
        assert response.json()=={'embeddings':[[0.1]],'dimensions':1}
        assert 'proof' not in response.text
