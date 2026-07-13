import re
from typing import List, Dict, Any, Optional

class Edge:
    """문서 간의 위키링크 방향성 관계를 나타내는 도메인 엔티티"""
    def __init__(self, source_path: str, target_topic: str, weight: float = 1.0):
        self.source_path = source_path
        self.target_topic = target_topic.strip()
        self.weight = weight

    @classmethod
    def create_with_4signal(
        cls, 
        source_path: str, 
        target_topic: str, 
        source_meta: Optional[Dict[str, Any]], 
        target_meta: Optional[Dict[str, Any]],
        custom_relations: List[Dict[str, Any]] = None
    ) -> "Edge":
        """
        4대 신호 규칙 및 custom_relations 메타데이터를 사용하여 
        맥락에 맞는 엣지 인스턴스와 가중치(weight)를 생성합니다. (도메인 정책 위임)
        """
        # 1. 직접 링크는 기본적으로 본문에 존재하므로 3.0점
        signals = [3.0]
        t_key = target_topic.lower()
        
        if source_meta and target_meta:
            # 2. 자료 중복 (Source overlap)
            s_source = source_meta.get("source_path")
            t_source = target_meta.get("source_path")
            if s_source and t_source and s_source == t_source:
                signals.append(4.0)
                
            # 3. 타입 친화도 (Type affinity)
            s_type = source_meta.get("type")
            t_type = target_meta.get("type")
            if s_type and t_type and s_type == t_type:
                signals.append(1.0)
                
        weight = max(signals)
        
        # 4. custom_relations 수동 지정 오버라이드
        if custom_relations:
            for relation in custom_relations:
                link_str = relation.get("link", "")
                link_topic = re.sub(r'[\[\]]', '', link_str).split('/')[-1].split('.')[0].strip().lower()
                if link_topic == t_key:
                    try:
                        weight = float(relation.get("weight", 1.0))
                    except (ValueError, TypeError):
                        pass
                    break
                    
        return cls(source_path, target_topic, weight)


class Chunk:
    """RAG 인덱싱 및 유사도 검색 단위가 되는 지식 조각 (Value Object)"""
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
        raw_frontmatter: Dict[str, Any], 
        content_hash: str
    ):
        self.file_path = file_path
        self.chunk_index = chunk_index
        self.doc_type = doc_type
        self.title = title
        self.description = description
        self.tags = tags
        self.content = content
        self.parent_content = parent_content
        self.raw_frontmatter = raw_frontmatter
        self.content_hash = content_hash

    def to_embedding_text(self) -> str:
        """임베딩 서비스에 보낼 표준 학습/참조 텍스트 빌드 (도메인 행동 위임)"""
        return f"Title: {self.title}\nDescription: {self.description}\n\nContent:\n{self.content}"

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 직렬화 변환"""
        visibility = self.raw_frontmatter.get("visibility", "public") if isinstance(self.raw_frontmatter, dict) else "public"
        if visibility not in ("public", "private"):
            visibility = "public"
        return {
            "file_path": self.file_path,
            "chunk_index": self.chunk_index,
            "doc_type": self.doc_type,
            "title": self.title,
            "description": self.description,
            "tags": self.tags,
            "content": self.content,
            "parent_content": self.parent_content,
            "raw_frontmatter": self.raw_frontmatter,
            "content_hash": self.content_hash,
            "visibility": visibility
        }
