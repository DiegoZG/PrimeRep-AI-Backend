from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exercise_service import get_exercise, list_exercises
from app.schemas.exercise import ExerciseListOut, ExerciseOut


router = APIRouter()


@router.get("", response_model=ExerciseListOut)
def list_exercises_endpoint(
    q: Optional[str] = None,
    muscle: Optional[str] = None,
    equipment_id: Optional[str] = None,
    type: Optional[str] = Query(None, alias="type"),
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    exercises = list_exercises(
        db,
        q=q,
        muscle=muscle,
        equipment_id=equipment_id,
        exercise_type=type,
        only_active=True,
        limit=limit,
        offset=offset,
    )

    items: list[ExerciseOut] = []
    for exercise in exercises:
        exercise_dict = {
            "id": exercise.id,
            "name": exercise.name,
            "exercise_type": exercise.exercise_type,
            "primary_muscle": exercise.primary_muscle,
            "secondary_muscles": exercise.secondary_muscles,
            "required_equipment_ids": [e.id for e in exercise.equipment],
            "demo_video_url": exercise.demo_video_url,
            "image_url": exercise.image_url,
            "is_active": exercise.is_active,
            "is_favorited": False,
        }
        items.append(ExerciseOut.model_validate(exercise_dict))

    return {"items": items}


@router.get("/{exercise_id}", response_model=ExerciseOut)
def get_exercise_endpoint(
    exercise_id: str,
    db: Session = Depends(get_db),
):
    exercise = get_exercise(db, exercise_id)
    if not exercise:
        raise HTTPException(status_code=404, detail="Exercise not found")

    exercise_dict = {
        "id": exercise.id,
        "name": exercise.name,
        "exercise_type": exercise.exercise_type,
        "primary_muscle": exercise.primary_muscle,
        "secondary_muscles": exercise.secondary_muscles,
        "required_equipment_ids": [e.id for e in exercise.equipment],
        "demo_video_url": exercise.demo_video_url,
        "image_url": exercise.image_url,
        "is_active": exercise.is_active,
        "is_favorited": False,
    }
    return ExerciseOut.model_validate(exercise_dict)


