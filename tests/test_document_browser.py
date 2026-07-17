import unittest
from unittest.mock import Mock, patch

from src.settings.documents import DocumentBrowserService, normalize_document_path


class DocumentBrowserTests(unittest.TestCase):
    def test_only_markdown_under_qa_and_topics_is_allowed(self):
        self.assertEqual(normalize_document_path("qa/2026/note.md"), "qa/2026/note.md")
        self.assertEqual(normalize_document_path("topics/Development/wiki.md"), "topics/Development/wiki.md")
        for path in ("../secret.md", "qa/../../secret.md", "/etc/passwd", "assets/file.md", "qa/file.txt"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                normalize_document_path(path)

    @patch("src.settings.documents.StorageManager")
    @patch("src.settings.documents.UserSettingsService")
    def test_storage_is_built_from_the_requested_owners_settings(self, settings_class, storage_manager):
        settings = settings_class.return_value
        settings.get_runtime_config.return_value = {
            "storage": {"storage_type": "s3", "s3_endpoint_url": "https://owner-1.example"},
        }
        storage = Mock()
        storage.list_files.side_effect = [
            ["qa/one.md", "qa/not-markdown.txt"],
            ["topics/Development/two.md"],
        ]
        storage_manager.return_value = storage

        result = DocumentBrowserService("USER_1").list_documents()

        settings.get_runtime_config.assert_called_once_with("USER_1")
        storage_manager.assert_called_once_with(user_id="USER_1")
        self.assertEqual({item["path"] for item in result}, {"qa/one.md", "topics/Development/two.md"})

    @patch.object(DocumentBrowserService, "_storage")
    def test_read_rejects_traversal_before_storage_access(self, storage):
        with self.assertRaises(ValueError):
            DocumentBrowserService("USER_1").read_document("qa/../../USER_2/private.md")
        storage.assert_not_called()


if __name__ == "__main__":
    unittest.main()
