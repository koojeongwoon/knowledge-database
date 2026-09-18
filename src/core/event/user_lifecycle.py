from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


LifecycleEventType = Literal[
    "USER_CREATED",
    "USER_UPDATED",
    "USER_DISABLED",
    "USER_REENABLED",
    "USER_DELETED",
]


class UserProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3)
    name: str = Field(min_length=1)


class IamUserLifecycleEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["iam.user.v1"] = Field(alias="schema")
    event_id: str = Field(alias="eventId", min_length=1)
    event_type: LifecycleEventType = Field(alias="eventType")
    occurred_at: datetime = Field(alias="occurredAt")
    issuer: str = Field(min_length=1)
    tenant_id: str = Field(alias="tenantId", min_length=1)
    subject_id: str = Field(alias="subjectId", min_length=1)
    user_version: int = Field(alias="userVersion", ge=1)
    profile: UserProfile | None = None

    @model_validator(mode="after")
    def profile_matches_event_type(self):
        carries_profile = self.event_type in {"USER_CREATED", "USER_UPDATED"}
        if carries_profile != (self.profile is not None):
            raise ValueError("profile is required only for create and update events")
        return self


def parse_iam_user_lifecycle_event(data: str) -> IamUserLifecycleEvent:
    return IamUserLifecycleEvent.model_validate_json(data)
