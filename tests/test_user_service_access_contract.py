import pytest
from pydantic import ValidationError

from src.core.event.user_service_access import parse_iam_user_service_access_event


WITHDRAWN_EVENT = """{
  "schema": "iam.user-service-access.v1",
  "eventId": "019d09c4-64f0-7000-8000-000000000002",
  "eventType": "USER_SERVICE_WITHDRAWN",
  "occurredAt": "2026-09-18T00:00:00Z",
  "issuer": "https://auth.snappytory.com/t/ten_9664c024babc4110",
  "tenantId": "ten_9664c024babc4110",
  "subjectId": "usr_contract_fixture",
  "clientId": "knowledge-service",
  "accessVersion": 2,
  "status": "WITHDRAWN"
}"""


def test_parses_canonical_service_access_withdrawn_event():
    event = parse_iam_user_service_access_event(WITHDRAWN_EVENT)
    assert event.schema_name == "iam.user-service-access.v1"
    assert event.event_type == "USER_SERVICE_WITHDRAWN"
    assert event.tenant_id == "ten_9664c024babc4110"
    assert event.subject_id == "usr_contract_fixture"
    assert event.client_id == "knowledge-service"
    assert event.access_version == 2
    assert event.status == "WITHDRAWN"


def test_rejects_mismatched_status_and_event_type():
    bad_data = WITHDRAWN_EVENT.replace('"WITHDRAWN"', '"ACTIVE"')
    with pytest.raises(ValidationError):
        parse_iam_user_service_access_event(bad_data)


def test_rejects_extra_fields():
    bad_data = WITHDRAWN_EVENT[:-1] + ',"unexpected":"field"}'
    with pytest.raises(ValidationError):
        parse_iam_user_service_access_event(bad_data)
