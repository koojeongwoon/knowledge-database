import json
import mimetypes
import posixpath
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from src.core.config import current_user_config
from src.core.storage.factory import StorageManager
from src.settings.service import UserSettingsService


MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_MARKDOWN_BYTES = 2 * 1024 * 1024
MAX_MCP_READ_BYTES = 2 * 1024 * 1024
INBOX_ROOT = "inbox"


def _safe_filename(filename: str) -> str:
    name = posixpath.basename((filename or "file").replace("\\", "/")).strip()
    name = re.sub(r"[^0-9A-Za-z가-힣._ -]+", "_", name).strip(" .")
    return name[:180] or "file"


def _validate_url(url: str) -> str:
    value = (url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("http 또는 https 링크만 등록할 수 있습니다.")
    return value


class InboxService:
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

    @staticmethod
    def _metadata_path(item_id: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{32}", item_id or ""):
            raise ValueError("올바르지 않은 Inbox 항목입니다.")
        return f"{INBOX_ROOT}/{item_id}/metadata.json"

    def list_items(self) -> List[Dict[str, Any]]:
        storage = self._storage()
        items = []
        for path in storage.list_files(INBOX_ROOT, "metadata.json"):
            try:
                item = json.loads(storage.read_text(path))
                if item.get("id") and item.get("type") in ("file", "link"):
                    items.append(item)
            except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError):
                continue
        return sorted(items, key=lambda item: item.get("created_at", ""), reverse=True)

    def get_item(self, item_id: str) -> Dict[str, Any]:
        storage = self._storage()
        path = self._metadata_path(item_id)
        if not storage.exists(path):
            raise FileNotFoundError(item_id)
        return json.loads(storage.read_text(path))

    def add_link(self, url: str, title: Optional[str] = None, note: Optional[str] = None) -> Dict[str, Any]:
        safe_url = _validate_url(url)
        item_id = uuid.uuid4().hex
        item = {
            "id": item_id,
            "type": "link",
            "title": (title or safe_url)[:300],
            "url": safe_url,
            "note": (note or "")[:2000],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._storage().write_text(self._metadata_path(item_id), json.dumps(item, ensure_ascii=False))
        return item

    def add_file(
        self,
        filename: str,
        content: bytes,
        content_type: Optional[str] = None,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not content:
            raise ValueError("빈 파일은 업로드할 수 없습니다.")
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError("파일은 최대 25MB까지 업로드할 수 있습니다.")
        safe_name = _safe_filename(filename)
        item_id = uuid.uuid4().hex
        file_path = f"{INBOX_ROOT}/{item_id}/content/{safe_name}"
        media_type = (content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream")[:255]
        storage = self._storage()
        storage.write_bytes(file_path, content, media_type)
        item = {
            "id": item_id,
            "type": "file",
            "title": safe_name,
            "filename": safe_name,
            "content_type": media_type,
            "size": len(content),
            "storage_path": file_path,
            "note": (note or "")[:2000],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        storage.write_text(self._metadata_path(item_id), json.dumps(item, ensure_ascii=False))
        return item

    def add_markdown(
        self,
        title: str,
        content: str,
        source_kind: str,
        original_filename: Optional[str] = None,
        original_url: Optional[str] = None,
        media_type: Optional[str] = None,
        extraction_complete: bool = True,
        warnings: Optional[List[str]] = None,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        safe_title = (title or "").strip()
        if not safe_title:
            raise ValueError("Markdown 제목은 필수입니다.")
        markdown = (content or "").strip()
        if not markdown:
            raise ValueError("Markdown 본문은 비어 있을 수 없습니다.")
        encoded = markdown.encode("utf-8")
        if len(encoded) > MAX_MARKDOWN_BYTES:
            raise ValueError("Markdown 본문은 최대 2MB까지 저장할 수 있습니다.")
        if source_kind not in ("chat_attachment", "external_link", "user_text", "other"):
            raise ValueError("지원하지 않는 Markdown 출처 유형입니다.")
        if source_kind == "external_link" and not original_url:
            raise ValueError("외부 링크 Markdown에는 원본 URL이 필요합니다.")

        safe_url = _validate_url(original_url) if original_url else None
        safe_original_filename = _safe_filename(original_filename) if original_filename else None
        safe_warnings = [str(value).strip()[:500] for value in (warnings or []) if str(value).strip()][:20]
        item_id = uuid.uuid4().hex
        filename = f"{_safe_filename(safe_title)[:160] or 'document'}.md"
        file_path = f"{INBOX_ROOT}/{item_id}/content/{filename}"
        item = {
            "id": item_id,
            "type": "file",
            "subtype": "derived_markdown",
            "title": safe_title[:300],
            "filename": filename,
            "content_type": "text/markdown",
            "size": len(encoded),
            "storage_path": file_path,
            "note": (note or "")[:2000],
            "status": "captured",
            "authority": "unverified",
            "indexed": False,
            "source": {
                "kind": source_kind,
                "original_filename": safe_original_filename,
                "original_url": safe_url,
                "media_type": (media_type or "")[:255] or None,
            },
            "extraction": {
                "method": "client_agent",
                "complete": bool(extraction_complete),
                "warnings": safe_warnings,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        storage = self._storage()
        storage.write_text(file_path, markdown + "\n")
        storage.write_text(self._metadata_path(item_id), json.dumps(item, ensure_ascii=False))
        return item

    def read_file(self, item_id: str):
        item = self.get_item(item_id)
        if item.get("type") != "file" or not item.get("storage_path"):
            raise ValueError("파일 Inbox 항목이 아닙니다.")
        expected_prefix = f"{INBOX_ROOT}/{item_id}/content/"
        if not item["storage_path"].startswith(expected_prefix):
            raise ValueError("올바르지 않은 파일 경로입니다.")
        return item, self._storage().read_bytes(item["storage_path"])

    def read_for_learning(self, item_id: str) -> Dict[str, Any]:
        item = self.get_item(item_id)
        if item.get("type") == "link":
            return {"item": item, "content_status": "metadata_only", "content": None}
        if item.get("type") != "file" or not item.get("storage_path"):
            raise ValueError("지원하지 않는 Inbox 항목입니다.")

        expected_prefix = f"{INBOX_ROOT}/{item_id}/content/"
        if not item["storage_path"].startswith(expected_prefix):
            raise ValueError("올바르지 않은 파일 경로입니다.")
        if int(item.get("size") or 0) > MAX_MCP_READ_BYTES:
            return {"item": item, "content_status": "too_large", "content": None}

        filename = str(item.get("filename") or "").lower()
        content_type = str(item.get("content_type") or "").lower()
        readable = (
            item.get("subtype") == "derived_markdown"
            or content_type.startswith("text/")
            or filename.endswith((".md", ".txt", ".json", ".csv"))
        )
        if not readable:
            return {"item": item, "content_status": "unsupported", "content": None}
        try:
            content = self._storage().read_text(item["storage_path"])
        except UnicodeDecodeError:
            return {"item": item, "content_status": "invalid_text", "content": None}
        return {"item": item, "content_status": "available", "content": content}
