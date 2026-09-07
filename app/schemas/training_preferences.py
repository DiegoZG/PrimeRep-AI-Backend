from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from app.schemas.onboarding import CustomWorkout


class TrainingPreferences(BaseModel):
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

    model_config = ConfigDict(extra="forbid")

class TrainingPreferencesResponse(TrainingPreferences):
    model_config = ConfigDict(populate_by_name=True)
