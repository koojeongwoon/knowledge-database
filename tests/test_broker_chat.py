import base64,json
from types import SimpleNamespace
from unittest.mock import Mock
import httpx,pytest
from src.indexing.infrastructure.broker_chat import BrokerStructuredChat
from src.indexing.infrastructure.broker_embedding import BrokerEmbeddingError
from src.indexing.infrastructure.expansion import BatchExpansionResponse,BrokerDocumentExpander

@pytest.fixture
def client_env(monkeypatch,tmp_path):
    token=tmp_path/'token';token.write_text('workload-token')
    monkeypatch.setenv('BROKER_WORKLOAD_TOKEN_FILE',str(token));monkeypatch.setenv('CREDENTIAL_BROKER_URL','https://broker.example')
    return SimpleNamespace(subject_for_owner=Mock(return_value='verified-sub'))

def stream_response(content,done=True):
    chunks=[{'choices':[{'index':0,'delta':{'content':content},'finish_reason':None}]},
        {'choices':[{'index':0,'delta':{},'finish_reason':'stop'}]}]
    return ''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+('data: [DONE]\n\n' if done else '')

def test_service_keeps_prompt_and_schema_broker_only_executes(client_env):
    parsed={'expansions':[{'chunk_index':0,'questions':['q1','q2','q3'],'keywords':['a','b','c','d','e']}]}
    def handler(req):
        body=json.loads(req.content)
        assert req.url.path=='/v1/proxy/api-key'
        assert req.headers['authorization']=='Bearer workload-token'
        assert json.loads(base64.b64decode(req.headers['x-broker-request']))['subject']=='verified-sub'
        assert 'Document title: Title' in body['messages'][1]['content']
        assert body['response_format']['json_schema']['schema']['additionalProperties'] is False
        assert not {'grant_id','connection_id','api_key'}&body.keys()
        return httpx.Response(200,headers={'x-broker-response-source':'upstream'},text=stream_response(json.dumps(parsed)))
    client=BrokerStructuredChat('owner',client_env,httpx.MockTransport(handler))
    result=BrokerDocumentExpander(client,'gpt-4o-mini').expand_batch('Title','Description',[(0,'chunk')])
    assert len(result)==1 and 'q1' in result[0][1]

def test_incomplete_stream_does_not_return_partial_structured_result(client_env):
    client=BrokerStructuredChat('owner',client_env,httpx.MockTransport(lambda _:httpx.Response(200,headers={'x-broker-response-source':'upstream'},text=stream_response('{"expansions":[]}',False))))
    with pytest.raises(BrokerEmbeddingError,match='incomplete'):
        client.parse('gpt-4o-mini',[{'role':'user','content':'x'}],BatchExpansionResponse,.2)

def test_broker_mode_never_loads_llm_credentials(monkeypatch):
    from src.core.config import current_user_config
    import src.core.config as config
    from src.settings.service import UserSettingsService
    from src.indexing.infrastructure.expansion import create_document_expander
    monkeypatch.setattr(config,'DOCUMENT_EXPANSION_ENABLED',True)
    monkeypatch.setattr(UserSettingsService,'get_runtime_config',lambda *a:pytest.fail('raw credentials loaded'),raising=False)
    monkeypatch.setattr(UserSettingsService,'get_llm_preferences',lambda *_:{'model':'gpt-5.6-luna','auth_type':'openai_oauth'})
    monkeypatch.setattr('src.indexing.infrastructure.broker_chat.BrokerIdentityRepository',lambda:SimpleNamespace(subject_for_owner=lambda _:'sub'))
    token=current_user_config.set({'user_id':'owner'})
    try:assert isinstance(create_document_expander(),BrokerDocumentExpander)
    finally:current_user_config.reset(token)


def test_oauth_preserves_model_and_omits_unsupported_temperature(client_env):
    class TrackingStream(httpx.SyncByteStream):
        def __init__(self, chunks):
            self.chunks=chunks;self.exhausted=False
        def __iter__(self):
            yield from self.chunks
            self.exhausted=True
    tracked=None
    def handler(req):
        nonlocal tracked
        body=json.loads(req.content)
        assert req.url.path=='/v1/proxy/oauth'
        metadata=json.loads(base64.b64decode(req.headers['x-broker-request']))
        assert metadata['profile']=='codex'
        assert metadata['url']=='https://chatgpt.com/backend-api/codex/responses'
        assert body['model']=='gpt-5.6-luna'
        assert not {'temperature','max_completion_tokens','auth_type'} & body.keys()
        assert body['input'][0]['content'][0]['type']=='input_text'
        events=[{'type':'response.output_text.delta','delta':'{"expansions":[]}'},{'type':'response.completed','response':{'status':'completed','output':[]}}]
        tracked=TrackingStream([
            ''.join('data: '+json.dumps(e)+'\n\n' for e in events).encode(),
            b'data: [DONE]\n\n',
        ])
        return httpx.Response(200,headers={'x-broker-response-source':'upstream'},stream=tracked)
    client=BrokerStructuredChat('owner',client_env,httpx.MockTransport(handler),auth_type='openai_oauth')
    assert client.parse('gpt-5.6-luna',[{'role':'user','content':'x'}],BatchExpansionResponse,.2).expansions==[]
    assert tracked.exhausted is True
