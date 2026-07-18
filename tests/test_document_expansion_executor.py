from unittest.mock import MagicMock

from src.indexing.application.expansion_executor import DocumentExpansionExecutor


def test_applies_results_to_matching_embedding_texts() -> None:
    expander = MagicMock(enabled=True)
    expander.expand_batch.return_value = ((1, "expanded"),)
    executor = DocumentExpansionExecutor(expander, batch_size=5, max_workers=2)

    result = executor.expand(
        title="Document",
        description="Description",
        embedding_texts=("first", "second"),
        tasks=((0, "first content"), (1, "second content")),
    )

    assert result == (
        "first",
        "second\n\n[Expected Questions & Keywords]\nexpanded",
    )


def test_batch_failure_preserves_original_embedding_texts() -> None:
    expander = MagicMock(enabled=True)
    expander.expand_batch.side_effect = RuntimeError("provider unavailable")
    executor = DocumentExpansionExecutor(expander, batch_size=1, max_workers=2)

    result = executor.expand(
        title="Document",
        description="",
        embedding_texts=("first", "second"),
        tasks=((0, "first"), (1, "second")),
    )

    assert result == ("first", "second")
