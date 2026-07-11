from src.core.config import (
    WIKI_DIR,
    STORAGE_TYPE,
    S3_ENDPOINT_URL,
    S3_ACCESS_KEY_ID,
    S3_SECRET_ACCESS_KEY,
    S3_BUCKET_NAME
)
from src.core.storage.local import LocalStorageManager

# 유저별 스토리지 인스턴스 캐싱 딕셔너리
_storage_instances = {}

def StorageManager(user_id: str = None, db_manager = None):
    """
    유저 세션 설정(current_user_config) 또는 전역 환경 변수에 따라 
    동적 스토리지 매니저 인스턴스를 반환합니다. (지식베이스 DB 의존성 완전 제거)
    """
    global _storage_instances
    
    # 1. current_user_config ContextVar에서 실시간 설정 조회
    config = {}
    try:
        from src.core.config import current_user_config
        config = current_user_config.get() or {}
    except Exception:
        pass
        
    api_key = config.get("api_key")
    storage_cfg = config.get("storage", {})
    
    # 2. 컨텍스트 세션 토큰이 존재하고 캐싱되어 있다면 즉시 반환
    if api_key and api_key in _storage_instances:
        return _storage_instances[api_key]
        
    # 3. 컨텍스트 설정에 스토리지 정보가 포함된 경우 동적 인스턴스화
    if storage_cfg:
        storage_type = storage_cfg.get("storage_type", "local")
        if storage_type == "s3":
            from src.core.storage.s3 import S3StorageManager
            endpoint = storage_cfg.get("s3_endpoint_url")
            access_key = storage_cfg.get("s3_access_key_id")
            secret_key = storage_cfg.get("s3_secret_access_key")
            bucket = storage_cfg.get("s3_bucket_name")
            
            if not all([endpoint, access_key, secret_key, bucket]):
                raise ConnectionError("R2/S3 스토리지 필수 설정 필드가 누락되었습니다.")
                
            manager = S3StorageManager(
                endpoint_url=endpoint,
                access_key_id=access_key,
                secret_access_key=secret_key,
                bucket_name=bucket
            )
            if api_key:
                _storage_instances[api_key] = manager
            return manager
        else:
            manager = LocalStorageManager(root_dir=WIKI_DIR)
            if api_key:
                _storage_instances[api_key] = manager
            return manager

    # 4. 컨텍스트 세션이 없거나 정보가 없는 경우 (로컬 CLI 단독 기동 등)
    # 기존 .env 환경 변수를 사용하는 하위 호환성 폴백
    default_key = "_global_default"
    if default_key in _storage_instances:
        return _storage_instances[default_key]
        
    if STORAGE_TYPE == "s3":
        if not all([S3_ENDPOINT_URL, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, S3_BUCKET_NAME]):
            raise ConnectionError(
                "R2/S3 스토리지 연동 환경변수 설정이 누락되어 가동이 중지되었습니다. "
                "통합인증 및 에이전트 설정(mcp.json)에 스토리지 정보를 올바르게 주입해 주세요."
            )
        from src.core.storage.s3 import S3StorageManager
        manager = S3StorageManager(
            endpoint_url=S3_ENDPOINT_URL,
            access_key_id=S3_ACCESS_KEY_ID,
            secret_access_key=S3_SECRET_ACCESS_KEY,
            bucket_name=S3_BUCKET_NAME
        )
    else:
        manager = LocalStorageManager(root_dir=WIKI_DIR)
        
    _storage_instances[default_key] = manager
    return manager



