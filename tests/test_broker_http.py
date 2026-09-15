import base64,json
import httpx,pytest
from src.indexing.infrastructure.broker_http import BrokerHttpClient,BrokerTransportError

@pytest.fixture
def configured(monkeypatch,tmp_path):
    token=tmp_path/'token';token.write_text('workload-one')
    monkeypatch.setenv('BROKER_WORKLOAD_TOKEN_FILE',str(token))
    monkeypatch.setenv('CREDENTIAL_BROKER_URL','https://broker.example')
    return token

@pytest.mark.parametrize('opt_in',[False,True])
def test_auth_retry_requires_explicit_call_site_opt_in(configured,opt_in):
    calls=[]
    def handler(req):
        calls.append(req)
        if len(calls)==1:
            configured.write_text('workload-two')
            return httpx.Response(401,headers={'x-broker-response-source':'upstream','x-broker-oauth-refreshed':'true'})
        assert req.headers['authorization']=='Bearer workload-two'
        return httpx.Response(200,headers={'x-broker-response-source':'upstream'},content=b'done')
    client=BrokerHttpClient('subject',httpx.MockTransport(handler))
    with client.stream('oauth','codex','POST','https://provider.example/api',content=b'provider-body',retry_authentication=opt_in) as response:
        assert response.status_code==(200 if opt_in else 401)
    assert len(calls)==(2 if opt_in else 1)
    if opt_in:
        ids=[json.loads(base64.b64decode(req.headers['x-broker-request']))['request_id'] for req in calls]
        assert ids[0]!=ids[1] and calls[0].content==calls[1].content==b'provider-body'


def test_auth_retry_is_bounded_to_one_and_never_for_broker_denial(configured):
    calls=[]
    def handler(req):
        calls.append(req)
        return httpx.Response(401,headers={'x-broker-response-source':'upstream','x-broker-oauth-refreshed':'true'})
    client=BrokerHttpClient('subject',httpx.MockTransport(handler))
    with client.stream('oauth','codex','POST','https://provider.example/api',retry_authentication=True) as response:
        assert response.status_code==401
    assert len(calls)==2
    calls.clear()
    def rejected(req):
        calls.append(req)
        return httpx.Response(403,headers={'x-broker-oauth-refreshed':'true'},text='private-error')
    client=BrokerHttpClient('subject',httpx.MockTransport(rejected))
    with pytest.raises(BrokerTransportError) as error:
        with client.stream('oauth','codex','POST','https://provider.example/api',retry_authentication=True):pass
    assert error.value.status_code==403 and 'private-error' not in str(error.value) and len(calls)==1
