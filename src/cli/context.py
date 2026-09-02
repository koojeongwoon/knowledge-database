from typing import Any
from src.core.config import current_user_config
from src.core.context import set_current_user_context, reset_current_user_context, UserContext


def activate_owner_context(owner_id: str):
    """DB에서 사용자 설정을 읽어 UserContext 및 ContextVar를 활성화합니다."""
    from src.settings.service import UserSettingsService

    service = UserSettingsService()
    try:
        stored_config = service.get_runtime_config(owner_id)
    finally:
        service.db_manager.close()
    if not stored_config:
        raise RuntimeError(f"사용자 {owner_id}의 OpenAI/S3 설정이 DB에 없습니다.")
    
    ctx_data = {
        "api_key": f"cli:{owner_id}",
        "user_id": owner_id,
        **stored_config,
    }
    # UserContext DTO 설정
    set_current_user_context(ctx_data)
    # 기존 current_user_config 호환성 유지
    return current_user_config.set(ctx_data)


def deactivate_owner_context(token: Any) -> None:
    """컨텍스트 토큰을 리셋합니다."""
    current_user_config.reset(token)
