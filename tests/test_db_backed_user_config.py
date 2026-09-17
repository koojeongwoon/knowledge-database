import json
import unittest
from unittest.mock import patch

from src.api.agent_tool import retrieve_wiki_knowledge
from src.api.middleware import _request_user_config
from src.api.decorators import with_fresh_user_settings
from src.core.config import current_user_config
from src.core.storage import factory as storage_factory
from src.settings.service import UserSettingsService


class DatabaseBackedUserConfigTests(unittest.TestCase):
    def tearDown(self):
        storage_factory._storage_instances.clear()

    def test_authenticated_user_credential_headers_are_ignored(self):
        config = _request_user_config({
            "authorization": "Bearer app-token",
            "x-openai-api-key": "header-openai-key",
            "x-storage-type": "s3",
            "x-s3-access-key-id": "header-access-key",
            "x-s3-secret-access-key": "header-secret-key",
        }, "USER_1")

        self.assertEqual(config, {"user_id": "USER_1"})

    @patch("src.api.agent_tool.RetrievalApiHandler.search", return_value="result")
    @patch("src.settings.service.UserSettingsService")
    def test_retrieval_uses_verified_owner_for_search_and_audit_identity(
        self, settings_service_class, search
    ):
        settings_service_class.return_value.get_storage_runtime_config.return_value = {}
        token = current_user_config.set({"user_id": "USER_1"})
        try:
            response = json.loads(retrieve_wiki_knowledge("query", limit=3))
        finally:
            current_user_config.reset(token)

        self.assertTrue(response["success"])
        search.assert_called_once_with("query", 3, "USER_1", "USER_1")

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
        service.get_storage_runtime_config.side_effect = [
            {"storage": {"storage_type": "s3", "s3_bucket_name": "old"}},
            {"storage": {"storage_type": "s3", "s3_bucket_name": "new"}},
        ]

        @with_fresh_user_settings
        def current_bucket():
            return current_user_config.get()["storage"]["s3_bucket_name"]

        token = current_user_config.set({"api_key": "app-token", "user_id": "USER_1"})
        try:
            self.assertEqual(current_bucket(), "old")
            self.assertEqual(current_bucket(), "new")
            self.assertNotIn("storage", current_user_config.get())
        finally:
            current_user_config.reset(token)

        self.assertEqual(service.get_storage_runtime_config.call_count, 2)

if __name__ == "__main__":
    unittest.main()
