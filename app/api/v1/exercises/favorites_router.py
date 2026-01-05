from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exercise_service import (
    favorite_exercise,
    get_exercise,
    list_favorites,
    unfavorite_exercise,
)
from app.core.security.deps import get_current_user
from app.models.user import User
from app.schemas.exercise import ExerciseListOut, ExerciseOut, FavoriteStatusOut


router = APIRouter()


@router.post("/{exercise_id}/favorite", response_model=FavoriteStatusOut)
def favorite_exercise_endpoint(
    exercise_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    exercise = get_exercise(db, exercise_id)
    if not exercise:
        raise HTTPException(status_code=404, detail="Exercise not found")

    favorite_exercise(db, user_id=str(current_user.id), exercise_id=exercise_id)
    return FavoriteStatusOut(exercise_id=exercise_id, is_favorited=True)


@router.delete("/{exercise_id}/favorite", response_model=FavoriteStatusOut)
def unfavorite_exercise_endpoint(
    exercise_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    exercise = get_exercise(db, exercise_id)
    if not exercise:
        raise HTTPException(status_code=404, detail="Exercise not found")

    unfavorite_exercise(db, user_id=str(current_user.id), exercise_id=exercise_id)
    return FavoriteStatusOut(exercise_id=exercise_id, is_favorited=False)


@router.get("/favorites", response_model=ExerciseListOut)
def list_favorites_endpoint(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    exercises = list_favorites(db, user_id=str(current_user.id))

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
            "is_favorited": True,
        }
        items.append(ExerciseOut.model_validate(exercise_dict))

    return {"items": items}


