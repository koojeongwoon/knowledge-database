import asyncio

import httpx

from src.core import http


def test_sync_pool_is_reused_without_authorization_or_cookie_leakage(monkeypatch):
    real = httpx.Client
    created, requests = [], []
    def receive(request):
        requests.append(request)
        return httpx.Response(200, headers={"Set-Cookie": "session=private; Path=/"})
    def create(**kwargs):
        assert kwargs['timeout'].connect == 3 and kwargs['timeout'].pool == 3
        assert kwargs['limits'].max_connections == 24
        kwargs['transport'] = httpx.MockTransport(receive)
        client = real(**kwargs)
        created.append(client)
        return client
    monkeypatch.setattr(http, '_clients', {})
    monkeypatch.setattr(http.httpx, 'Client', create)
    try:
        with http.http_client('broker') as first:
            first.get('https://broker.invalid/', headers={'Authorization':'Bearer alice'})
        with http.http_client('broker') as second:
            second.get('https://broker.invalid/', headers={'Authorization':'Bearer bob'})
        assert first is second
        assert len(created) == 1
        assert [r.headers['Authorization'] for r in requests] == ['Bearer alice','Bearer bob']
        assert all('Cookie' not in r.headers for r in requests)
        assert 'Authorization' not in first.headers and not first.cookies
        assert not first.is_closed
    finally:
        http.close_http_clients()
    assert first.is_closed


def test_async_pool_is_reused_per_loop_and_closed_at_shutdown(monkeypatch):
    real = httpx.AsyncClient
    created = []
    def create(**kwargs):
        kwargs['transport'] = httpx.MockTransport(lambda r: httpx.Response(200,headers={'Set-Cookie':'private=value'}))
        client = real(**kwargs)
        created.append(client)
        return client
    monkeypatch.setattr(http.httpx, 'AsyncClient', create)
    async def scenario():
        async with http.async_http_client('control') as first:
            await first.get('https://broker.invalid/')
        async with http.async_http_client('control') as second:
            assert first is second
            assert not second.cookies
        assert not first.is_closed
        await http.close_async_http_clients()
        assert first.is_closed
    asyncio.run(scenario())
    asyncio.run(scenario())
    assert len(created) == 2
