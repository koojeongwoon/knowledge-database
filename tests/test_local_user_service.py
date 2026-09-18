from contextlib import contextmanager

import pytest

from src.users.service import LocalUserService, VerifiedUserIdentity


class FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


class FakeDb:
    def __init__(self, rows):
        self.cursor_value = FakeCursor(rows)

    @contextmanager
    def transaction(self):
        yield self.cursor_value


def identity(**overrides):
    values = {
        "issuer": "https://auth.snappytory.com/t/ten_9664c024babc4110",
        "tenant_id": "ten_9664c024babc4110",
        "subject_id": "iam-user-1",
        "email": "user@example.com",
        "name": "User",
        "user_version": 2,
    }
    values.update(overrides)
    return VerifiedUserIdentity(**values)


def test_atomic_ensure_uses_verified_tenant_subject_and_preserves_existing_id():
    db = FakeDb([("existing-local-id",)])

    result = LocalUserService(db).ensure_local_user(identity())

    assert result == "existing-local-id"
    sql, params = db.cursor_value.calls[0]
    assert "ON CONFLICT (sub_val) DO UPDATE" in sql
    assert params[1:3] == ("iam-user-1", "ten_9664c024babc4110")


def test_rejects_identity_from_another_tenant_before_database_access():
    db = FakeDb([])

    with pytest.raises(ValueError, match="does not belong"):
        LocalUserService(db).ensure_local_user(identity(tenant_id="other"))

    assert db.cursor_value.calls == []


def test_does_not_claim_legacy_row_without_verified_tenant():
    db = FakeDb([None, (None,)])

    with pytest.raises(ValueError, match="requires verified tenant migration"):
        LocalUserService(db).ensure_local_user(identity())
