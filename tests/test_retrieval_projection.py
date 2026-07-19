from src.retrieval.domain.policy import RetrievalPolicy
from src.retrieval.domain.projection import filter_candidates, project_direct_documents


def test_raw_signal_filter_accepts_either_vector_or_lexical_threshold():
    policy = RetrievalPolicy(similarity_threshold=0.5, lexical_rank_threshold=0.05)
    documents = [
        {"file_path": "vector", "vector_similarity": 0.5, "lexical_rank": 0.0},
        {"file_path": "lexical", "vector_similarity": 0.0, "lexical_rank": 0.05},
        {"file_path": "weak", "vector_similarity": 0.49, "lexical_rank": 0.049},
    ]

    assert [item["file_path"] for item in filter_candidates(
        documents, policy, reranked=False,
    )] == ["vector", "lexical"]


def test_reranked_filter_uses_only_reranker_absolute_score():
    policy = RetrievalPolicy(similarity_threshold=0.5)
    documents = [
        {"file_path": "keep", "reranker_score": 0.5, "vector_similarity": 0.0},
        {"file_path": "drop", "reranker_score": 0.49, "vector_similarity": 1.0},
    ]

    assert [item["file_path"] for item in filter_candidates(
        documents, policy, reranked=True,
    )] == ["keep"]


def test_direct_projection_is_immutable_deduplicated_and_limit_bounded():
    source = {
        "file_path": "qa/a.md", "doc_type": "qa", "title": "A",
        "content": "child", "parent_content": "parent", "vector_similarity": 0.7,
    }
    documents, paths = project_direct_documents([source, source | {"content": "other"}], 1)

    assert paths == ("qa/a.md",)
    assert documents[0]["content"] == "parent"
    assert documents[0]["retrieval_kind"] == "direct"
    assert "retrieval_kind" not in source
