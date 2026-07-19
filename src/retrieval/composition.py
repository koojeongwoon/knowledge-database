from src.core.config import (
    CONFIDENCE_FILTER_ENABLED,
    CONFIDENCE_SPARSE_MARGIN,
    CONFIDENCE_WEAK_LEXICAL,
    CONFIDENCE_WEAK_VECTOR,
    GRAPH_CONTEXT_ENABLED,
    GRAPH_CONTEXT_LIMIT,
    GRAPH_SEED_LEXICAL_THRESHOLD,
    GRAPH_SEED_VECTOR_THRESHOLD,
    LEXICAL_RANK_THRESHOLD,
    RERANKER_ENABLED,
    RERANKER_MODEL,
    RRF_K,
    SIMILARITY_THRESHOLD,
)
from src.retrieval.application.service import WikiSearcher
from src.retrieval.domain.policy import RetrievalPolicy
from src.retrieval.domain.ports import NoOpReranker
from src.retrieval.infrastructure.observer import LoggingRetrievalObserver
from src.retrieval.infrastructure.repository import RetrievalRepository
from src.retrieval.infrastructure.reranker import CrossEncoderReranker


def create_wiki_searcher(db_manager, embedding_service) -> WikiSearcher:
    policy = RetrievalPolicy(
        similarity_threshold=SIMILARITY_THRESHOLD,
        lexical_rank_threshold=LEXICAL_RANK_THRESHOLD,
        rrf_k=RRF_K,
        confidence_filter_enabled=CONFIDENCE_FILTER_ENABLED,
        confidence_weak_vector=CONFIDENCE_WEAK_VECTOR,
        confidence_weak_lexical=CONFIDENCE_WEAK_LEXICAL,
        confidence_sparse_margin=CONFIDENCE_SPARSE_MARGIN,
        graph_context_enabled=GRAPH_CONTEXT_ENABLED,
        graph_seed_vector_threshold=GRAPH_SEED_VECTOR_THRESHOLD,
        graph_seed_lexical_threshold=GRAPH_SEED_LEXICAL_THRESHOLD,
        graph_context_limit=GRAPH_CONTEXT_LIMIT,
    )
    reranker = CrossEncoderReranker(RERANKER_MODEL) if RERANKER_ENABLED else NoOpReranker()
    return WikiSearcher(
        repository=RetrievalRepository(db_manager),
        embedding_service=embedding_service,
        policy=policy,
        reranker=reranker,
        observer=LoggingRetrievalObserver(),
    )
