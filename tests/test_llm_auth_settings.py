import unittest
from contextlib import contextmanager
from unittest.mock import patch

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
                row = (row[7], row[6])
            elif row and "select llm_model_name from" in normalized:
                row = (row[7],)
            self._last_result = row
            return
        if normalized.startswith("insert into knowledge_user_settings"):
            owner_id = params[0]
            self.storage[owner_id] = (
                params[1], params[2], params[3], params[4], params[5],
                None, params[6], params[7],
            )

    def fetchone(self):
        return self._last_result

    def fetchall(self):
        return [(version,) for version in range(1, 27)]


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
        self.encryption_key = patch.dict(
            "os.environ", {"SETTINGS_ENCRYPTION_KEY": "test-encryption-master-key-1234567890"}
        )
        self.encryption_key.start()
        self.linked = patch(
            "src.indexing.infrastructure.broker_chat.BrokerStructuredChat.linked",
            return_value=False,
        )
        self.linked.start()

    def tearDown(self):
        self.linked.stop()
        self.encryption_key.stop()

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
        runtime = self.service.get_storage_runtime_config("USER_1")
        self.assertEqual(runtime["storage"]["s3_bucket_name"], "my-bucket")
        self.assertFalse(any("openai" in key or "embedding" in key for key in runtime))

    def test_provider_keys_are_never_stored_or_returned(self):
        with self.assertRaisesRegex(ValueError, "Credential Broker"):
            self.service.save("USER_1", {
                "openai_api_key": "general-api-key",
                "embedding_api_key": "dedicated-embedding-key",
            })


if __name__ == "__main__":
    unittest.main()
