import os
import unittest
from unittest.mock import patch

from src.api.middleware import _request_user_config
from src.core.config import current_user_config
from src.core.storage import factory as storage_factory
from src.indexing.domain.embedding import OpenAIEmbeddingService


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
