from types import SimpleNamespace
from unittest.mock import MagicMock

from src.indexing.domain.expansion import NoOpDocumentExpander
from src.indexing.infrastructure.expansion import OpenAIDocumentExpander


def test_noop_expander_is_disabled_and_returns_immutable_empty_result() -> None:
    expander = NoOpDocumentExpander()

    assert expander.enabled is False
    assert expander.expand_batch("title", "description", [(0, "content")]) == ()


def test_openai_expander_translates_structured_output_to_search_text() -> None:
    client = MagicMock()
    parsed = SimpleNamespace(
        expansions=[
            SimpleNamespace(
                chunk_index=2,
                questions=["어떻게 동작하나요?", "언제 사용하나요?"],
                keywords=["인덱싱", "indexing"],
            )
        ]
    )
    client.beta.chat.completions.parse.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed))]
    )

    result = OpenAIDocumentExpander(client).expand_batch(
        "문서", "설명", [(2, "본문")]
    )

    assert result == (
        (
            2,
            "[Expected Questions]\n- 어떻게 동작하나요?\n- 언제 사용하나요?"
            "\n\n[Keywords]\n인덱싱, indexing",
        ),
    )
