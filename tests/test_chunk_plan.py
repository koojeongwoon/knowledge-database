import pytest

from src.indexing.domain.chunk_plan import materialize_chunk_records, plan_document_chunks


def test_plans_reused_and_pending_chunks_without_side_effects() -> None:
    existing_embeddings = {"reused": [0.1, 0.2]}
    parents = (
        {"header": "Intro", "content": "reused"},
        {"header": "Details", "content": "new content"},
    )

    plan = plan_document_chunks(
        file_path="qa/example.md",
        doc_type="QAJournal",
        title="Example",
        description="Description",
        tags=("tdd",),
        raw_frontmatter={"visibility": "private"},
        content_hash="hash",
        parents=parents,
        existing_embeddings=existing_embeddings,
        expansion_enabled=True,
        chunker=lambda content: (content,),
    )

    assert tuple(item.chunk.content for item in plan.reused) == ("reused",)
    assert plan.reused[0].embedding == (0.1, 0.2)
    assert tuple(item.chunk.content for item in plan.pending) == ("new content",)
    assert plan.embedding_texts == (
        "Title: Example > Details\nDescription: Description\n\nContent:\nnew content",
    )
    assert plan.expansion_tasks == ((0, "new content"),)
    assert existing_embeddings == {"reused": [0.1, 0.2]}


def test_does_not_create_expansion_tasks_when_disabled() -> None:
    plan = plan_document_chunks(
        file_path="topics/example.md",
        doc_type="TopicSummary",
        title="Example",
        description="",
        tags=(),
        raw_frontmatter={},
        content_hash="hash",
        parents=({"header": "Intro", "content": "new"},),
        existing_embeddings={},
        expansion_enabled=False,
        chunker=lambda content: (content,),
    )

    assert plan.expansion_tasks == ()


def test_materialization_rejects_missing_embedding_results() -> None:
    plan = plan_document_chunks(
        file_path="qa/example.md",
        doc_type="QAJournal",
        title="Example",
        description="",
        tags=(),
        raw_frontmatter={},
        content_hash="hash",
        parents=({"header": "Intro", "content": "new"},),
        existing_embeddings={},
        expansion_enabled=False,
        chunker=lambda content: (content,),
    )

    with pytest.raises(ValueError, match="embedding count"):
        materialize_chunk_records(plan, ())
