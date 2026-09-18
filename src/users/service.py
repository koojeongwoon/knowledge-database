import uuid
from dataclasses import dataclass
from typing import Mapping, Optional

from src.api_keys.auth import AUTH_TOKEN_ISSUER, KNOWLEDGE_TENANT_ID
from src.core.database.factory import DatabaseManager


@dataclass(frozen=True)
class VerifiedUserIdentity:
    issuer: str
    tenant_id: str
    subject_id: str
    email: str
    name: Optional[str] = None
    user_version: int = 1

    @classmethod
    def from_claims(cls, claims: Mapping[str, object]) -> "VerifiedUserIdentity":
        issuer = claims.get("iss")
        tenant_id = claims.get("tenant_id")
        subject_id = claims.get("sub")
        email = claims.get("email")
        name = claims.get("name")
        raw_version = claims.get("user_version", 1)
        if not all(isinstance(value, str) and value.strip() for value in (issuer, tenant_id, subject_id, email)):
            raise ValueError("Verified user identity claims are incomplete")
        try:
            user_version = int(raw_version)
        except (TypeError, ValueError) as exc:
            raise ValueError("user_version must be a positive integer") from exc
        if user_version < 1:
            raise ValueError("user_version must be a positive integer")
        return cls(
            issuer=issuer,
            tenant_id=tenant_id,
            subject_id=subject_id,
            email=email,
            name=name if isinstance(name, str) and name.strip() else None,
            user_version=user_version,
        )


class LocalUserService:
    def __init__(self, db_manager=None):
        self.db_manager = db_manager or DatabaseManager()

    def ensure_local_user(self, identity: VerifiedUserIdentity) -> str:
        if identity.issuer != AUTH_TOKEN_ISSUER or identity.tenant_id != KNOWLEDGE_TENANT_ID:
            raise ValueError("Verified identity does not belong to Knowledge")
        user_id = str(uuid.uuid4())
        with self.db_manager.transaction() as cur:
            cur.execute(
                """
                INSERT INTO knowledge_users
                    (user_id, sub_val, tenant_id, email, name, lifecycle_status, user_version)
                SELECT %s, %s, %s, %s, %s, 'ACTIVE', %s
                WHERE NOT EXISTS (
                    SELECT 1 FROM iam_user_lifecycle_states state
                    WHERE state.tenant_id = %s AND state.subject_id = %s
                      AND (state.lifecycle_status <> 'ACTIVE' OR state.user_version > %s)
                )
                AND EXISTS (
                    SELECT 1 FROM iam_user_lifecycle_health
                    WHERE singleton
                      AND last_seen_at > CURRENT_TIMESTAMP - INTERVAL '60 seconds'
                )
                ON CONFLICT (sub_val) DO UPDATE SET
                    email = EXCLUDED.email,
                    name = COALESCE(EXCLUDED.name, knowledge_users.name),
                    user_version = GREATEST(knowledge_users.user_version, EXCLUDED.user_version)
                WHERE knowledge_users.tenant_id = EXCLUDED.tenant_id
                RETURNING user_id
                """,
                (
                    user_id, identity.subject_id, identity.tenant_id, identity.email, identity.name,
                    identity.user_version, identity.tenant_id, identity.subject_id, identity.user_version,
                ),
            )
            row = cur.fetchone()
            if row:
                return row[0]
            cur.execute("SELECT tenant_id FROM knowledge_users WHERE sub_val = %s", (identity.subject_id,))
            if cur.fetchone():
                raise ValueError("Existing Knowledge user requires verified tenant migration")
            raise RuntimeError("Knowledge user could not be created")
