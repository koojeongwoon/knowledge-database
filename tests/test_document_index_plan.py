from dataclasses import FrozenInstanceError

import pytest

from src.indexing.domain.document_plan import plan_document_edges, resolve_document_metadata


@pytest.mark.parametrize(
    ("path", "expected_type"),
    [
        ("qa/note.md", "QAJournal"),
        ("topics/tdd.md", "TopicSummary"),
        ("assets/source.md", "Unknown"),
    ],
)
def test_resolves_document_defaults_from_path(path: str, expected_type: str) -> None:
    metadata = resolve_document_metadata(path, {})

    assert metadata.doc_type == expected_type
    assert metadata.title == path.rsplit("/", 1)[-1].removesuffix(".md")
    assert metadata.description == ""
    assert metadata.tags == ()


def test_explicit_metadata_overrides_path_defaults_and_is_immutable() -> None:
    metadata = resolve_document_metadata(
        "qa/note.md",
        {"type": "Guide", "title": "Title", "description": "Desc", "tags": ["ddd"]},
    )

    assert metadata.doc_type == "Guide"
    assert metadata.tags == ("ddd",)
    with pytest.raises(FrozenInstanceError):
        metadata.title = "Changed"


def test_plans_weighted_edges_without_mutating_topic_metadata() -> None:
    topic_metadata = {
        "target": {"source_path": "book.md", "type": "QAJournal"}
    }

    edges = plan_document_edges(
        source_path="qa/source.md",
        target_topics=("Target", "Manual"),
        source_metadata={"source_path": "book.md", "type": "QAJournal"},
        topic_metadata=topic_metadata,
        custom_relations=({"link": "[[Manual]]", "weight": 7.0},),
    )

    assert tuple((edge.target_topic, edge.weight) for edge in edges) == (
        ("Target", 4.0),
        ("Manual", 7.0),
    )
    assert topic_metadata["target"]["source_path"] == "book.md"
