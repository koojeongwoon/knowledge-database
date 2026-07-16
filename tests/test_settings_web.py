import os
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.settings.service import UserSettingsService
from src.settings.web import SettingsPathDispatcher, settings_app
from src.api_keys.auth import _jwk_client


class SettingsWebTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(settings_app)

    def test_public_routes_are_available_without_redirecting_root(self):
        root = self.client.get("/")
        self.assertEqual(root.status_code, 200)
        self.assertEqual(root.json()["mcp_endpoint"], "/mcp")
        self.assertEqual(self.client.get("/health").status_code, 200)
        page = self.client.get("/settings", follow_redirects=False)
        self.assertEqual(page.status_code, 302)
        self.assertIn("auth.snappytory.com", page.headers["location"])
        callback = self.client.get("/callback")
        self.assertEqual(callback.status_code, 200)
        self.assertIn("access_token", callback.text)

    def test_jwks_client_uses_service_user_agent(self):
        client = _jwk_client()
        self.assertEqual(client.headers["User-Agent"], "llm-wiki-jwks/1.0")
        self.assertEqual(client.headers["Accept"], "application/json")
        self.assertEqual(client.timeout, 10)

    @patch("src.settings.web.verify_auth_token")
    def test_callback_token_is_exchanged_for_secure_http_only_cookie(self, verify_token):
        verify_token.return_value = {"sub": "auth-user", "exp": int(time.time()) + 3600}
        response = self.client.post("/api/session", json={"access_token": "x" * 40})
        self.assertEqual(response.status_code, 200)
        cookie = response.headers["set-cookie"]
        self.assertIn("knowledge_session=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=lax", cookie)

    @patch("src.settings.web.verify_auth_token")
    def test_callback_logs_only_verification_error_type(self, verify_token):
        token = "sensitive-token-value-that-must-not-be-logged"
        verify_token.side_effect = __import__("jwt").InvalidIssuerError("issuer mismatch")
        with self.assertLogs("settings_auth", level="WARNING") as captured:
            response = self.client.post("/api/session", json={"access_token": token})
        self.assertEqual(response.status_code, 401)
        output = "\n".join(captured.output)
        self.assertIn("InvalidIssuerError", output)
        self.assertNotIn(token, output)
        self.assertNotIn("issuer mismatch", output)

    @patch("src.settings.web.verify_auth_token")
    def test_settings_page_is_available_with_valid_session(self, verify_token):
        verify_token.return_value = {"sub": "auth-user", "exp": int(time.time()) + 3600}
        response = self.client.get("/settings", cookies={"knowledge_session": "valid-token"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("LLM-Wiki 설정", response.text)

    def test_settings_api_requires_authorization(self):
        response = self.client.get("/api/settings")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.client.get("/api/search-feedback/events").status_code, 401)

    @patch("src.settings.web.SearchFeedbackService")
    @patch("src.settings.web._authenticated_user")
    def test_recent_search_feedback_is_owner_scoped(self, authenticated_user, service_class):
        authenticated_user.return_value = "USER_1"
        service_class.return_value.list_recent.return_value = [{"search_id": "event-1"}]

        response = self.client.get("/api/search-feedback/events?limit=10")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["events"][0]["search_id"], "event-1")
        service_class.return_value.list_recent.assert_called_once_with("USER_1", 10)

    def test_secret_values_can_be_encrypted_and_decrypted(self):
        previous = os.environ.get("SETTINGS_ENCRYPTION_KEY")
        os.environ["SETTINGS_ENCRYPTION_KEY"] = "test-master-key"
        try:
            service = UserSettingsService(db_manager=object())
            encrypted = service._encrypt("secret-value")
            self.assertNotIn("secret-value", encrypted)
            self.assertEqual(service._decrypt(encrypted), "secret-value")
        finally:
            if previous is None:
                os.environ.pop("SETTINGS_ENCRYPTION_KEY", None)
            else:
                os.environ["SETTINGS_ENCRYPTION_KEY"] = previous

    def test_dispatcher_separates_settings_and_mcp_hosts(self):
        async def fallback(scope, receive, send):
            from starlette.responses import PlainTextResponse
            await PlainTextResponse("mcp")(scope, receive, send)

        old_settings = os.environ.get("SETTINGS_PUBLIC_HOST")
        old_mcp = os.environ.get("MCP_PUBLIC_HOST")
        os.environ["SETTINGS_PUBLIC_HOST"] = "knowledge.lynply.com"
        os.environ["MCP_PUBLIC_HOST"] = "mcp.lynply.com"
        try:
            client = TestClient(SettingsPathDispatcher(settings_app, fallback))
            self.assertEqual(client.get("/settings", headers={"host": "mcp.lynply.com"}).status_code, 404)
            self.assertEqual(client.post("/mcp", headers={"host": "knowledge.lynply.com"}).status_code, 404)
            self.assertEqual(client.get("/settings", headers={"host": "knowledge.lynply.com"}).status_code, 200)
        finally:
            if old_settings is None:
                os.environ.pop("SETTINGS_PUBLIC_HOST", None)
            else:
                os.environ["SETTINGS_PUBLIC_HOST"] = old_settings
            if old_mcp is None:
                os.environ.pop("MCP_PUBLIC_HOST", None)
            else:
                os.environ["MCP_PUBLIC_HOST"] = old_mcp


if __name__ == "__main__":
    unittest.main()
