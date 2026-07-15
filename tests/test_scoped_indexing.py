import unittest
from unittest.mock import MagicMock, patch

from src.indexing.application.service import WikiIndexer


class ScopedIndexingTests(unittest.TestCase):
    def setUp(self):
        self.indexer = WikiIndexer.__new__(WikiIndexer)
        self.indexer.root_dir = "/vault"
        self.indexer.target_dirs = ["qa", "topics", "assets", "attachments"]
        self.indexer.storage = MagicMock()
        self.indexer.repository = MagicMock()
        self.indexer.db_manager = MagicMock()

    @patch("src.indexing.application.service.parse_markdown_content")
    def test_only_requested_existing_file_is_inspected(self, parse_markdown):
        parse_markdown.return_value = {
            "content_hash": "same-hash",
            "frontmatter": {},
        }
        self.indexer.storage.exists.return_value = True
        self.indexer.storage.read_text.return_value = "body"
        self.indexer.repository.get_file_hashes.return_value = {
            "qa/requested.md": "same-hash"
        }

        stats = self.indexer.run_indexing(file_paths=["qa/requested.md"])

        self.indexer.storage.list_files.assert_not_called()
        self.indexer.repository.get_all_file_hashes.assert_not_called()
        self.indexer.repository.get_file_hashes.assert_called_once_with(["qa/requested.md"])
        self.indexer.storage.read_text.assert_called_with("qa/requested.md")
        self.assertEqual(stats, {"created": 0, "updated": 0, "deleted": 0, "skipped": 1})

    def test_requested_missing_file_deletes_only_its_index(self):
        self.indexer.storage.exists.return_value = False
        self.indexer.repository.get_file_hashes.return_value = {
            "topics/removed.md": "old-hash"
        }

        stats = self.indexer.run_indexing(file_paths=["topics/removed.md"])

        self.indexer.repository.delete_document.assert_called_once_with("topics/removed.md")
        self.indexer.repository.get_all_file_hashes.assert_not_called()
        self.assertEqual(stats["deleted"], 1)

    def test_rejects_path_outside_knowledge_directories(self):
        with self.assertRaises(ValueError):
            self.indexer.run_indexing(file_paths=["../malware-scaner/README.md"])

    @patch("src.indexing.infrastructure.repository.IndexingRepository")
    @patch("src.wiki.domain.parser.chunk_text", return_value=["changed chunk"])
    @patch(
        "src.wiki.domain.parser.split_markdown_by_headers",
        return_value=[{"header": "Intro", "content": "changed chunk"}],
    )
    @patch("src.wiki.domain.parser.extract_wiki_links", return_value=[])
    @patch("src.indexing.application.service.parse_markdown_content")
    def test_embedding_failure_keeps_existing_index(
        self,
        parse_markdown,
        _extract_links,
        _split_headers,
        _chunk_text,
        repository_factory,
    ):
        parse_markdown.return_value = {
            "content_hash": "new-hash",
            "frontmatter": {},
            "body": "changed chunk",
        }
        repo = repository_factory.return_value
        repo.get_document_chunks.return_value = []
        self.indexer.storage.read_text.return_value = "changed chunk"
        self.indexer.embedding_service = MagicMock()
        self.indexer.embedding_service.embed_batch.side_effect = RuntimeError("embedding failed")
        self.indexer.openai_client = None
        self.indexer.topic_metadata = {}

        with self.assertRaises(RuntimeError):
            self.indexer._process_single_file("qa/changed.md", False, self.indexer.db_manager)

        repo.delete_document.assert_not_called()
        repo.replace_document.assert_not_called()


if __name__ == "__main__":
    unittest.main()
