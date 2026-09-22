from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.exercise import ExerciseOut
from app.schemas.workout_week import WorkoutWeekResponseOut


Goal = Literal["build-muscle", "get-stronger", "general-fitness", "conditioning"]
Level = Literal["no-experience", "beginner", "intermediate", "advanced"]
Scope = Literal["system", "private"]


class TemplateExerciseInput(BaseModel):
    exercise_id: str = Field(..., alias="exerciseId", min_length=1)
    block_type: Literal["main", "accessory"] = Field("main", alias="blockType")
    sets: int = Field(..., ge=1, le=20)
    reps_min: int = Field(..., alias="repsMin", ge=1, le=100)
    reps_max: int = Field(..., alias="repsMax", ge=1, le=100)
    rest_seconds: int = Field(..., alias="restSeconds", ge=0, le=900)
    cue: Optional[str] = Field(None, max_length=500)

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def validate_rep_range(self):
        if self.reps_max < self.reps_min:
            raise ValueError("repsMax must be greater than or equal to repsMin")
        return self


class TemplateDayInput(BaseModel):
    title: str = Field(..., min_length=1, max_length=100)
    day_type: str = Field(..., alias="dayType", min_length=1, max_length=50)
    exercises: list[TemplateExerciseInput] = Field(..., min_length=1, max_length=30)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("title", "day_type")
    @classmethod
    def validate_nonblank_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def reject_duplicate_exercises(self):
        exercise_ids = [item.exercise_id for item in self.exercises]
        if len(exercise_ids) != len(set(exercise_ids)):
            raise ValueError("An exercise may appear only once in a workout day")
        return self


class WorkoutTemplateWrite(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field("", max_length=2000)
    goal: Goal
    level: Level
    duration_minutes: int = Field(..., alias="durationMinutes", ge=15, le=120)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    days: list[TemplateDayInput] = Field(..., min_length=1, max_length=7)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    @field_validator("aliases")
    @classmethod
    def validate_aliases(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            value = value.strip()
            if not value:
                raise ValueError("aliases must not be blank")
            if len(value) > 100:
                raise ValueError("aliases must be at most 100 characters")
            normalized.append(value)
        return normalized


class WorkoutTemplateUpdate(WorkoutTemplateWrite):
    version: int = Field(..., ge=1)


class TemplateExerciseOut(TemplateExerciseInput):
    id: str
    position: int
    exercise: ExerciseOut


class TemplateDayOut(BaseModel):
    id: str
    position: int
    title: str
    day_type: str = Field(..., alias="dayType")
    exercises: list[TemplateExerciseOut]

    model_config = ConfigDict(populate_by_name=True)


class WorkoutTemplateSummaryOut(BaseModel):
    id: str
    scope: Scope
    name: str
    description: str
    goal: Goal
    level: Level
    frequency: int
    duration_minutes: int = Field(..., alias="durationMinutes")
    featured_rank: Optional[int] = Field(None, alias="featuredRank")
    version: int
    equipment_ids: list[str] = Field(default_factory=list, alias="equipmentIds")
    missing_equipment_ids: list[str] = Field(
        default_factory=list, alias="missingEquipmentIds"
    )
    equipment_compatible: bool = Field(..., alias="equipmentCompatible")
    recommendation_score: Optional[int] = Field(None, alias="recommendationScore")
    match_reasons: list[str] = Field(default_factory=list, alias="matchReasons")

    model_config = ConfigDict(populate_by_name=True)


class WorkoutTemplateDetailOut(WorkoutTemplateSummaryOut):
    aliases: list[str]
    days: list[TemplateDayOut]


class WorkoutTemplateListOut(BaseModel):
    items: list[WorkoutTemplateSummaryOut]
    total: int
    limit: int
    offset: int


class ActivationPreviewRequest(BaseModel):
    week_start: date = Field(..., alias="weekStart")
    effective_date: date = Field(..., alias="effectiveDate")
    weekdays: list[int] = Field(..., min_length=1, max_length=7)
    apply_mode: Optional[Literal["now", "next-week"]] = Field(None, alias="applyMode")

    model_config = ConfigDict(populate_by_name=True)


class EquipmentConflictOut(BaseModel):
    template_day_id: str = Field(..., alias="templateDayId")
    template_exercise_id: str = Field(..., alias="templateExerciseId")
    original_exercise_id: str = Field(..., alias="originalExerciseId")
    missing_equipment_ids: list[str] = Field(..., alias="missingEquipmentIds")
    suggested_exercise_id: Optional[str] = Field(None, alias="suggestedExerciseId")
    alternative_exercise_ids: list[str] = Field(default_factory=list, alias="alternativeExerciseIds")
    suggested_exercise: Optional[ExerciseOut] = Field(None, alias="suggestedExercise")
    alternatives: list[ExerciseOut] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class ScheduleImpactOut(BaseModel):
    preserved_workout_day_ids: list[str] = Field(default_factory=list, alias="preservedWorkoutDayIds")
    replaced_workout_day_ids: list[str] = Field(default_factory=list, alias="replacedWorkoutDayIds")
    scheduled_dates: list[date] = Field(default_factory=list, alias="scheduledDates")

    model_config = ConfigDict(populate_by_name=True)


class ActivationPreviewOut(BaseModel):
    template: WorkoutTemplateSummaryOut
    weekdays: list[int]
    schedule_impact: ScheduleImpactOut = Field(..., alias="scheduleImpact")
    equipment_conflicts: list[EquipmentConflictOut] = Field(..., alias="equipmentConflicts")
    unresolved_conflict_count: int = Field(..., alias="unresolvedConflictCount")

    model_config = ConfigDict(populate_by_name=True)


class ActivateProgramRequest(ActivationPreviewRequest):
    template_version: int = Field(..., alias="templateVersion", ge=1)
    substitutions: dict[str, str] = Field(default_factory=dict)
    apply_mode: Literal["now", "next-week"] = Field(..., alias="applyMode")
    client_operation_id: str = Field(..., alias="clientOperationId", min_length=1, max_length=100)


class ActiveProgramOut(BaseModel):
    id: str
    template_id: Optional[str] = Field(None, alias="templateId")
    template_version: int = Field(..., alias="templateVersion")
    template_name: str = Field(..., alias="templateName")
    status: Literal["active", "scheduled", "superseded", "deactivated"]
    effective_date: date = Field(..., alias="effectiveDate")
    weekdays: list[int]
    apply_mode: Literal["now", "next-week"] = Field(..., alias="applyMode")
    requires_review: bool = Field(False, alias="requiresReview")
    created_at: datetime = Field(..., alias="createdAt")

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


class ActiveProgramsOut(BaseModel):
    current: Optional[ActiveProgramOut] = None
    scheduled: Optional[ActiveProgramOut] = None


class ActivateProgramOut(BaseModel):
    active_program: ActiveProgramOut = Field(..., alias="activeProgram")
    week_plan: WorkoutWeekResponseOut = Field(..., alias="weekPlan")

    model_config = ConfigDict(populate_by_name=True)


class SearchPagination(BaseModel):
    limit: int
    offset: int
    total: int
    has_more: bool


class ExploreSearchOut(BaseModel):
    programs: list[WorkoutTemplateSummaryOut]
    exercises: list[ExerciseOut]
    program_pagination: Optional[SearchPagination] = None
    exercise_pagination: Optional[SearchPagination] = None
