"""Coach content generation: day-level intent and per-exercise rationale.

Called once per workout during generation. Never raises — if the LLM call
fails (no key, timeout, bad JSON) a templated fallback is returned so
/week and /next never 500.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.onboarding_service import get_onboarding_by_user_id
from app.core.settings import settings

_ANTHROPIC_TIMEOUT_SECONDS = 8.0
_ANTHROPIC_MAX_TOKENS = 1000


@dataclass
class CoachContent:
    workout_intent: str
    exercise_rationale: dict[str, str] = field(default_factory=dict)


def generate_coach_content(
    db: Optional[Session],
    *,
    user_id: Optional[str],
    day_type: str,
    title: str,
    exercise_items: list[tuple[Any, Any]],
) -> CoachContent:
    """Generate workoutIntent and per-exercise exerciseRationale for a workout day.

    Args:
        db: Database session. If None, falls back to template immediately.
        user_id: Authenticated user ID. If None, falls back to template.
        day_type: e.g. "upper", "lower", "full_body".
        title: Human-readable workout title e.g. "Upper Body".
        exercise_items: List of (exercise_orm_object, WorkoutPrescriptionOut) tuples.

    Returns:
        CoachContent with workout_intent always set and exercise_rationale
        populated for any exercise the LLM returned a rationale for.
    """
    fallback = CoachContent(
        workout_intent=f"Your {title} workout for today.",
        exercise_rationale={},
    )

    if not user_id or db is None:
        return fallback

    if not settings.ANTHROPIC_API_KEY:
        return fallback

    experience_level = _get_experience_level(db, user_id)
    fitness_goal = _get_fitness_goal(db, user_id)

    system_prompt = _build_system_prompt(
        day_type=day_type,
        title=title,
        experience_level=experience_level,
        fitness_goal=fitness_goal,
        exercise_items=exercise_items,
    )

    user_message = (
        "Return ONLY valid JSON (no markdown fences, no extra text) matching:\n"
        '{"workoutIntent": "<1-2 sentence summary>", '
        '"exerciseRationale": {"<exercise_id>": "<1 sentence rationale>"}}'
    )

    try:
        raw = _call_anthropic(system_prompt, user_message)
        return _parse_response(raw, fallback)
    except Exception:
        return fallback


# ── Internal helpers ──────────────────────────────────────────────────────────


def _get_experience_level(db: Session, user_id: str) -> Optional[str]:
    profile = get_onboarding_by_user_id(db, user_id)
    if not profile or not profile.data:
        return None
    return profile.data.get("experienceLevel")


def _get_fitness_goal(db: Session, user_id: str) -> Optional[str]:
    profile = get_onboarding_by_user_id(db, user_id)
    if not profile or not profile.data:
        return None
    # App writes "fitnessGoal"; legacy tests may write "goal"
    return profile.data.get("fitnessGoal") or profile.data.get("goal")


def _suggestion_line(prescription: Any) -> str:
    """Convert prescription suggestion fields into a human-readable hint."""
    reason = getattr(prescription, "suggested_weight_reason", None)
    weight = getattr(prescription, "suggested_weight_kg", None)

    if reason and weight is not None:
        if reason == "increase":
            return f"increase to {weight}kg"
        if reason == "hold":
            return f"hold at {weight}kg"
        if reason == "deload":
            return f"deload to {weight}kg"
        return f"{reason} ({weight}kg)"

    return "no suggestion yet"


def _build_system_prompt(
    *,
    day_type: str,
    title: str,
    experience_level: Optional[str],
    fitness_goal: Optional[str],
    exercise_items: list[tuple[Any, Any]],
) -> str:
    lines = [
        "You are PrimeRep's coaching assistant. Generate motivating, concise coach "
        "content for today's workout. Stay practical and encouraging.",
        "",
        f"Workout type: {title} ({day_type})",
        f"User experience level: {experience_level or 'unknown'}",
        f"User fitnessGoal: {fitness_goal or 'general fitness'}",
        "",
        "Exercises in this workout:",
    ]

    for exercise, prescription in exercise_items:
        suggestion = _suggestion_line(prescription)
        context = ""
        if getattr(exercise, "why_it_works", None):
            context = f" — {exercise.why_it_works}"
        lines.append(
            f"  - id={exercise.id}, name={exercise.name}{context}; "
            f"weight suggestion: {suggestion}"
        )

    lines += [
        "",
        "Instructions:",
        "1. Write a workoutIntent: 1-2 sentences describing the purpose of today's "
        "   session and what the user will feel/achieve. Do not list exercises.",
        "2. Write an exerciseRationale for each exercise_id listed above: 1 sentence "
        "   explaining why this exercise is in the plan today. Do NOT copy why_it_works "
        "   verbatim — paraphrase or add context about the weight suggestion.",
        "3. Return ONLY the JSON object. No markdown fences, no extra commentary.",
    ]

    return "\n".join(lines)


def _parse_response(raw: str, fallback: CoachContent) -> CoachContent:
    """Parse LLM response JSON, stripping markdown fences if present."""
    cleaned = raw.strip()

    # Strip markdown code fences e.g. ```json ... ```
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return fallback

    if not isinstance(data, dict):
        return fallback

    intent = data.get("workoutIntent")
    rationale_raw = data.get("exerciseRationale", {})

    if not isinstance(intent, str) or not intent.strip():
        return fallback

    rationale: dict[str, str] = {}
    if isinstance(rationale_raw, dict):
        for ex_id, text in rationale_raw.items():
            if isinstance(ex_id, str) and isinstance(text, str) and text.strip():
                rationale[ex_id] = text.strip()

    return CoachContent(workout_intent=intent.strip(), exercise_rationale=rationale)


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
