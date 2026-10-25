from typing import Literal, Optional
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator


class SubscriptionFlags(BaseModel):
    subscriptions_purchases_enabled: bool
    subscriptions_enforcement_enabled: bool


class SubscriptionFlagChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subscriptions_purchases_enabled: StrictBool = False
    subscriptions_enforcement_enabled: StrictBool = False


class FeatureFlagAudiences(BaseModel):
    subscriptions_purchases_enabled: Literal["everyone", "selected"]
    subscriptions_enforcement_enabled: Literal["everyone", "selected"]


class FeatureFlagAudienceChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subscriptions_purchases_enabled: Literal["everyone", "selected"] = "everyone"
    subscriptions_enforcement_enabled: Literal["everyone", "selected"] = "everyone"


class FeatureFlagPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: StrictInt = Field(ge=0)
    flags: SubscriptionFlagChanges = Field(default_factory=SubscriptionFlagChanges)
    audiences: FeatureFlagAudienceChanges = Field(default_factory=FeatureFlagAudienceChanges)
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
    scope: Literal["global", "user"]
    target_user_id: Optional[str]
    before: dict
    after: dict


class AdminFeatureFlagSnapshot(FeatureFlagSnapshot):
    audiences: FeatureFlagAudiences
    history: list[FeatureFlagHistory]


class FeatureFlagOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subscriptions_purchases_enabled: Optional[StrictBool] = None
    subscriptions_enforcement_enabled: Optional[StrictBool] = None


class UserFeatureFlagPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: StrictInt = Field(ge=0)
    overrides: FeatureFlagOverrides


class FeatureFlagUserLookup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identifier: str = Field(strict=True, min_length=1, max_length=320)


class FeatureFlagUser(BaseModel):
    id: str
    email: str
    preferred_name: str


class UserFeatureFlagSnapshot(BaseModel):
    environment: str
    version: int
    user: FeatureFlagUser
    overrides: FeatureFlagOverrides
    effective_flags: SubscriptionFlags
    global_flags: SubscriptionFlags
    audiences: FeatureFlagAudiences
    subscriptions_launch_at: Optional[datetime]
    transition_ends_at: Optional[datetime]
    launch_locked: bool


class FeatureFlagUserListItem(BaseModel):
    user: FeatureFlagUser
    overrides: FeatureFlagOverrides
    effective_flags: SubscriptionFlags


class FeatureFlagUserList(BaseModel):
    environment: str
    version: int
    items: list[FeatureFlagUserListItem]
    total: int
    limit: int
    offset: int
