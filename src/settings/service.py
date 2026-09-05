import base64
import hashlib
import os
import threading
import time
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
                       llm_auth_type, openai_oauth_access_token_encrypted,
                       openai_oauth_refresh_token_encrypted, openai_oauth_expires_at,
                       embedding_api_key_encrypted, llm_model_name
                FROM knowledge_user_settings WHERE owner_id = %s;
            """, (owner_id,))
            return cur.fetchone()

    def save(self, owner_id: str, values: Dict[str, Any]) -> Dict[str, Any]:
        existing = self._get_row(owner_id)
        
        # LLM & OAuth & Embedding fields
        llm_auth_type = values.get("llm_auth_type") or (existing[7] if existing and existing[7] else "api_key")
        if llm_auth_type not in ("api_key", "openai_oauth"):
            llm_auth_type = "api_key"

        llm_model_name = values.get("llm_model_name") or (existing[12] if existing and len(existing) > 12 and existing[12] else "gpt-5.6-luna")

        openai_key = (
            self._encrypt(values.get("openai_api_key"))
            if values.get("openai_api_key") is not None
            else (existing[0] if existing else None)
        )
        oauth_access_token = (
            self._encrypt(values.get("openai_oauth_access_token"))
            if values.get("openai_oauth_access_token") is not None
            else (existing[8] if existing else None)
        )
        oauth_refresh_token = (
            self._encrypt(values.get("openai_oauth_refresh_token"))
            if values.get("openai_oauth_refresh_token") is not None
            else (existing[9] if existing else None)
        )
        oauth_expires_at = (
            values.get("openai_oauth_expires_at")
            if "openai_oauth_expires_at" in values
            else (existing[10] if existing else None)
        )
        embedding_key = (
            self._encrypt(values.get("embedding_api_key"))
            if values.get("embedding_api_key") is not None
            else (existing[11] if existing else None)
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
                    llm_auth_type, openai_oauth_access_token_encrypted,
                    openai_oauth_refresh_token_encrypted, openai_oauth_expires_at,
                    embedding_api_key_encrypted, llm_model_name
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (owner_id) DO UPDATE SET
                    openai_api_key_encrypted = EXCLUDED.openai_api_key_encrypted,
                    storage_type = EXCLUDED.storage_type,
                    s3_endpoint_url = EXCLUDED.s3_endpoint_url,
                    s3_bucket_name = EXCLUDED.s3_bucket_name,
                    s3_access_key_id_encrypted = EXCLUDED.s3_access_key_id_encrypted,
                    s3_secret_access_key_encrypted = EXCLUDED.s3_secret_access_key_encrypted,
                    llm_auth_type = EXCLUDED.llm_auth_type,
                    openai_oauth_access_token_encrypted = EXCLUDED.openai_oauth_access_token_encrypted,
                    openai_oauth_refresh_token_encrypted = EXCLUDED.openai_oauth_refresh_token_encrypted,
                    openai_oauth_expires_at = EXCLUDED.openai_oauth_expires_at,
                    embedding_api_key_encrypted = EXCLUDED.embedding_api_key_encrypted,
                    llm_model_name = EXCLUDED.llm_model_name,
                    updated_at = CURRENT_TIMESTAMP;
            """, (
                owner_id, openai_key, storage_type,
                s3_endpoint, s3_bucket, access_key, secret_key,
                llm_auth_type, oauth_access_token, oauth_refresh_token,
                oauth_expires_at, embedding_key, llm_model_name
            ))

        invalidate_user_settings_cache(owner_id)
        return self.get_public(owner_id)

    def save_openai_oauth_tokens(
        self, owner_id: str, access_token: str, refresh_token: str, expires_at: int
    ) -> Dict[str, Any]:
        """
        OAuth 인증 완료 후 토큰을 저장하고 LLM 인증 모드를 'openai_oauth'로 전환합니다.
        """
        return self.save(owner_id, {
            "llm_auth_type": "openai_oauth",
            "openai_oauth_access_token": access_token,
            "openai_oauth_refresh_token": refresh_token,
            "openai_oauth_expires_at": expires_at,
        })

    def switch_llm_auth_type(self, owner_id: str, auth_type: str) -> Dict[str, Any]:
        """
        LLM 인증 방식을 'api_key' 또는 'openai_oauth'로 전환합니다.
        """
        if auth_type not in ("api_key", "openai_oauth"):
            raise ValueError(f"유효하지 않은 auth_type입니다: {auth_type}. ('api_key', 'openai_oauth' 중 선택)")
        return self.save(owner_id, {"llm_auth_type": auth_type})

    def _resolve_iam_user_id(self, owner_id: str) -> str:
        """
        knowledge_users 테이블에서 SSO auth_id(sub_val)를 조회하여 반환합니다.
        """
        try:
            with self.db_manager.cursor() as cur:
                cur.execute("SELECT sub_val FROM knowledge_users WHERE user_id = %s OR sub_val = %s LIMIT 1;", (owner_id, owner_id))
                u_row = cur.fetchone()
                if u_row and u_row[0]:
                    return u_row[0]
        except Exception:
            pass
        return owner_id

    def get_public(self, owner_id: str) -> Dict[str, Any]:
        row = self._get_row(owner_id)

        # IAM 중앙 인증 서버로부터 연동 정보 및 자격증명 상태 조회
        iam_codex_linked = False
        iam_openai_configured = False
        iam_embedding_configured = False

        try:
            from src.settings.iam_codex_client import IAMCodexClient
            iam_client = IAMCodexClient()
            lookup_user_id = self._resolve_iam_user_id(owner_id)

            # 1. AI 자격증명 번들 조회 (Codex 토큰 발급 가능 여부 및 API Key)
            bundle = iam_client.get_ai_bundle(user_id=lookup_user_id)
            if bundle:
                if bundle.get("codex", {}).get("linked"):
                    iam_codex_linked = True
                if bundle.get("openai_api_key", {}).get("configured"):
                    iam_openai_configured = True
                if bundle.get("embedding_api_key", {}).get("configured"):
                    iam_embedding_configured = True

            # 2. 번들에서 linked가 아니더라도, IAM 서버에 Codex 계정이 등록되어 있는지 status API로 추가 확인
            if not iam_codex_linked:
                status_res = iam_client.get_status(user_id=lookup_user_id)
                if status_res and (status_res.get("user_linked") or status_res.get("org_linked")):
                    iam_codex_linked = True
        except Exception as exc:
            print(f"Warning: Failed to check IAM Codex/Credentials status: {exc}")

        if not row:
            return {
                "configured": False,
                "llm_auth_type": "openai_oauth" if iam_codex_linked else "api_key",
                "llm_model_name": "gpt-5.6-luna",
                "openai_configured": iam_openai_configured,
                "openai_oauth_configured": iam_codex_linked,
                "openai_oauth_expires_at": None,
                "embedding_configured": iam_embedding_configured or iam_openai_configured,
                "storage_type": "s3",
                "s3_endpoint_url": "",
                "s3_bucket_name": "",
                "s3_access_key_configured": False,
                "s3_secret_key_configured": False,
                "updated_at": None,
            }

        local_oauth_configured = bool(row[8]) if len(row) > 8 and row[8] else False
        openai_oauth_configured = local_oauth_configured or iam_codex_linked
        local_llm_auth_type = row[7] if len(row) > 7 and row[7] else "api_key"

        # 로컬에 OAuth 토큰이 없더라도 IAM에 연동되어 있고 사용자가 oauth로 설정했거나 연동된 경우
        llm_auth_type = local_llm_auth_type
        if not local_oauth_configured and iam_codex_linked and local_llm_auth_type == "openai_oauth":
            llm_auth_type = "openai_oauth"

        openai_configured = (bool(row[0]) if len(row) > 0 else False) or iam_openai_configured
        embedding_configured = (
            (bool(row[11]) if len(row) > 11 and row[11] else False)
            or (bool(row[0]) if len(row) > 0 and row[0] else False)
            or iam_embedding_configured
            or iam_openai_configured
        )

        return {
            "configured": True,
            "llm_auth_type": llm_auth_type,
            "llm_model_name": row[12] if len(row) > 12 and row[12] else "gpt-5.6-luna",
            "openai_configured": openai_configured,
            "openai_oauth_configured": openai_oauth_configured,
            "openai_oauth_expires_at": row[10] if len(row) > 10 else None,
            "embedding_configured": embedding_configured,
            "storage_type": row[1] if len(row) > 1 else "s3",
            "s3_endpoint_url": (row[2] or "") if len(row) > 2 else "",
            "s3_bucket_name": (row[3] or "") if len(row) > 3 else "",
            "s3_access_key_configured": bool(row[4]) if len(row) > 4 else False,
            "s3_secret_key_configured": bool(row[5]) if len(row) > 5 else False,
            "updated_at": row[6].isoformat() if len(row) > 6 and row[6] else None,
        }

    def get_runtime_config(self, owner_id: str, allow_refresh: bool = True) -> Dict[str, Any]:
        with _runtime_config_cache_lock:
            cached = _runtime_config_cache.get(owner_id)
        if cached is not None:
            # 캐시된 토큰의 만료 시간을 확인
            auth_type = cached.get("llm_auth_type", "api_key")
            expires_at = cached.get("openai_oauth_expires_at")
            refresh_token = cached.get("openai_oauth_refresh_token")
            if (
                auth_type == "openai_oauth"
                and expires_at
                and time.time() > (expires_at - 60)
                and refresh_token
                and allow_refresh
            ):
                # 만료 임박 시 캐시를 무효화하고 리프레시 진행
                invalidate_user_settings_cache(owner_id)
            else:
                return deepcopy(cached)

        row = self._get_row(owner_id)
        if not row:
            return {}

        auth_type = row[7] if len(row) > 7 and row[7] else "api_key"
        openai_api_key = self._decrypt(row[0]) if len(row) > 0 else None
        oauth_access_token = self._decrypt(row[8]) if len(row) > 8 else None
        oauth_refresh_token = self._decrypt(row[9]) if len(row) > 9 else None
        oauth_expires_at = row[10] if len(row) > 10 else None
        embedding_api_key = (self._decrypt(row[11]) if len(row) > 11 else None) or openai_api_key
        llm_model_name = row[12] if len(row) > 12 and row[12] else "gpt-5.6-luna"


        # 만료 임박 토큰 자동 갱신 (로컬 OAuth 토큰이 존재하는 경우)
        if auth_type == "openai_oauth" and oauth_expires_at and time.time() > (oauth_expires_at - 60) and oauth_refresh_token and allow_refresh:
            try:
                from src.settings.openai_oauth import OpenAIOAuthClient
                oauth_client = OpenAIOAuthClient()
                refreshed_tokens = oauth_client.refresh_access_token_sync(oauth_refresh_token)
                self.save(owner_id, {
                    "openai_oauth_access_token": refreshed_tokens.access_token,
                    "openai_oauth_refresh_token": refreshed_tokens.refresh_token,
                    "openai_oauth_expires_at": refreshed_tokens.expires_at,
                })
                oauth_access_token = refreshed_tokens.access_token
                oauth_refresh_token = refreshed_tokens.refresh_token
                oauth_expires_at = refreshed_tokens.expires_at
            except Exception as e:
                print(f"Warning: Failed to auto-refresh OAuth token: {e}")

        # IAM 중앙 인증 서버로부터 최신 AI 자격증명 번들 획득 (Codex Token + OpenAI Key + Embedding Key)
        try:
            from src.settings.iam_codex_client import IAMCodexClient
            iam_client = IAMCodexClient()
            # knowledge_users 테이블에서 SSO auth_id(sub_val)를 조회하여 IAM 서버에 표준 전달
            auth_sub = None
            with self.db_manager.cursor() as cur:
                cur.execute("SELECT sub_val FROM knowledge_users WHERE user_id = %s OR sub_val = %s LIMIT 1;", (owner_id, owner_id))
                u_row = cur.fetchone()
                if u_row and u_row[0]:
                    auth_sub = u_row[0]
            lookup_user_id = auth_sub or owner_id
            bundle = iam_client.get_ai_bundle(user_id=lookup_user_id)
            if bundle:
                if bundle.get("codex", {}).get("linked"):
                    oauth_access_token = bundle["codex"].get("access_token")
                if bundle.get("openai_api_key", {}).get("configured"):
                    openai_api_key = bundle["openai_api_key"].get("api_key")
                if bundle.get("embedding_api_key", {}).get("configured"):
                    embedding_api_key = bundle["embedding_api_key"].get("api_key")
                elif openai_api_key:
                    embedding_api_key = openai_api_key
        except Exception as exc:
            print(f"Warning: Failed to fetch AI bundle from IAM server: {exc}")

        llm_bearer_token = oauth_access_token if auth_type == "openai_oauth" else openai_api_key

        config = {
            "llm_auth_type": auth_type,
            "llm_model_name": llm_model_name,
            "openai_api_key": openai_api_key,
            "openai_oauth_access_token": oauth_access_token,
            "openai_oauth_refresh_token": oauth_refresh_token,
            "openai_oauth_expires_at": oauth_expires_at,
            "llm_bearer_token": llm_bearer_token,
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

