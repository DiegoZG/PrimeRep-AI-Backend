"""Rules-based weight progression. Deterministic, no LLM.

Called at week-plan generation to stamp `suggested_weight_kg` onto each
exercise prescription. The sets/reps/rest constants stay exactly as they are;
this only proposes a load from the user's completed history.

Rules, evaluated against the most recent completed session for the exercise:

- All weighted sets hit the top of the prescribed rep range → add 2.5 kg
  (upper body) or 5 kg (lower body).
- Any weighted set missed the bottom of the range:
    - once → hold at last session's working weight
    - two consecutive sessions → deload 10% from that weight
- In range but not all at the top → hold.
- No completed history, or no weighted sets last session → None (the
  prescription is unchanged; the player keeps pre-filling from last-sets).

"Consecutive" means consecutive completed sessions that include this exercise,
not calendar days. Bodyweight sets (weight_kg is null) are ignored; if a
session has none with a weight, it does not count as history for progression.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.core.workout_logging_service import get_recent_completed_sets_by_session
from app.models.set_log import SetLog

UPPER_INCREMENT_KG = 2.5
LOWER_INCREMENT_KG = 5.0
DELOAD_FRACTION = 0.10
MISSED_SESSIONS_FOR_DELOAD = 2
PLATE_INCREMENT_KG = 0.5


@dataclass(frozen=True)
class SuggestedWeight:
    weight_kg: float
    reason: str  # "increase" | "hold" | "deload"


def suggest_weight_kg(
    db: Session,
    *,
    user_id: str,
    exercise_id: str,
    is_lower_body: bool,
    reps_min: int,
    reps_max: int,
) -> Optional[SuggestedWeight]:
    history = get_recent_completed_sets_by_session(
        db,
        user_id,
        exercise_id,
        session_limit=MISSED_SESSIONS_FOR_DELOAD,
    )
    if not history:
        return None

    last_sets = _weighted(history[0][1])
    if not last_sets:
        return None

    working_weight = min(set_log.weight_kg for set_log in last_sets)
    last_missed = _missed_bottom(last_sets, reps_min)

    if last_missed:
        prior_missed = False
        if len(history) >= MISSED_SESSIONS_FOR_DELOAD:
            prior_sets = _weighted(history[1][1])
            prior_missed = bool(prior_sets) and _missed_bottom(prior_sets, reps_min)
        if prior_missed:
            return SuggestedWeight(
                weight_kg=_round_to_plate(working_weight * (1 - DELOAD_FRACTION)),
                reason="deload",
            )
        return SuggestedWeight(weight_kg=working_weight, reason="hold")

    if _hit_top(last_sets, reps_max):
        increment = LOWER_INCREMENT_KG if is_lower_body else UPPER_INCREMENT_KG
        return SuggestedWeight(
            weight_kg=_round_to_plate(working_weight + increment),
            reason="increase",
        )

    return SuggestedWeight(weight_kg=working_weight, reason="hold")


def _weighted(sets: list[SetLog]) -> list[SetLog]:
    return [s for s in sets if s.weight_kg is not None]


def _hit_top(sets: list[SetLog], reps_max: int) -> bool:
    return bool(sets) and all(s.reps >= reps_max for s in sets)


def _missed_bottom(sets: list[SetLog], reps_min: int) -> bool:
    return any(s.reps < reps_min for s in sets)


def _round_to_plate(kg: float) -> float:
    return round(kg / PLATE_INCREMENT_KG) * PLATE_INCREMENT_KG
