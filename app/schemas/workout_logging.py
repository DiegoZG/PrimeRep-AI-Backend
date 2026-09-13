"""Schemas for workout session logging."""

import uuid
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ── Requests ──────────────────────────────────────────────────────────────────

class SessionCreateRequest(BaseModel):
    workout_day_id: str = Field(..., alias="workoutDayId")
    workout_date: date = Field(..., alias="workoutDate")
    day_type: str = Field(..., alias="dayType")
    client_session_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        alias="clientSessionId",
        min_length=1,
        max_length=128,
    )

    model_config = ConfigDict(populate_by_name=True)


class SetLogRequest(BaseModel):
    exercise_id: str = Field(..., alias="exerciseId")
    set_number: int = Field(..., alias="setNumber", ge=1)
    reps: int = Field(..., ge=1)
    # None for bodyweight exercises
    weight_kg: Optional[float] = Field(None, alias="weightKg", ge=0)
    client_operation_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        alias="clientOperationId",
        min_length=1,
        max_length=128,
    )

    model_config = ConfigDict(populate_by_name=True)


class SetLogUpdateRequest(BaseModel):
    reps: Optional[int] = Field(None, ge=1)
    weight_kg: Optional[float] = Field(None, alias="weightKg", ge=0)
    client_operation_id: str = Field(..., alias="clientOperationId", min_length=1, max_length=128)
    expected_version: int = Field(..., alias="expectedVersion", ge=1)

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def requires_a_value_change(self):
        if "reps" not in self.model_fields_set and "weightKg" not in self.model_fields_set and "weight_kg" not in self.model_fields_set:
            raise ValueError("Provide reps or weightKg.")
        return self


class SessionNoteRequest(BaseModel):
    note: Optional[str] = Field(None, max_length=2000)
    client_operation_id: str = Field(..., alias="clientOperationId", min_length=1, max_length=128)

    model_config = ConfigDict(populate_by_name=True)


class MutationOperationRequest(BaseModel):
    client_operation_id: str = Field(..., alias="clientOperationId", min_length=1, max_length=128)

    model_config = ConfigDict(populate_by_name=True)


class ExerciseFeedbackRequest(BaseModel):
    effort: Optional[Literal["easy", "right", "hard"]] = None
    rir: Optional[int] = Field(None, ge=0, le=5)
    client_operation_id: str = Field(..., alias="clientOperationId", min_length=1, max_length=128)

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def requires_effort_for_rir(self):
        if self.rir is not None and self.effort is None:
            raise ValueError("Choose Easy, Right, or Hard before adding RIR.")
        return self


class UserExerciseNoteRequest(BaseModel):
    note: Optional[str] = Field(None, max_length=2000)

    model_config = ConfigDict(populate_by_name=True)


# ── Responses ─────────────────────────────────────────────────────────────────

class SetLogOut(BaseModel):
    id: str
    exercise_id: str = Field(..., alias="exerciseId")
    set_number: int = Field(..., alias="setNumber")
    reps: int
    weight_kg: Optional[float] = Field(None, alias="weightKg")
    logged_at: datetime = Field(..., alias="loggedAt")
    updated_at: datetime = Field(..., alias="updatedAt")
    version: int

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


class ExerciseFeedbackOut(BaseModel):
    exercise_id: str = Field(..., alias="exerciseId")
    effort: Optional[Literal["easy", "right", "hard"]] = None
    rir: Optional[int] = None

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


class UserExerciseNoteOut(BaseModel):
    exercise_id: str = Field(..., alias="exerciseId")
    note: str

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


class WorkoutSessionSummaryOut(BaseModel):
    title: str
    duration_seconds: int = Field(..., alias="durationSeconds")
    completed_set_count: int = Field(..., alias="completedSetCount")
    total_volume_kg: float = Field(..., alias="totalVolumeKg")

    model_config = ConfigDict(populate_by_name=True)


class LastSetOut(BaseModel):
    id: str
    exercise_id: str = Field(..., alias="exerciseId")
    set_number: int = Field(..., alias="setNumber")
    reps: int
    weight_kg: Optional[float] = Field(None, alias="weightKg")
    logged_at: datetime = Field(..., alias="loggedAt")

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


class LastSetsOut(BaseModel):
    items: list[LastSetOut]


class SessionOut(BaseModel):
    id: str
    workout_day_id: str = Field(..., alias="workoutDayId")
    workout_date: date = Field(..., alias="workoutDate")
    day_type: str = Field(..., alias="dayType")
    workout_snapshot: Optional[dict] = Field(None, alias="workoutSnapshot")
    recovery_required: bool = Field(False, alias="recoveryRequired")
    client_session_id: Optional[str] = Field(None, alias="clientSessionId")
    status: str
    started_at: datetime = Field(..., alias="startedAt")
    completed_at: Optional[datetime] = Field(None, alias="completedAt")
    created_at: datetime = Field(..., alias="createdAt")
    set_logs: list[SetLogOut] = Field(default_factory=list, alias="setLogs")
    deleted_set_logs: list[SetLogOut] = Field(default_factory=list, alias="deletedSetLogs")
    workout_note: Optional[str] = Field(None, alias="workoutNote")
    exercise_feedback: list[ExerciseFeedbackOut] = Field(default_factory=list, alias="exerciseFeedback")
    summary: WorkoutSessionSummaryOut

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


class SessionSummaryOut(BaseModel):
    """Lightweight session record for history lists."""

    id: str
    workout_day_id: str = Field(..., alias="workoutDayId")
    workout_date: date = Field(..., alias="workoutDate")
    day_type: str = Field(..., alias="dayType")
    status: str
    completed_at: Optional[datetime] = Field(None, alias="completedAt")
    set_count: int = Field(..., alias="setCount")
    title: str
    total_volume_kg: float = Field(..., alias="totalVolumeKg")

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


class SessionListOut(BaseModel):
    items: list[SessionSummaryOut]
    total: int


class StatsOut(BaseModel):
    streak: int
    total_completed: int = Field(..., alias="totalCompleted")
    prs_this_week: int = Field(..., alias="prsThisWeek")
    total_volume_kg: float = Field(..., alias="totalVolumeKg")

    model_config = ConfigDict(populate_by_name=True)
