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
        response = self.client.beta.chat.completions.parse(**kwargs)
        return response.choices[0].message.parsed


class BrokerDocumentExpander(OpenAIDocumentExpander):
    def parse(self, **kwargs):
        return self.client.parse(**kwargs)


def create_document_expander() -> BaseDocumentExpander:
    from src.core.config import DOCUMENT_EXPANSION_ENABLED, current_user_config

    if not DOCUMENT_EXPANSION_ENABLED:
        return NoOpDocumentExpander()
    config = current_user_config.get() or {}
    import os
    if os.getenv('LLM_PROVIDER') == 'broker':
        from src.settings.service import UserSettingsService
        from src.indexing.infrastructure.broker_chat import BrokerStructuredChat
        owner = config.get('user_id')
        if not owner or owner == 'SYSTEM':
            raise ValueError('Verified owner is required for Broker LLM execution')
        service = UserSettingsService()
        try:
            model = service.get_llm_model(owner)
        finally:
            service.db_manager.close()
        return BrokerDocumentExpander(BrokerStructuredChat(owner), model=model)
    if os.getenv('EMBEDDING_PROVIDER') == 'broker' and config.get('user_id'):
        from src.settings.service import UserSettingsService
        service = UserSettingsService()
        try:
            config = service.get_runtime_config(config['user_id'])
        finally:
            service.db_manager.close()
    api_key = config.get("llm_bearer_token") or config.get("openai_api_key")
    if not api_key:
        return NoOpDocumentExpander()
    model = config.get("llm_model_name") or ("gpt-5.6-luna" if config.get("llm_auth_type") == "openai_oauth" else "gpt-4o-mini")
    try:
        from openai import OpenAI

        return OpenAIDocumentExpander(OpenAI(api_key=api_key), model=model)
    except Exception as exc:
        print(f"Warning: Failed to initialize document expansion: {exc}")
        return NoOpDocumentExpander()

