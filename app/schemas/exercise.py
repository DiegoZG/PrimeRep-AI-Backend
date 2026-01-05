from typing import Optional

from pydantic import BaseModel, ConfigDict


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


class ExerciseListOut(BaseModel):
    items: list[ExerciseOut]


class FavoriteStatusOut(BaseModel):
    exercise_id: str
    is_favorited: bool


