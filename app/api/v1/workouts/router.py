"""Workout endpoints: weekly plans and backward-compatible /next wrapper."""

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security.deps import get_current_user
from app.core.workout_week_service import (
    get_or_create_week_plan,
    get_week_start,
    select_next_scheduled_workout,
    skip_workout_day,
    update_workout_duration,
)
from app.models.user import User
from app.schemas.workout import WorkoutExerciseBlockOut, WorkoutNextResponseOut
from app.schemas.workout_week import (
    WorkoutWeekDurationPatchRequest,
    WorkoutWeekResponseOut,
    WorkoutWeekSkipRequest,
)


router = APIRouter(prefix="/workouts", tags=["workouts"])


@router.get("/week", response_model=WorkoutWeekResponseOut)
def get_week_plan_endpoint(
    week_start: Optional[date] = Query(None, description="Monday of the week (YYYY-MM-DD). Defaults to current week."),
    force: bool = Query(False, description="Force regenerate the week plan with new workoutDayIds"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get or create the weekly workout plan for the authenticated user.

    - If a plan exists for the week and force=false: returns stored plan.
    - If force=true: regenerates the plan with new workoutDayIds (preserves per-slot durations).
    - If no plan exists: generates a new one.

    The week is Monday-based. If weekStart is not provided, uses current week.
    """
    user_id = str(current_user.id)

    # Normalize to Monday if provided date is not a Monday
    if week_start is not None:
        week_start = get_week_start(week_start)

    return get_or_create_week_plan(
        db,
        user_id=user_id,
        week_start=week_start,
        force_regenerate=force,
    )


@router.post("/week/skip", response_model=WorkoutWeekResponseOut)
def skip_workout_endpoint(
    request: WorkoutWeekSkipRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Skip a workout day: removes it from the week and appends a new workout at the end.

    - The total number of workouts remains the same (days_per_week).
    - The new workout follows the split rotation from the last workout in the list.
    - Template dates are re-assigned to maintain proper scheduling.

    Returns 404 if the workoutDayId is not found in the current week plan.
    """
    user_id = str(current_user.id)
    week_start = get_week_start(date.today())

    result = skip_workout_day(
        db,
        user_id=user_id,
        workout_day_id=request.workout_day_id,
        week_start=week_start,
    )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Workout day not found in current plan. Refresh your week plan.",
        )

    return result


@router.patch("/week/duration", response_model=WorkoutWeekResponseOut)
def update_duration_endpoint(
    request: WorkoutWeekDurationPatchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update the duration for a specific workout day and regenerate the week.

    - Updates the durationMinutes for the specified workoutDayId.
    - Regenerates all workout content based on the new durations.
    - Preserves all workoutDayIds and per-slot durations.
    - Duration affects exercise count (more duration = more exercises).

    Returns 404 if the workoutDayId is not found in the current week plan.
    """
    user_id = str(current_user.id)
    week_start = get_week_start(date.today())

    result = update_workout_duration(
        db,
        user_id=user_id,
        workout_day_id=request.workout_day_id,
        duration_minutes=request.duration_minutes,
        week_start=week_start,
    )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Workout day not found in current plan. Refresh your week plan.",
        )

    return result


@router.post("/next", response_model=WorkoutNextResponseOut)
def get_next_workout(
    force: bool = Query(False, description="Force regenerate the week plan first"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get the next scheduled workout for the authenticated user.

    This is a backward-compatible wrapper over the weekly plan system.

    - Ensures the current week plan exists (creates if missing).
    - If force=true: regenerates the week plan first (with new workoutDayIds).
    - Returns the workout with the smallest date >= today from the plan.
    - If no workouts remain this week: creates next week's plan and returns its first workout.

    Response uses the existing single-workout shape (WorkoutNextResponseOut).
    """
    user_id = str(current_user.id)
    today = date.today()
    week_start = get_week_start(today)

    # Get or create this week's plan
    week_plan = get_or_create_week_plan(
        db,
        user_id=user_id,
        week_start=week_start,
        force_regenerate=force,
    )

    # Convert to dict for selection
    plan_dict = {
        "weekStart": week_plan.week_start.isoformat(),
        "daysPerWeek": week_plan.days_per_week,
        "generatedAt": week_plan.generated_at.isoformat() + "Z",
        "seed": week_plan.seed,
        "workouts": [
            {
                "workoutDayId": w.workout_day_id,
                "slotIndex": w.slot_index,
                "date": w.date.isoformat(),
                "durationMinutes": w.duration_minutes,
                "title": w.title,
                "splitKey": w.split_key,
                "dayType": w.day_type,
                "estimatedMinutes": w.estimated_minutes,
                "workoutIntent": w.workout_intent,
                "exerciseBlocks": [b.model_dump(by_alias=True) for b in w.exercise_blocks],
            }
            for w in week_plan.workouts
        ],
    }

    # Select next scheduled workout
    next_workout = select_next_scheduled_workout(plan_dict, target_date=today)

    # If no workouts left this week, create next week's plan
    if next_workout is None:
        next_week_start = week_start + timedelta(days=7)
        next_week_plan = get_or_create_week_plan(
            db,
            user_id=user_id,
            week_start=next_week_start,
            force_regenerate=False,
        )

        next_plan_dict = {
            "weekStart": next_week_plan.week_start.isoformat(),
            "daysPerWeek": next_week_plan.days_per_week,
            "generatedAt": next_week_plan.generated_at.isoformat() + "Z",
            "seed": next_week_plan.seed,
            "workouts": [
                {
                    "workoutDayId": w.workout_day_id,
                    "slotIndex": w.slot_index,
                    "date": w.date.isoformat(),
                    "durationMinutes": w.duration_minutes,
                    "title": w.title,
                    "splitKey": w.split_key,
                    "dayType": w.day_type,
                    "estimatedMinutes": w.estimated_minutes,
                    "workoutIntent": w.workout_intent,
                    "exerciseBlocks": [b.model_dump(by_alias=True) for b in w.exercise_blocks],
                }
                for w in next_week_plan.workouts
            ],
        }

        next_workout = select_next_scheduled_workout(next_plan_dict, target_date=next_week_start)

    if next_workout is None:
        # This shouldn't happen, but handle it gracefully
        raise HTTPException(
            status_code=500,
            detail="Unable to generate workout. Please try again.",
        )

    # Convert to WorkoutNextResponseOut format
    exercise_blocks = [
        WorkoutExerciseBlockOut.model_validate(block)
        for block in next_workout.get("exerciseBlocks", [])
    ]

    return WorkoutNextResponseOut(
        workout_id=next_workout["workoutDayId"],
        title=next_workout["title"],
        split_key=next_workout["splitKey"],
        day_type=next_workout["dayType"],
        estimated_minutes=next_workout["estimatedMinutes"],
        workout_intent=next_workout.get("workoutIntent"),
        exercise_blocks=exercise_blocks,
    )
