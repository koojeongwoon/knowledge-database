import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
from uuid import uuid4
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.indexing.infrastructure.broker_embedding import DelegatedBrokerEmbeddingService,BrokerEmbeddingError
from src.settings.web_embedding_binding import create_embedding_binding_router
from src.settings.embedding_binding import BindingBusyError

@pytest.fixture
def binding():
    return dict(grant_id=str(uuid4()),connection_id=str(uuid4()),credential_version=1,expires_at='2026-10-01T00:00:00Z')

def test_delegated_batches_only_send_projected_token_and_rotate(monkeypatch,tmp_path,binding):
    token=tmp_path/'token';token.write_text('sa-first')
    monkeypatch.setenv('BROKER_WORKLOAD_TOKEN_FILE',str(token))
    monkeypatch.setenv('CREDENTIAL_BROKER_URL','https://broker.example')
    monkeypatch.setenv('OPENAI_API_KEY','must-not-read')
    requests=[]
    def handler(req):
        assert req.url.path=='/v1/delegated-execute'
        assert req.headers['authorization']=='Bearer '+token.read_text()
        assert 'x-workload-authorization' not in req.headers
        body=json.loads(req.content)
        assert not {'subject','tenant_id','user_id','api_key'} & body.keys()
        assert body['grant_id']==binding['grant_id']
        requests.append(body['request_id'])
        return httpx.Response(200,json={'success':True,'data':{'embeddings':[[0.1]]}})
    repo=SimpleNamespace(get=Mock(return_value=binding))
    service=DelegatedBrokerEmbeddingService('verified-owner',1,repo,httpx.MockTransport(handler))
    assert service.embed_text('x')==[0.1]
    token.write_text('sa-rotated')
    assert service.embed_text('y')==[0.1]
    assert len(set(requests))==2
    repo.get.assert_called_with('verified-owner')

def test_missing_binding_fails_closed():
    service=DelegatedBrokerEmbeddingService('owner',binding_repository=SimpleNamespace(get=lambda _:None))
    with pytest.raises(BrokerEmbeddingError) as exc:
        service.embed_text('text')
    assert exc.value.status_code==409

class Repository:
    def __init__(self,binding=None):
        self.binding=binding; self.busy=False; self.fail_save=False
    @contextmanager
    def lock(self,owner):
        assert owner=='local-owner'
        if self.busy:
            raise BindingBusyError()
        yield
    def get(self,owner):
        assert owner=='local-owner'
        return self.binding
    def save(self,owner,grant):
        if self.fail_save:
            raise RuntimeError('storage failure')
        self.binding={**grant,'grant_id':grant['id']}
    def delete(self,owner):
        self.binding=None

def web(repo,binding):
    client=SimpleNamespace(request=AsyncMock(return_value={**binding,'id':binding['grant_id']}))
    identity=SimpleNamespace(get_or_create_user=Mock(return_value='local-owner'),db_manager=SimpleNamespace(close=Mock()))
    store=SimpleNamespace(resolve=AsyncMock(return_value=SimpleNamespace(auth_id='verified-sub',access_token='verified-token')))
    app=FastAPI();app.include_router(create_embedding_binding_router(lambda:store,lambda:repo,lambda:client,lambda:identity))
    browser=TestClient(app,raise_server_exceptions=False)
    browser.cookies.set('knowledge_session','cookie')
    return browser,client,identity

def test_binding_requires_same_origin_and_verified_owner(binding):
    repo=Repository();browser,client,identity=web(repo,binding)
    body=dict(connection_id=binding['connection_id'],credential_version=1)
    assert browser.put('/api/settings/embedding-binding',json=body).status_code==403
    assert browser.put('/api/settings/embedding-binding',headers={'Origin':'https://evil.example'},json=body).status_code==403
    result=browser.put('/api/settings/embedding-binding',headers={'Origin':'http://testserver'},json=body)
    assert result.status_code==200
    identity.get_or_create_user.assert_called_with('verified-sub')
    args=client.request.call_args.args
    assert args[0]=='verified-token'
    assert set(args[2]['workloads'])=={'system:serviceaccount:llm-wiki:knowledge','system:serviceaccount:llm-wiki:knowledge-indexing-worker'}
    assert 'verified-token' not in result.text
    assert browser.delete('/api/settings/embedding-binding',headers={'Origin':'http://testserver'}).status_code==200
    assert repo.binding is None

def test_rebind_revokes_old_grant_and_save_failure_revokes_new(binding):
    repo=Repository({**binding,'grant_id':'old'});repo.fail_save=True
    browser,client,_=web(repo,binding)
    result=browser.put('/api/settings/embedding-binding',headers={'Origin':'http://testserver'},json=dict(connection_id=binding['connection_id'],credential_version=1))
    assert result.status_code==500
    assert [c.args[1] for c in client.request.call_args_list]==[
        '/v1/execution-grants/old/revoke','/v1/execution-grants',
        '/v1/execution-grants/'+binding['grant_id']+'/revoke']

def test_concurrent_binding_change_returns_conflict_without_broker_call(binding):
    repo=Repository();repo.busy=True
    browser,client,_=web(repo,binding)
    response=browser.put('/api/settings/embedding-binding',headers={'Origin':'http://testserver'},json=dict(connection_id=binding['connection_id'],credential_version=1))
    assert response.status_code==409
    client.request.assert_not_awaited()

def test_storage_context_does_not_query_or_decrypt_ai_credentials():
    from src.settings.service import UserSettingsService
    cur=Mock();cur.fetchone.return_value=('r2','https://storage','bucket','storage-id','storage-secret')
    @contextmanager
    def cursor():
        yield cur
    service=UserSettingsService(db_manager=SimpleNamespace(cursor=cursor))
    service.initialize=Mock()
    service._decrypt=Mock(side_effect=lambda value:'decrypted-'+value)
    config=service.get_storage_runtime_config('owner')
    assert set(config)=={'storage'}
    assert config['storage']['storage_type']=='s3'
    sql=cur.execute.call_args.args[0]
    assert 'openai' not in sql and 'embedding_api_key' not in sql
    assert [c.args[0] for c in service._decrypt.call_args_list]==['storage-id','storage-secret']

def test_delegation_management_exchanges_verified_session_and_sanitizes_failure(monkeypatch,tmp_path):
    from src.settings.broker_delegation_client import BrokerDelegationClient
    token=tmp_path/'token';token.write_text('projected-sa')
    monkeypatch.setenv('BROKER_WORKLOAD_TOKEN_FILE',str(token))
    monkeypatch.setenv('CREDENTIAL_BROKER_URL','https://broker.example')
    monkeypatch.setenv('IAM_SERVER_URL','https://iam.example')
    seen=[]
    def handler(req):
        seen.append(req)
        if req.url.host=='iam.example':
            assert b'subject_token=verified-session' in req.content
            assert b'client_id=credential-broker' in req.content
            return httpx.Response(200,json={'access_token':'broker-proof'})
        assert req.headers['authorization']=='Bearer broker-proof'
        assert req.headers['x-workload-authorization']=='Bearer projected-sa'
        return httpx.Response(409,text='provider-secret-never-reflect')
    with pytest.raises(BrokerEmbeddingError) as exc:
        import asyncio
        asyncio.run(BrokerDelegationClient(httpx.MockTransport(handler)).request('verified-session','/v1/execution-grants',{}))
    assert exc.value.status_code==409
    assert 'provider-secret' not in str(exc.value)
    assert len(seen)==2
