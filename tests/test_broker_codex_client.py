import json
from types import SimpleNamespace

import asyncio
import httpx,pytest

from src.settings.broker_codex_client import BrokerCodexClient


@pytest.fixture
def configured(monkeypatch,tmp_path):
    token=tmp_path/'token';token.write_text('workload-token')
    monkeypatch.setenv('BROKER_WORKLOAD_TOKEN_FILE',str(token))
    monkeypatch.setenv('CREDENTIAL_BROKER_URL','https://broker.example')
    return SimpleNamespace(subject_for_owner=lambda owner:'verified-subject')


def test_lifecycle_uses_verified_subject_and_never_receives_credentials(configured):
    calls=[]
    def handler(request):
        calls.append(request)
        body=json.loads(request.content)
        assert body['subject']=='verified-subject' and 'owner_id' not in body
        assert request.headers['authorization']=='Bearer workload-token'
        if request.url.path.endswith('/start'):
            return httpx.Response(200,json={'device_code':'device','user_code':'CODE'})
        if request.url.path.endswith('/check'):
            assert body['device_auth_id']=='device' and body['user_code']=='CODE'
            return httpx.Response(200,json={'status':'COMPLETED'})
        return httpx.Response(200,json={'unlinked':True})
    client=BrokerCodexClient('local-owner',configured,httpx.MockTransport(handler))
    assert asyncio.run(client.start_device_flow())['device_code']=='device'
    assert asyncio.run(client.check_device_token('device','CODE'))['status']=='COMPLETED'
    assert asyncio.run(client.unlink())['unlinked']
    assert not any(b'access_token' in request.content or b'refresh_token' in request.content for request in calls)


def test_broker_errors_are_sanitized(configured):
    client=BrokerCodexClient('owner',configured,httpx.MockTransport(
        lambda _:httpx.Response(409,text='refresh-secret-must-not-leak')))
    with pytest.raises(RuntimeError) as error:asyncio.run(client.start_device_flow())
    assert 'refresh-secret' not in str(error.value)
