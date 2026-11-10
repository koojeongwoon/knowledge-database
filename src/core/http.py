"""Shared HTTP pools; identity, authorization and cookies never become defaults."""
import asyncio
import atexit
import os
from contextlib import contextmanager, asynccontextmanager
from http.cookiejar import CookieJar, DefaultCookiePolicy
from threading import Lock
from pathlib import Path

import httpx

from src.core.config import settings

_clients = {}
_lock = Lock()


def broker_base_url():
    return os.getenv('CREDENTIAL_BROKER_URL','').rstrip('/')


def workload_token_path():
    return Path(os.getenv('BROKER_WORKLOAD_TOKEN_FILE','/var/run/secrets/credential-broker/token'))


class _NoCookies(DefaultCookiePolicy):
    def set_ok(self, cookie, request):
        return False

    def return_ok(self, cookie, request):
        return False


def _options(profile, timeout=None, transport=None):
    read = timeout if timeout is not None else {
        "broker": settings.broker_http_timeout_seconds,
        "control": settings.broker_control_timeout_seconds,
        "iam": settings.auth_http_timeout_seconds,
    }[profile]
    return dict(timeout=httpx.Timeout(read, connect=settings.http_connect_timeout_seconds,
                                     pool=settings.http_pool_timeout_seconds),
                limits=httpx.Limits(max_connections=settings.http_max_connections,
                                    max_keepalive_connections=settings.http_max_connections,
                                    keepalive_expiry=settings.http_keepalive_seconds),
                follow_redirects=False, trust_env=False, transport=transport,
                cookies=CookieJar(policy=_NoCookies()))


@contextmanager
def http_client(profile, *, timeout=None, transport=None):
    if transport is not None:
        with httpx.Client(**_options(profile, timeout, transport)) as client:
            yield client
        return
    key = (profile, timeout)
    with _lock:
        if key not in _clients:
            manager = httpx.Client(**_options(profile, timeout))
            _clients[key] = (manager, manager.__enter__())
        client = _clients[key][1]
    yield client


@asynccontextmanager
async def async_http_client(profile, *, timeout=None, transport=None):
    if transport is not None:
        async with httpx.AsyncClient(**_options(profile, timeout, transport)) as client:
            yield client
        return
    loop = asyncio.get_running_loop()
    state = getattr(loop, "_knowledge_http_clients", None)
    if state is None:
        state = {"lock": asyncio.Lock(), "clients": {}}
        setattr(loop, "_knowledge_http_clients", state)
    key = (profile, timeout)
    async with state["lock"]:
        if key not in state["clients"]:
            manager = httpx.AsyncClient(**_options(profile, timeout))
            state["clients"][key] = (manager, await manager.__aenter__())
        client = state["clients"][key][1]
    yield client


def close_http_clients():
    with _lock:
        clients = list(_clients.values())
        _clients.clear()
    for manager, _ in clients:
        manager.__exit__(None, None, None)


async def close_async_http_clients():
    loop = asyncio.get_running_loop()
    state = getattr(loop, "_knowledge_http_clients", None)
    if state:
        clients = list(state["clients"].values())
        state["clients"].clear()
        for manager, _ in clients:
            await manager.__aexit__(None, None, None)


atexit.register(close_http_clients)
