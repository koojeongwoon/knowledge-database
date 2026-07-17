import posixpath
from typing import Dict, List

from src.core.config import current_user_config
from src.core.storage.factory import StorageManager
from src.settings.service import UserSettingsService


ALLOWED_DOCUMENT_ROOTS = ("qa", "topics")


def normalize_document_path(file_path: str) -> str:
    normalized = posixpath.normpath((file_path or "").replace("\\", "/")).lstrip("/")
    if (
        normalized in ("", ".")
        or normalized.startswith("../")
        or not normalized.lower().endswith(".md")
        or normalized.split("/", 1)[0] not in ALLOWED_DOCUMENT_ROOTS
    ):
        raise ValueError("qa 또는 topics 아래의 Markdown 문서만 열 수 있습니다.")
    return normalized


class DocumentBrowserService:
    """Reads documents through the authenticated owner's storage configuration."""

    def __init__(self, owner_id: str):
        self.owner_id = owner_id

    def _storage(self):
        service = UserSettingsService()
        try:
            runtime_config = service.get_runtime_config(self.owner_id)
        finally:
            service.db_manager.close()
        if not runtime_config.get("storage"):
            raise ConnectionError("먼저 S3/R2 저장소 연결 정보를 등록해 주세요.")
        token = current_user_config.set({"user_id": self.owner_id, **runtime_config})
        try:
            return StorageManager(user_id=self.owner_id)
        finally:
            current_user_config.reset(token)

    def list_documents(self) -> List[Dict[str, str]]:
        storage = self._storage()
        paths = []
        for root in ALLOWED_DOCUMENT_ROOTS:
            paths.extend(storage.list_files(root, "*.md"))
        documents = []
        for path in sorted(set(paths), reverse=True):
            try:
                normalized = normalize_document_path(path)
            except ValueError:
                continue
            documents.append({
                "path": normalized,
                "name": posixpath.basename(normalized),
                "section": normalized.split("/", 1)[0],
            })
        return documents

    def read_document(self, file_path: str) -> Dict[str, str]:
        normalized = normalize_document_path(file_path)
        storage = self._storage()
        if not storage.exists(normalized):
            raise FileNotFoundError(normalized)
        return {
            "path": normalized,
            "name": posixpath.basename(normalized),
            "content": storage.read_text(normalized),
        }
