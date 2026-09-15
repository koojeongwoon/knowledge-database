import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock
import httpx
import pytest
from src.settings.broker_identity import BrokerIdentityRepository
from src.indexing.infrastructure.broker_embedding import WorkloadBrokerEmbeddingService,BrokerEmbeddingError

def test_exact_persisted_identity_mapping_and_no_fallback():
    cur=Mock();cur.fetchone.return_value=('iam-sub',)
    @contextmanager
    def cursor():yield cur
    repo=BrokerIdentityRepository(lambda:SimpleNamespace(cursor=cursor))
    assert repo.subject_for_owner('local-owner')=='iam-sub'
    assert cur.execute.call_args.args==('SELECT sub_val FROM knowledge_users WHERE user_id=%s',('local-owner',))
    cur.fetchone.return_value=None
    with pytest.raises(ValueError):repo.subject_for_owner('other')
    with pytest.raises(ValueError):repo.subject_for_owner('SYSTEM')

def test_workload_embedding_uses_identity_without_grant_or_provider_key(monkeypatch,tmp_path):
    token=tmp_path/'token';token.write_text('workload-one')
    monkeypatch.setenv('BROKER_WORKLOAD_TOKEN_FILE',str(token));monkeypatch.setenv('CREDENTIAL_BROKER_URL','https://broker.example')
    repo=SimpleNamespace(subject_for_owner=Mock(return_value='verified-sub'))
    calls=[]
    def handler(req):
        assert req.url.path=='/v1/workload/embeddings'
        assert req.headers['authorization']=='Bearer '+token.read_text()
        body=json.loads(req.content)
        assert body['subject']=='verified-sub'
        assert not {'grant_id','connection_id','credential_version','tenant_id','api_key'} & body.keys()
        calls.append(body)
        return httpx.Response(200,json={'success':True,'data':{'embeddings':[[.1]]}})
    service=WorkloadBrokerEmbeddingService('local-owner',1,repo,httpx.MockTransport(handler))
    assert service.embed_text('one')==[.1]
    token.write_text('workload-two')
    assert service.embed_text('two')==[.1]
    assert calls[0]['request_id']!=calls[1]['request_id']
    repo.subject_for_owner.assert_called_once_with('local-owner')

def test_consent_page_and_routes_are_removed():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    assert not (root/'src/settings/static/embedding.html').exists()
    assert 'embedding-binding' not in (root/'src/settings/web.py').read_text()
    assert 'create_embedding_binding_router' not in (root/'src/settings/web.py').read_text()
