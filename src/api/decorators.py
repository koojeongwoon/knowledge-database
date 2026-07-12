import functools
import json
import logging
import os
import traceback

from src.api.dto import ToolResponse
from src.api.exceptions import WikiBaseException

logger = logging.getLogger("knowledge_base")

def tool_wrapper(func):
    """지식베이스 도구 실행 결과를 일관된 ToolResponse JSON String 규격으로 변환하는 통합 데코레이터"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
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
