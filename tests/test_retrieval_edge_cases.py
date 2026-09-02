import pytest
from src.retrieval.domain.model import Query, RankFusion, RetrievalConfidence
from src.retrieval.domain.policy import RetrievalPolicy
from src.api.dto import SearchQueryDTO


def test_query_edge_cases_empty_and_whitespace():
    q = Query("   ")
    assert q.text == ""
    assert q.get_clean_keywords() == []


def test_query_edge_cases_special_characters():
    q = Query("!@#$%^&*()_+={}[]:;\"'<>?,./~`")
    assert q.get_clean_keywords() == []

    q2 = Query("FastMCP & Redis 캐싱 (v2.0)!")
    keywords = q2.get_clean_keywords()
    assert "FastMCP" in keywords
    assert "Redis" in keywords
    assert "캐싱" in keywords


def test_search_query_dto_validation():
    dto = SearchQueryDTO(query="test", limit=10)
    assert dto.query == "test"
    assert dto.limit == 10

    with pytest.raises(Exception):
        # limit < 1 validation error
        SearchQueryDTO(query="test", limit=0)


def test_rank_fusion_empty_inputs():
    candidates = RankFusion.rrf_fusion([], [])
    assert candidates == []


def test_rank_fusion_single_source_vector_only():
    vector_results = [
        {
            "file_path": "doc1.md",
            "chunk_index": 0,
            "doc_type": "wiki",
            "title": "Doc 1",
            "description": "",
            "tags": [],
            "content": "Content 1",
            "parent_content": "",
            "similarity": 0.85,
            "raw_frontmatter": {},
        }
    ]
    candidates = RankFusion.rrf_fusion(vector_results, [], k=60)
    assert len(candidates) == 1
    assert candidates[0]["file_path"] == "doc1.md"
    assert "vector" in candidates[0]["search_sources"]


def test_rank_fusion_single_source_lexical_only():
    lexical_results = [
        {
            "file_path": "doc2.md",
            "chunk_index": 0,
            "doc_type": "wiki",
            "title": "Doc 2",
            "description": "",
            "tags": [],
            "content": "Content 2",
            "parent_content": "",
            "rank": 0.05,
            "raw_frontmatter": {},
        }
    ]
    candidates = RankFusion.rrf_fusion([], lexical_results, k=60)
    assert len(candidates) == 1
    assert candidates[0]["file_path"] == "doc2.md"
    assert "keyword" in candidates[0]["search_sources"]


def test_confidence_rejection_edge_case():
    policy = RetrievalPolicy(
        rrf_k=60,
        similarity_threshold=0.35,
        lexical_rank_threshold=0.02,
        confidence_filter_enabled=True,
        confidence_weak_vector=0.38,
        confidence_weak_lexical=0.015,
        confidence_sparse_margin=0.5,
    )
    # agreement가 없는 경우(lexical_rank = 0) + threshold 미달일 때 reject
    weak_candidate = {
        "file_path": "weak.md",
        "chunk_index": 0,
        "doc_type": "wiki",
        "title": "Weak",
        "description": "",
        "tags": [],
        "content": "Weak",
        "parent_content": "",
        "vector_similarity": 0.20,
        "lexical_rank": 0.0,
        "rrf_score": 0.01,
        "search_sources": ["vector"],
    }
    assert (
        RetrievalConfidence.should_reject(
            [weak_candidate],
            weak_vector=policy.confidence_weak_vector,
            weak_lexical=policy.confidence_weak_lexical,
            sparse_margin=policy.confidence_sparse_margin,
        )
        is True
    )
