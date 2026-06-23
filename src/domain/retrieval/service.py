import re
import math
from typing import List, Dict, Any
from collections import defaultdict
from src.infrastructure.database import DatabaseManager
from src.domain.indexing.embedding import BaseEmbeddingService
from src.core.config import SIMILARITY_THRESHOLD, RERANKER_ENABLED, RERANKER_MODEL, RRF_K


class WikiSearcher:
    def __init__(self, db_manager: DatabaseManager, embedding_service: BaseEmbeddingService):
        self.db_manager = db_manager
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

    @staticmethod
    def _rrf_fusion(
        vector_results: List[Dict[str, Any]],
        keyword_results: List[Dict[str, Any]],
        k: int = 60,
    ) -> List[Dict[str, Any]]:
        """
        Reciprocal Rank Fusion으로 벡터 검색과 키워드 검색 두 랭킹 리스트를 결합합니다.
        score(doc) = Σ 1/(k + rank_i)
        결과 점수는 0~1 범위로 정규화됩니다.
        """
        scores: Dict[tuple, float] = defaultdict(float)
        doc_map: Dict[tuple, Dict] = {}
        source_map: Dict[tuple, List[str]] = defaultdict(list)

        for rank, doc in enumerate(vector_results):
            key = (doc["file_path"], doc.get("chunk_index", 0))
            scores[key] += 1.0 / (k + rank + 1)
            doc_map[key] = doc
            source_map[key].append("vector")

        for rank, doc in enumerate(keyword_results):
            key = (doc["file_path"], doc.get("chunk_index", 0))
            scores[key] += 1.0 / (k + rank + 1)
            if key not in doc_map:
                doc_map[key] = doc
            source_map[key].append("keyword")

        if not scores:
            return []

        # 0~1 범위로 정규화
        max_score = max(scores.values())
        if max_score > 0:
            normalized = {k: v / max_score for k, v in scores.items()}
        else:
            normalized = scores

        # 점수 내림차순 정렬
        sorted_keys = sorted(normalized.keys(), key=lambda k: normalized[k], reverse=True)

        results = []
        for key in sorted_keys:
            doc = doc_map[key].copy()
            doc["rrf_score"] = normalized[key]
            doc["search_sources"] = source_map[key]
            results.append(doc)

        return results

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
        # 1. 쿼리 임베딩 생성
        query_embedding = self.embedding_service.embed_text(query)

        # 2. 이중 경로 검색 — 최종 limit보다 넓은 후보 풀 확보
        candidate_limit = max(limit * 4, 20)
        vector_results = self.db_manager.similarity_search(query_embedding, limit=candidate_limit)
        keyword_results = self.db_manager.keyword_search(query, limit=candidate_limit)

        if not vector_results and not keyword_results:
            return []

        # 3. RRF 결합
        fused = self._rrf_fusion(vector_results, keyword_results, k=RRF_K)

        # 4. (선택) 리랭커 재정렬 — RRF 상위 후보만 리랭크
        if self.reranker:
            rerank_pool_size = max(limit * 3, 15)
            fused = self._rerank(query, fused[:rerank_pool_size], top_k=limit * 2)
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
                # 리랭커 비활성화 시: 보정된 RRF 필터링
                # 키워드 매칭이 전혀 없이 벡터 단독 매칭되었으나, 절대 코사인 유사도가 임계치 미만인 경우 제외
                sources = doc.get("search_sources", [])
                if "keyword" not in sources:
                    raw_sim = doc.get("similarity", 0.0)
                    if raw_sim < SIMILARITY_THRESHOLD:
                        continue
                filtered.append(doc)

        # 6. Parent-Child RAG: 중복 제거 및 자식 청크 매칭 결과를 부모 문맥으로 교체하여 반환
        retrieved_docs = []
        file_paths = []
        seen_parents = set()

        for doc in filtered:
            parent_key = (doc["file_path"], doc["title"])
            if parent_key in seen_parents:
                continue
            seen_parents.add(parent_key)

            file_paths.append(doc["file_path"])
            retrieved_docs.append({
                "file_path": doc["file_path"],
                "doc_type": doc["doc_type"],
                "title": doc["title"],
                "description": doc.get("description", ""),
                "tags": doc.get("tags", []),
                # 부모 문맥 전달 (더 넓은 컨텍스트)
                "content": doc.get("parent_content", doc["content"]),
                "similarity": doc.get(score_key, 0),
                "raw_frontmatter": doc.get("raw_frontmatter")  # frontmatter 정보 포함
            })
            if len(retrieved_docs) >= limit:
                break

        # 7. Graph-link RAG: 매칭된 문서들과 위키링크로 1촌 연결된 연관 개념 확장
        try:
            connected_docs = self.db_manager.get_connected_documents(file_paths, limit=3)
            for doc in connected_docs:
                # 중복 반환 방지
                if any(r["file_path"] == doc["file_path"] for r in retrieved_docs):
                    continue

                # 4대 신호 및 수동 지정 연결 강도(edge_weight)를 유사도에 동적 반영
                # 기본 기준값인 0.85에 edge_weight 가중치를 곱해 정합성 있게 계산
                edge_weight = doc.get("edge_weight", 1.0)
                dynamic_similarity = min(0.8500 * edge_weight, 0.9900)

                retrieved_docs.append({
                    "file_path": doc["file_path"],
                    "doc_type": f"{doc['doc_type']} (Graph Extension)",
                    "title": doc["title"],
                    "description": doc.get("description", ""),
                    "tags": doc.get("tags", []),
                    "content": doc.get("parent_content", doc["content"]),
                    "similarity": dynamic_similarity,
                })
        except Exception as ex:
            print(f"Warning: Failed to expand graph context: {ex}")

        return retrieved_docs
