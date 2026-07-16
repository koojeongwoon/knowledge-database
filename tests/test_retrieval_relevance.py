import unittest
from unittest.mock import patch

from src.retrieval.application.service import WikiSearcher
from src.retrieval.domain.formatter import format_retrieved_documents
from src.retrieval.domain.model import GraphExpansionPolicy, RankFusion, RetrievalConfidence


def _doc(path, *, similarity=0.0, rank=0.0, citation_count=0):
    return {
        "file_path": path,
        "chunk_index": 0,
        "doc_type": "qa",
        "title": path,
        "description": "",
        "tags": [],
        "content": "content",
        "parent_content": "parent content",
        "similarity": similarity,
        "rank": rank,
        "citation_count": citation_count,
        "raw_frontmatter": {},
    }


class _Embedding:
    def embed_text(self, _text):
        return [0.0]


class _Repository:
    def __init__(self, vector_results, keyword_results, connected_results=None):
        self.vector_results = vector_results
        self.keyword_results = keyword_results
        self.connected_results = connected_results or []
        self.graph_calls = []

    def similarity_search(self, _embedding, limit):
        return self.vector_results[:limit]

    def keyword_search(self, _query, limit):
        return self.keyword_results[:limit]

    def increment_citation_count(self, _paths):
        return None

    def get_connected_documents(self, paths, limit):
        self.graph_calls.append((paths, limit))
        return self.connected_results[:limit]


def _searcher(repository):
    searcher = object.__new__(WikiSearcher)
    searcher.repository = repository
    searcher.embedding_service = _Embedding()
    searcher.reranker = None
    return searcher


class RankFusionSignalTests(unittest.TestCase):
    def test_preserves_raw_vector_and_lexical_signals(self):
        result = RankFusion.rrf_fusion(
            [_doc("qa/a.md", similarity=0.42)],
            [_doc("qa/a.md", rank=0.07)],
        )[0]

        self.assertEqual(result["vector_similarity"], 0.42)
        self.assertEqual(result["lexical_rank"], 0.07)
        self.assertEqual(result["search_sources"], ["vector", "keyword"])
        self.assertEqual(result["rrf_score"], 1.0)

    def test_combines_vector_and_keyword_signals_across_chunks_of_same_file(self):
        vector = [_doc("qa/other.md", similarity=0.6), _doc("qa/answer.md", similarity=0.58)]
        keyword = [
            _doc("qa/answer.md", rank=0.08) | {"chunk_index": 4},
            _doc("qa/other.md", rank=0.03) | {"chunk_index": 2},
        ]

        results = RankFusion.rrf_fusion(vector, keyword)

        self.assertEqual([doc["file_path"] for doc in results], ["qa/answer.md", "qa/other.md"])
        answer = results[0]
        self.assertEqual(answer["search_sources"], ["vector", "keyword"])
        self.assertEqual(answer["vector_chunk_index"], 0)
        self.assertEqual(answer["keyword_chunk_index"], 4)
        self.assertEqual(answer["vector_similarity"], 0.58)
        self.assertEqual(answer["lexical_rank"], 0.08)

    def test_counts_each_file_only_once_per_search_path(self):
        vector = [
            _doc("qa/a.md", similarity=0.7),
            _doc("qa/a.md", similarity=0.6) | {"chunk_index": 1},
            _doc("qa/b.md", similarity=0.5),
        ]

        results = RankFusion.rrf_fusion(vector, [])

        self.assertEqual([doc["file_path"] for doc in results], ["qa/a.md", "qa/b.md"])
        self.assertGreater(results[0]["rrf_score"], results[1]["rrf_score"])


class RetrievalConfidenceTests(unittest.TestCase):
    def test_rejects_weak_vector_only_result(self):
        documents = [{"vector_similarity": 0.37, "lexical_rank": 0.0, "rrf_score": 1.0}]

        self.assertTrue(RetrievalConfidence.should_reject(documents))

    def test_rejects_weak_agreement_with_sparse_large_margin(self):
        documents = [
            {"vector_similarity": 0.374, "lexical_rank": 0.012, "rrf_score": 1.0},
            {"vector_similarity": 0.36, "lexical_rank": 0.0, "rrf_score": 0.49},
        ]

        self.assertTrue(RetrievalConfidence.should_reject(documents))

    def test_keeps_weak_but_consistent_agreement(self):
        documents = [
            {"vector_similarity": 0.36, "lexical_rank": 0.010, "rrf_score": 1.0},
            {"vector_similarity": 0.35, "lexical_rank": 0.0, "rrf_score": 0.96},
        ]

        self.assertFalse(RetrievalConfidence.should_reject(documents))

    def test_keeps_any_strong_absolute_signal(self):
        self.assertFalse(RetrievalConfidence.should_reject([
            {"vector_similarity": 0.5, "lexical_rank": 0.0, "rrf_score": 1.0},
        ]))


class GraphExpansionPolicyTests(unittest.TestCase):
    def test_selects_only_strong_direct_seeds(self):
        documents = [
            {"file_path": "qa/weak.md", "vector_similarity": 0.4, "lexical_rank": 0.01},
            {"file_path": "qa/vector.md", "vector_similarity": 0.6, "lexical_rank": 0.0},
            {"file_path": "qa/lexical.md", "vector_similarity": 0.2, "lexical_rank": 0.08},
            {"file_path": "qa/extra.md", "vector_similarity": 0.9, "lexical_rank": 0.1},
        ]

        self.assertEqual(
            GraphExpansionPolicy.strong_seed_paths(documents),
            ["qa/vector.md", "qa/lexical.md"],
        )

class RetrievalRelevanceTests(unittest.TestCase):
    def test_rejects_candidate_when_both_raw_signals_are_weak(self):
        repo = _Repository(
            [_doc("qa/irrelevant.md", similarity=0.26)],
            [_doc("qa/irrelevant.md", rank=0.011)],
            connected_results=[_doc("qa/graph.md")],
        )

        self.assertEqual(_searcher(repo).search("penguin habitat", limit=5), [])
        self.assertEqual(repo.graph_calls, [])

    def test_accepts_strong_lexical_signal_even_with_weak_vector_signal(self):
        path = "qa/exact.md"
        repo = _Repository(
            [_doc(path, similarity=0.26, citation_count=1000)],
            [_doc(path, rank=0.049)],
        )

        result = _searcher(repo).search("exact phrase", limit=5)[0]

        self.assertEqual(result["file_path"], path)
        self.assertEqual(result["vector_similarity"], 0.26)
        self.assertEqual(result["lexical_rank"], 0.049)
        self.assertEqual(result["rrf_score"], 1.0)

    def test_graph_score_is_not_reported_as_query_similarity(self):
        direct = _doc("qa/direct.md", similarity=0.7)
        graph = _doc("qa/graph.md") | {
            "edge_weight": 0.8,
            "graph_sources": ["qa/direct.md"],
            "graph_target": "graph",
        }
        with patch("src.retrieval.application.service.GRAPH_CONTEXT_ENABLED", True):
            result = _searcher(_Repository([direct], [], [graph])).search("query", limit=2)

        self.assertEqual(len(result), 1)
        context = result[0]["graph_context"][0]
        self.assertEqual(context["retrieval_kind"], "graph")
        self.assertEqual(context["similarity"], 0.0)
        rendered = format_retrieved_documents(result)
        self.assertIn("<graph_context>", rendered)
        self.assertIn("Graph Weight: 0.8000", rendered)
        self.assertIn("Graph Sources: qa/direct.md", rendered)

    def test_weak_direct_result_does_not_query_graph(self):
        repo = _Repository([_doc("qa/weak.md", similarity=0.4)], [], [_doc("qa/graph.md")])

        with patch("src.retrieval.application.service.GRAPH_CONTEXT_ENABLED", True):
            result = _searcher(repo).search("query", limit=2)

        self.assertEqual([doc["file_path"] for doc in result], ["qa/weak.md"])
        self.assertEqual(repo.graph_calls, [])

    def test_never_returns_more_than_requested_limit(self):
        direct = [_doc(f"qa/{index}.md", similarity=0.7) for index in range(3)]
        result = _searcher(_Repository(direct, [])).search("query", limit=2)

        self.assertEqual(len(result), 2)

    def test_returns_each_file_only_once_across_multiple_chunks(self):
        first = _doc("qa/same.md", similarity=0.7)
        second = _doc("qa/same.md", similarity=0.6) | {"chunk_index": 1, "title": "child"}
        result = _searcher(_Repository([first, second], [])).search("query", limit=5)

        self.assertEqual([doc["file_path"] for doc in result], ["qa/same.md"])

if __name__ == "__main__":
    unittest.main()
