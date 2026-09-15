import base64
import hashlib
import os
import threading
from copy import deepcopy
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet, InvalidToken

from src.core.database.factory import DatabaseManager


_runtime_config_cache: Dict[str, Dict[str, Any]] = {}
_runtime_config_cache_lock = threading.Lock()


def invalidate_user_settings_cache(owner_id: str) -> None:
    with _runtime_config_cache_lock:
        _runtime_config_cache.pop(owner_id, None)


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
                       s3_secret_access_key_encrypted, updated_at,
                       llm_auth_type, embedding_api_key_encrypted, llm_model_name
                FROM knowledge_user_settings WHERE owner_id = %s;
            """, (owner_id,))
            return cur.fetchone()

    def save(self, owner_id: str, values: Dict[str, Any]) -> Dict[str, Any]:
        existing = self._get_row(owner_id)

        # LLM preference and embedding fields. Provider credentials stay in Broker.
        llm_auth_type = values.get("llm_auth_type") or (existing[7] if existing and existing[7] else "api_key")
        if llm_auth_type not in ("api_key", "openai_oauth"):
            llm_auth_type = "api_key"

        llm_model_name = values.get("llm_model_name") or (existing[9] if existing and existing[9] else "gpt-5.6-luna")

        openai_key = (
            self._encrypt(values.get("openai_api_key"))
            if values.get("openai_api_key") is not None
            else (existing[0] if existing else None)
        )
        embedding_key = (
            self._encrypt(values.get("embedding_api_key"))
            if values.get("embedding_api_key") is not None
            else (existing[8] if existing else None)
        )

        # Storage fields
        access_key = (
            self._encrypt(values.get("s3_access_key_id"))
            if values.get("s3_access_key_id") is not None
            else (existing[4] if existing else None)
        )
        secret_key = (
            self._encrypt(values.get("s3_secret_access_key"))
            if values.get("s3_secret_access_key") is not None
            else (existing[5] if existing else None)
        )

        storage_type = values.get("storage_type", existing[1] if existing else "s3")
        s3_endpoint = values.get("s3_endpoint_url", existing[2] if existing else None) or None
        s3_bucket = values.get("s3_bucket_name", existing[3] if existing else None) or None

        with self.db_manager.cursor() as cur:
            cur.execute("""
                INSERT INTO knowledge_user_settings (
                    owner_id, openai_api_key_encrypted, storage_type, s3_endpoint_url,
                    s3_bucket_name, s3_access_key_id_encrypted,
                    s3_secret_access_key_encrypted, updated_at,
                    llm_auth_type, embedding_api_key_encrypted, llm_model_name
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s, %s, %s)
                ON CONFLICT (owner_id) DO UPDATE SET
                    openai_api_key_encrypted = EXCLUDED.openai_api_key_encrypted,
                    storage_type = EXCLUDED.storage_type,
                    s3_endpoint_url = EXCLUDED.s3_endpoint_url,
                    s3_bucket_name = EXCLUDED.s3_bucket_name,
                    s3_access_key_id_encrypted = EXCLUDED.s3_access_key_id_encrypted,
                    s3_secret_access_key_encrypted = EXCLUDED.s3_secret_access_key_encrypted,
                    llm_auth_type = EXCLUDED.llm_auth_type,
                    embedding_api_key_encrypted = EXCLUDED.embedding_api_key_encrypted,
                    llm_model_name = EXCLUDED.llm_model_name,
                    updated_at = CURRENT_TIMESTAMP;
            """, (
                owner_id, openai_key, storage_type,
                s3_endpoint, s3_bucket, access_key, secret_key,
                llm_auth_type, embedding_key, llm_model_name
            ))

        invalidate_user_settings_cache(owner_id)
        return self.get_public(owner_id)

    def switch_llm_auth_type(self, owner_id: str, auth_type: str) -> Dict[str, Any]:
        """
        LLM 인증 방식을 'api_key' 또는 'openai_oauth'로 전환합니다.
        """
        if auth_type not in ("api_key", "openai_oauth"):
            raise ValueError(f"유효하지 않은 auth_type입니다: {auth_type}. ('api_key', 'openai_oauth' 중 선택)")
        return self.save(owner_id, {"llm_auth_type": auth_type})

    def get_public(self, owner_id: str) -> Dict[str, Any]:
        row = self._get_row(owner_id)
        from src.indexing.infrastructure.broker_chat import BrokerStructuredChat
        try:
            codex_linked = BrokerStructuredChat(owner_id).linked()
        except Exception:
            codex_linked = False

        if not row:
            return {
                "configured": False,
                "llm_auth_type": "openai_oauth" if codex_linked else "api_key",
                "llm_model_name": "gpt-5.6-luna",
                "openai_configured": False,
                "openai_oauth_configured": codex_linked,
                "embedding_configured": False,
                "storage_type": "s3",
                "s3_endpoint_url": "",
                "s3_bucket_name": "",
                "s3_access_key_configured": False,
                "s3_secret_key_configured": False,
                "updated_at": None,
            }

        openai_configured = bool(row[0])
        embedding_configured = (
            bool(row[8]) or bool(row[0])
        )

        return {
            "configured": True,
            "llm_auth_type": row[7] if row[7] else "api_key",
            "llm_model_name": row[9] if row[9] else "gpt-5.6-luna",
            "openai_configured": openai_configured,
            "openai_oauth_configured": codex_linked,
            "embedding_configured": embedding_configured,
            "storage_type": row[1] if len(row) > 1 else "s3",
            "s3_endpoint_url": (row[2] or "") if len(row) > 2 else "",
            "s3_bucket_name": (row[3] or "") if len(row) > 3 else "",
            "s3_access_key_configured": bool(row[4]) if len(row) > 4 else False,
            "s3_secret_key_configured": bool(row[5]) if len(row) > 5 else False,
            "updated_at": row[6].isoformat() if len(row) > 6 and row[6] else None,
        }

    def get_llm_preferences(self, owner_id: str) -> Dict[str, str]:
        with self.db_manager.cursor() as cur:
            cur.execute('SELECT llm_model_name,llm_auth_type FROM knowledge_user_settings WHERE owner_id=%s',(owner_id,))
            row=cur.fetchone()
        return {'model':row[0] if row and row[0] else 'gpt-4o-mini',
                'auth_type':row[1] if row and row[1] else 'api_key'}

    def get_llm_model(self, owner_id: str) -> str:
        with self.db_manager.cursor() as cur:
            cur.execute('SELECT llm_model_name FROM knowledge_user_settings WHERE owner_id=%s', (owner_id,))
            row = cur.fetchone()
        return row[0] if row and row[0] else 'gpt-4o-mini'

    def get_storage_runtime_config(self, owner_id: str) -> Dict[str, Any]:
        """Embedding/search context loads storage only, without any provider credential cache."""
        self.initialize()
        with self.db_manager.cursor() as cur:
            cur.execute("""
                SELECT storage_type,s3_endpoint_url,s3_bucket_name,
                       s3_access_key_id_encrypted,s3_secret_access_key_encrypted
                FROM knowledge_user_settings WHERE owner_id=%s
            """, (owner_id,))
            row = cur.fetchone()
        if not row:
            return {}
        return {'storage': {
            'storage_type':'s3' if row[0]=='r2' else row[0],
            's3_endpoint_url':row[1],'s3_bucket_name':row[2],
            's3_access_key_id':self._decrypt(row[3]),'s3_secret_access_key':self._decrypt(row[4]),
        }}

    def get_runtime_config(self, owner_id: str) -> Dict[str, Any]:
        with _runtime_config_cache_lock:
            cached = _runtime_config_cache.get(owner_id)
        if cached is not None:
            return deepcopy(cached)

        row = self._get_row(owner_id)
        if not row:
            return {}

        auth_type = row[7] if row[7] else "api_key"
        openai_api_key = None
        embedding_api_key = None
        if os.getenv("EMBEDDING_PROVIDER") != "broker":
            openai_api_key = self._decrypt(row[0])
            embedding_api_key = self._decrypt(row[8]) or openai_api_key

        config = {
            "llm_auth_type": auth_type,
            "llm_model_name": row[9] if row[9] else "gpt-5.6-luna",
            "openai_api_key": openai_api_key,
            "embedding_api_key": embedding_api_key,
            "storage": {
                "storage_type": "s3" if row[1] == "r2" else row[1],
                "s3_endpoint_url": row[2],
                "s3_bucket_name": row[3],
                "s3_access_key_id": self._decrypt(row[4]),
                "s3_secret_access_key": self._decrypt(row[5]),
            },
        }

        with _runtime_config_cache_lock:
            _runtime_config_cache[owner_id] = deepcopy(config)
        return config
