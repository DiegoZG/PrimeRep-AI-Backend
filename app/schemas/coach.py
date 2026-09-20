from datetime import date, datetime, time
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class NoTarget(BaseModel):
    type: Literal["none"] = "none"


class ActiveWorkoutTarget(BaseModel):
    type: Literal["active_workout"]
    session_id: str = Field(..., alias="sessionId")

    model_config = ConfigDict(populate_by_name=True)


class PlannedWorkoutTarget(BaseModel):
    type: Literal["planned_workout"]
    workout_day_id: str = Field(..., alias="workoutDayId")
    week_start: date = Field(..., alias="weekStart")

    model_config = ConfigDict(populate_by_name=True)


class CompletedSessionTarget(BaseModel):
    type: Literal["completed_session"]
    session_id: str = Field(..., alias="sessionId")

    model_config = ConfigDict(populate_by_name=True)


class ProgramDetailTarget(BaseModel):
    type: Literal["program_detail"]
    template_id: str = Field(..., alias="templateId")

    model_config = ConfigDict(populate_by_name=True)


class ProgramReviewTarget(BaseModel):
    type: Literal["program_review"]
    activation_id: str = Field(..., alias="activationId")
    template_id: Optional[str] = Field(None, alias="templateId")

    model_config = ConfigDict(populate_by_name=True)


class SettingsTarget(BaseModel):
    type: Literal["settings"]
    section: Literal["notifications", "training_preferences"]


CoachTarget = Annotated[
    Union[
        NoTarget,
        ActiveWorkoutTarget,
        PlannedWorkoutTarget,
        CompletedSessionTarget,
        ProgramDetailTarget,
        ProgramReviewTarget,
        SettingsTarget,
    ],
    Field(discriminator="type"),
]


class CoachNextActionOut(BaseModel):
    type: Literal["active_workout", "planned_workout", "caught_up"]
    title: str
    body: str
    target: CoachTarget


class CoachFeedItemOut(BaseModel):
    id: str
    kind: Literal[
        "progression",
        "recovery",
        "missed_workout",
        "personal_record",
        "consistency",
        "program_review",
        "upcoming_workout",
    ]
    priority: int
    title: str
    body: str
    detail: Optional[str] = None
    target: CoachTarget
    is_ai_assisted: bool = Field(False, alias="isAiAssisted")
    read_at: Optional[datetime] = Field(None, alias="readAt")
    created_at: datetime = Field(..., alias="createdAt")
    expires_at: datetime = Field(..., alias="expiresAt")

    model_config = ConfigDict(populate_by_name=True)


class CoachFeedOut(BaseModel):
    next_action: CoachNextActionOut = Field(..., alias="nextAction")
    items: list[CoachFeedItemOut]
    next_cursor: Optional[str] = Field(None, alias="nextCursor")
    has_more: bool = Field(..., alias="hasMore")
    generated_at: datetime = Field(..., alias="generatedAt")

    model_config = ConfigDict(populate_by_name=True)


class CoachReadRequest(BaseModel):
    reason: Literal["expanded", "primaryAction"]


class CoachPreferenceOut(BaseModel):
    notifications_enabled: bool = Field(..., alias="notificationsEnabled")
    reminder_time: time = Field(..., alias="reminderTime")
    time_zone: str = Field(..., alias="timeZone")

    model_config = ConfigDict(populate_by_name=True)


class CoachPreferenceUpdate(CoachPreferenceOut):
    @field_validator("time_zone")
    @classmethod
    def valid_time_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("timeZone must be a valid IANA timezone") from error
        return value
