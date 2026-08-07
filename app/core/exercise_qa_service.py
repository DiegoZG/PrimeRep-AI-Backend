"""Business logic for on-demand AI exercise Q&A.

A user can ask a free-text question about a specific exercise. We answer it
with a short-lived call to Claude, scoped tightly to that exercise using
content columns on `Exercise` plus the user's onboarding profile and recent
set history for extra personalization. Answers are persisted for history.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.core.onboarding_service import get_onboarding_by_user_id
from app.core.settings import settings
from app.core.workout_logging_service import get_last_sets
from app.models.exercise import Exercise
from app.models.exercise_question import ExerciseQuestion
from app.models.set_log import SetLog
from app.models.user import User

DAILY_QUESTION_LIMIT = 20
_ANTHROPIC_TIMEOUT_SECONDS = 10.0
_ANTHROPIC_MAX_TOKENS = 500


class RateLimitExceededError(Exception):
    """Raised when a user has hit their daily question quota. Router maps to 429."""


class CoachUnavailableError(Exception):
    """Raised when the LLM call fails for any reason. Router maps to 503."""


def count_questions_today(db: Session, user_id: str) -> int:
    """Count questions asked by this user since midnight UTC."""
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return (
        db.query(ExerciseQuestion)
        .filter(
            ExerciseQuestion.user_id == user_id,
            ExerciseQuestion.created_at >= today_start,
        )
        .count()
    )


def list_questions(
    db: Session,
    user_id: str,
    exercise_id: str,
    limit: int = 50,
) -> list[ExerciseQuestion]:
    """User's Q&A history for a given exercise, newest first."""
    return (
        db.query(ExerciseQuestion)
        .filter(
            ExerciseQuestion.user_id == user_id,
            ExerciseQuestion.exercise_id == exercise_id,
        )
        .order_by(ExerciseQuestion.created_at.desc(), ExerciseQuestion.id.desc())
        .limit(limit)
        .all()
    )


def ask_question(
    db: Session,
    user: User,
    exercise: Exercise,
    question: str,
) -> ExerciseQuestion:
    """Answer a user's question about `exercise` and persist the Q&A pair.

    Raises RateLimitExceededError if the user is over DAILY_QUESTION_LIMIT,
    or CoachUnavailableError if the LLM call fails for any reason.
    """
    if count_questions_today(db, str(user.id)) >= DAILY_QUESTION_LIMIT:
        raise RateLimitExceededError(
            f"Daily question limit reached ({DAILY_QUESTION_LIMIT}/day). Try again tomorrow."
        )

    experience_level = _get_experience_level(db, str(user.id))
    recent_sets = get_last_sets(db, user_id=str(user.id), exercise_id=exercise.id, limit=3)

    system_prompt = _build_system_prompt(exercise, experience_level, recent_sets)

    try:
        answer = _call_anthropic(system_prompt, question)
    except Exception as exc:
        raise CoachUnavailableError(
            "Coach is temporarily unavailable. Please try again in a moment."
        ) from exc

    qa = ExerciseQuestion(
        id=str(uuid.uuid4()),
        user_id=str(user.id),
        exercise_id=exercise.id,
        question=question,
        answer=answer,
    )
    db.add(qa)
    db.commit()
    db.refresh(qa)
    return qa


def _get_experience_level(db: Session, user_id: str) -> Optional[str]:
    profile = get_onboarding_by_user_id(db, user_id)
    if not profile or not profile.data:
        return None
    return profile.data.get("experienceLevel")


def _build_system_prompt(
    exercise: Exercise,
    experience_level: Optional[str],
    recent_sets: list[SetLog],
) -> str:
    muscles = ", ".join(
        [exercise.primary_muscle, *(exercise.secondary_muscles or [])]
    )
    equipment_names = (
        ", ".join(f"{e.name} ({e.id})" for e in exercise.equipment)
        if exercise.equipment
        else "none (bodyweight)"
    )

    lines = [
        "You are PrimeRep's sports-coaching assistant. You are scoped to answering "
        f'questions ONLY about the exercise "{exercise.name}". If the user asks about '
        "anything unrelated to this exercise, politely decline and redirect them back "
        "to questions about this exercise.",
        "",
        f"Exercise: {exercise.name} ({exercise.id})",
        f"Muscles worked: {muscles}",
        f"Equipment: {equipment_names}",
    ]

    if exercise.how_to:
        lines.append(f"How to perform it:\n{exercise.how_to}")
    if exercise.why_it_works:
        lines.append(f"Why it works:\n{exercise.why_it_works}")
    if exercise.common_mistakes:
        lines.append(f"Common mistakes:\n{exercise.common_mistakes}")
    if exercise.beginner_notes:
        lines.append(f"Beginner notes:\n{exercise.beginner_notes}")

    lines.append(f"\nUser's experience level: {experience_level or 'unknown'}")

    if recent_sets:
        set_lines = "; ".join(
            f"set {s.set_number}: {s.reps} reps"
            + (f" @ {s.weight_kg}kg" if s.weight_kg is not None else " (bodyweight)")
            + f", logged {s.logged_at.isoformat()}"
            for s in recent_sets
        )
        lines.append(f"User's recent logged sets for this exercise: {set_lines}")
    else:
        lines.append("User's recent logged sets for this exercise: none logged yet")

    lines.append(
        "\nGuardrails: Never give medical advice or diagnose injuries. If the user "
        "describes pain, an injury, or a medical condition, tell them to consult a "
        "doctor or physical therapist rather than trying to solve it yourself. Stay "
        "strictly on fitness/training topics related to this exercise. Be concise, "
        "practical, and encouraging — a few short sentences is usually enough."
    )

    return "\n".join(lines)


def _call_anthropic(system: str, user_message: str) -> str:
    """Thin wrapper around the Anthropic SDK, isolated so tests can monkeypatch it."""
    if not settings.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")

    import anthropic

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=settings.ANTHROPIC_QA_MODEL,
        max_tokens=_ANTHROPIC_MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user_message}],
        timeout=_ANTHROPIC_TIMEOUT_SECONDS,
    )
    text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()

    if not text:
        raise RuntimeError("Anthropic returned an empty response")

    return text
