import functools
import json
import logging
import os
import traceback
import sys
from typing import Callable, TypeVar, Any

if sys.version_info >= (3, 10):
    from typing import ParamSpec
else:
    from typing_extensions import ParamSpec

from src.api.dto import ToolResponse
from src.api.exceptions import WikiBaseException
from src.core.config import current_user_config

logger = logging.getLogger("knowledge_base")

P = ParamSpec("P")
R = TypeVar("R")


def with_fresh_user_settings(func: Callable[P, R]) -> Callable[P, R]:
    """도구 실행마다 캐시된 최신 설정을 주입하고, 캐시 miss일 때만 DB를 조회한다."""
    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        base_config = current_user_config.get() or {}
        owner_id = base_config.get("user_id", "SYSTEM")
        if not owner_id or owner_id == "SYSTEM":
            return func(*args, **kwargs)

        from src.settings.service import UserSettingsService

        service = UserSettingsService()
        try:
            stored_config = service.get_runtime_config(owner_id)
        finally:
            service.db_manager.close()

        fresh_config = {
            "api_key": base_config.get("api_key"),
            "user_id": owner_id,
            **stored_config,
        }
        context_token = current_user_config.set(fresh_config)
        try:
            return func(*args, **kwargs)
        finally:
            current_user_config.reset(context_token)

    return wrapper

def tool_wrapper(func: Callable[P, R]) -> Callable[P, str]:
    """지식베이스 도구 실행 결과를 일관된 ToolResponse JSON String 규격으로 변환하는 통합 데코레이터"""
    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> str:
        try:
            result = func(*args, **kwargs)
            if isinstance(result, ToolResponse):
                return result.model_dump_json()
            if isinstance(result, dict) and "success" in result:
                return json.dumps(result, ensure_ascii=False)
            return ToolResponse(
                success=True,
                code="SUCCESS",
                message=f"{func.__name__} executed successfully.",
                data=result
            ).model_dump_json()
        except WikiBaseException as e:
            logger.warning(f"Business Exception in {func.__name__}: {e.code} - {str(e)}")
            return ToolResponse(
                success=False,
                code=e.code,
                message=str(e)
            ).model_dump_json()
        except Exception as e:
            tb = traceback.format_exc()
            logger.error(f"System Exception in {func.__name__}: {str(e)}\n{tb}")
            return ToolResponse(
                success=False,
                code="UNKNOWN_SYSTEM_ERROR",
                message="An unexpected system error occurred.",
                error_details=tb if os.getenv("DEBUG") else str(e)
            ).model_dump_json()
    return wrapper
