# 유저별 스토리지 인스턴스 캐싱 딕셔너리
_storage_instances = {}

def invalidate_storage_cache(owner_id: str = None):
    """사용자 설정 변경 후 캐시된 스토리지 클라이언트를 폐기합니다."""
    if owner_id:
        _storage_instances.pop(owner_id, None)

def StorageManager(user_id: str = None, db_manager = None):
    """
    DB에서 current_user_config에 주입한 사용자별 설정으로
    동적 스토리지 매니저 인스턴스를 반환합니다.
    """
    global _storage_instances
    
    # 1. current_user_config ContextVar에서 실시간 설정 조회
    config = {}
    try:
        from src.core.config import current_user_config
        config = current_user_config.get() or {}
    except Exception:
        pass
        
    storage_cfg = config.get("storage", {})
    user_id = config.get("user_id", user_id)
    if not user_id or user_id == "SYSTEM":
        raise ConnectionError("S3/R2 저장소를 선택하려면 owner_id가 필요합니다.")
    cache_key = user_id
    
    # 2. 컨텍스트 세션 토큰이 존재하고 캐싱되어 있다면 즉시 반환
    if cache_key and cache_key in _storage_instances:
        return _storage_instances[cache_key]
        
    # 3. 컨텍스트 설정에 스토리지 정보가 포함된 경우 동적 인스턴스화
    if storage_cfg:
        storage_type = storage_cfg.get("storage_type")
        if storage_type in ("s3", "r2"):
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
            if cache_key:
                _storage_instances[cache_key] = manager
            return manager
    raise ConnectionError("사용자의 S3/R2 저장소가 DB에 설정되지 않았습니다.")
