from typing import Any, Optional

from pydantic import BaseModel


class ToolResponse(BaseModel):
    """지식베이스 모든 API/도구 응답 규격을 통일화하는 데이터 모델 DTO"""
    success: bool
    code: str
    message: str
    data: Optional[Any] = None
    error_details: Optional[str] = None
