import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

from src.settings.service import UserSettingsService
from src.settings.web import SettingsPathDispatcher, settings_app
from src.api_keys.auth import _jwk_client


class SettingsWebTests(unittest.TestCase):
    def setUp(self):
        self.store = Mock()
        self.store.session_ttl = 2592000
        self.store.begin_login.return_value = ("state-value", "verifier", "challenge-value")
        self.store.oauth_client.authorization_url.return_value = "https://auth.snappytory.com/oauth2/authorize?state=state-value"
        self.store.resolve = AsyncMock()
        self.store.oauth_client.exchange_code = AsyncMock(return_value={"access_token": "access", "refresh_token": "refresh"})
        self.store.create.return_value = "opaque-session-id"
        self.store_patcher = patch("src.settings.web.session_store", return_value=self.store)
        self.store_patcher.start()
        self.client = TestClient(settings_app)

    def tearDown(self):
        self.store_patcher.stop()

    def test_public_routes_are_available_without_redirecting_root(self):
        root = self.client.get("/")
        self.assertEqual(root.status_code, 200)
        self.assertEqual(root.json()["mcp_endpoint"], "/mcp")
        self.assertEqual(self.client.get("/health").status_code, 200)
        page = self.client.get("/settings", follow_redirects=False)
        self.assertEqual(page.status_code, 302)
        self.assertEqual(page.headers["location"], "/login")
        login = self.client.get("/login", follow_redirects=False)
        self.assertIn("/oauth2/authorize", login.headers["location"])
        callback = self.client.get("/callback")
        self.assertEqual(callback.status_code, 400)

    def test_jwks_client_uses_service_user_agent(self):
        client = _jwk_client()
        self.assertEqual(client.headers["User-Agent"], "llm-wiki-jwks/1.0")
        self.assertEqual(client.headers["Accept"], "application/json")
        self.assertEqual(client.timeout, 10)

    def test_callback_code_is_exchanged_for_secure_http_only_cookie(self):
        self.store.consume_login.return_value = "verifier"
        response = self.client.get("/callback?code=one-time-code&state=state-value", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        cookie = response.headers["set-cookie"]
        self.assertIn("knowledge_session=opaque-session-id", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=lax", cookie)
        self.assertEqual(response.headers["location"], "/dashboard")
        self.store.consume_login.assert_called_once_with("state-value")
        self.store.oauth_client.exchange_code.assert_awaited_once_with("one-time-code", "verifier")

    def test_callback_logs_only_error_type(self):
        from src.settings.oauth_session import OAuthSessionExpired
        self.store.consume_login.side_effect = OAuthSessionExpired("sensitive-state-detail")
        with self.assertLogs("settings_auth", level="WARNING") as captured:
            response = self.client.get("/callback?code=secret-code&state=secret-state")
        self.assertEqual(response.status_code, 401)
        output = "\n".join(captured.output)
        self.assertIn("OAuthSessionExpired", output)
        self.assertNotIn("secret-code", output)
        self.assertNotIn("secret-state", output)
        self.assertNotIn("sensitive-state-detail", output)

    def test_settings_page_is_available_with_valid_server_session(self):
        response = self.client.get("/settings", cookies={"knowledge_session": "opaque-session"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("LLM-Wiki 설정", response.text)
        self.assertIn("등록·수정", response.text)
        self.store.resolve.assert_awaited_once_with("opaque-session")

    def test_settings_edit_is_separate_from_read_only_page(self):
        response = self.client.get("/settings/edit", cookies={"knowledge_session": "opaque-session"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("OpenAI API Key", response.text)
        self.assertIn("settings-form", response.text)

    def test_dashboard_is_the_authenticated_landing_page(self):
        response = self.client.get("/dashboard", cookies={"knowledge_session": "opaque-session"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("LLM-Wiki 대시보드", response.text)
        self.assertIn("새 키 발급", response.text)

    def test_documents_page_is_protected_and_serves_local_assets(self):
        unauthenticated = self.client.get("/documents", follow_redirects=False)
        self.assertEqual(unauthenticated.status_code, 302)
        self.assertEqual(unauthenticated.headers["location"], "/login")

        response = self.client.get("/documents", cookies={"knowledge_session": "opaque-session"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("LLM-Wiki 문서", response.text)
        self.assertIn("문서 검색", response.text)
        self.assertEqual(self.client.get("/settings/assets/documents.js").status_code, 200)

    @patch("src.settings.web.DocumentBrowserService")
    @patch("src.settings.web._authenticated_user")
    def test_document_list_is_scoped_to_authenticated_owner(self, authenticated_user, service_class):
        authenticated_user.return_value = "USER_1"
        service_class.return_value.list_documents.return_value = [
            {"path": "qa/one.md", "name": "one.md", "section": "qa"},
        ]

        response = self.client.get("/api/documents")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["documents"][0]["path"], "qa/one.md")
        service_class.assert_called_once_with("USER_1")

    @patch("src.settings.web.DocumentBrowserService")
    @patch("src.settings.web._authenticated_user")
    def test_document_body_is_scoped_to_authenticated_owner(self, authenticated_user, service_class):
        authenticated_user.return_value = "USER_2"
        service_class.return_value.read_document.return_value = {
            "path": "topics/Development/wiki.md", "name": "wiki.md", "content": "# Mine",
        }

        response = self.client.get("/api/documents/topics/Development/wiki.md")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["content"], "# Mine")
        service_class.assert_called_once_with("USER_2")
        service_class.return_value.read_document.assert_called_once_with("topics/Development/wiki.md")

    def test_search_feedback_has_a_dedicated_page(self):
        response = self.client.get("/search-feedback", cookies={"knowledge_session": "opaque-session"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("LLM-Wiki 검색 평가", response.text)
        self.assertIn("전체 만족도", (self.client.get("/settings/assets/feedback.js").text))
        feedback_js = self.client.get("/settings/assets/feedback.js").text
        self.assertIn("매우 도움", feedback_js)
        self.assertIn("더 최신 문서가 있음", feedback_js)
        self.assertIn("relation_helpful", feedback_js)

    def test_search_graph_page_loads_bundled_visualization(self):
        response = self.client.get("/search-feedback/event-1", cookies={"knowledge_session": "opaque-session"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("검색 그래프", response.text)
        asset = self.client.get("/settings/assets/cytoscape-3.34.0.min.js")
        self.assertEqual(asset.status_code, 200)
        self.assertGreater(len(asset.content), 400000)

    def test_logout_revokes_server_session(self):
        response = self.client.post("/logout", cookies={"knowledge_session": "opaque-session"})
        self.assertEqual(response.status_code, 200)
        self.store.revoke.assert_called_once_with("opaque-session")

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

    @patch("src.settings.web.SearchFeedbackService")
    @patch("src.settings.web._authenticated_user")
    def test_search_behavior_is_owner_scoped(self, authenticated_user, service_class):
        authenticated_user.return_value = "USER_1"
        service_class.return_value.record_behavior.return_value = {
            "search_id": "event-1", "occurred_at": "2026-07-17T00:00:00+00:00",
        }

        response = self.client.post(
            "/api/search-feedback/event-1/behavior",
            json={"action": "follow_graph", "file_path": None, "position": None},
        )

        self.assertEqual(response.status_code, 201)
        service_class.return_value.record_behavior.assert_called_once_with(
            "USER_1", "event-1", action="follow_graph", file_path=None, position=None,
        )

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
            self.assertEqual(client.get("/dashboard", headers={"host": "mcp.lynply.com"}).status_code, 404)
            self.assertEqual(client.get("/search-feedback", headers={"host": "mcp.lynply.com"}).status_code, 404)
            self.assertEqual(client.get("/documents", headers={"host": "mcp.lynply.com"}).status_code, 404)
            self.assertEqual(client.get("/api/documents", headers={"host": "mcp.lynply.com"}).status_code, 404)
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
