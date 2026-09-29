from collections import OrderedDict
import hashlib
from threading import RLock

# Only cached references are bounded; callers may still own evicted managers.
MAX_STORAGE_INSTANCES = 32
_storage_instances = OrderedDict()
_storage_cache_lock = RLock()
# Stable, bounded creation locks prevent duplicate clients for the same owner.
_storage_creation_locks = tuple(RLock() for _ in range(64))


def _creation_lock(owner_id: str):
    index = int(hashlib.sha256(owner_id.encode("utf-8")).hexdigest(), 16) % len(_storage_creation_locks)
    return _storage_creation_locks[index]

def invalidate_storage_cache(owner_id: str = None):
    """사용자 설정 변경 후 캐시된 스토리지 클라이언트를 폐기합니다."""
    if owner_id:
        # Wait for construction already running on this stripe before removing
        # its cached reference. Other cache hits do not acquire this lock.
        with _creation_lock(owner_id):
            with _storage_cache_lock:
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
    
    # 2. 컨텍스트 설정에 스토리지 정보가 포함된 경우 동적 인스턴스화
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

            fingerprint = (storage_type, endpoint, access_key, secret_key, bucket)
            with _storage_cache_lock:
                cached = _storage_instances.get(cache_key)
                if cached and cached[0] == fingerprint:
                    _storage_instances.move_to_end(cache_key)
                    return cached[1]

            with _creation_lock(cache_key):
                with _storage_cache_lock:
                    cached = _storage_instances.get(cache_key)
                    if cached and cached[0] == fingerprint:
                        _storage_instances.move_to_end(cache_key)
                        return cached[1]

                # Creation may be slow; cache hits never wait on this work.
                manager = S3StorageManager(
                    endpoint_url=endpoint,
                    access_key_id=access_key,
                    secret_access_key=secret_key,
                    bucket_name=bucket
                )
                with _storage_cache_lock:
                    _storage_instances[cache_key] = (fingerprint, manager)
                    _storage_instances.move_to_end(cache_key)
                    while len(_storage_instances) > MAX_STORAGE_INSTANCES:
                        # Do not close a client that an in-flight caller still uses.
                        _storage_instances.popitem(last=False)
            return manager
    raise ConnectionError("사용자의 S3/R2 저장소가 DB에 설정되지 않았습니다.")
