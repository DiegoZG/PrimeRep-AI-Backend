from typing import Optional
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator


class SubscriptionFlags(BaseModel):
    subscriptions_purchases_enabled: bool
    subscriptions_enforcement_enabled: bool


class SubscriptionFlagChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subscriptions_purchases_enabled: StrictBool = False
    subscriptions_enforcement_enabled: StrictBool = False


class FeatureFlagPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: StrictInt = Field(ge=0)
    flags: SubscriptionFlagChanges = Field(default_factory=SubscriptionFlagChanges)
    subscriptions_launch_at: Optional[datetime] = None

    @field_validator("subscriptions_launch_at", mode="before")
    @classmethod
    def require_iso_timestamp(cls, value):
        if value is not None and not isinstance(value, (str, datetime)):
            raise ValueError("Launch date must be an ISO 8601 timestamp with a timezone")
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as error:
                raise ValueError("Launch date must be an ISO 8601 timestamp with a timezone") from error
        return value

    @field_validator("subscriptions_launch_at")
    @classmethod
    def require_timezone(cls, value):
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("Launch date must include a timezone")
            return value.astimezone(timezone.utc)
        return value


class FeatureFlagSnapshot(BaseModel):
    environment: str
    version: int
    flags: SubscriptionFlags
    subscriptions_launch_at: Optional[datetime]
    transition_ends_at: Optional[datetime]
    launch_locked: bool
    can_manage_flags: bool
    updated_at: Optional[datetime]


class FeatureFlagHistory(BaseModel):
    version: int
    changed_at: datetime
    changed_by: Optional[str]
    before: dict
    after: dict


class AdminFeatureFlagSnapshot(FeatureFlagSnapshot):
    history: list[FeatureFlagHistory]
