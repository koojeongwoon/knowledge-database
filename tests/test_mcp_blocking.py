import ast
import asyncio
from pathlib import Path
import threading

import anyio
import pytest
from mcp.server.fastmcp import FastMCP

from src.api.blocking import BlockingWorkPool, MCPBusyError
from src.core.config import current_user_config


def test_sdk_search_keeps_event_loop_responsive_and_preserves_owner():
    async def scenario():
        started, release = threading.Event(), threading.Event()
        event_thread = threading.get_ident()
        calls = []

        def search(*args):
            calls.append((threading.get_ident(), current_user_config.get()["user_id"], args))
            started.set()
            assert release.wait(2)
            return "found"

        server = FastMCP("test")
        namespace = dict(mcp=server, mcp_work_pool=BlockingWorkPool(2, 1, .2),
                         retrieve_wiki_knowledge=search, search_wiki_knowledge_baseline=search)
        tree = ast.parse(Path("src/api/mcp_server.py").read_text())
        nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name in {"search_wiki_knowledge", "search_knowledge_baseline"}]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "mcp_search", "exec"), namespace)

        async def request(owner, tool, args):
            token = current_user_config.set({"user_id": owner})
            try:
                return await server.call_tool(tool, args)
            finally:
                current_user_config.reset(token)

        first = asyncio.create_task(request("alice", "search_wiki_knowledge", {"query": "q"}))
        second = asyncio.create_task(request("bob", "search_knowledge_baseline", {"query": "q", "release_id": "r"}))
        try:
            # This cannot execute before the blocking timeout with a sync SDK tool.
            with anyio.fail_after(1):
                while len(calls) < 2:
                    await anyio.sleep(.001)
            assert started.is_set()
            assert not first.done() and not second.done()
            assert {owner for _, owner, _ in calls} == {"alice", "bob"}
            assert all(thread != event_thread for thread, _, _ in calls)
        finally:
            release.set()
            await asyncio.gather(first, second)
        assert current_user_config.get().get("user_id") not in {"alice", "bob"}
    asyncio.run(scenario())


def test_database_exhaustion_during_authentication_is_retryable(monkeypatch):
    from src.api import middleware
    from src.api.exceptions import DatabaseException

    async def scenario():
        monkeypatch.setattr(middleware, "mcp_work_pool", BlockingWorkPool(1, 0, .1))
        def unavailable(*args):
            raise DatabaseException("internal connection details")
        monkeypatch.setattr(middleware, "_authenticated_config", unavailable)
        async def app(*args):
            raise AssertionError("Unauthenticated request reached the application")
        async def receive():
            return {"type": "http.request", "body": b""}
        messages = []
        async def send(message):
            messages.append(message)
        await middleware.MCPAuthMiddleware(app)({
            "type": "http", "method": "POST", "path": "/mcp",
            "headers": [(b"authorization", b"Bearer fixture")],
        }, receive, send)
        assert messages[0]["status"] == 503
        assert b"internal connection details" not in messages[1]["body"]
    asyncio.run(scenario())


def test_pool_bounds_running_and_waiting_work_and_recovers_after_timeout():
    async def scenario():
        pool = BlockingWorkPool(1, 1, .05)
        started, release = threading.Event(), threading.Event()
        def blocked():
            started.set()
            assert release.wait(2)
        active = asyncio.create_task(pool.run(blocked))
        while not started.is_set():
            await anyio.sleep(.001)
        queued = asyncio.create_task(pool.run(lambda: "queued"))
        await anyio.sleep(.005)
        try:
            with pytest.raises(MCPBusyError):
                await pool.run(lambda: "must not execute")
            with pytest.raises(MCPBusyError):
                await queued
        finally:
            release.set()
            await active
        assert await pool.run(lambda: "recovered") == "recovered"
    asyncio.run(scenario())


def test_cancelling_request_keeps_running_worker_slot_until_completion():
    async def scenario():
        pool = BlockingWorkPool(1, 0, .05)
        started, release = threading.Event(), threading.Event()
        scope = anyio.CancelScope()
        def blocked():
            started.set()
            assert release.wait(2)
        async def request():
            with scope:
                await pool.run(blocked)
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(request)
            while not started.is_set():
                await anyio.sleep(.001)
            scope.cancel()
            try:
                with pytest.raises(MCPBusyError):
                    await pool.run(lambda: "must not execute")
            finally:
                release.set()
        assert await pool.run(lambda: "recovered") == "recovered"
    asyncio.run(scenario())


def test_http_authentication_is_offloaded_and_reports_saturation(monkeypatch):
    from src.api import middleware

    async def scenario():
        pool = BlockingWorkPool(1, 0, .1)
        monkeypatch.setattr(middleware, "mcp_work_pool", pool)
        started, release = threading.Event(), threading.Event()
        event_thread = threading.get_ident()
        def authenticate(token, headers):
            assert threading.get_ident() != event_thread
            started.set()
            assert release.wait(2)
            return {"user_id": "alice"}
        monkeypatch.setattr(middleware, "_authenticated_config", authenticate)
        async def app(scope, receive, send):
            assert current_user_config.get()["user_id"] == "alice"
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})
        scope = {"type": "http", "method": "POST", "path": "/mcp",
                 "headers": [(b"authorization", b"Bearer synthetic-token")]}
        first_messages, rejected_messages = [], []
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def first_send(message):
            first_messages.append(message)
        async def rejected_send(message):
            rejected_messages.append(message)
        handler = middleware.MCPAuthMiddleware(app)
        first = asyncio.create_task(handler(dict(scope), receive, first_send))
        try:
            with anyio.fail_after(1):
                while not started.is_set():
                    await anyio.sleep(.001)
            await handler(dict(scope), receive, rejected_send)
            assert rejected_messages[0]["status"] == 503
        finally:
            release.set()
            await first
        assert first_messages[0]["status"] == 200
    asyncio.run(scenario())
