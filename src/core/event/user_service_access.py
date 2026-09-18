from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ServiceAccessEventType = Literal[
    "USER_SERVICE_ENABLED",
    "USER_SERVICE_DISABLED",
    "USER_SERVICE_WITHDRAWN",
]

ServiceAccessStatus = Literal["ACTIVE", "DISABLED", "WITHDRAWN"]


class IamUserServiceAccessEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["iam.user-service-access.v1"] = Field(alias="schema")
    event_id: str = Field(alias="eventId", min_length=1)
    event_type: ServiceAccessEventType = Field(alias="eventType")
    occurred_at: datetime = Field(alias="occurredAt")
    issuer: str = Field(min_length=1)
    tenant_id: str = Field(alias="tenantId", min_length=1)
    subject_id: str = Field(alias="subjectId", min_length=1)
    client_id: str = Field(alias="clientId", min_length=1)
    access_version: int = Field(alias="accessVersion", ge=1)
    status: ServiceAccessStatus

    @model_validator(mode="after")
    def status_matches_event_type(self):
        expected_status = {
            "USER_SERVICE_ENABLED": "ACTIVE",
            "USER_SERVICE_DISABLED": "DISABLED",
            "USER_SERVICE_WITHDRAWN": "WITHDRAWN",
        }[self.event_type]
        if self.status != expected_status:
            raise ValueError(f"status {self.status} does not match eventType {self.event_type}")
        return self


def parse_iam_user_service_access_event(data: str) -> IamUserServiceAccessEvent:
    return IamUserServiceAccessEvent.model_validate_json(data)
