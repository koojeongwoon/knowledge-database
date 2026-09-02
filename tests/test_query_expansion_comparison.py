import pytest
from src.retrieval.application.service import WikiSearcher
from src.retrieval.domain.ports import RetrievalEmbeddingService
from src.retrieval.domain.repository import BaseRetrievalRepository
from src.retrieval.domain.policy import RetrievalPolicy


class MockEmbeddingService(RetrievalEmbeddingService):
    def embed_text(self, text: str) -> list[float]:
        return [0.1] * 10


class MockRetrievalRepository(BaseRetrievalRepository):
    def similarity_search(self, embedding: list[float], limit: int = 5) -> list[dict]:
        return [
            {
                "file_path": "architecture/fastmcp.md",
                "chunk_index": 0,
                "doc_type": "wiki",
                "title": "FastMCP Architecture",
                "description": "FastMCP Server implementation",
                "tags": ["fastmcp", "python"],
                "content": "FastMCP provides high-performance MCP server bindings.",
                "parent_content": "Full parent content of FastMCP Architecture",
                "similarity": 0.88,
                "raw_frontmatter": {"source_path": "architecture/fastmcp.md"},
            }
        ]

    def keyword_search(self, keyword_query: str, limit: int = 5) -> list[dict]:
        return [
            {
                "file_path": "architecture/fastmcp.md",
                "chunk_index": 0,
                "doc_type": "wiki",
                "title": "FastMCP Architecture",
                "description": "FastMCP Server implementation",
                "tags": ["fastmcp", "python"],
                "content": "FastMCP provides high-performance MCP server bindings.",
                "parent_content": "Full parent content of FastMCP Architecture",
                "rank": 0.08,
                "raw_frontmatter": {"source_path": "architecture/fastmcp.md"},
            }
        ]

    def get_connected_documents(self, file_paths: list[str], limit: int = 2) -> list[dict]:
        if "architecture/fastmcp.md" in file_paths:
            return [
                {
                    "file_path": "architecture/redis_cache.md",
                    "doc_type": "wiki",
                    "title": "Redis Cache Integration",
                    "description": "Caching layer for FastMCP",
                    "content": "Redis Cache Integration content",
                    "edge_weight": 3.0,
                    "target_topic": "redis_cache",
                }
            ]
        return []

    def increment_citation_count(self, file_paths: list[str]) -> None:
        pass


def test_compare_graph_expansion_off_vs_on():
    repo = MockRetrievalRepository()
    embedding = MockEmbeddingService()

    # 1. Graph Expansion 비활성화 (Baseline)
    policy_off = RetrievalPolicy(
        rrf_k=60,
        similarity_threshold=0.35,
        lexical_rank_threshold=0.02,
        graph_context_enabled=False,
    )
    searcher_off = WikiSearcher(repo, embedding, policy_off)
    results_off = searcher_off.search("FastMCP", limit=5)

    assert len(results_off) == 1
    # graph_context가 부착되지 않아야 함 (순수 검색 문서만 존재)
    assert "graph_context" not in results_off[0]

    # 2. Graph Expansion 활성화 (2차 연관 지식 확장)
    policy_on = RetrievalPolicy(
        rrf_k=60,
        similarity_threshold=0.35,
        lexical_rank_threshold=0.02,
        graph_context_enabled=True,
        graph_seed_vector_threshold=0.5,
        graph_seed_lexical_threshold=0.05,
        graph_context_limit=2,
    )
    searcher_on = WikiSearcher(repo, embedding, policy_on)
    results_on = searcher_on.search("FastMCP", limit=5)

    assert len(results_on) == 1
    # graph_context가 부착되어 2차 연관 문서 정보가 함께 제공되어야 함
    assert "graph_context" in results_on[0]
    graph_ctx = results_on[0]["graph_context"]
    assert len(graph_ctx) == 1
    assert graph_ctx[0]["file_path"] == "architecture/redis_cache.md"
    assert graph_ctx[0]["title"] == "Redis Cache Integration"
    assert graph_ctx[0]["retrieval_kind"] == "graph"
