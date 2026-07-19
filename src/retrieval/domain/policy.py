from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalPolicy:
    similarity_threshold: float = 0.35
    lexical_rank_threshold: float = 0.02
    rrf_k: int = 60
    confidence_filter_enabled: bool = False
    confidence_weak_vector: float = 0.38
    confidence_weak_lexical: float = 0.015
    confidence_sparse_margin: float = 0.5
    graph_context_enabled: bool = False
    graph_seed_vector_threshold: float = 0.5
    graph_seed_lexical_threshold: float = 0.05
    graph_context_limit: int = 2

    def candidate_limit(self, requested_limit: int) -> int:
        return max(requested_limit * 4, 20)

    def rerank_pool_size(self, requested_limit: int) -> int:
        return max(requested_limit * 3, 15)
