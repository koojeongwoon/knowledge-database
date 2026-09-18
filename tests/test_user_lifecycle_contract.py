import pytest
from pydantic import ValidationError

from src.core.event.user_lifecycle import parse_iam_user_lifecycle_event


DISABLED_EVENT = """{
  "schema": "iam.user.v1",
  "eventId": "019d09c4-64f0-7000-8000-000000000001",
  "eventType": "USER_DISABLED",
  "occurredAt": "2026-09-18T00:00:00Z",
  "issuer": "https://auth.snappytory.com/t/ten_9664c024babc4110",
  "tenantId": "ten_9664c024babc4110",
  "subjectId": "usr_contract_fixture",
  "userVersion": 2
}"""


def test_parses_canonical_disabled_event():
    event = parse_iam_user_lifecycle_event(DISABLED_EVENT)
    assert event.schema_name == "iam.user.v1"
    assert event.event_type == "USER_DISABLED"
    assert event.tenant_id == "ten_9664c024babc4110"
    assert event.subject_id == "usr_contract_fixture"
    assert event.user_version == 2
    assert event.profile is None


def test_rejects_profile_on_status_event():
    with pytest.raises(ValidationError):
        parse_iam_user_lifecycle_event(DISABLED_EVENT[:-1] + ',"profile":{"email":"u@example.com","name":"U"}}')
