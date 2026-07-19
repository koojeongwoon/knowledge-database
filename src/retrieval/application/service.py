from typing import Any

from src.retrieval.domain.model import GraphExpansionPolicy, Query, RankFusion, RetrievalConfidence
from src.retrieval.domain.policy import RetrievalPolicy
from src.retrieval.domain.ports import (
    NoOpReranker,
    NoOpRetrievalObserver,
    RetrievalEmbeddingService,
    RetrievalObserver,
    RetrievalReranker,
)
from src.retrieval.domain.projection import (
    filter_candidates,
    project_direct_documents,
    project_graph_context,
)
from src.retrieval.domain.repository import BaseRetrievalRepository


class WikiSearcher:
    def __init__(
        self,
        repository: BaseRetrievalRepository,
        embedding_service: RetrievalEmbeddingService,
        policy: RetrievalPolicy,
        *,
        reranker: RetrievalReranker | None = None,
        observer: RetrievalObserver | None = None,
    ):
        self.repository = repository
        self.embedding_service = embedding_service
        self.policy = policy
        self.reranker = reranker or NoOpReranker()
        self.observer = observer or NoOpRetrievalObserver()

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        query_value = Query(query)
        query_embedding = self.embedding_service.embed_text(query_value.text)
        candidate_limit = self.policy.candidate_limit(limit)
        vector_results = self.repository.similarity_search(
            query_embedding, limit=candidate_limit,
        )
        clean_keywords = query_value.get_clean_keywords()
        keyword_query = " ".join(clean_keywords) if clean_keywords else query_value.text
        keyword_results = self.repository.keyword_search(keyword_query, limit=candidate_limit)
        if not vector_results and not keyword_results:
            return []

        candidates = RankFusion.rrf_fusion(
            vector_results, keyword_results, k=self.policy.rrf_k,
        )
        if self.reranker.available:
            pool_size = self.policy.rerank_pool_size(limit)
            candidates = self.reranker.rerank(
                query_value.text, candidates[:pool_size], limit * 2,
            )
        filtered = filter_candidates(
            candidates, self.policy, reranked=self.reranker.available,
        )
        if self.policy.confidence_filter_enabled and RetrievalConfidence.should_reject(
            filtered,
            weak_vector=self.policy.confidence_weak_vector,
            weak_lexical=self.policy.confidence_weak_lexical,
            sparse_margin=self.policy.confidence_sparse_margin,
        ):
            return []

        direct_documents, cited_paths = project_direct_documents(filtered, limit)
        if cited_paths:
            self.repository.increment_citation_count(list(cited_paths))
        self._attach_graph_context(direct_documents)
        return direct_documents

    def _attach_graph_context(self, direct_documents: list[dict[str, Any]]) -> None:
        if not direct_documents or not self.policy.graph_context_enabled:
            return
        try:
            seed_paths = GraphExpansionPolicy.strong_seed_paths(
                direct_documents,
                vector_threshold=self.policy.graph_seed_vector_threshold,
                lexical_threshold=self.policy.graph_seed_lexical_threshold,
            )
            if not seed_paths or self.policy.graph_context_limit <= 0:
                return
            connected = self.repository.get_connected_documents(
                seed_paths, limit=self.policy.graph_context_limit,
            )
            graph_context = project_graph_context(
                connected, {document["file_path"] for document in direct_documents},
            )
            if graph_context:
                direct_documents[0]["graph_context"] = graph_context
        except Exception as error:
            self.observer.graph_expansion_failed(error)
