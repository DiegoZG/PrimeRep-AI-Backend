from datetime import datetime
import math
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CustomWorkout(BaseModel):
    id: str
    name: str
    type: str
    muscleGroups: list[str]

    model_config = ConfigDict(extra="allow")


class OnboardingData(BaseModel):
    preferredName: Optional[str] = None
    lastName: Optional[str] = None
    email: Optional[str] = None
    age: Optional[int] = Field(default=None, ge=0)
    gender: Optional[str] = None
    weight: Optional[float] = Field(default=None, ge=0)
    weightUnit: Optional[str] = None
    reason: Optional[str] = None
    fitnessGoal: Optional[str] = None
    experienceLevel: Optional[str] = None
    workoutFrequency: Optional[str] = None
    workoutSplit: Optional[str] = None
    varietyLevel: Optional[str] = None
    trainingPlace: Optional[str] = None
    selectedEquipment: Optional[list[str]] = None
    dumbbellWeights: Optional[list[float]] = None
    plateWeights: Optional[list[float]] = None
    customWorkouts: Optional[list[CustomWorkout]] = None
    preferredWorkoutTime: Optional[str] = None
    notificationsEnabled: Optional[bool] = None
    benchPress1RM: Optional[float] = Field(default=None, ge=0)
    backSquat1RM: Optional[float] = Field(default=None, ge=0)
    deadlift1RM: Optional[float] = Field(default=None, ge=0)

    model_config = ConfigDict(extra="allow")

    @field_validator("dumbbellWeights", "plateWeights", mode="before")
    @classmethod
    def validate_weight_arrays(cls, values: Any) -> Any:
        if values is None:
            return None
        if not isinstance(values, list):
            raise ValueError("weights must be arrays")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            for value in values
        ):
            raise ValueError("weights must be non-negative numeric values")
        return values


class OnboardingUpsertRequest(BaseModel):
    data: OnboardingData
    is_complete: bool = False


class OnboardingResponse(BaseModel):
    user_id: str
    data: Dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
