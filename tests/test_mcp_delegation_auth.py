import asyncio
from unittest.mock import Mock, patch

from src.api.middleware import MCPAuthMiddleware
from src.core.config import current_user_config


def test_mcp_delegation_maps_verified_subject_without_retaining_token():
    captured = {}
    messages = []

    async def app(_scope, _receive, send):
        captured.update(current_user_config.get())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    user_service = Mock()
    user_service.get_or_create_user.return_value = "local-owner-1"
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": [(b"authorization", b"Bearer delegated-jwt")],
    }
    claims = {
        "iss": "https://auth.snappytory.com/t/ten_9664c024babc4110",
        "tenant_id": "ten_9664c024babc4110",
        "sub": "iam-user-1",
        "email": "user@example.com",
        "user_version": "2",
    }
    with patch("src.api.middleware.verify_gateway_delegation_token", return_value=claims), \
            patch("src.api.middleware.ApiKeyService", return_value=user_service):
        asyncio.run(MCPAuthMiddleware(app)(scope, receive, send))

    assert messages[0]["status"] == 200
    assert captured == {"user_id": "local-owner-1"}
    assert "delegated-jwt" not in repr(captured)
    identity = user_service.get_or_create_user.call_args.args[0]
    assert identity.subject_id == "iam-user-1"
    assert identity.tenant_id == "ten_9664c024babc4110"
    assert identity.user_version == 2
