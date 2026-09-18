from contextlib import contextmanager

from src.core.event.service_access_consumer import apply_service_access_event
from src.core.event.user_service_access import IamUserServiceAccessEvent


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


def service_access_event(event_type, client_id="knowledge-service", version=1):
    status_map = {
        "USER_SERVICE_ENABLED": "ACTIVE",
        "USER_SERVICE_DISABLED": "DISABLED",
        "USER_SERVICE_WITHDRAWN": "WITHDRAWN",
    }
    return IamUserServiceAccessEvent.model_validate({
        "schema": "iam.user-service-access.v1",
        "eventId": f"evt-{event_type}-{version}",
        "eventType": event_type,
        "occurredAt": "2026-09-18T00:00:00Z",
        "issuer": "https://auth.snappytory.com/t/ten_9664c024babc4110",
        "tenantId": "ten_9664c024babc4110",
        "subjectId": "iam-user-1",
        "clientId": client_id,
        "accessVersion": version,
        "status": status_map[event_type],
    })


def test_ignores_events_for_other_clients():
    db = FakeDb([])
    applied, user_id, hashes = apply_service_access_event(
        service_access_event("USER_SERVICE_ENABLED", client_id="tools-gateway"), db
    )
    assert applied is False
    assert user_id is None
    assert hashes == []
    assert len(db.cursor_value.calls) == 0


def test_enabled_event_records_state_without_user_lookup():
    db = FakeDb([("evt",), ("iam-user-1",)])
    applied, user_id, hashes = apply_service_access_event(
        service_access_event("USER_SERVICE_ENABLED"), db
    )
    assert applied is True
    assert user_id is None
    assert hashes == []


def test_withdrawn_event_deactivates_keys_and_returns_hashes():
    db = FakeDb([("evt",), ("iam-user-1",), ("local-user-id",)], [("hash-1",)])
    applied, user_id, hashes = apply_service_access_event(
        service_access_event("USER_SERVICE_WITHDRAWN", version=2), db
    )
    assert applied is True
    assert user_id == "local-user-id"
    assert hashes == ["hash-1"]


def test_duplicate_event_is_idempotent():
    db = FakeDb([None])
    assert apply_service_access_event(service_access_event("USER_SERVICE_WITHDRAWN"), db) == (False, None, [])
