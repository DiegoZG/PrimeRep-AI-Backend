from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class WorkoutExerciseOut(BaseModel):
    """Exercise details embedded in workout response."""

    id: str
    name: str
    exercise_type: str = Field(..., alias="exerciseType")
    primary_muscle: str = Field(..., alias="primaryMuscle")
    secondary_muscles: list[str] = Field(..., alias="secondaryMuscles")
    image_url: Optional[str] = Field(None, alias="imageUrl")
    demo_video_url: Optional[str] = Field(None, alias="demoVideoUrl")
    required_equipment_ids: list[str] = Field(..., alias="requiredEquipmentIds")

    model_config = ConfigDict(populate_by_name=True)


class WorkoutPrescriptionOut(BaseModel):
    """Prescription for an exercise (sets, reps, rest)."""

    sets: int
    reps_min: int = Field(..., alias="repsMin")
    reps_max: int = Field(..., alias="repsMax")
    rest_seconds: int = Field(..., alias="restSeconds")

    model_config = ConfigDict(populate_by_name=True)


class WorkoutBlockItemOut(BaseModel):
    """Single exercise + prescription in a workout block."""

    exercise: WorkoutExerciseOut
    prescription: WorkoutPrescriptionOut

    model_config = ConfigDict(populate_by_name=True)


class WorkoutExerciseBlockOut(BaseModel):
    """A block of exercises (main or accessory)."""

    block_type: str = Field(..., alias="blockType")
    items: list[WorkoutBlockItemOut]

    model_config = ConfigDict(populate_by_name=True)


class WorkoutNextResponseOut(BaseModel):
    """Response for POST /v1/workouts/next."""

    workout_id: str = Field(..., alias="workoutId")
    title: str
    split_key: str = Field(..., alias="splitKey")
    day_type: str = Field(..., alias="dayType")
    estimated_minutes: int = Field(..., alias="estimatedMinutes")
    exercise_blocks: list[WorkoutExerciseBlockOut] = Field(..., alias="exerciseBlocks")

    model_config = ConfigDict(populate_by_name=True)

