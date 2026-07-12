from collections import defaultdict
from typing import List, Dict, Any, Tuple


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


class RankFusion:
    """두 검색 경로(벡터, 키워드)의 결과를 결합하는 RRF 도메인 서비스"""
    @staticmethod
    def rrf_fusion(
        vector_results: List[Dict[str, Any]],
        keyword_results: List[Dict[str, Any]],
        k: int = 60
    ) -> List[Dict[str, Any]]:
        """Reciprocal Rank Fusion 알고리즘 결합 연산 규칙 (도메인 정책 격리)"""
        scores: Dict[Tuple[str, int], float] = defaultdict(float)
        doc_map: Dict[Tuple[str, int], Dict[str, Any]] = {}
        source_map: Dict[Tuple[str, int], List[str]] = defaultdict(list)

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

        # 0~1 범위 정규화
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
