import os
import unittest
from unittest.mock import patch

from src.api.middleware import _request_user_config
from src.api.decorators import with_fresh_user_settings
from src.core.config import current_user_config
from src.core.storage import factory as storage_factory
from src.indexing.domain.embedding import OpenAIEmbeddingService
from src.settings import service as settings_service_module
from src.settings.service import UserSettingsService, invalidate_user_settings_cache


class DatabaseBackedUserConfigTests(unittest.TestCase):
    def tearDown(self):
        storage_factory._storage_instances.clear()
        settings_service_module._runtime_config_cache.clear()

    def test_authenticated_user_credential_headers_are_ignored(self):
        config = _request_user_config({
            "authorization": "Bearer app-token",
            "x-openai-api-key": "header-openai-key",
            "x-storage-type": "s3",
            "x-s3-access-key-id": "header-access-key",
            "x-s3-secret-access-key": "header-secret-key",
        }, "USER_1")

        self.assertEqual(config, {"api_key": "app-token", "user_id": "USER_1"})

    def test_authenticated_user_without_db_storage_fails_closed(self):
        token = current_user_config.set({"api_key": "app-token", "user_id": "USER_1"})
        try:
            with self.assertRaisesRegex(ConnectionError, "S3/R2 저장소가 DB에 설정되지"):
                storage_factory.StorageManager()
        finally:
            current_user_config.reset(token)

    def test_storage_without_owner_fails_closed(self):
        token = current_user_config.set({})
        try:
            with self.assertRaisesRegex(ConnectionError, "owner_id"):
                storage_factory.StorageManager()
        finally:
            current_user_config.reset(token)

    @patch("src.settings.service.UserSettingsService")
    def test_existing_session_refreshes_settings_for_each_tool_call(self, settings_service_class):
        service = settings_service_class.return_value
        service.get_runtime_config.side_effect = [
            {"openai_api_key": "old-key", "storage": {"storage_type": "s3"}},
            {"openai_api_key": "new-key", "storage": {"storage_type": "s3"}},
        ]

        @with_fresh_user_settings
        def current_openai_key():
            return current_user_config.get()["openai_api_key"]

        token = current_user_config.set({"api_key": "app-token", "user_id": "USER_1"})
        try:
            self.assertEqual(current_openai_key(), "old-key")
            self.assertEqual(current_openai_key(), "new-key")
            self.assertNotIn("openai_api_key", current_user_config.get())
        finally:
            current_user_config.reset(token)

        self.assertEqual(service.get_runtime_config.call_count, 2)

    def test_runtime_settings_use_cache_until_invalidated(self):
        service = UserSettingsService(db_manager=unittest.mock.Mock())
        old_row = ("encrypted-old", "s3", "endpoint", "bucket", "access", "secret", None)
        new_row = ("encrypted-new", "s3", "endpoint", "bucket", "access", "secret", None)

        with patch.object(service, "_get_row", side_effect=[old_row, new_row]) as get_row, \
             patch.object(service, "_decrypt", side_effect=lambda value: value):
            first = service.get_runtime_config("USER_1")
            cached = service.get_runtime_config("USER_1")
            self.assertEqual(first["openai_api_key"], "encrypted-old")
            self.assertEqual(cached["openai_api_key"], "encrypted-old")
            self.assertEqual(get_row.call_count, 1)

            invalidate_user_settings_cache("USER_1")
            refreshed = service.get_runtime_config("USER_1")
            self.assertEqual(refreshed["openai_api_key"], "encrypted-new")
            self.assertEqual(get_row.call_count, 2)

    @patch("openai.OpenAI")
    def test_authenticated_user_without_db_openai_key_does_not_use_environment(self, openai_client):
        previous = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "environment-key"
        token = current_user_config.set({"user_id": "USER_1"})
        try:
            with self.assertRaisesRegex(ValueError, "설정되지 않았거나"):
                OpenAIEmbeddingService()
        finally:
            current_user_config.reset(token)
            if previous is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = previous

        openai_client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
