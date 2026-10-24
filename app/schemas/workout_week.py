"""Schemas for weekly workout plans."""

from datetime import date, datetime
from typing import Optional, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.workout import WorkoutExerciseBlockOut


class WorkoutDayOut(BaseModel):
    """Single workout day in a weekly plan."""

    workout_day_id: str = Field(..., alias="workoutDayId")
    slot_index: int = Field(..., alias="slotIndex")
    date: date
    planning_mode: Literal["trainer_led", "time_budget"] = Field("time_budget", alias="planningMode")
    time_budget_minutes: Optional[int] = Field(None, alias="timeBudgetMinutes")
    planning_editable: Optional[bool] = Field(None, alias="planningEditable")
    planning_source: Literal["default", "override"] = Field("default", alias="planningSource")
    duration_minutes: int = Field(..., alias="durationMinutes")
    title: str
    split_key: str = Field(..., alias="splitKey")
    day_type: str = Field(..., alias="dayType")
    estimated_minutes: int = Field(..., alias="estimatedMinutes")
    workout_intent: Optional[str] = Field(None, alias="workoutIntent")
    deferred_muscles: list[str] = Field(default_factory=list, alias="deferredMuscles")
    unavailable_muscles: list[str] = Field(default_factory=list, alias="unavailableMuscles")
    exercise_blocks: list[WorkoutExerciseBlockOut] = Field(..., alias="exerciseBlocks")
    program_activation_id: Optional[str] = Field(None, alias="programActivationId")
    template_id: Optional[str] = Field(None, alias="templateId")
    template_version: Optional[int] = Field(None, alias="templateVersion")
    template_day_id: Optional[str] = Field(None, alias="templateDayId")
    template_day_position: Optional[int] = Field(None, alias="templateDayPosition")

    model_config = ConfigDict(populate_by_name=True)


class WorkoutWeekResponseOut(BaseModel):
    """Response for GET /v1/workouts/week."""

    week_start: date = Field(..., alias="weekStart")
    days_per_week: int = Field(..., alias="daysPerWeek")
    generated_at: datetime = Field(..., alias="generatedAt")
    seed: str
    workouts: list[WorkoutDayOut]
    program_activation_id: Optional[str] = Field(None, alias="programActivationId")
    template_id: Optional[str] = Field(None, alias="templateId")
    template_version: Optional[int] = Field(None, alias="templateVersion")

    model_config = ConfigDict(populate_by_name=True)


class WorkoutWeekSkipRequest(BaseModel):
    """Request body for POST /v1/workouts/week/skip."""

    workout_day_id: str = Field(..., alias="workoutDayId")
    week_start: Optional[date] = Field(None, alias="weekStart")

    model_config = ConfigDict(populate_by_name=True)


class WorkoutWeekDurationPatchRequest(BaseModel):
    """Request body for PATCH /v1/workouts/week/duration."""

    workout_day_id: str = Field(..., alias="workoutDayId")
    duration_minutes: int = Field(..., alias="durationMinutes", ge=15, le=120)
    week_start: Optional[date] = Field(None, alias="weekStart")

    model_config = ConfigDict(populate_by_name=True)


class WorkoutPlanningRequest(BaseModel):
    workout_day_id: str = Field(..., alias="workoutDayId")
    planning_mode: Literal["trainer_led", "time_budget"] = Field(..., alias="planningMode")
    time_budget_minutes: Optional[int] = Field(..., alias="timeBudgetMinutes", ge=15, le=120, strict=True)
    week_start: date = Field(..., alias="weekStart")
    effective_date: date = Field(..., alias="effectiveDate")
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    @model_validator(mode="after")
    def mode_matches_budget(self):
        if (self.planning_mode == "time_budget") != (self.time_budget_minutes is not None):
            raise ValueError("Time budget mode requires minutes; Trainer-led requires null minutes")
        return self


class ApplyPlanningPreferenceRequest(BaseModel):
    week_start: date = Field(..., alias="weekStart")
    effective_date: date = Field(..., alias="effectiveDate")
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class ApplyPlanningPreferenceOut(BaseModel):
    week_plan: WorkoutWeekResponseOut = Field(..., alias="weekPlan")
    updated_workout_count: int = Field(..., alias="updatedWorkoutCount")
    protected_workout_count: int = Field(..., alias="protectedWorkoutCount")
    model_config = ConfigDict(populate_by_name=True)


class AddWorkoutExerciseRequest(BaseModel):
    workout_day_id: str = Field(..., alias="workoutDayId", min_length=1)
    exercise_id: str = Field(..., alias="exerciseId", min_length=1)
    expected_exercise_ids: list[str] = Field(..., alias="expectedExerciseIds")
    week_start: date = Field(..., alias="weekStart")
    effective_date: date = Field(..., alias="effectiveDate")
    allow_over_budget: bool = Field(False, alias="allowOverBudget", strict=True)
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
