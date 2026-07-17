import math
from typing import List, Dict, Any

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
from src.core.database.factory import DatabaseManager
from src.indexing.domain.embedding import BaseEmbeddingService
from src.retrieval.domain.model import GraphExpansionPolicy, Query, RankFusion, RetrievalConfidence
from src.retrieval.infrastructure.repository import RetrievalRepository


class WikiSearcher:
    def __init__(self, db_manager: DatabaseManager, embedding_service: BaseEmbeddingService):
        self.db_manager = db_manager
        self.repository = RetrievalRepository(db_manager)
        self.embedding_service = embedding_service
        self.reranker = None

        if RERANKER_ENABLED:
            self._load_reranker()

    def _load_reranker(self):
        """Cross-Encoder 리랭커를 지연 로딩합니다."""
        try:
            from sentence_transformers import CrossEncoder
            print(f"Loading reranker model '{RERANKER_MODEL}'...")
            self.reranker = CrossEncoder(RERANKER_MODEL)
            print("Reranker loaded successfully.")
        except Exception as e:
            print(f"Warning: Failed to load reranker model: {e}. Reranking disabled.")

    def _rerank(self, query: str, docs: List[Dict[str, Any]], top_k: int = 10) -> List[Dict[str, Any]]:
        """
        Cross-Encoder로 후보 문서들의 쿼리-문서 관련성을 정밀 재평가하여 재정렬합니다.
        출력 점수에 sigmoid를 적용하여 0~1 범위로 정규화합니다.
        """
        if not docs or not self.reranker:
            return docs

        # 리랭커는 부모 문맥(더 넓은 컨텍스트)으로 평가
        pairs = [(query, doc.get("parent_content", doc["content"])) for doc in docs]
        raw_scores = self.reranker.predict(pairs)

        for doc, score in zip(docs, raw_scores):
            doc["reranker_score"] = 1.0 / (1.0 + math.exp(-float(score)))

        docs.sort(key=lambda x: x["reranker_score"], reverse=True)
        return docs[:top_k]

    def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        하이브리드 검색 파이프라인:
        1) 벡터 검색 + 키워드 검색으로 넓은 후보 확보
        2) RRF로 두 랭킹 결합
        3) (선택) Cross-Encoder 리랭커로 정밀 재정렬
        4) 임계치 미만 결과 제거
        5) 위키링크 그래프 확장
        """
        # 1. 쿼리 객체 캡슐화 및 임베딩 생성
        query_obj = Query(query)
        query_embedding = self.embedding_service.embed_text(query_obj.text)

        # 2. 이중 경로 검색 — 최종 limit보다 넓은 후보 풀 확보
        candidate_limit = max(limit * 4, 20)
        vector_results = self.repository.similarity_search(query_embedding, limit=candidate_limit)
        
        # 도메인 정책에 따른 정제된 키워드 사용
        clean_keywords = query_obj.get_clean_keywords()
        search_query_text = " ".join(clean_keywords) if clean_keywords else query_obj.text
        keyword_results = self.repository.keyword_search(search_query_text, limit=candidate_limit)
        if not vector_results and not keyword_results:
            return []

        # 3. RRF 결합 (도메인 모델 서비스 호출)
        fused = RankFusion.rrf_fusion(vector_results, keyword_results, k=RRF_K)

        # 4. (선택) 리랭커 재정렬 — RRF 상위 후보만 리랭크
        if self.reranker:
            rerank_pool_size = max(limit * 3, 15)
            fused = self._rerank(query_obj.text, fused[:rerank_pool_size], top_k=limit * 2)
            score_key = "reranker_score"
        else:
            score_key = "rrf_score"

        # 5. 임계치 필터링 — 파이프라인 최종 점수 기준
        filtered = []
        for doc in fused:
            if self.reranker:
                # 리랭커 활성화 시: Cross-Encoder의 절대 관련도 점수 기준 필터링
                if doc.get(score_key, 0) >= SIMILARITY_THRESHOLD:
                    filtered.append(doc)
            else:
                # RRF는 순위 결합에만 사용하고, 원시 신호 중 하나가 절대 기준을
                # 통과한 경우에만 관련 문서로 인정합니다.
                vector_pass = doc.get("vector_similarity", 0.0) >= SIMILARITY_THRESHOLD
                lexical_pass = doc.get("lexical_rank", 0.0) >= LEXICAL_RANK_THRESHOLD
                if vector_pass or lexical_pass:
                    filtered.append(doc)

        if CONFIDENCE_FILTER_ENABLED and RetrievalConfidence.should_reject(
            filtered,
            weak_vector=CONFIDENCE_WEAK_VECTOR,
            weak_lexical=CONFIDENCE_WEAK_LEXICAL,
            sparse_margin=CONFIDENCE_SPARSE_MARGIN,
        ):
            return []

        # 6. Parent-Child RAG: 중복 제거 및 자식 청크 매칭 결과를 부모 문맥으로 교체하여 반환
        retrieved_docs = []
        file_paths = []
        seen_files = set()

        for doc in filtered:
            # 여러 청크가 같은 부모 문서 전체를 반환하므로 파일당 한 번만 노출합니다.
            if doc["file_path"] in seen_files:
                continue
            seen_files.add(doc["file_path"])

            file_paths.append(doc["file_path"])
            retrieved_docs.append({
                "file_path": doc["file_path"],
                "doc_type": doc["doc_type"],
                "title": doc["title"],
                "description": doc.get("description", ""),
                "tags": doc.get("tags", []),
                # 부모 문맥 전달 (더 넓은 컨텍스트)
                "content": doc.get("parent_content", doc["content"]),
                "similarity": doc.get("vector_similarity", 0.0),
                "vector_similarity": doc.get("vector_similarity", 0.0),
                "lexical_rank": doc.get("lexical_rank", 0.0),
                "rrf_score": doc.get("rrf_score", 0.0),
                "search_sources": doc.get("search_sources", []),
                "vector_chunk_index": doc.get("vector_chunk_index"),
                "keyword_chunk_index": doc.get("keyword_chunk_index"),
                "matched_chunk_index": doc.get("chunk_index"),
                "matched_chunk_preview": doc.get("content", "")[:500],
                "retrieval_kind": "direct",
                "raw_frontmatter": doc.get("raw_frontmatter"),  # frontmatter 정보 포함
                "citation_count": doc.get("citation_count", 0)  # [추가] 인용 횟수 전달
            })
            if len(retrieved_docs) >= limit:
                break

        # 인용수 업데이트 (기존 agent_tool에 있던 비즈니스 로직을 이관)
        if file_paths:
            self.repository.increment_citation_count(file_paths)

        # 7. Graph-link RAG: direct 순위/슬롯과 분리된 보조 컨텍스트로만 제공
        try:
            seed_paths = GraphExpansionPolicy.strong_seed_paths(
                retrieved_docs,
                vector_threshold=GRAPH_SEED_VECTOR_THRESHOLD,
                lexical_threshold=GRAPH_SEED_LEXICAL_THRESHOLD,
            ) if GRAPH_CONTEXT_ENABLED else []
            connected_docs = (
                self.repository.get_connected_documents(seed_paths, limit=GRAPH_CONTEXT_LIMIT)
                if seed_paths and GRAPH_CONTEXT_LIMIT > 0
                else []
            )
            graph_context = []
            for doc in connected_docs:
                if any(r["file_path"] == doc["file_path"] for r in retrieved_docs):
                    continue

                edge_weight = doc.get("edge_weight", 1.0)
                graph_context.append({
                    "file_path": doc["file_path"],
                    "doc_type": f"{doc['doc_type']} (Graph Context)",
                    "title": doc["title"],
                    "description": doc.get("description", ""),
                    "tags": doc.get("tags", []),
                    "content": doc.get("parent_content", doc["content"]),
                    "similarity": 0.0,
                    "graph_weight": edge_weight,
                    "graph_sources": doc.get("graph_sources", []),
                    "graph_target": doc.get("graph_target", ""),
                    "retrieval_kind": "graph",
                })
            if graph_context:
                retrieved_docs[0]["graph_context"] = graph_context
        except Exception as ex:
            print(f"Warning: Failed to expand graph context: {ex}")

        return retrieved_docs
