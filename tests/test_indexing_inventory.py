from unittest.mock import MagicMock

from src.indexing.application.inventory_collector import IndexingInventoryCollector
from src.indexing.domain.inventory import (
    InventoryDocument,
    TopicSyncCommand,
    build_indexing_inventory,
)


def test_builds_deterministic_hash_alias_and_topic_sync_inventory() -> None:
    inventory = build_indexing_inventory(
        (
            InventoryDocument(
                file_path="topics/Development/tdd.md",
                content_hash="topic-hash",
                title="Test Driven Development",
                source_path="book.md",
                doc_type="TopicSummary",
            ),
            InventoryDocument(
                file_path="qa/note.md",
                content_hash="qa-hash",
                title="Note",
                source_path=None,
                doc_type="QAJournal",
            ),
        )
    )

    assert inventory.local_hashes == {
        "qa/note.md": "qa-hash",
        "topics/Development/tdd.md": "topic-hash",
    }
    assert inventory.topic_metadata["tdd"].source_path == "book.md"
    assert inventory.topic_metadata["test driven development"].doc_type == "TopicSummary"
    assert inventory.topic_sync_commands == (
        TopicSyncCommand("tdd", "Development", "topics/Development/tdd.md"),
    )


def test_collector_reads_and_parses_each_file_once() -> None:
    storage = MagicMock()
    storage.read_text.side_effect = lambda path: f"content:{path}"
    parser = MagicMock(
        side_effect=lambda content, path: {
            "content_hash": f"hash:{path}",
            "frontmatter": {"title": path, "type": "QAJournal"},
        }
    )

    inventory = IndexingInventoryCollector(storage, parser).collect(
        ("qa/b.md", "qa/a.md")
    )

    assert tuple(inventory.local_hashes) == ("qa/a.md", "qa/b.md")
    assert storage.read_text.call_count == 2
    assert parser.call_count == 2
