"""Coach content generation: day-level intent and per-exercise rationale.

Two entry points:
- generate_week_coach_content: one Anthropic call for the full week (used by
  _generate_week_plan). Cheaper and gives the LLM cross-day context.
- generate_coach_content: single-day call (used by skip_workout_day where only
  one new day is appended).

Both never raise — LLM failures silently fall back to template text so /week
and /next never 500.

Access is gated by subscription tier and user opt-in. Free users and opted-out
premium users receive only template content with zero Anthropic calls.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.onboarding_service import get_onboarding_by_user_id
from app.core.settings import settings

_ANTHROPIC_TIMEOUT_SECONDS = 10.0
_ANTHROPIC_MAX_TOKENS = 2000


@dataclass
class CoachContent:
    workout_intent: str
    exercise_rationale: dict[str, str] = field(default_factory=dict)


@dataclass
class WeekCoachContent:
    """Coach content for an entire week, keyed by workoutDayId."""
    days: dict[str, CoachContent]


# ── Eligibility gate ──────────────────────────────────────────────────────────


def _is_coach_eligible(db: Session, user_id: str) -> bool:
    """Return True only for premium users who have opted in to coach insights."""
    from app.models.user import User

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return False
    tier = getattr(user, "subscription_tier", "free")
    enabled = getattr(user, "coach_insights_enabled", False)
    return tier == "premium" and bool(enabled)


# ── Week-level coach call (primary path) ──────────────────────────────────────


def generate_week_coach_content(
    db: Optional[Session],
    *,
    user_id: Optional[str],
    days: list[dict],
) -> WeekCoachContent:
    """Generate workoutIntent and exerciseRationale for an entire week in one call.

    Args:
        db: Database session.
        user_id: Authenticated user ID.
        days: List of dicts, each with keys:
            - workoutDayId: str
            - day_type: str
            - title: str
            - exercise_items: list of (exercise_orm, WorkoutPrescriptionOut) tuples

    Returns:
        WeekCoachContent keyed by workoutDayId. Missing or failed days fall back
        to template CoachContent. Never raises.
    """
    fallback_map = _make_fallback_map(days)
    week_fallback = WeekCoachContent(days=fallback_map)

    if not days or not user_id or db is None:
        return week_fallback

    if not settings.ANTHROPIC_API_KEY:
        return week_fallback

    if not _is_coach_eligible(db, user_id):
        return week_fallback

    experience_level = _get_experience_level(db, user_id)
    fitness_goal = _get_fitness_goal(db, user_id)

    system_prompt = _build_week_system_prompt(
        experience_level=experience_level,
        fitness_goal=fitness_goal,
        days=days,
    )

    user_message = (
        "Return ONLY valid JSON (no markdown fences, no extra text) with this shape:\n"
        '{"days": [{"workoutDayId": "...", "workoutIntent": "...", '
        '"exerciseRationale": {"<exercise_id>": "..."}}]}'
    )

    try:
        raw = _call_anthropic(system_prompt, user_message)
        return _parse_week_response(raw, days, fallback_map)
    except Exception:
        return week_fallback


# ── Single-day coach call (skip path only) ────────────────────────────────────


def generate_coach_content(
    db: Optional[Session],
    *,
    user_id: Optional[str],
    day_type: str,
    title: str,
    exercise_items: list[tuple[Any, Any]],
) -> CoachContent:
    """Generate coach content for a single day. Used only by skip_workout_day.

    Never raises. Falls back to template on any failure.
    """
    fallback = CoachContent(
        workout_intent=f"Your {title} workout for today.",
        exercise_rationale={},
    )

    if not user_id or db is None:
        return fallback

    if not settings.ANTHROPIC_API_KEY:
        return fallback

    if not _is_coach_eligible(db, user_id):
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


# ── Prompt builders ───────────────────────────────────────────────────────────


def _build_week_system_prompt(
    *,
    experience_level: Optional[str],
    fitness_goal: Optional[str],
    days: list[dict],
) -> str:
    lines = [
        "You are PrimeRep's coaching assistant. Generate motivating, concise coach "
        "content for a user's full training week. Write intent and rationale that "
        "feel connected across the week — not isolated per day.",
        "",
        f"User experience level: {experience_level or 'unknown'}",
        f"User fitnessGoal: {fitness_goal or 'general fitness'}",
        "",
        "This week's workouts:",
    ]

    for day in days:
        workout_day_id = day["workoutDayId"]
        title = day["title"]
        day_type = day["day_type"]
        exercise_items = day.get("exercise_items", [])

        lines.append(f"\n  workoutDayId={workout_day_id} — {title} ({day_type})")
        lines.append("  Exercises:")
        for exercise, prescription in exercise_items:
            suggestion = _suggestion_line(prescription)
            context = ""
            if getattr(exercise, "why_it_works", None):
                context = f" — {exercise.why_it_works}"
            lines.append(
                f"    - id={exercise.id}, name={exercise.name}{context}; "
                f"weight suggestion: {suggestion}"
            )

    lines += [
        "",
        "Instructions:",
        "1. For each workoutDayId, write workoutIntent: 1-2 sentences on the session "
        "   purpose. Do not list exercises.",
        "2. For each exercise_id in each day, write exerciseRationale: 1 sentence why "
        "   it's in today's plan. Do NOT copy why_it_works verbatim.",
        "3. Return ONLY the JSON object. No markdown fences, no extra text.",
    ]

    return "\n".join(lines)


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


# ── Parsers ───────────────────────────────────────────────────────────────────


def _make_fallback_map(days: list[dict]) -> dict[str, CoachContent]:
    return {
        day["workoutDayId"]: CoachContent(
            workout_intent=f"Your {day['title']} workout for today.",
            exercise_rationale={},
        )
        for day in days
    }


def _parse_week_response(
    raw: str,
    days: list[dict],
    fallback_map: dict[str, CoachContent],
) -> WeekCoachContent:
    """Parse the batched LLM response; merge with fallbacks for missing days."""
    cleaned = raw.strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    result: dict[str, CoachContent] = dict(fallback_map)

    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return WeekCoachContent(days=result)

    if not isinstance(data, dict):
        return WeekCoachContent(days=result)

    days_list = data.get("days", [])
    if not isinstance(days_list, list):
        return WeekCoachContent(days=result)

    for day_data in days_list:
        if not isinstance(day_data, dict):
            continue
        day_id = day_data.get("workoutDayId")
        if not isinstance(day_id, str) or day_id not in result:
            continue
        intent = day_data.get("workoutIntent")
        if not isinstance(intent, str) or not intent.strip():
            continue
        rationale_raw = day_data.get("exerciseRationale", {})
        rationale: dict[str, str] = {}
        if isinstance(rationale_raw, dict):
            for ex_id, text in rationale_raw.items():
                if isinstance(ex_id, str) and isinstance(text, str) and text.strip():
                    rationale[ex_id] = text.strip()
        result[day_id] = CoachContent(
            workout_intent=intent.strip(),
            exercise_rationale=rationale,
        )

    return WeekCoachContent(days=result)


def _parse_response(raw: str, fallback: CoachContent) -> CoachContent:
    """Parse single-day LLM response JSON, stripping markdown fences if present."""
    cleaned = raw.strip()

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


# ── Shared helpers ────────────────────────────────────────────────────────────


def _get_experience_level(db: Session, user_id: str) -> Optional[str]:
    profile = get_onboarding_by_user_id(db, user_id)
    if not profile or not profile.data:
        return None
    return profile.data.get("experienceLevel")


def _get_fitness_goal(db: Session, user_id: str) -> Optional[str]:
    profile = get_onboarding_by_user_id(db, user_id)
    if not profile or not profile.data:
        return None
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
