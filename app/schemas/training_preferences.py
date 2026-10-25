from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.onboarding import CustomWorkout


class TrainingPreferences(BaseModel):
    workoutPlanningMode: Optional[Literal["trainer_led", "time_budget"]] = None
    preferredWorkoutMinutes: Optional[int] = Field(None, ge=15, le=120, strict=True)
    fitnessGoal: Optional[Literal["build-muscle", "get-stronger", "general-fitness", "conditioning"]] = None
    experienceLevel: Optional[Literal["no-experience", "beginner", "intermediate", "advanced"]] = None
    workoutFrequency: Optional[Literal["1-day", "2-days", "3-days", "4-days", "5-days", "6-days", "every-day"]] = None
    workoutSplit: Optional[Literal[
        "ai-optimized", "push-pull-legs", "custom", "upper-lower",
        "push-pull-legs-full-body", "push-pull-legs-upper-lower",
        "upper-lower-full-body", "full-body", "bro-split",
        "lower-focused-upper", "push-pull-legs-upper-body",
    ]] = None
    trainingPlace: Optional[Literal["large-gym", "small-gym", "garage-gym"]] = None
    selectedEquipment: Optional[list[str]] = None
    customWorkouts: Optional[list[CustomWorkout]] = None

    @model_validator(mode="after")
    def planning_fields_cannot_be_null(self):
        if any(field in self.model_fields_set and getattr(self, field) is None for field in ("workoutPlanningMode", "preferredWorkoutMinutes")):
            raise ValueError("Planning settings cannot be null")
        return self

    model_config = ConfigDict(extra="forbid")

class TrainingPreferencesResponse(TrainingPreferences):
    model_config = ConfigDict(populate_by_name=True)
