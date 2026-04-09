"""Schemas for workout session logging."""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ── Requests ──────────────────────────────────────────────────────────────────

class SessionCreateRequest(BaseModel):
    workout_day_id: str = Field(..., alias="workoutDayId")
    workout_date: date = Field(..., alias="workoutDate")
    day_type: str = Field(..., alias="dayType")

    model_config = ConfigDict(populate_by_name=True)


class SetLogRequest(BaseModel):
    exercise_id: str = Field(..., alias="exerciseId")
    set_number: int = Field(..., alias="setNumber", ge=1)
    reps: int = Field(..., ge=1)
    # None for bodyweight exercises
    weight_kg: Optional[float] = Field(None, alias="weightKg", ge=0)

    model_config = ConfigDict(populate_by_name=True)


# ── Responses ─────────────────────────────────────────────────────────────────

class SetLogOut(BaseModel):
    id: str
    exercise_id: str = Field(..., alias="exerciseId")
    set_number: int = Field(..., alias="setNumber")
    reps: int
    weight_kg: Optional[float] = Field(None, alias="weightKg")
    logged_at: datetime = Field(..., alias="loggedAt")

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


class SessionOut(BaseModel):
    id: str
    workout_day_id: str = Field(..., alias="workoutDayId")
    workout_date: date = Field(..., alias="workoutDate")
    day_type: str = Field(..., alias="dayType")
    completed_at: Optional[datetime] = Field(None, alias="completedAt")
    created_at: datetime = Field(..., alias="createdAt")
    set_logs: list[SetLogOut] = Field(default_factory=list, alias="setLogs")

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


class SessionSummaryOut(BaseModel):
    """Lightweight session record for history lists."""

    id: str
    workout_day_id: str = Field(..., alias="workoutDayId")
    workout_date: date = Field(..., alias="workoutDate")
    day_type: str = Field(..., alias="dayType")
    completed_at: Optional[datetime] = Field(None, alias="completedAt")
    set_count: int = Field(..., alias="setCount")

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


class SessionListOut(BaseModel):
    items: list[SessionSummaryOut]
    total: int


class StatsOut(BaseModel):
    streak: int
    total_completed: int = Field(..., alias="totalCompleted")
    prs_this_week: int = Field(..., alias="prsThisWeek")

    model_config = ConfigDict(populate_by_name=True)
