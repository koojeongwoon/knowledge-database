import os
import time
import unittest
from unittest.mock import MagicMock, patch

from src.core.config import current_user_config
from src.indexing.domain.embedding import OpenAIEmbeddingService
from src.indexing.infrastructure.expansion import create_document_expander
from src.settings import service as settings_service_module
from src.settings.openai_oauth import OpenAITokenSet
from src.settings.service import UserSettingsService, invalidate_user_settings_cache


class FakeCursor:
    def __init__(self, storage):
        self.storage = storage
        self._last_result = None

    def execute(self, query, params=None):
        query_str = " ".join(query.split()).lower()
        if query_str.startswith("select"):
            owner_id = params[0] if params else "USER_1"
            row = self.storage.get(owner_id)
            self._last_result = row
        elif query_str.startswith("insert into knowledge_user_settings"):
            # owner_id, openai_key, storage_type, s3_endpoint, s3_bucket, access_key, secret_key,
            # llm_auth_type, oauth_access, oauth_refresh, oauth_expires_at, embedding_key
            owner_id = params[0]
            openai_key = params[1]
            storage_type = params[2]
            s3_endpoint = params[3]
            s3_bucket = params[4]
            access_key = params[5]
            secret_key = params[6]
            llm_auth_type = params[7]
            oauth_access = params[8]
            oauth_refresh = params[9]
            oauth_expires_at = params[10]
            embedding_key = params[11]

            self.storage[owner_id] = (
                openai_key,
                storage_type,
                s3_endpoint,
                s3_bucket,
                access_key,
                secret_key,
                None,  # updated_at
                llm_auth_type,
                oauth_access,
                oauth_refresh,
                oauth_expires_at,
                embedding_key,
            )

    def fetchone(self):
        return self._last_result

    def fetchall(self):
        return [(v,) for v in range(1, 25)]



class FakeDbManager:
    def __init__(self):
        self.storage = {}

    def cursor(self):
        from contextlib import contextmanager

        @contextmanager
        def _cursor_ctx():
            yield FakeCursor(self.storage)

        return _cursor_ctx()

    def transaction(self):
        from contextlib import contextmanager

        @contextmanager
        def _tx_ctx():
            yield FakeCursor(self.storage)

        return _tx_ctx()

    def close(self):
        pass



class LLMAuthSettingsTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDbManager()
        self.service = UserSettingsService(db_manager=self.db)
        os.environ["SETTINGS_ENCRYPTION_KEY"] = "test-encryption-master-key-1234567890"
        settings_service_module._runtime_config_cache.clear()

    def tearDown(self):
        settings_service_module._runtime_config_cache.clear()

    def test_save_and_switch_auth_type(self):
        # 1. Initially save with API Key
        saved = self.service.save("USER_1", {
            "llm_auth_type": "api_key",
            "openai_api_key": "sk-proj-test-1234",
            "embedding_api_key": "sk-proj-embed-5678",
            "storage_type": "s3",
            "s3_endpoint_url": "https://s3.example.com",
            "s3_bucket_name": "my-bucket",
            "s3_access_key_id": "access-key",
            "s3_secret_access_key": "secret-key",
        })

        self.assertEqual(saved["llm_auth_type"], "api_key")
        self.assertTrue(saved["openai_configured"])
        self.assertFalse(saved["openai_oauth_configured"])
        self.assertTrue(saved["embedding_configured"])

        runtime = self.service.get_runtime_config("USER_1")
        self.assertEqual(runtime["llm_auth_type"], "api_key")
        self.assertEqual(runtime["openai_api_key"], "sk-proj-test-1234")
        self.assertEqual(runtime["llm_bearer_token"], "sk-proj-test-1234")
        self.assertEqual(runtime["embedding_api_key"], "sk-proj-embed-5678")

        # 2. Save OAuth tokens (e.g. from ChatGPT Plus OAuth login)
        oauth_saved = self.service.save_openai_oauth_tokens(
            "USER_1",
            access_token="oauth-access-token-123",
            refresh_token="oauth-refresh-token-456",
            expires_at=int(time.time()) + 3600,
        )

        self.assertEqual(oauth_saved["llm_auth_type"], "openai_oauth")
        self.assertTrue(oauth_saved["openai_oauth_configured"])

        invalidate_user_settings_cache("USER_1")
        runtime_oauth = self.service.get_runtime_config("USER_1")
        self.assertEqual(runtime_oauth["llm_auth_type"], "openai_oauth")
        self.assertEqual(runtime_oauth["llm_bearer_token"], "oauth-access-token-123")
        # Embedding API key is preserved
        self.assertEqual(runtime_oauth["embedding_api_key"], "sk-proj-embed-5678")

        # 3. Switch back to api_key mode
        switched = self.service.switch_llm_auth_type("USER_1", "api_key")
        self.assertEqual(switched["llm_auth_type"], "api_key")

        invalidate_user_settings_cache("USER_1")
        runtime_switched = self.service.get_runtime_config("USER_1")
        self.assertEqual(runtime_switched["llm_auth_type"], "api_key")
        self.assertEqual(runtime_switched["llm_bearer_token"], "sk-proj-test-1234")

    @patch("src.settings.openai_oauth.OpenAIOAuthClient.refresh_access_token_sync")
    def test_auto_token_refresh_when_expired(self, mock_refresh_sync):
        mock_refresh_sync.return_value = OpenAITokenSet(
            access_token="refreshed-access-token-999",
            refresh_token="refreshed-refresh-token-888",
            expires_at=int(time.time()) + 3600,
        )

        # Save an OAuth token that expires in 10 seconds (near expiry < 60s)
        self.service.save("USER_1", {
            "llm_auth_type": "openai_oauth",
            "openai_oauth_access_token": "expired-access-token",
            "openai_oauth_refresh_token": "valid-refresh-token",
            "openai_oauth_expires_at": int(time.time()) + 10,
            "storage_type": "s3",
            "s3_endpoint_url": "https://s3.example.com",
            "s3_bucket_name": "my-bucket",
            "s3_access_key_id": "access-key",
            "s3_secret_access_key": "secret-key",
        })

        invalidate_user_settings_cache("USER_1")
        runtime = self.service.get_runtime_config("USER_1")

        mock_refresh_sync.assert_called_once_with("valid-refresh-token")
        self.assertEqual(runtime["openai_oauth_access_token"], "refreshed-access-token-999")
        self.assertEqual(runtime["llm_bearer_token"], "refreshed-access-token-999")

    @patch("openai.OpenAI")
    def test_embedding_service_prioritizes_embedding_api_key(self, mock_openai):
        # When embedding_api_key is set in config
        token = current_user_config.set({
            "user_id": "USER_1",
            "llm_auth_type": "openai_oauth",
            "llm_bearer_token": "oauth-chatgpt-token",
            "openai_api_key": "general-api-key",
            "embedding_api_key": "dedicated-embedding-key",
        })
        try:
            service = OpenAIEmbeddingService()
            mock_openai.assert_called_with(api_key="dedicated-embedding-key")
        finally:
            current_user_config.reset(token)

    @patch("openai.OpenAI")
    def test_embedding_service_falls_back_to_openai_api_key(self, mock_openai):
        # When embedding_api_key is not set, falls back to openai_api_key
        token = current_user_config.set({
            "user_id": "USER_1",
            "openai_api_key": "fallback-api-key",
        })
        try:
            service = OpenAIEmbeddingService()
            mock_openai.assert_called_with(api_key="fallback-api-key")
        finally:
            current_user_config.reset(token)

    @patch("openai.OpenAI")
    def test_document_expander_uses_llm_bearer_token(self, mock_openai):
        token = current_user_config.set({
            "user_id": "USER_1",
            "llm_bearer_token": "oauth-bearer-token",
        })
        try:
            with patch("src.core.config.DOCUMENT_EXPANSION_ENABLED", True):
                expander = create_document_expander()
                self.assertTrue(expander.enabled)
                mock_openai.assert_called_with(api_key="oauth-bearer-token")
        finally:
            current_user_config.reset(token)


if __name__ == "__main__":
    unittest.main()
