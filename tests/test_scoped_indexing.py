import unittest
from unittest.mock import MagicMock, patch

import src.indexing.application.service as indexing_service
from src.indexing.application.inventory_collector import IndexingInventoryCollector
from src.indexing.application.service import WikiIndexer
from src.indexing.domain.execution import (
    FileIndexingBatchResult,
    FileIndexingOutcome,
    FileIndexingResult,
)


class ScopedIndexingTests(unittest.TestCase):
    def test_constructor_uses_injected_storage_and_repository_factory(self):
        db_manager = MagicMock()
        storage = MagicMock()
        repository = MagicMock()
        repository_factory = MagicMock(return_value=repository)
        file_executor = MagicMock()
        expansion_executor = MagicMock()
        inventory_collector = MagicMock()
        ontology_shadow_factory = MagicMock()
        observer = MagicMock()

        indexer = WikiIndexer(
            db_manager=db_manager,
            embedding_service=MagicMock(),
            storage=storage,
            repository_factory=repository_factory,
            file_executor=file_executor,
            expansion_executor=expansion_executor,
            inventory_collector=inventory_collector,
            ontology_shadow_factory=ontology_shadow_factory,
            observer=observer,
        )

        self.assertIs(indexer.storage, storage)
        self.assertIs(indexer.repository, repository)
        self.assertIs(indexer.file_executor, file_executor)
        self.assertIs(indexer.expansion_executor, expansion_executor)
        self.assertIs(indexer.inventory_collector, inventory_collector)
        self.assertIs(indexer.ontology_shadow_factory, ontology_shadow_factory)
        self.assertIs(indexer.observer, observer)
        repository_factory.assert_called_once_with(db_manager)

    def setUp(self):
        self.indexer = WikiIndexer.__new__(WikiIndexer)
        self.indexer.target_dirs = ["qa", "topics", "assets", "attachments"]
        self.indexer.storage = MagicMock()
        self.indexer.repository = MagicMock()
        self.indexer.repository_factory = MagicMock(return_value=self.indexer.repository)
        self.indexer.file_executor = MagicMock(max_workers=4)
        self.indexer.expansion_executor = MagicMock(enabled=False)
        self.indexer.expansion_executor.expand.side_effect = (
            lambda **kwargs: tuple(kwargs["embedding_texts"])
        )
        self.indexer.inventory_collector = IndexingInventoryCollector(
            self.indexer.storage,
            lambda content, path: indexing_service.parse_markdown_content(content, path),
        )
        self.indexer.db_manager = MagicMock()
        self.indexer.observer = MagicMock()

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
        shadow = MagicMock()
        self.indexer.ontology_shadow_factory = lambda _db: shadow

        stats = self.indexer.run_indexing(file_paths=["topics/removed.md"])

        self.indexer.repository.delete_document.assert_called_once_with("topics/removed.md")
        self.indexer.repository.get_all_file_hashes.assert_not_called()
        shadow.delete_safely.assert_called_once_with("topics/removed.md")
        self.assertEqual(stats["deleted"], 1)

    def test_rejects_path_outside_knowledge_directories(self):
        with self.assertRaises(ValueError):
            self.indexer.run_indexing(file_paths=["../malware-scaner/README.md"])

    @patch("src.indexing.application.service.parse_markdown_content")
    def test_parse_failure_aborts_before_deleting_existing_index(self, parse_markdown):
        self.indexer.storage.exists.return_value = True
        self.indexer.storage.read_text.return_value = "invalid document"
        self.indexer.repository.get_file_hashes.return_value = {
            "qa/broken.md": "existing-hash"
        }
        parse_markdown.side_effect = ValueError("invalid frontmatter")

        with self.assertRaisesRegex(ValueError, "invalid frontmatter"):
            self.indexer.run_indexing(file_paths=["qa/broken.md"])

        self.indexer.repository.delete_document.assert_not_called()
        self.indexer.repository.replace_document.assert_not_called()

    @patch("src.indexing.application.service.parse_markdown_content")
    def test_parallel_batch_result_updates_stats_without_string_protocol(self, parse_markdown):
        files = [f"qa/{index:02d}.md" for index in range(11)]
        self.indexer.storage.list_files.side_effect = (
            lambda target, _pattern: files if target == "qa" else []
        )
        self.indexer.storage.read_text.return_value = "body"
        parse_markdown.return_value = {
            "content_hash": "new-hash",
            "frontmatter": {},
            "body": "body",
        }
        self.indexer.repository.get_all_file_hashes.return_value = {}
        items = tuple(
            FileIndexingResult(path, FileIndexingOutcome.CREATED)
            for path in files[:-1]
        ) + (
            FileIndexingResult(
                files[-1],
                FileIndexingOutcome.FAILED,
                error_message="embedding unavailable",
            ),
        )
        self.indexer.file_executor.execute.return_value = FileIndexingBatchResult(items)

        stats = self.indexer.run_indexing()

        self.assertEqual(stats["created"], 10)
        self.assertEqual(stats["updated"], 0)
        self.indexer.file_executor.execute.assert_called_once()
        self.assertEqual(
            tuple(self.indexer.file_executor.execute.call_args.kwargs["targets"]),
            tuple((path, True) for path in files),
        )

    @patch("src.indexing.application.service.parse_markdown_content")
    def test_topic_sync_failure_does_not_discard_collected_hash(self, parse_markdown):
        path = "topics/Development/tdd.md"
        self.indexer.storage.list_files.side_effect = (
            lambda target, _pattern: [path] if target == "topics" else []
        )
        self.indexer.storage.read_text.return_value = "body"
        parse_markdown.return_value = {
            "content_hash": "same-hash",
            "frontmatter": {"title": "TDD", "type": "TopicSummary"},
            "body": "body",
        }
        self.indexer.repository.upsert_topic.side_effect = RuntimeError("db unavailable")
        self.indexer.repository.get_all_file_hashes.return_value = {
            path: "same-hash"
        }

        stats = self.indexer.run_indexing()

        self.assertEqual(stats["skipped"], 1)
        self.indexer.repository.delete_document.assert_not_called()
        self.assertEqual(parse_markdown.call_count, 1)

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
    ):
        parse_markdown.return_value = {
            "content_hash": "new-hash",
            "frontmatter": {},
            "body": "changed chunk",
        }
        repo = self.indexer.repository
        repo.get_document_chunks.return_value = []
        self.indexer.storage.read_text.return_value = "changed chunk"
        self.indexer.embedding_service = MagicMock()
        self.indexer.embedding_service.embed_batch.side_effect = RuntimeError("embedding failed")
        self.indexer.topic_metadata = {}

        with self.assertRaises(RuntimeError):
            self.indexer._process_single_file("qa/changed.md", False, self.indexer.db_manager)

        repo.delete_document.assert_not_called()
        repo.replace_document.assert_not_called()

    @patch("src.wiki.domain.parser.chunk_text", return_value=["changed chunk"])
    @patch(
        "src.wiki.domain.parser.split_markdown_by_headers",
        return_value=[{"header": "Intro", "content": "changed chunk"}],
    )
    @patch("src.wiki.domain.parser.extract_wiki_links", return_value=[])
    @patch("src.indexing.application.service.parse_markdown_content")
    def test_ontology_shadow_runs_after_direct_replace_and_cannot_fail_indexing(
        self,
        parse_markdown,
        _extract_links,
        _split_headers,
        _chunk_text,
    ):
        frontmatter = {"ontology": {"concepts": [{"id": "a", "name": "A"}]}}
        parse_markdown.return_value = {
            "content_hash": "new-hash",
            "frontmatter": frontmatter,
            "body": "changed chunk",
        }
        repo = self.indexer.repository
        repo.get_document_chunks.return_value = []
        self.indexer.storage.read_text.return_value = "changed chunk"
        self.indexer.embedding_service = MagicMock()
        self.indexer.embedding_service.embed_batch.return_value = [[0.1, 0.2]]
        self.indexer.topic_metadata = {}
        order = []
        repo.replace_document.side_effect = lambda *_args: order.append("direct")
        shadow = MagicMock()
        def fail_shadow(*_args):
            order.append("ontology")
            raise RuntimeError("shadow wiring failed")
        shadow.process_safely.side_effect = fail_shadow
        self.indexer.ontology_shadow_factory = lambda _db: shadow

        result = self.indexer._process_single_file(
            "qa/changed.md", False, self.indexer.db_manager,
        )

        self.assertIs(result, FileIndexingOutcome.UPDATED)
        repo.replace_document.assert_called_once()
        shadow.process_safely.assert_called_once_with("qa/changed.md", frontmatter, "new-hash")
        self.assertEqual(order, ["direct", "ontology"])


if __name__ == "__main__":
    unittest.main()
