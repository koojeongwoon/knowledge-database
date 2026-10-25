"""Opt-in real Knowledge clients -> Broker ASGI -> IAM adapter -> mock providers.

Run with RUN_IAM_BROKER_SERVICE_CONTRACT=true and BROKER_SOURCE_ROOT pointing at
an adjacent credential-broker checkout with its test virtualenv available.
"""
import base64
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel

from src.indexing.infrastructure.broker_embedding import WorkloadBrokerEmbeddingService, BrokerEmbeddingError
from src.indexing.infrastructure.broker_chat import BrokerStructuredChat

pytestmark = pytest.mark.skipif(os.getenv('RUN_IAM_BROKER_SERVICE_CONTRACT') != 'true', reason='opt-in adjacent Broker source contract')


class Answer(BaseModel):
    answer: str


class BrokerTransport(httpx.BaseTransport):
    def __init__(self, process): self.process = process
    def handle_request(self, request):
        self.process.stdin.write(json.dumps({'method': request.method, 'path': request.url.path,
            'headers': dict(request.headers), 'body': base64.b64encode(request.read()).decode()}) + '\n')
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line: raise RuntimeError('Broker service contract worker stopped')
        response = json.loads(line)
        return httpx.Response(response['status'], headers=response['headers'], content=base64.b64decode(response['body']), request=request)
    def close(self): pass  # Multiple production client instances share the one fixture process.


@pytest.fixture
def clients(tmp_path, monkeypatch):
    root = Path(os.getenv('BROKER_SOURCE_ROOT', str(Path(__file__).resolve().parents[2] / 'credential-broker')))
    python = root / '.venv/bin/python'
    worker = root / 'tests/iam_service_contract_worker.py'
    assert python.is_file() and worker.is_file(), 'Adjacent Broker checkout and test venv are required'
    token = tmp_path / 'iam-token'; token.write_text('projected.proxy.jwt')
    service_token = tmp_path / 'workload-token'; service_token.write_text('projected.workload.jwt')
    monkeypatch.setenv('BROKER_WORKLOAD_TOKEN_FILE', str(service_token))
    monkeypatch.setenv('CREDENTIAL_BROKER_URL', 'https://broker.example')
    # Neither service may authenticate directly with environment credentials.
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-use-directly')
    environment = dict(os.environ, BROKER_IAM_CREDENTIAL_TOKEN_FILE=str(token))
    log = tmp_path / 'worker.stderr'
    with log.open('w') as stderr:
        process = subprocess.Popen([str(python), str(worker)], cwd=root, env=environment,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, text=True)
        transport = BrokerTransport(process)
        identity = SimpleNamespace(subject_for_owner=lambda _: 'service-owner')
        embedding = WorkloadBrokerEmbeddingService('local-owner', dimension=1, identity_repository=identity, transport=transport)
        api_chat = BrokerStructuredChat('local-owner', identity, transport, auth_type='api_key')
        codex = BrokerStructuredChat('local-owner', identity, transport, auth_type='openai_oauth')
        control = httpx.Client(transport=transport, base_url='https://broker.example')
        try:
            yield embedding, api_chat, codex, control
        finally:
            control.close(); process.stdin.close()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
        assert process.returncode == 0, log.read_text()


def ask(client):
    return client.parse('gpt-4o-mini' if client.auth_type == 'api_key' else 'gpt-5.6-luna',
                        [{'role': 'user', 'content': 'test-only-private-prompt'}], Answer, 0.2).answer


def test_api_key_embedding_chat_and_codex_all_survive_iam_custody(clients):
    embedding, api_chat, codex, control = clients
    assert embedding.embed_batch(['test-only-private-text', 'second input']) == [[0.25], [0.25]]
    assert ask(api_chat) == 'api-key-ok'
    assert ask(codex) == 'codex-ok'
    state = control.get('/__state').json()
    assert state['provider_calls'] == ['/v1/embeddings', '/v1/chat/completions', '/backend-api/codex/responses']
    assert state['bindings'] == ['openai', 'openai', 'codex']
    assert not any(state['legacy_backends'].values())


def test_codex_outage_does_not_disable_api_key_service_calls(clients):
    embedding, api_chat, codex, control = clients
    control.post('/__iam_failure/codex/503')
    with pytest.raises(BrokerEmbeddingError): ask(codex)
    assert embedding.embed_text('test-only-private-text') == [0.25]
    assert ask(api_chat) == 'api-key-ok'
    state = control.get('/__state').json()
    assert state['provider_calls'] == ['/v1/embeddings', '/v1/chat/completions']


def test_api_key_outage_does_not_fallback_to_codex_or_disable_oauth(clients):
    embedding, api_chat, codex, control = clients
    control.post('/__iam_failure/openai/503')
    for operation in (lambda: embedding.embed_text('test-only-private-text'), lambda: ask(api_chat)):
        with pytest.raises(BrokerEmbeddingError) as error: operation()
        assert 'test-only-private-iam-error' not in str(error.value)
    assert ask(codex) == 'codex-ok'
    state = control.get('/__state').json()
    assert state['provider_calls'] == ['/backend-api/codex/responses']
