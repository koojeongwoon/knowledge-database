from contextlib import contextmanager
from unittest.mock import Mock, patch

import pytest

from src.api_keys import auth
from src.api_keys.auth import (
    ServiceAccessDisabledError,
    ServiceAccessStaleError,
    ServiceAccessVersionMismatchError,
    verify_auth_token,
    verify_service_access,
)


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = list(rows) if rows is not None else []
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


class FakeDb:
    def __init__(self, rows=None):
        self.cursor_obj = FakeCursor(rows)

    @contextmanager
    def cursor(self):
        yield self.cursor_obj


def test_verify_service_access_fails_when_health_is_stale():
    # 1st fetchone: health check -> returns None (stale/offline)
    db = FakeDb(rows=[None])
    with pytest.raises(ServiceAccessStaleError, match="stale or offline"):
        verify_service_access("ten_1", "user_1", "knowledge-service", 1, db_manager=db)


def test_verify_service_access_passes_when_no_state_record_exists_yet():
    # 1st fetchone: health check (healthy) -> returns (1,)
    # 2nd fetchone: state record -> returns None (no restriction recorded yet)
    db = FakeDb(rows=[(1,), None])
    verify_service_access("ten_1", "user_1", "knowledge-service", 1, db_manager=db)


def test_verify_service_access_fails_when_status_is_not_active():
    # 1st fetchone: health check -> (1,)
    # 2nd fetchone: state record -> ('DISABLED', 2)
    db = FakeDb(rows=[(1,), ("DISABLED", 2)])
    with pytest.raises(ServiceAccessDisabledError, match="not ACTIVE"):
        verify_service_access("ten_1", "user_1", "knowledge-service", 2, db_manager=db)


def test_verify_service_access_fails_when_token_version_is_older():
    # 1st fetchone: health check -> (1,)
    # 2nd fetchone: state record -> ('ACTIVE', 5)
    # token version is 3 (< 5)
    db = FakeDb(rows=[(1,), ("ACTIVE", 5)])
    with pytest.raises(ServiceAccessVersionMismatchError, match="older than local version"):
        verify_service_access("ten_1", "user_1", "knowledge-service", 3, db_manager=db)


def test_verify_service_access_succeeds_when_active_and_version_matches_or_higher():
    # token version 5 >= db version 5
    db = FakeDb(rows=[(1,), ("ACTIVE", 5)])
    verify_service_access("ten_1", "user_1", "knowledge-service", 5, db_manager=db)

    # token version 6 >= db version 5
    db2 = FakeDb(rows=[(1,), ("ACTIVE", 5)])
    verify_service_access("ten_1", "user_1", "knowledge-service", 6, db_manager=db2)


def test_verify_auth_token_enforces_service_access_when_enabled(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_SERVICE_ACCESS_ENFORCEMENT_ENABLED", "true")
    jwk_client = Mock()
    jwk_client.get_signing_key_from_jwt.return_value = Mock(key="public-key")
    monkeypatch.setattr(auth, "_jwk_client", lambda: jwk_client)

    # Missing service_access_version in claims
    token_claims = {
        "sub": "auth-user-1",
        "email": "user@example.com",
        "client_id": auth.KNOWLEDGE_CLIENT_ID,
        "tenant_id": auth.KNOWLEDGE_TENANT_ID,
    }
    with patch.object(auth.jwt, "decode", return_value=token_claims):
        with pytest.raises(ServiceAccessVersionMismatchError, match="missing required service_access_version"):
            verify_auth_token("test-token")

    # With service_access_version claim present and valid DB state
    token_claims_with_ver = {
        **token_claims,
        "service_access_version": 2,
    }
    db = FakeDb(rows=[(1,), ("ACTIVE", 2)])
    with patch.object(auth.jwt, "decode", return_value=token_claims_with_ver):
        claims = verify_auth_token("test-token", db_manager=db)
        assert claims["sub"] == "auth-user-1"
        assert claims["service_access_version"] == 2
