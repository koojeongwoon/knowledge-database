import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.settings.web import settings_app


class ApiKeyWebTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(settings_app)
        self.headers = {"Authorization": "Bearer auth-login-token"}

    @patch("src.settings.web.verify_auth_token", return_value={"sub": "auth-user-1"})
    @patch("src.settings.web.ApiKeyService")
    def test_create_api_key_uses_authenticated_subject(self, service_type, _verify):
        service_type.return_value.create.return_value = {
            "plain_key": "kb_live_secret",
            "api_key": {"key_id": "key-1", "key_name": "codex"},
        }
        response = self.client.post(
            "/api/keys",
            headers=self.headers,
            json={"key_name": "codex", "validity_days": 30},
        )
        self.assertEqual(response.status_code, 201)
        service_type.return_value.create.assert_called_once_with("auth-user-1", "codex", 30)

    def test_create_api_key_requires_auth_server_token(self):
        response = self.client.post("/api/keys", json={"key_name": "codex"})
        self.assertEqual(response.status_code, 401)

    @patch("src.settings.web.ApiKeyService")
    @patch("src.settings.web._authenticated_auth_id")
    def test_dashboard_lists_keys_for_current_login(self, authenticated_auth_id, service_type):
        authenticated_auth_id.return_value = "auth-user-1"
        service_type.return_value.list_for_user.return_value = [{"key_id": "key-1"}]
        response = self.client.get("/api/keys")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [{"key_id": "key-1"}])
        service_type.return_value.list_for_user.assert_called_once_with("auth-user-1")

    @patch("src.settings.web.verify_auth_token", return_value={"sub": "auth-user-1"})
    @patch("src.settings.web.ApiKeyService")
    def test_revoke_is_scoped_to_authenticated_subject(self, service_type, _verify):
        service_type.return_value.revoke.return_value = True
        response = self.client.delete("/api/keys/key-1", headers=self.headers)
        self.assertEqual(response.status_code, 204)
        service_type.return_value.revoke.assert_called_once_with("auth-user-1", "key-1")


if __name__ == "__main__":
    unittest.main()
