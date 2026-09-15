from typing import Sequence

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


class BrokerDocumentExpander(BaseDocumentExpander):
    def __init__(self, client, model: str = "gpt-4o-mini") -> None:
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
            parsed = self.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You optimize technical search indexes."},
                    {"role": "user", "content": prompt},
                ],
                response_format=BatchExpansionResponse,
                temperature=0.2,
            )
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


    def parse(self, **kwargs):
        return self.client.parse(**kwargs)


def create_document_expander() -> BaseDocumentExpander:
    from src.core.config import DOCUMENT_EXPANSION_ENABLED, current_user_config

    if not DOCUMENT_EXPANSION_ENABLED:
        return NoOpDocumentExpander()
    config = current_user_config.get() or {}
    from src.settings.service import UserSettingsService
    from src.indexing.infrastructure.broker_chat import BrokerStructuredChat
    owner = config.get('user_id')
    if not owner or owner == 'SYSTEM':
        raise ValueError('Verified owner is required for Broker LLM execution')
    service = UserSettingsService()
    try:
        preferences = service.get_llm_preferences(owner)
    finally:
        service.db_manager.close()
    return BrokerDocumentExpander(
        BrokerStructuredChat(owner, auth_type=preferences['auth_type']),
        model=preferences['model'],
    )
