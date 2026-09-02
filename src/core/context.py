from contextvars import ContextVar, Token
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field


class UserContext(BaseModel):
    """사용자 요청/실행 컨텍스트를 캡슐화한 불변 DTO (DDD Value Object / DTO)"""
    model_config = ConfigDict(frozen=True, extra="allow")

    user_id: str = Field(description="사용자/소유자 ID")
    api_key: Optional[str] = Field(default=None, description="API 키 (예: cli:owner_id)")
    s3_endpoint_url: Optional[str] = None
    s3_access_key: Optional[str] = None
    s3_secret_key: Optional[str] = None
    s3_bucket: Optional[str] = None
    s3_region: Optional[str] = None
    s3_prefix: Optional[str] = None
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """기존 코드와의 호환성을 위한 dict 변환 메서드"""
        return self.model_dump()


_current_user_context: ContextVar[Optional[UserContext]] = ContextVar("current_user_context", default=None)


def get_current_user_context() -> Optional[UserContext]:
    """현재 스레드/비동기 컨텍스트의 UserContext 반환"""
    return _current_user_context.get()


def set_current_user_context(context: UserContext | dict[str, Any]) -> Token:
    """UserContext를 현재 컨텍스트에 설정"""
    if isinstance(context, dict):
        user_ctx = UserContext(**context)
    else:
        user_ctx = context
    return _current_user_context.set(user_ctx)


def reset_current_user_context(token: Token) -> None:
    """컨텍스트 복원"""
    _current_user_context.reset(token)
