from typing import Any, Sequence

from src.retrieval.domain.policy import RetrievalPolicy


def filter_candidates(
    documents: Sequence[dict[str, Any]],
    policy: RetrievalPolicy,
    *,
    reranked: bool,
) -> list[dict[str, Any]]:
    if reranked:
        return [
            document for document in documents
            if float(document.get("reranker_score", 0.0)) >= policy.similarity_threshold
        ]
    return [
        document for document in documents
        if (
            float(document.get("vector_similarity", 0.0)) >= policy.similarity_threshold
            or float(document.get("lexical_rank", 0.0)) >= policy.lexical_rank_threshold
        )
    ]


def project_direct_documents(
    documents: Sequence[dict[str, Any]], limit: int,
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    projected: list[dict[str, Any]] = []
    paths: list[str] = []
    seen: set[str] = set()
    for document in documents:
        path = document["file_path"]
        if path in seen:
            continue
        seen.add(path)
        paths.append(path)
        projected.append({
            "file_path": path,
            "doc_type": document["doc_type"],
            "title": document["title"],
            "description": document.get("description", ""),
            "tags": document.get("tags", []),
            "content": document.get("parent_content", document["content"]),
            "similarity": document.get("vector_similarity", 0.0),
            "vector_similarity": document.get("vector_similarity", 0.0),
            "lexical_rank": document.get("lexical_rank", 0.0),
            "rrf_score": document.get("rrf_score", 0.0),
            "search_sources": document.get("search_sources", []),
            "vector_chunk_index": document.get("vector_chunk_index"),
            "keyword_chunk_index": document.get("keyword_chunk_index"),
            "matched_chunk_index": document.get("chunk_index"),
            "matched_chunk_preview": document.get("content", "")[:500],
            "retrieval_kind": "direct",
            "raw_frontmatter": document.get("raw_frontmatter"),
            "citation_count": document.get("citation_count", 0),
        })
        if len(projected) >= limit:
            break
    return projected, tuple(paths)


def project_graph_context(
    documents: Sequence[dict[str, Any]], direct_paths: set[str],
) -> list[dict[str, Any]]:
    return [
        {
            "file_path": document["file_path"],
            "doc_type": f"{document['doc_type']} (Graph Context)",
            "title": document["title"],
            "description": document.get("description", ""),
            "tags": document.get("tags", []),
            "content": document.get("parent_content", document["content"]),
            "similarity": 0.0,
            "graph_weight": document.get("edge_weight", 1.0),
            "graph_sources": document.get("graph_sources", []),
            "graph_target": document.get("graph_target", ""),
            "retrieval_kind": "graph",
        }
        for document in documents
        if document["file_path"] not in direct_paths
    ]
