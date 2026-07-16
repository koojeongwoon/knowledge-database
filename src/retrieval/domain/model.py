from collections import defaultdict
from typing import List, Dict, Any


class Query:
    """사용자가 입력한 질문 정보를 캡슐화한 Value Object"""
    def __init__(self, text: str):
        self.text = text.strip()

    def get_clean_keywords(self) -> List[str]:
        """쿼리에서 키워드 필터링 및 정합어만 분리 추출 (도메인 정책 위임)"""
        import re
        words = re.findall(r'\w+', self.text, re.UNICODE)
        return [w for w in words if len(w) > 1]

class SearchResult:
    """하이브리드 경로에서 반환된 후보군 단일 문서 객체"""
    def __init__(
        self, 
        file_path: str, 
        chunk_index: int, 
        doc_type: str, 
        title: str, 
        description: str, 
        tags: List[str], 
        content: str, 
        parent_content: str, 
        similarity: float, 
        raw_frontmatter: Dict[str, Any],
        search_sources: List[str] = None
    ):
        self.file_path = file_path
        self.chunk_index = chunk_index
        self.doc_type = doc_type
        self.title = title
        self.description = description
        self.tags = tags
        self.content = content
        self.parent_content = parent_content
        self.similarity = similarity
        self.raw_frontmatter = raw_frontmatter
        self.search_sources = search_sources or []

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 직렬화"""
        return {
            "file_path": self.file_path,
            "chunk_index": self.chunk_index,
            "doc_type": self.doc_type,
            "title": self.title,
            "description": self.description,
            "tags": self.tags,
            "content": self.content,
            "parent_content": self.parent_content,
            "similarity": self.similarity,
            "raw_frontmatter": self.raw_frontmatter,
            "search_sources": self.search_sources
        }


class RetrievalConfidence:
    """절대 신호가 약한 sparse-fusion 결과만 no-answer로 보정한다."""

    @staticmethod
    def should_reject(
        documents: List[Dict[str, Any]],
        weak_vector: float = 0.38,
        weak_lexical: float = 0.015,
        sparse_margin: float = 0.5,
    ) -> bool:
        if not documents:
            return False
        top = documents[0]
        vector = float(top.get("vector_similarity", 0.0))
        lexical = float(top.get("lexical_rank", 0.0))
        if vector >= weak_vector or lexical >= weak_lexical:
            return False

        agreement = vector > 0.0 and lexical > 0.0
        second_score = float(documents[1].get("rrf_score", 0.0)) if len(documents) > 1 else 0.0
        margin = float(top.get("rrf_score", 0.0)) - second_score
        return not agreement or margin >= sparse_margin


class GraphExpansionPolicy:
    """강한 direct 결과만 graph seed로 허용한다."""

    @staticmethod
    def strong_seed_paths(
        documents: List[Dict[str, Any]],
        vector_threshold: float = 0.5,
        lexical_threshold: float = 0.05,
        max_seeds: int = 2,
    ) -> List[str]:
        paths = []
        for document in documents:
            if (
                float(document.get("vector_similarity", 0.0)) >= vector_threshold
                or float(document.get("lexical_rank", 0.0)) >= lexical_threshold
            ):
                paths.append(document["file_path"])
            if len(paths) >= max_seeds:
                break
        return paths


class RankFusion:
    """두 검색 경로(벡터, 키워드)의 결과를 결합하는 RRF 도메인 서비스"""
    @staticmethod
    def rrf_fusion(
        vector_results: List[Dict[str, Any]],
        keyword_results: List[Dict[str, Any]],
        k: int = 60
    ) -> List[Dict[str, Any]]:
        """Reciprocal Rank Fusion 알고리즘 결합 연산 규칙 (도메인 정책 격리)"""
        # 최종 응답이 파일 단위인 만큼 fusion도 파일 단위로 계산한다. 같은 문서의
        # vector/keyword 최고 청크가 달라도 두 검색 경로의 합의가 보존된다.
        scores: Dict[str, float] = defaultdict(float)
        doc_map: Dict[str, Dict[str, Any]] = {}
        source_map: Dict[str, List[str]] = defaultdict(list)
        seen_vector_files = set()
        seen_keyword_files = set()

        for rank, doc in enumerate(vector_results):
            key = doc["file_path"]
            if key in seen_vector_files:
                continue
            seen_vector_files.add(key)
            scores[key] += 1.0 / (k + rank + 1)
            doc_map[key] = doc.copy()
            doc_map[key]["vector_similarity"] = float(doc.get("similarity", 0.0))
            doc_map[key]["vector_chunk_index"] = doc.get("chunk_index", 0)
            source_map[key].append("vector")

        for rank, doc in enumerate(keyword_results):
            key = doc["file_path"]
            if key in seen_keyword_files:
                continue
            seen_keyword_files.add(key)
            scores[key] += 1.0 / (k + rank + 1)
            if key not in doc_map:
                doc_map[key] = doc.copy()
            doc_map[key]["lexical_rank"] = float(doc.get("rank", 0.0))
            doc_map[key]["keyword_chunk_index"] = doc.get("chunk_index", 0)
            source_map[key].append("keyword")

        if not scores:
            return []

        # 0~1 범위 정규화
        max_score = max(scores.values())
        if max_score > 0:
            normalized = {k: v / max_score for k, v in scores.items()}
        else:
            normalized = scores

        # RRF 동점은 원시 lexical 신호와 vector 신호 순으로 결정한다. 입력 순서에
        # 기대지 않으면서도 비동점 RRF 순위는 그대로 유지한다.
        sorted_keys = sorted(
            normalized.keys(),
            key=lambda key: (
                normalized[key],
                float(doc_map[key].get("lexical_rank", 0.0)),
                float(doc_map[key].get("vector_similarity", 0.0)),
            ),
            reverse=True,
        )

        results = []
        for key in sorted_keys:
            doc = doc_map[key].copy()
            doc["rrf_score"] = normalized[key]
            doc["search_sources"] = source_map[key]
            results.append(doc)

        return results
