from typing import List, Dict, Any

def format_retrieved_documents(docs: List[Dict[str, Any]]) -> str:
    """검색 결과 문서 목록을 XML 포맷으로 직렬화하는 순수 프레젠테이션 함수입니다."""
    if not docs:
        return "지식베이스에서 관련된 문서를 찾지 못했습니다."
        
    formatted_docs = []
    graph_docs = []
    for doc in docs:
        graph_docs.extend(doc.get("graph_context", []))
        raw_fm = doc.get("raw_frontmatter") or {}
        image_path = raw_fm.get("image_path")
        image_path_str = f"Image Path: {image_path}\n" if image_path else ""

        if doc.get("retrieval_kind") == "graph":
            score_str = f"Graph Weight: {doc.get('graph_weight', 0.0):.4f}\n"
        else:
            score_str = (
                f"Vector Similarity: {doc.get('vector_similarity', doc.get('similarity', 0.0)):.4f}\n"
                f"Lexical Rank: {doc.get('lexical_rank', 0.0):.4f}\n"
                f"RRF Score: {doc.get('rrf_score', 0.0):.4f}\n"
            )

        doc_str = (
            f"<document>\n"
            f"File: {doc['file_path']}\n"
            f"Title: {doc['title']}\n"
            f"Type: {doc['doc_type']}\n"
            f"{image_path_str}"
            f"{score_str}"
            f"Citation Count: {doc.get('citation_count', 0) + 1}\n"
            f"Tags: {', '.join(doc['tags']) if doc['tags'] else 'None'}\n"
            f"Content:\n{doc['content']}\n"
            f"</document>"
        )
        formatted_docs.append(doc_str)

    for doc in graph_docs:
        sources = ", ".join(doc.get("graph_sources", [])) or "Unknown"
        formatted_docs.append(
            f"<graph_context>\n"
            f"File: {doc['file_path']}\n"
            f"Title: {doc['title']}\n"
            f"Type: {doc['doc_type']}\n"
            f"Graph Target: {doc.get('graph_target', '')}\n"
            f"Graph Sources: {sources}\n"
            f"Graph Weight: {doc.get('graph_weight', 0.0):.4f}\n"
            f"Tags: {', '.join(doc['tags']) if doc['tags'] else 'None'}\n"
            f"Content:\n{doc['content']}\n"
            f"</graph_context>"
        )
        
    return "\n\n---\n\n".join(formatted_docs)
