from typing import Any, Protocol, Sequence


class RetrievalEmbeddingService(Protocol):
    def embed_text(self, text: str) -> list[float]: ...


class RetrievalReranker(Protocol):
    @property
    def available(self) -> bool: ...

    def rerank(
        self, query: str, documents: Sequence[dict[str, Any]], limit: int,
    ) -> list[dict[str, Any]]: ...


class RetrievalObserver(Protocol):
    def graph_expansion_failed(self, error: Exception) -> None: ...


class NoOpReranker:
    @property
    def available(self) -> bool:
        return False

    def rerank(
        self, query: str, documents: Sequence[dict[str, Any]], limit: int,
    ) -> list[dict[str, Any]]:
        return [dict(document) for document in documents[:limit]]


class NoOpRetrievalObserver:
    def graph_expansion_failed(self, error: Exception) -> None:
        return None
