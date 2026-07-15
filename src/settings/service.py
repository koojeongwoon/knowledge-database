import base64
import hashlib
import os
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet, InvalidToken

from src.core.database.factory import DatabaseManager


class SettingsEncryptionError(RuntimeError):
    pass


class UserSettingsService:
    def __init__(self, db_manager=None):
        self.db_manager = db_manager or DatabaseManager()

    def _cipher(self) -> Fernet:
        master_key = os.getenv("SETTINGS_ENCRYPTION_KEY", "").strip()
        if not master_key:
            raise SettingsEncryptionError("SETTINGS_ENCRYPTION_KEY가 설정되지 않았습니다.")
        key = base64.urlsafe_b64encode(hashlib.sha256(master_key.encode("utf-8")).digest())
        return Fernet(key)

    def _encrypt(self, value: Optional[str]) -> Optional[str]:
        return self._cipher().encrypt(value.encode()).decode() if value else None

    def _decrypt(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        try:
            return self._cipher().decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise SettingsEncryptionError("저장된 설정을 복호화할 수 없습니다.") from exc

    def initialize(self) -> None:
        from src.core.database.migrations import run_database_migrations

        run_database_migrations(self.db_manager)

    def _get_row(self, owner_id: str):
        self.initialize()
        with self.db_manager.cursor() as cur:
            cur.execute("""
                SELECT openai_api_key_encrypted, storage_type, s3_endpoint_url,
                       s3_bucket_name, s3_access_key_id_encrypted,
                       s3_secret_access_key_encrypted, updated_at
                FROM knowledge_user_settings WHERE owner_id = %s;
            """, (owner_id,))
            return cur.fetchone()

    def save(self, owner_id: str, values: Dict[str, Any]) -> Dict[str, Any]:
        existing = self._get_row(owner_id)
        openai_key = self._encrypt(values.get("openai_api_key")) if values.get("openai_api_key") else (existing[0] if existing else None)
        access_key = self._encrypt(values.get("s3_access_key_id")) if values.get("s3_access_key_id") else (existing[4] if existing else None)
        secret_key = self._encrypt(values.get("s3_secret_access_key")) if values.get("s3_secret_access_key") else (existing[5] if existing else None)
        with self.db_manager.cursor() as cur:
            cur.execute("""
                INSERT INTO knowledge_user_settings (
                    owner_id, openai_api_key_encrypted, storage_type, s3_endpoint_url,
                    s3_bucket_name, s3_access_key_id_encrypted,
                    s3_secret_access_key_encrypted, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (owner_id) DO UPDATE SET
                    openai_api_key_encrypted = EXCLUDED.openai_api_key_encrypted,
                    storage_type = EXCLUDED.storage_type,
                    s3_endpoint_url = EXCLUDED.s3_endpoint_url,
                    s3_bucket_name = EXCLUDED.s3_bucket_name,
                    s3_access_key_id_encrypted = EXCLUDED.s3_access_key_id_encrypted,
                    s3_secret_access_key_encrypted = EXCLUDED.s3_secret_access_key_encrypted,
                    updated_at = CURRENT_TIMESTAMP;
            """, (owner_id, openai_key, values.get("storage_type", "s3"),
                  values.get("s3_endpoint_url") or None, values.get("s3_bucket_name") or None,
                  access_key, secret_key))
        return self.get_public(owner_id)

    def get_public(self, owner_id: str) -> Dict[str, Any]:
        row = self._get_row(owner_id)
        if not row:
            return {"configured": False, "openai_configured": False,
                    "storage_type": "s3", "s3_endpoint_url": "",
                    "s3_bucket_name": "", "s3_access_key_configured": False,
                    "s3_secret_key_configured": False, "updated_at": None}
        return {"configured": True, "openai_configured": bool(row[0]),
                "storage_type": row[1], "s3_endpoint_url": row[2] or "",
                "s3_bucket_name": row[3] or "", "s3_access_key_configured": bool(row[4]),
                "s3_secret_key_configured": bool(row[5]),
                "updated_at": row[6].isoformat() if row[6] else None}

    def get_runtime_config(self, owner_id: str) -> Dict[str, Any]:
        row = self._get_row(owner_id)
        if not row:
            return {}
        return {"openai_api_key": self._decrypt(row[0]), "storage": {
            "storage_type": "s3" if row[1] == "r2" else row[1],
            "s3_endpoint_url": row[2], "s3_bucket_name": row[3],
            "s3_access_key_id": self._decrypt(row[4]),
            "s3_secret_access_key": self._decrypt(row[5]),
        }}
