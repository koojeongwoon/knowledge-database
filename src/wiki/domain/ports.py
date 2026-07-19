from typing import Any, Protocol


class TopicMetadataRepository(Protocol):
    def get_topic_by_name(self, topic_name: str) -> dict[str, Any] | None: ...
    def upsert_topic(self, topic_name: str, category: str, file_path: str) -> None: ...


class IndexingQueue(Protocol):
    def enqueue(self, file_paths: list[str]) -> None: ...
