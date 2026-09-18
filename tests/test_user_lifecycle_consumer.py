from contextlib import contextmanager

from src.core.event.lifecycle_consumer import apply_user_lifecycle_event
from src.core.event.user_lifecycle import IamUserLifecycleEvent


class FakeCursor:
    def __init__(self, rows, all_rows=None):
        self.rows = list(rows)
        self.all_rows = all_rows or []
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        return self.all_rows


class FakeDb:
    def __init__(self, rows, all_rows=None):
        self.cursor_value = FakeCursor(rows, all_rows)

    @contextmanager
    def transaction(self):
        yield self.cursor_value


def lifecycle_event(event_type, version=1):
    return IamUserLifecycleEvent.model_validate({
        "schema": "iam.user.v1",
        "eventId": f"evt-{event_type}-{version}",
        "eventType": event_type,
        "occurredAt": "2026-09-18T00:00:00Z",
        "issuer": "https://auth.snappytory.com/t/ten_9664c024babc4110",
        "tenantId": "ten_9664c024babc4110",
        "subjectId": "iam-user-1",
        "userVersion": version,
        **({"profile": {"email": "user@example.com", "name": "User"}}
           if event_type in {"USER_CREATED", "USER_UPDATED"} else {}),
    })


def test_created_event_records_state_without_creating_local_user():
    db = FakeDb([("evt",), ("iam-user-1",)])

    applied, user_id, hashes = apply_user_lifecycle_event(lifecycle_event("USER_CREATED"), db)

    assert applied is True
    assert user_id is None
    assert hashes == []
    assert not any("UPDATE knowledge_users" in sql for sql, _ in db.cursor_value.calls)


def test_disabled_event_updates_existing_user_and_returns_cache_keys():
    db = FakeDb([("evt",), ("iam-user-1",), ("local-user",)], [("hash-1",), ("hash-2",)])

    applied, user_id, hashes = apply_user_lifecycle_event(lifecycle_event("USER_DISABLED", 2), db)

    assert applied is True
    assert user_id == "local-user"
    assert hashes == ["hash-1", "hash-2"]


def test_duplicate_event_is_idempotent():
    db = FakeDb([None])

    assert apply_user_lifecycle_event(lifecycle_event("USER_DISABLED", 2), db) == (False, None, [])
