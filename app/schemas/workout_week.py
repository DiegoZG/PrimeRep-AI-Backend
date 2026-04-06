"""Schemas for weekly workout plans."""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.workout import WorkoutExerciseBlockOut


class WorkoutDayOut(BaseModel):
    """Single workout day in a weekly plan."""

    workout_day_id: str = Field(..., alias="workoutDayId")
    slot_index: int = Field(..., alias="slotIndex")
    date: date
    duration_minutes: int = Field(..., alias="durationMinutes")
    title: str
    split_key: str = Field(..., alias="splitKey")
    day_type: str = Field(..., alias="dayType")
    estimated_minutes: int = Field(..., alias="estimatedMinutes")
    exercise_blocks: list[WorkoutExerciseBlockOut] = Field(..., alias="exerciseBlocks")

    model_config = ConfigDict(populate_by_name=True)


class WorkoutWeekResponseOut(BaseModel):
    """Response for GET /v1/workouts/week."""

    week_start: date = Field(..., alias="weekStart")
    days_per_week: int = Field(..., alias="daysPerWeek")
    generated_at: datetime = Field(..., alias="generatedAt")
    seed: str
    workouts: list[WorkoutDayOut]

    model_config = ConfigDict(populate_by_name=True)


class WorkoutWeekSkipRequest(BaseModel):
    """Request body for POST /v1/workouts/week/skip."""

    workout_day_id: str = Field(..., alias="workoutDayId")

    model_config = ConfigDict(populate_by_name=True)


class WorkoutWeekDurationPatchRequest(BaseModel):
    """Request body for PATCH /v1/workouts/week/duration."""

    workout_day_id: str = Field(..., alias="workoutDayId")
    duration_minutes: int = Field(..., alias="durationMinutes", ge=15, le=120)

    model_config = ConfigDict(populate_by_name=True)

