import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from src.settings.openai_oauth import (
    OpenAIOAuthClient,
    OpenAIOAuthDenied,
    OpenAIOAuthError,
    OpenAIOAuthExpired,
    OpenAIOAuthPending,
    OpenAIOAuthSlowDown,
    OpenAITokenSet,
)


class OpenAIOAuthClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = OpenAIOAuthClient(
            client_id="test-client-id",
            device_endpoint="https://auth.openai.com/oauth/device/code",
            token_endpoint="https://auth.openai.com/oauth/token",
            timeout=5.0,
        )

    async def test_start_device_flow_success(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "device_auth_id": "dev-12345",
            "user_code": "ABCD-WXYZ",
            "interval": "5",
        }

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            result = await self.client.start_device_flow()

            self.assertEqual(result["device_code"], "dev-12345")
            self.assertEqual(result["user_code"], "ABCD-WXYZ")
            self.assertEqual(result["verification_uri"], "https://auth.openai.com/codex/device")
            self.assertEqual(result["expires_in"], 900)
            self.assertEqual(result["interval"], 5)

    async def test_start_device_flow_failure_raises_error(self):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "invalid_client"

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            with self.assertRaisesRegex(OpenAIOAuthError, "OpenAI Device 인증 시작 실패"):
                await self.client.start_device_flow()

    async def test_check_device_token_pending(self):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"error": "authorization_pending"}

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            result = await self.client.check_device_token("dev-12345")
            self.assertIsNone(result)

    async def test_check_device_token_slow_down(self):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"error": "slow_down"}

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            with self.assertRaises(OpenAIOAuthSlowDown):
                await self.client.check_device_token("dev-12345")

    async def test_check_device_token_expired(self):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"error": "expired_token"}

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            with self.assertRaises(OpenAIOAuthExpired):
                await self.client.check_device_token("dev-12345")

    async def test_check_device_token_denied(self):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"error": "access_denied"}

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            with self.assertRaises(OpenAIOAuthDenied):
                await self.client.check_device_token("dev-12345")

    async def test_check_device_token_success(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "access-token-xyz",
            "refresh_token": "refresh-token-xyz",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            token_set = await self.client.check_device_token("dev-12345")

            self.assertIsNotNone(token_set)
            self.assertEqual(token_set.access_token, "access-token-xyz")
            self.assertEqual(token_set.refresh_token, "refresh-token-xyz")
            self.assertGreater(token_set.expires_at, 0)

    async def test_refresh_access_token_async(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
            "expires_in": 3600,
        }

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            token_set = await self.client.refresh_access_token("old-refresh-token")

            self.assertEqual(token_set.access_token, "new-access-token")
            self.assertEqual(token_set.refresh_token, "new-refresh-token")

    def test_refresh_access_token_sync(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "sync-new-access-token",
            "refresh_token": "sync-new-refresh-token",
            "expires_in": 3600,
        }

        with patch("httpx.Client.post") as mock_post:
            mock_post.return_value = mock_response
            token_set = self.client.refresh_access_token_sync("sync-old-refresh")

            self.assertEqual(token_set.access_token, "sync-new-access-token")
            self.assertEqual(token_set.refresh_token, "sync-new-refresh-token")


if __name__ == "__main__":
    unittest.main()
