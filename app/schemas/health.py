from datetime import date, datetime
from typing import Literal, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


HealthSourceName = Literal["apple_health", "health_connect"]


class HealthSourceRequest(BaseModel):
    source: HealthSourceName
    read_enabled: bool = Field(False, alias="readEnabled")
    export_enabled: bool = Field(False, alias="exportEnabled")
    model_config = ConfigDict(populate_by_name=True)


class HealthSourceOut(BaseModel):
    source: HealthSourceName
    connection_revision: int = Field(alias="connectionRevision")
    read_enabled: bool = Field(alias="readEnabled")
    export_enabled: bool = Field(alias="exportEnabled")
    connected_at: datetime = Field(alias="connectedAt")
    last_successful_sync_at: Optional[datetime] = Field(None, alias="lastSuccessfulSyncAt")
    model_config = ConfigDict(populate_by_name=True)


class HealthPreferencesRequest(BaseModel):
    primary_source: Optional[HealthSourceName] = Field(None, alias="primarySource")
    coach_enabled: bool = Field(False, alias="coachEnabled")
    model_config = ConfigDict(populate_by_name=True)


class HealthDayIn(BaseModel):
    local_date: date = Field(alias="localDate")
    time_zone: str = Field(alias="timeZone", min_length=1, max_length=100)
    steps: Optional[int] = Field(None, ge=0, le=2147483647)
    asleep_minutes: Optional[int] = Field(None, alias="asleepMinutes", ge=0, le=1440)
    model_config = ConfigDict(populate_by_name=True)

    @field_validator("time_zone")
    @classmethod
    def validate_time_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("Use an IANA time zone") from error
        return value


class HealthDaysRequest(BaseModel):
    source: HealthSourceName
    connection_revision: int = Field(alias="connectionRevision", gt=0)
    days: list[HealthDayIn] = Field(max_length=35)
    replace_window: Optional["HealthReplaceWindow"] = Field(None, alias="replaceWindow")
    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def validate_days(self):
        if not self.days and self.replace_window is None:
            raise ValueError("Provide daily summaries or a replacement window")
        if self.replace_window and any(not (self.replace_window.start_date <= day.local_date <= self.replace_window.end_date) for day in self.days):
            raise ValueError("All daily summaries must fall inside the replacement window")
        return self


class HealthReplaceWindow(BaseModel):
    start_date: date = Field(alias="startDate")
    end_date: date = Field(alias="endDate")
    time_zone: str = Field(alias="timeZone", min_length=1, max_length=100)
    model_config = ConfigDict(populate_by_name=True)

    @field_validator("time_zone")
    @classmethod
    def validate_time_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("Use an IANA time zone") from error
        return value

    @model_validator(mode="after")
    def validate_window(self):
        if self.end_date < self.start_date or (self.end_date - self.start_date).days > 34:
            raise ValueError("Replacement window must cover at most 35 days")
        return self


class HealthDayOut(BaseModel):
    source: HealthSourceName
    local_date: date = Field(alias="localDate")
    time_zone: str = Field(alias="timeZone")
    steps: Optional[int] = None
    asleep_minutes: Optional[int] = Field(alias="asleepMinutes")
    model_config = ConfigDict(populate_by_name=True)


class HealthStateOut(BaseModel):
    collection_available: bool = Field(alias="collectionAvailable")
    sources: list[HealthSourceOut]
    primary_source: Optional[HealthSourceName] = Field(alias="primarySource")
    coach_enabled: bool = Field(alias="coachEnabled")
    days: list[HealthDayOut]
    model_config = ConfigDict(populate_by_name=True)


class HealthDaysResult(BaseModel):
    source: HealthSourceName
    connection_revision: int = Field(alias="connectionRevision")
    accepted_days: int = Field(alias="acceptedDays")
    last_successful_sync_at: datetime = Field(alias="lastSuccessfulSyncAt")
    model_config = ConfigDict(populate_by_name=True)
