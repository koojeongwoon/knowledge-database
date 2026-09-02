import pytest
from src.indexing.domain.chunk_plan import (
    plan_document_chunks,
    materialize_chunk_records,
)
from src.api.dto import IndexingSummaryDTO


def test_indexing_summary_dto():
    dto = IndexingSummaryDTO(created=5, updated=2, deleted=1, skipped=10)
    assert dto.created == 5
    assert dto.updated == 2
    assert dto.deleted == 1
    assert dto.skipped == 10
    assert dto.model_config.get("frozen") is True


def test_plan_document_chunks_empty_parents():
    plan = plan_document_chunks(
        file_path="empty.md",
        doc_type="wiki",
        title="Empty",
        description="",
        tags=(),
        raw_frontmatter={},
        content_hash="empty_hash",
        parents=(),
        existing_embeddings={},
        expansion_enabled=False,
        chunker=lambda content: (content,),
    )
    assert plan.reused == ()
    assert plan.pending == ()
    assert plan.embedding_texts == ()


def test_plan_document_chunks_large_document_splitting():
    parents = tuple({"header": f"Header {i}", "content": f"Content paragraph {i}"} for i in range(10))
    plan = plan_document_chunks(
        file_path="large_doc.md",
        doc_type="wiki",
        title="Large Doc",
        description="Many sections",
        tags=("large", "docs"),
        raw_frontmatter={},
        content_hash="large_hash",
        parents=parents,
        existing_embeddings={},
        expansion_enabled=True,
        chunker=lambda content: (content,),
    )
    assert len(plan.pending) == 10
    assert len(plan.embedding_texts) == 10
    assert len(plan.expansion_tasks) == 10

    # chunk records 재료화
    dummy_embeddings = [[0.1] * 10 for _ in range(10)]
    records = materialize_chunk_records(
        plan=plan,
        embeddings=dummy_embeddings,
    )
    assert len(records) == 10
    for idx, r in enumerate(records):
        assert r["chunk_index"] == idx
        assert r["title"] == f"Large Doc > Header {idx}"
        assert r["embedding"] == [0.1] * 10
