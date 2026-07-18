from typing import Any, Sequence

from pydantic import BaseModel, Field

from src.indexing.domain.expansion import (
    BaseDocumentExpander,
    ExpansionResult,
    ExpansionTask,
    NoOpDocumentExpander,
)


class SingleChunkExpansion(BaseModel):
    chunk_index: int = Field(description="The unique index of the chunk being expanded")
    questions: list[str] = Field(description="Exactly three natural user questions in Korean")
    keywords: list[str] = Field(description="Exactly five search terms or synonyms")


class BatchExpansionResponse(BaseModel):
    expansions: list[SingleChunkExpansion]


class OpenAIDocumentExpander(BaseDocumentExpander):
    def __init__(self, client: Any, model: str = "gpt-4o-mini") -> None:
        self.client = client
        self.model = model

    @property
    def enabled(self) -> bool:
        return True

    def expand_batch(
        self,
        title: str,
        description: str,
        tasks: Sequence[ExpansionTask],
    ) -> tuple[ExpansionResult, ...]:
        chunks = "".join(
            f"=== [CHUNK INDEX {index}] ===\n{content}\n\n"
            for index, content in tasks
        )
        prompt = (
            "Analyze each separate knowledge-base chunk and generate three natural "
            "Korean questions plus five Korean/English keywords for each chunk.\n\n"
            f"Document title: {title}\nDocument description: {description}\n\n{chunks}"
        )
        try:
            response = self.client.beta.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You optimize technical search indexes."},
                    {"role": "user", "content": prompt},
                ],
                response_format=BatchExpansionResponse,
                temperature=0.2,
            )
            parsed = response.choices[0].message.parsed
            if not parsed:
                return ()
            return tuple(
                (
                    expansion.chunk_index,
                    "[Expected Questions]\n"
                    + "\n".join(f"- {question}" for question in expansion.questions)
                    + "\n\n[Keywords]\n"
                    + ", ".join(expansion.keywords),
                )
                for expansion in parsed.expansions
            )
        except Exception as exc:
            print(f"Warning: Failed to generate document expansion: {exc}")
            return ()


def create_document_expander() -> BaseDocumentExpander:
    from src.core.config import DOCUMENT_EXPANSION_ENABLED, current_user_config

    if not DOCUMENT_EXPANSION_ENABLED:
        return NoOpDocumentExpander()
    api_key = (current_user_config.get() or {}).get("openai_api_key")
    if not api_key:
        return NoOpDocumentExpander()
    try:
        from openai import OpenAI

        return OpenAIDocumentExpander(OpenAI(api_key=api_key))
    except Exception as exc:
        print(f"Warning: Failed to initialize document expansion: {exc}")
        return NoOpDocumentExpander()
