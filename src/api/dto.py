from typing import Any, Optional, List, Dict
from pydantic import BaseModel, ConfigDict, Field


class ToolResponse(BaseModel):
    """지식베이스 모든 API/도구 응답 규격을 통일화하는 데이터 모델 DTO"""
    model_config = ConfigDict(frozen=True)

    success: bool
    code: str
    message: str
    data: Optional[Any] = None
    error_details: Optional[str] = None


class SearchQueryDTO(BaseModel):
    """검색 요청 파라미터를 캡슐화한 불변 DTO"""
    model_config = ConfigDict(frozen=True)

    query: str = Field(description="검색 질의어")
    limit: int = Field(default=5, ge=1, le=100, description="반환 최대 문서 수")
    owner_id: Optional[str] = Field(default=None, description="소유자 ID")


class SearchDocumentDTO(BaseModel):
    """검색 결과 단일 문서 항목을 표현하는 불변 DTO"""
    model_config = ConfigDict(frozen=True)

    file_path: str
    chunk_index: int
    doc_type: str
    title: str
    description: str = ""
    tags: List[str] = Field(default_factory=list)
    content: str
    parent_content: str = ""
    similarity: float
    raw_frontmatter: Dict[str, Any] = Field(default_factory=dict)
    search_sources: List[str] = Field(default_factory=list)
    graph_context: Optional[List[Dict[str, Any]]] = None


class IndexingSummaryDTO(BaseModel):
    """인덱싱 완료 요약 통계 DTO"""
    model_config = ConfigDict(frozen=True)

    created: int = 0
    updated: int = 0
    deleted: int = 0
    skipped: int = 0

