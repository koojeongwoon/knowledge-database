import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from src.core.cache.factory import WikiCacheManager
from src.core.database.factory import DatabaseManager
from src.users.service import LocalUserService, VerifiedUserIdentity


def hash_api_key(plain_key: str) -> str:
    return base64.b64encode(hashlib.sha256(plain_key.encode("utf-8")).digest()).decode("utf-8")


class ApiKeyService:
    def __init__(self, db_manager=None, cache_manager=None):
        self.db_manager = db_manager or DatabaseManager()
        self.cache_manager = cache_manager or WikiCacheManager()

    def create(self, identity: VerifiedUserIdentity, key_name: str, validity_days: int = 365) -> dict:
        user_id = self.get_or_create_user(identity)
        key_id = str(uuid.uuid4())
        plain_key = f"kb_live_{secrets.token_urlsafe(32)}"
        key_hash = hash_api_key(plain_key)
        key_prefix = plain_key[:16]
        expires_at = datetime.now(timezone.utc) + timedelta(days=validity_days)

        with self.db_manager.cursor() as cur:
            cur.execute(
                """
                INSERT INTO knowledge_api_keys
                    (key_id, api_key_hash, user_id, key_name, key_prefix, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (key_id, key_hash, user_id, key_name, key_prefix, expires_at),
            )

        return {
            "plain_key": plain_key,
            "api_key": {
                "key_id": key_id,
                "key_name": key_name,
                "key_prefix": key_prefix,
                "expires_at": expires_at.isoformat(),
            },
        }

    def get_or_create_user(self, identity: VerifiedUserIdentity) -> str:
        return LocalUserService(self.db_manager).ensure_local_user(identity)

    def list_for_user(self, identity: VerifiedUserIdentity) -> list[dict]:
        user_id = self.get_or_create_user(identity)
        with self.db_manager.cursor() as cur:
            cur.execute(
                """
                SELECT key_id, key_name, key_prefix, created_at, expires_at
                FROM knowledge_api_keys
                WHERE user_id = %s
                ORDER BY created_at DESC
                """,
                (user_id,),
            )
            rows = cur.fetchall()
        return [
            {
                "key_id": row[0],
                "key_name": row[1],
                "key_prefix": row[2],
                "created_at": row[3].isoformat() if row[3] else None,
                "expires_at": row[4].isoformat() if row[4] else None,
            }
            for row in rows
        ]

    def revoke(self, identity: VerifiedUserIdentity, key_id: str) -> bool:
        user_id = self.get_or_create_user(identity)
        with self.db_manager.transaction() as cur:
            cur.execute(
                "SELECT api_key_hash FROM knowledge_api_keys WHERE key_id = %s AND user_id = %s",
                (key_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                return False
            cur.execute(
                "DELETE FROM knowledge_api_keys WHERE key_id = %s AND user_id = %s",
                (key_id, user_id),
            )
        self.cache_manager.delete(f"auth:token:hash:{row[0]}")
        return True
