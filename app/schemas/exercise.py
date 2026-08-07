from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ExerciseOut(BaseModel):
    id: str
    name: str
    exercise_type: str
    primary_muscle: str
    secondary_muscles: list[str]
    required_equipment_ids: list[str]
    demo_video_url: Optional[str] = None
    image_url: Optional[str] = None
    is_active: bool
    is_favorited: bool = False

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

