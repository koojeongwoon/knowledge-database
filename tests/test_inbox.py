import json
import unittest
from unittest.mock import Mock, patch

from src.settings.inbox import InboxService, MAX_UPLOAD_BYTES, _safe_filename, _validate_url


class InboxServiceTests(unittest.TestCase):
    def test_link_validation_allows_http_only_and_rejects_credentials(self):
        self.assertEqual(_validate_url("https://example.com/article"), "https://example.com/article")
        for url in ("file:///etc/passwd", "javascript:alert(1)", "https://user:pass@example.com"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                _validate_url(url)

    def test_filename_is_reduced_to_a_safe_basename(self):
        self.assertEqual(_safe_filename("../../내 문서?.pdf"), "내 문서_.pdf")

    @patch.object(InboxService, "_storage")
    @patch("src.settings.inbox.uuid.uuid4")
    def test_file_content_is_written_before_visible_metadata(self, uuid4, storage_method):
        uuid4.return_value.hex = "a" * 32
        storage = storage_method.return_value

        item = InboxService("USER_1").add_file("notes.txt", b"hello", "text/plain", "study")

        self.assertEqual(item["storage_path"], f"inbox/{'a' * 32}/content/notes.txt")
        self.assertEqual(storage.method_calls[0][0], "write_bytes")
        self.assertEqual(storage.method_calls[1][0], "write_text")
        metadata = json.loads(storage.write_text.call_args.args[1])
        self.assertEqual(metadata["type"], "file")
        self.assertEqual(metadata["size"], 5)

    @patch.object(InboxService, "_storage")
    def test_oversized_file_is_rejected_before_storage_access(self, storage):
        with self.assertRaises(ValueError):
            InboxService("USER_1").add_file("large.bin", b"x" * (MAX_UPLOAD_BYTES + 1))
        storage.assert_not_called()

    @patch.object(InboxService, "_storage")
    def test_list_reads_only_completed_metadata_items(self, storage_method):
        storage = storage_method.return_value
        storage.list_files.return_value = ["inbox/a/metadata.json", "inbox/b/metadata.json"]
        storage.read_text.side_effect = [
            json.dumps({"id": "a", "type": "link", "created_at": "2026-01-02", "title": "A"}),
            "not-json",
        ]
        items = InboxService("USER_1").list_items()
        self.assertEqual([item["id"] for item in items], ["a"])

    @patch.object(InboxService, "_storage")
    @patch("src.settings.inbox.uuid.uuid4")
    def test_markdown_is_written_before_unverified_metadata(self, uuid4, storage_method):
        uuid4.return_value.hex = "b" * 32
        storage = storage_method.return_value

        item = InboxService("USER_1").add_markdown(
            title="OAuth 문서",
            content="## 핵심\nPKCE",
            source_kind="chat_attachment",
            original_filename="oauth.pdf",
            media_type="application/pdf",
            extraction_complete=False,
            warnings=["표는 생략됨"],
        )

        self.assertEqual(item["subtype"], "derived_markdown")
        self.assertEqual(item["authority"], "unverified")
        self.assertFalse(item["indexed"])
        self.assertEqual(storage.method_calls[0][0], "write_text")
        self.assertEqual(storage.method_calls[1][0], "write_text")
        metadata = json.loads(storage.write_text.call_args.args[1])
        self.assertEqual(metadata["source"]["original_filename"], "oauth.pdf")
        self.assertFalse(metadata["extraction"]["complete"])

    @patch.object(InboxService, "_storage")
    def test_external_link_markdown_requires_original_url(self, storage):
        with self.assertRaisesRegex(ValueError, "원본 URL"):
            InboxService("USER_1").add_markdown(
                title="링크 정리",
                content="## 핵심",
                source_kind="external_link",
            )
        storage.assert_not_called()

    @patch.object(InboxService, "_storage")
    def test_read_for_learning_returns_markdown_content(self, storage_method):
        item_id = "c" * 32
        storage = storage_method.return_value
        storage.exists.return_value = True
        storage.read_text.side_effect = [
            json.dumps({
                "id": item_id,
                "type": "file",
                "subtype": "derived_markdown",
                "filename": "study.md",
                "content_type": "text/markdown",
                "size": 12,
                "storage_path": f"inbox/{item_id}/content/study.md",
            }),
            "## 학습 내용",
        ]

        result = InboxService("USER_1").read_for_learning(item_id)

        self.assertEqual(result["content_status"], "available")
        self.assertEqual(result["content"], "## 학습 내용")

    @patch.object(InboxService, "_storage")
    def test_read_for_learning_does_not_decode_binary_file(self, storage_method):
        item_id = "d" * 32
        storage = storage_method.return_value
        storage.exists.return_value = True
        storage.read_text.return_value = json.dumps({
            "id": item_id,
            "type": "file",
            "filename": "study.pdf",
            "content_type": "application/pdf",
            "size": 100,
            "storage_path": f"inbox/{item_id}/content/study.pdf",
        })

        result = InboxService("USER_1").read_for_learning(item_id)

        self.assertEqual(result["content_status"], "unsupported")
        self.assertIsNone(result["content"])
        self.assertEqual(storage.read_text.call_count, 1)


if __name__ == "__main__":
    unittest.main()
