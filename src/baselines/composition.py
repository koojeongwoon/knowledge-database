from src.baselines.service import BaselineRepository, BaselineService
from src.core.storage.factory import StorageManager


def create_baseline_service(owner_id: str, db_manager) -> BaselineService:
    return BaselineService(
        owner_id,
        StorageManager(owner_id, db_manager),
        BaselineRepository(db_manager),
    )
