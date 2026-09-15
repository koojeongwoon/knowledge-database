import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from src.settings import service as settings_service_module
from src.settings.service import UserSettingsService


class FakeCursor:
    def __init__(self, storage):
        self.storage = storage
        self._last_result = None

    def execute(self, query, params=None):
        normalized = " ".join(query.split()).lower()
        if normalized.startswith("select"):
            row = self.storage.get(params[0] if params else "USER_1")
            if row and "select llm_model_name,llm_auth_type" in normalized:
                row = (row[9], row[7])
            elif row and "select llm_model_name from" in normalized:
                row = (row[9],)
            self._last_result = row
            return
        if normalized.startswith("insert into knowledge_user_settings"):
            owner_id = params[0]
            self.storage[owner_id] = (
                params[1], params[2], params[3], params[4], params[5], params[6],
                None, params[7], params[8], params[9],
            )

    def fetchone(self):
        return self._last_result

    def fetchall(self):
        return [(version,) for version in range(1, 26)]


class FakeDbManager:
    def __init__(self):
        self.storage = {}

    @contextmanager
    def cursor(self):
        yield FakeCursor(self.storage)

    @contextmanager
    def transaction(self):
        yield FakeCursor(self.storage)

    def close(self):
        pass


class LLMAuthSettingsTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDbManager()
        self.service = UserSettingsService(db_manager=self.db)
        os.environ["SETTINGS_ENCRYPTION_KEY"] = "test-encryption-master-key-1234567890"
        os.environ.pop("EMBEDDING_PROVIDER", None)
        settings_service_module._runtime_config_cache.clear()
        self.linked = patch(
            "src.indexing.infrastructure.broker_chat.BrokerStructuredChat.linked",
            return_value=False,
        )
        self.linked.start()

    def tearDown(self):
        self.linked.stop()
        os.environ.pop("EMBEDDING_PROVIDER", None)
        settings_service_module._runtime_config_cache.clear()

    def test_saves_llm_preference_without_local_oauth_tokens(self):
        saved = self.service.save("USER_1", {
            "llm_auth_type": "openai_oauth",
            "llm_model_name": "gpt-5.6-luna",
            "storage_type": "s3",
            "s3_endpoint_url": "https://s3.example.com",
            "s3_bucket_name": "my-bucket",
            "s3_access_key_id": "access-key",
            "s3_secret_access_key": "secret-key",
        })

        self.assertEqual(saved["llm_auth_type"], "openai_oauth")
        self.assertEqual(self.service.get_llm_preferences("USER_1"), {
            "model": "gpt-5.6-luna",
            "auth_type": "openai_oauth",
        })
        runtime = self.service.get_runtime_config("USER_1")
        self.assertNotIn("openai_oauth_access_token", runtime)
        self.assertNotIn("openai_oauth_refresh_token", runtime)
        self.assertNotIn("llm_bearer_token", runtime)

    def test_broker_embedding_mode_does_not_decrypt_provider_keys(self):
        self.service.save("USER_1", {
            "openai_api_key": "general-api-key",
            "embedding_api_key": "dedicated-embedding-key",
            "storage_type": "s3",
            "s3_endpoint_url": "https://s3.example.com",
            "s3_bucket_name": "my-bucket",
            "s3_access_key_id": "access-key",
            "s3_secret_access_key": "secret-key",
        })
        os.environ["EMBEDDING_PROVIDER"] = "broker"
        settings_service_module._runtime_config_cache.clear()

        runtime = self.service.get_runtime_config("USER_1")

        self.assertIsNone(runtime["openai_api_key"])
        self.assertIsNone(runtime["embedding_api_key"])


if __name__ == "__main__":
    unittest.main()
