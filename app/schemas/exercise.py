from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ExerciseType = Literal["strength", "accessory", "bodyweight", "cardio"]
PrimaryMuscle = Literal[
    "abs",
    "back",
    "biceps",
    "calves",
    "cardio",
    "chest",
    "forearms",
    "glutes",
    "hamstrings",
    "neck",
    "quads",
    "shoulders",
    "triceps",
]
MuscleGroup = Literal[
    "abs",
    "back",
    "biceps",
    "calves",
    "cardio",
    "chest",
    "forearms",
    "full_body",
    "glutes",
    "hamstrings",
    "neck",
    "quads",
    "shoulders",
    "triceps",
]


class ExerciseOut(BaseModel):
    id: str
    name: str
    exercise_type: ExerciseType
    primary_muscle: PrimaryMuscle
    secondary_muscles: list[MuscleGroup]
    required_equipment_ids: list[str]
    demo_video_url: Optional[str] = None
    image_url: Optional[str] = None
    is_active: bool
    is_favorited: bool = False
    source: str = "seed"
    is_editable: bool = False

    model_config = ConfigDict(from_attributes=True)


class ExerciseDetailOut(ExerciseOut):
    """Exercise detail view — adds long-form content fields (deliberately
    excluded from ExerciseOut/list responses to keep list payloads small)."""

    how_to: Optional[str] = None
    why_it_works: Optional[str] = None
    common_mistakes: Optional[str] = None
    beginner_notes: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ExerciseListOut(BaseModel):
    items: list[ExerciseOut]


class CustomExerciseWrite(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    exercise_type: ExerciseType = Field(..., alias="exerciseType")
    primary_muscle: PrimaryMuscle = Field(..., alias="primaryMuscle")
    secondary_muscles: list[MuscleGroup] = Field(
        default_factory=list, alias="secondaryMuscles", max_length=20
    )
    equipment_ids: list[str] = Field(default_factory=list, alias="equipmentIds", max_length=30)
    instructions: Optional[str] = Field(None, max_length=4000)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("name")
    @classmethod
    def validate_nonblank_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def validate_secondary_muscles(self):
        if self.primary_muscle in self.secondary_muscles:
            raise ValueError("primaryMuscle cannot also be a secondary muscle")
        if len(self.secondary_muscles) != len(set(self.secondary_muscles)):
            raise ValueError("secondaryMuscles must not contain duplicates")
        return self


class CustomExerciseOut(ExerciseDetailOut):
    pass


class ExerciseSubstitutionsOut(BaseModel):
    items: list[ExerciseOut]


class FavoriteStatusOut(BaseModel):
    exercise_id: str
    is_favorited: bool


# ── Exercise Q&A ────────────────────────────────────────────────────────────

class AskQuestionRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)

    model_config = ConfigDict(populate_by_name=True)


class AskQuestionOut(BaseModel):
    id: str
    exercise_id: str = Field(..., alias="exerciseId")
    question: str
    answer: str
    created_at: datetime = Field(..., alias="createdAt")

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


class QuestionHistoryOut(BaseModel):
    items: list[AskQuestionOut]
