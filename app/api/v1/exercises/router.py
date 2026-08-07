from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exercise_qa_service import (
    CoachUnavailableError,
    RateLimitExceededError,
    ask_question,
    list_questions,
)
from app.core.exercise_service import get_exercise, list_exercises
from app.core.security.deps import get_current_user
from app.core.workout_logging_service import get_last_sets
from app.models.user import User
from app.schemas.exercise import (
    AskQuestionOut,
    AskQuestionRequest,
    ExerciseDetailOut,
    ExerciseListOut,
    ExerciseOut,
    QuestionHistoryOut,
)
from app.schemas.workout_logging import LastSetOut, LastSetsOut


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


@router.get("/{exercise_id}/last-sets", response_model=LastSetsOut)
def get_last_sets_endpoint(
    exercise_id: str,
    limit: int = Query(3, ge=1, le=20),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return the authenticated user's most recently logged sets for an exercise,
    newest first, drawn only from completed workout sessions.
    """
    exercise = get_exercise(db, exercise_id)
    if not exercise:
        raise HTTPException(status_code=404, detail="Exercise not found")

    sets = get_last_sets(db, user_id=str(current_user.id), exercise_id=exercise_id, limit=limit)
    return LastSetsOut(items=[LastSetOut.model_validate(s) for s in sets])


@router.post("/{exercise_id}/ask", response_model=AskQuestionOut)
def ask_exercise_question_endpoint(
    exercise_id: str,
    payload: AskQuestionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Ask the AI coach a question scoped to this exercise."""
    exercise = get_exercise(db, exercise_id)
    if not exercise:
        raise HTTPException(status_code=404, detail="Exercise not found")

    try:
        qa = ask_question(db, user=current_user, exercise=exercise, question=payload.question)
    except RateLimitExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except CoachUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return AskQuestionOut.model_validate(qa)


@router.get("/{exercise_id}/questions", response_model=QuestionHistoryOut)
def get_exercise_question_history_endpoint(
    exercise_id: str,
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The authenticated user's Q&A history for this exercise, newest first."""
    exercise = get_exercise(db, exercise_id)
    if not exercise:
        raise HTTPException(status_code=404, detail="Exercise not found")

    questions = list_questions(
        db, user_id=str(current_user.id), exercise_id=exercise_id, limit=limit
    )
    return QuestionHistoryOut(items=[AskQuestionOut.model_validate(q) for q in questions])


@router.get("/{exercise_id}", response_model=ExerciseDetailOut)
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
        "how_to": exercise.how_to,
        "why_it_works": exercise.why_it_works,
        "common_mistakes": exercise.common_mistakes,
        "beginner_notes": exercise.beginner_notes,
    }
    return ExerciseDetailOut.model_validate(exercise_dict)


