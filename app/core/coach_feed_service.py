from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import statistics
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, TypeAdapter
from sqlalchemy import and_, case, func, or_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.achievement_service import workout_milestones
from app.core.coach_weight_data import public_weight_data
from app.core.coach_service import _is_coach_eligible
from app.core.progression_service import suggest_weight_kg
from app.core.settings import settings
from app.models.coach import CoachFeedItem, CoachNotificationJob, CoachPreference
from app.models.exercise import Exercise
from app.models.set_log import SetLog
from app.models.workout_session import WorkoutSession
from app.models.workout_session_exercise_feedback import WorkoutSessionExerciseFeedback
from app.models.workout_template import UserProgramActivation
from app.models.workout_week_plan import WorkoutWeekPlan
from app.schemas.coach import (
    CoachFeedItemOut,
    CoachFeedOut,
    CoachNextActionOut,
    CoachPreferenceOut,
    CoachPreferenceUpdate,
    CoachTarget,
)


logger = logging.getLogger("primerep.coach")
TARGET_ADAPTER = TypeAdapter(CoachTarget)
RETENTION_DAYS = 30
HISTORY_DAYS = 7
MISSED_ACTION_DAYS = 3
AI_BATCH_LIMIT = 10
AI_ATTEMPT_LIMIT = 2
AI_SAFE_KINDS = {"progression", "recovery", "program_review"}
AI_STYLE_VARIANTS = ("direct", "supportive", "focused")
FEED_KINDS = {
    "progression",
    "recovery",
    "missed_workout",
    "personal_record",
    "consistency",
    "program_review",
    "upcoming_workout",
}


class CoachAISelection(BaseModel):
    id: str
    variant: Literal["direct", "supportive", "focused"]

    model_config = ConfigDict(extra="forbid")


class CoachAISelectionBatch(BaseModel):
    items: list[CoachAISelection]

    model_config = ConfigDict(extra="forbid")


AI_VARIANT_TITLES = {
    "progression": {
        "direct": "{action} on {exercise_name}",
        "supportive": "Your next step: {action} on {exercise_name}",
        "focused": "{exercise_name}: {action}",
    },
    "recovery": {
        "direct": "Keep the next session steady",
        "supportive": "Give your training room to settle",
        "focused": "Protect the quality of your next session",
    },
    "program_review": {
        "direct": "Review your program equipment",
        "supportive": "Keep your program equipment aligned",
        "focused": "Equipment review needed",
    },
}


def _event(event: str, **fields: Any) -> None:
    safe = {"event": event, **fields}
    logger.info(json.dumps(safe, sort_keys=True, default=str))


def _opaque(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _iso_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _workout_items(workout: dict) -> list[dict]:
    return [
        item
        for block in workout.get("exerciseBlocks", [])
        for item in block.get("items", [])
        if isinstance(item, dict)
    ]


def _exercise_name(item: dict) -> str:
    exercise = item.get("exercise") or {}
    return exercise.get("name") or str(exercise.get("id") or "Exercise").replace("_", " ").title()


def _all_planned_workouts(
    db: Session,
    user_id: str,
    *,
    earliest_week_start: Optional[date] = None,
    latest_week_start: Optional[date] = None,
) -> list[tuple[WorkoutWeekPlan, dict]]:
    rows: list[tuple[WorkoutWeekPlan, dict]] = []
    query = db.query(WorkoutWeekPlan).filter(WorkoutWeekPlan.user_id == user_id)
    if earliest_week_start is not None:
        query = query.filter(WorkoutWeekPlan.week_start_date >= earliest_week_start)
    if latest_week_start is not None:
        query = query.filter(WorkoutWeekPlan.week_start_date <= latest_week_start)
    plans = query.order_by(WorkoutWeekPlan.week_start_date.asc()).all()
    for plan in plans:
        for workout in plan.plan_json.get("workouts", []):
            if isinstance(workout, dict):
                rows.append((plan, workout))
    return rows


def _completed_day_ids(db: Session, user_id: str) -> set[str]:
    return {
        row[0]
        for row in db.query(WorkoutSession.workout_day_id)
        .filter(WorkoutSession.user_id == user_id, WorkoutSession.status == "completed")
        .all()
    }


def build_next_action(db: Session, user_id: str, local_date: date) -> CoachNextActionOut:
    active = (
        db.query(WorkoutSession)
        .filter(WorkoutSession.user_id == user_id, WorkoutSession.status == "in_progress")
        .order_by(WorkoutSession.started_at.desc())
        .first()
    )
    if active:
        snapshot = active.workout_snapshot or {}
        title = snapshot.get("title") or active.day_type.replace("_", " ").title()
        return CoachNextActionOut(
            type="active_workout",
            title="Continue your workout",
            body=title,
            target={"type": "active_workout", "sessionId": active.id},
        )

    completed = _completed_day_ids(db, user_id)
    candidates = []
    for plan, workout in _all_planned_workouts(db, user_id):
        workout_date = _iso_date(workout.get("date"))
        workout_id = workout.get("workoutDayId")
        if workout_date and workout_date >= local_date and workout_id not in completed:
            candidates.append((workout_date, plan, workout))
    if candidates:
        workout_date, plan, workout = min(candidates, key=lambda item: (item[0], item[2].get("slotIndex", 0)))
        date_copy = "Today" if workout_date == local_date else workout_date.strftime("%A")
        return CoachNextActionOut(
            type="planned_workout",
            title=workout.get("title") or "Next workout",
            body=f"{date_copy} · {workout.get('durationMinutes', workout.get('estimatedMinutes', 0))} min",
            target={
                "type": "planned_workout",
                "workoutDayId": workout["workoutDayId"],
                "weekStart": plan.week_start_date,
            },
        )
    return CoachNextActionOut(
        type="caught_up",
        title="Training is done for now",
        body="Your planned workouts are complete. Recovery is part of the plan.",
        target={"type": "none"},
    )


def _candidate(
    *,
    kind: str,
    evidence: Any,
    priority: int,
    title: str,
    body: str,
    detail: Optional[str],
    target: dict,
    evidence_at: Optional[datetime] = None,
    evidence_data: Optional[dict[str, Any]] = None,
    protected_facts: Optional[dict[str, Any]] = None,
    expires_at: Optional[datetime] = None,
) -> dict:
    fingerprint = _fingerprint(evidence)
    evidence_at = evidence_at or _now()
    return {
        "kind": kind,
        "priority": priority,
        "dedupe_key": f"{kind}:{fingerprint}",
        "evidence_fingerprint": fingerprint,
        "evidence_at": evidence_at,
        "evidence_data": evidence_data or {},
        "protected_facts": protected_facts or {},
        "title": title,
        "body": body,
        "detail": detail,
        "target_data": TARGET_ADAPTER.validate_python(target).model_dump(
            by_alias=True, mode="json"
        ),
        "expires_at": expires_at or (evidence_at + timedelta(days=RETENTION_DAYS)),
    }


def _progression_candidates(db: Session, user_id: str, local_date: date) -> list[dict]:
    completed = _completed_day_ids(db, user_id)
    upcoming: list[tuple[date, WorkoutWeekPlan, dict]] = []
    week_start = local_date - timedelta(days=local_date.weekday())
    for plan, workout in _all_planned_workouts(
        db, user_id, earliest_week_start=week_start
    ):
        workout_date = _iso_date(workout.get("date"))
        workout_id = workout.get("workoutDayId")
        if not workout_date or workout_date < local_date or workout_id in completed:
            continue
        upcoming.append((workout_date, plan, workout))
    if not upcoming:
        return []
    workout_date, plan, workout = min(
        upcoming, key=lambda entry: (entry[0], entry[2].get("slotIndex", 0))
    )
    workout_id = workout.get("workoutDayId")
    result = []
    evidence_at = _now()
    for item in _workout_items(workout):
        prescription = item.get("prescription") or {}
        exercise = item.get("exercise") or {}
        exercise_id = exercise.get("id")
        if not exercise_id:
            continue
        suggestion = suggest_weight_kg(
            db,
            user_id=user_id,
            exercise_id=exercise_id,
            is_lower_body=(exercise.get("primaryMuscle") or exercise.get("primary_muscle"))
            in {"quads", "hamstrings", "glutes", "calves"},
            reps_min=int(prescription.get("repsMin") or 1),
            reps_max=int(prescription.get("repsMax") or prescription.get("repsMin") or 1),
        )
        if suggestion is None or suggestion.reason not in {"increase", "deload"}:
            continue
        reason = suggestion.reason
        suggested = suggestion.weight_kg
        name = _exercise_name(item)
        action = "Move up" if reason == "increase" else "Use a lighter load"
        detail = (
            "You reached the top of your prescribed rep range last time."
            if reason == "increase"
            else "Recent sets fell below the prescribed range twice. A small deload can help rebuild momentum."
        )
        required_text = [name, f"{float(suggested):g} kg", action]
        result.append(
            _candidate(
                kind="progression",
                evidence=[workout_id, exercise_id, reason, suggested],
                evidence_data={
                    "workoutDayIds": [workout_id],
                    "exerciseIds": [exercise_id],
                },
                evidence_at=evidence_at,
                priority=70,
                title=f"{action} on {name}",
                body=f"Your next {name} target is {float(suggested):g} kg.",
                detail=detail,
                protected_facts={
                    "exerciseName": name,
                    "recommendation": reason,
                    "targetWeightKg": suggested,
                    "currentWeightKg": suggestion.previous_weight_kg,
                    "equipmentIds": exercise.get("requiredEquipmentIds") or exercise.get("required_equipment_ids") or [],
                    "loadProfile": exercise.get("loadProfile") or exercise.get("load_profile"),
                    "requiredText": required_text,
                },
                target={"type": "planned_workout", "workoutDayId": workout_id, "weekStart": plan.week_start_date},
            )
        )
    return result


def _session_volume(session: WorkoutSession) -> float:
    return sum(float(row.weight_kg or 0) * row.reps for row in session.set_logs)


def _recovery_candidate(db: Session, user_id: str) -> list[dict]:
    sessions = (
        db.query(WorkoutSession)
        .filter(WorkoutSession.user_id == user_id, WorkoutSession.status == "completed")
        .order_by(WorkoutSession.completed_at.desc(), WorkoutSession.id.desc())
        .limit(5)
        .all()
    )
    if not sessions:
        return []
    latest = sessions[0]
    demanding = (
        db.query(func.count(WorkoutSessionExerciseFeedback.id))
        .filter(
            WorkoutSessionExerciseFeedback.session_id == latest.id,
            or_(
                WorkoutSessionExerciseFeedback.effort == "hard",
                WorkoutSessionExerciseFeedback.rir.between(0, 1),
            ),
        )
        .scalar()
        or 0
    )
    latest_volume = _session_volume(latest)
    prior_volumes = [value for session in sessions[1:] if (value := _session_volume(session)) > 0]
    volume_spike = len(prior_volumes) == 4 and latest_volume >= 1.25 * statistics.median(prior_volumes)
    if demanding < 2 and not volume_spike:
        return []
    expires = min(_now() + timedelta(days=3), latest.completed_at + timedelta(days=3))
    return [
        _candidate(
            kind="recovery",
            evidence=[latest.id, int(demanding), round(latest_volume, 2), volume_spike],
            evidence_data={"metricSessionIds": [session.id for session in sessions]},
            evidence_at=latest.completed_at,
            priority=80,
            title="Keep the next session steady",
            body="That workout was demanding. Follow the next session as written and skip extra work for now.",
            detail="This is training guidance based on your recent workout, not medical advice.",
            protected_facts={
                "recommendation": "keep_next_session_as_written",
                "requiredText": ["next session", "as written"],
            },
            target={"type": "completed_session", "sessionId": latest.id},
            expires_at=expires,
        )
    ]


def _missed_candidates(db: Session, user_id: str, local_date: date) -> list[dict]:
    completed = _completed_day_ids(db, user_id)
    result = []
    earliest_date = local_date - timedelta(days=HISTORY_DAYS)
    earliest_week = earliest_date - timedelta(days=earliest_date.weekday())
    latest_week = local_date - timedelta(days=local_date.weekday())
    for plan, workout in _all_planned_workouts(
        db,
        user_id,
        earliest_week_start=earliest_week,
        latest_week_start=latest_week,
    ):
        workout_date = _iso_date(workout.get("date"))
        workout_id = workout.get("workoutDayId")
        if (
            workout_date
            and earliest_date <= workout_date < local_date
            and workout_id not in completed
        ):
            evidence_at = datetime.combine(workout_date, time.min, tzinfo=timezone.utc)
            result.append(
                _candidate(
                    kind="missed_workout",
                    evidence=[workout_id, workout_date],
                    evidence_data={"workoutDayIds": [workout_id]},
                    priority=90,
                    evidence_at=evidence_at,
                    title="Review your training week",
                    body=f"{workout.get('title', 'A planned workout')} was scheduled for {workout_date.strftime('%A')}.",
                    detail="Open the workout before deciding how it fits the rest of your week.",
                    protected_facts={
                        "workoutTitle": workout.get("title") or "A planned workout",
                        "scheduledWeekday": workout_date.strftime("%A"),
                        "requiredText": [
                            workout.get("title") or "A planned workout",
                            workout_date.strftime("%A"),
                        ],
                    },
                    target={"type": "planned_workout", "workoutDayId": workout_id, "weekStart": plan.week_start_date},
                    expires_at=evidence_at + timedelta(days=MISSED_ACTION_DAYS),
                )
            )
    return result


def _upcoming_notification_candidates(
    db: Session, user_id: str, local_date: date
) -> list[dict]:
    completed = _completed_day_ids(db, user_id)
    candidates = []
    week_start = local_date - timedelta(days=local_date.weekday())
    for plan, workout in _all_planned_workouts(
        db,
        user_id,
        earliest_week_start=week_start,
        latest_week_start=week_start,
    ):
        workout_date = _iso_date(workout.get("date"))
        workout_id = workout.get("workoutDayId")
        if workout_date == local_date and workout_id not in completed:
            candidates.append((plan, workout))
    if not candidates:
        return []
    plan, workout = min(candidates, key=lambda entry: entry[1].get("slotIndex", 0))
    evidence_at = datetime.combine(local_date, time.min, tzinfo=timezone.utc)
    return [
        _candidate(
            kind="upcoming_workout",
            evidence=[workout.get("workoutDayId"), local_date],
            evidence_data={"workoutDayIds": [workout.get("workoutDayId")]},
            evidence_at=evidence_at,
            # The caller's local date can still be the prior UTC date. Keep the
            # target addressable through that local day; reconciliation removes
            # it as soon as it is no longer today's workout.
            expires_at=evidence_at + timedelta(days=2),
            priority=95,
            title="Today’s workout is ready",
            body=workout.get("title") or "Open today’s planned workout.",
            detail=None,
            protected_facts={"workoutTitle": workout.get("title")},
            target={
                "type": "planned_workout",
                "workoutDayId": workout["workoutDayId"],
                "weekStart": plan.week_start_date,
            },
        )
    ]


def _pr_candidates(db: Session, user_id: str) -> list[dict]:
    cutoff = _now() - timedelta(days=RETENTION_DAYS)
    rows = (
        db.query(SetLog, WorkoutSession, Exercise)
        .join(WorkoutSession, WorkoutSession.id == SetLog.session_id)
        .join(Exercise, Exercise.id == SetLog.exercise_id)
        .filter(
            WorkoutSession.user_id == user_id,
            WorkoutSession.status == "completed",
            SetLog.deleted_at.is_(None),
            SetLog.weight_kg.isnot(None),
            SetLog.weight_kg > 0,
            WorkoutSession.completed_at >= cutoff,
        )
        .order_by(WorkoutSession.completed_at.asc(), SetLog.logged_at.asc(), SetLog.id.asc())
        .all()
    )
    session_bests: dict[tuple[str, str], tuple[float, WorkoutSession, Exercise]] = {}
    ordered_keys: list[tuple[str, str]] = []
    for set_log, session, exercise in rows:
        key = (session.id, set_log.exercise_id)
        weight = float(set_log.weight_kg)
        if key not in session_bests:
            ordered_keys.append(key)
            session_bests[key] = (weight, session, exercise)
        elif weight > session_bests[key][0]:
            session_bests[key] = (weight, session, exercise)

    exercise_ids = {exercise_id for _, exercise_id in ordered_keys}
    prior_rows = (
        db.query(SetLog.exercise_id, func.max(SetLog.weight_kg))
        .join(WorkoutSession, WorkoutSession.id == SetLog.session_id)
        .filter(
            WorkoutSession.user_id == user_id,
            WorkoutSession.status == "completed",
            WorkoutSession.completed_at < cutoff,
            SetLog.exercise_id.in_(exercise_ids),
            SetLog.deleted_at.is_(None),
            SetLog.weight_kg.isnot(None),
            SetLog.weight_kg > 0,
        )
        .group_by(SetLog.exercise_id)
        .all()
        if exercise_ids
        else []
    )
    prior: dict[str, float] = {
        exercise_id: float(maximum) for exercise_id, maximum in prior_rows
    }
    result = []
    for session_id, exercise_id in ordered_keys:
        weight, session, exercise = session_bests[(session_id, exercise_id)]
        old = prior.get(exercise_id)
        if old is not None and weight > old:
            result.append(
                _candidate(
                    kind="personal_record",
                    evidence=[session.id, exercise_id, weight],
                    evidence_data={
                        "sourceSessionIds": [session.id],
                        "exerciseIds": [exercise_id],
                    },
                    evidence_at=session.completed_at,
                    priority=60,
                    title=f"New {exercise.name} best",
                    body=f"You lifted {weight:g} kg—your heaviest logged set for this exercise.",
                    detail=None,
                    protected_facts={
                        "exerciseName": exercise.name,
                        "recordWeightKg": weight,
                        "equipmentIds": [item.id for item in exercise.equipment],
                        "loadProfile": exercise.load_profile,
                        "requiredText": [exercise.name, f"{weight:g} kg"],
                    },
                    target={"type": "completed_session", "sessionId": session.id},
                    expires_at=session.completed_at + timedelta(days=HISTORY_DAYS),
                )
            )
        prior[exercise_id] = max(weight, old or weight)
    return result[-20:]


def _consistency_candidates(db: Session, user_id: str, local_date: date) -> list[dict]:
    completed_sessions = (
        db.query(WorkoutSession)
        .filter(WorkoutSession.user_id == user_id, WorkoutSession.status == "completed")
        .order_by(WorkoutSession.completed_at.asc(), WorkoutSession.id.asc())
        .all()
    )
    total = len(completed_sessions)
    milestones = workout_milestones(total)
    result = []
    for milestone in milestones:
        if total >= milestone:
            session = completed_sessions[milestone - 1]
            if session.completed_at < _now() - timedelta(days=HISTORY_DAYS):
                continue
            result.append(
                _candidate(
                    kind="consistency",
                    evidence=["completed_workouts", milestone, session.id],
                    evidence_data={"statusSessionIds": [session.id]},
                    evidence_at=session.completed_at,
                    priority=40,
                    title=f"{milestone} {'workout' if milestone == 1 else 'workouts'} completed",
                    body="Your completed sessions are adding up. Keep following the schedule that works for you.",
                    detail=None,
                    protected_facts={
                        "completedWorkoutMilestone": milestone,
                        "requiredText": [str(milestone)],
                    },
                    target={"type": "completed_session", "sessionId": session.id},
                    expires_at=session.completed_at + timedelta(days=HISTORY_DAYS),
                )
            )

    closed_week_start = local_date - timedelta(days=local_date.weekday() + 7)
    plan = (
        db.query(WorkoutWeekPlan)
        .filter(
            WorkoutWeekPlan.user_id == user_id,
            WorkoutWeekPlan.week_start_date == closed_week_start,
        )
        .first()
    )
    if plan:
        planned_ids = {
            workout.get("workoutDayId")
            for workout in plan.plan_json.get("workouts", [])
            if workout.get("workoutDayId")
        }
        completed_ids = _completed_day_ids(db, user_id)
        if planned_ids and planned_ids <= completed_ids:
            result.append(
                _candidate(
                    kind="consistency",
                    evidence=["complete_week", closed_week_start, sorted(planned_ids)],
                    evidence_data={"workoutDayIds": sorted(planned_ids)},
                    evidence_at=datetime.combine(
                        closed_week_start + timedelta(days=6),
                        time.max,
                        tzinfo=timezone.utc,
                    ),
                    priority=45,
                    title="You completed the week",
                    body="Every workout on last week’s schedule is complete.",
                    detail=None,
                    protected_facts={
                        "achievement": "completed_scheduled_week",
                        "requiredText": ["completed the week"],
                    },
                    target={"type": "none"},
                    expires_at=datetime.combine(
                        closed_week_start + timedelta(days=HISTORY_DAYS + 6),
                        time.min,
                        tzinfo=timezone.utc,
                    ),
                )
            )
    return result


def _program_review_candidates(db: Session, user_id: str) -> list[dict]:
    activations = (
        db.query(UserProgramActivation)
        .filter(
            UserProgramActivation.user_id == user_id,
            UserProgramActivation.status.in_(["active", "scheduled"]),
            UserProgramActivation.requires_review.is_(True),
        )
        .all()
    )
    return [
        _candidate(
            kind="program_review",
            evidence=[
                row.id,
                row.template_version,
                row.activation_snapshot.get("equipmentReviewFingerprint"),
            ],
            evidence_data={
                "activationIds": [row.id],
                "templateIds": [row.template_id] if row.template_id else [],
            },
            evidence_at=_iso_datetime(
                row.activation_snapshot.get("equipmentReviewChangedAt")
            )
            or row.created_at,
            priority=100,
            title="Review your program equipment",
            body="Your equipment changed. Review replacements before the program updates.",
            detail="Your current workout snapshot will not change.",
            protected_facts={
                "recommendation": "review_equipment",
                "requiredText": ["equipment review"],
            },
            target={"type": "program_review", "activationId": row.id, "templateId": row.template_id},
        )
        for row in activations
    ]


def reconcile_feed(
    db: Session,
    user_id: str,
    local_date: date,
    *,
    commit: bool = True,
    source: str = "feed",
) -> int:
    started = _now()
    db.execute(
        insert(CoachPreference)
        .values(user_id=user_id)
        .on_conflict_do_nothing(index_elements=[CoachPreference.user_id])
    )
    db.flush()
    claimed_revision = (
        db.query(CoachPreference.reconciliation_revision)
        .filter(CoachPreference.user_id == user_id)
        .scalar()
        or 0
    )
    candidates = (
        _progression_candidates(db, user_id, local_date)
        + _recovery_candidate(db, user_id)
        + _missed_candidates(db, user_id, local_date)
        + _upcoming_notification_candidates(db, user_id, local_date)
        + _pr_candidates(db, user_id)
        + _consistency_candidates(db, user_id, local_date)
        + _program_review_candidates(db, user_id)
    )
    now = _now()
    candidates = [candidate for candidate in candidates if candidate["expires_at"] > now]
    preference = (
        db.query(CoachPreference)
        .filter(CoachPreference.user_id == user_id)
        .with_for_update()
        .one()
    )
    if preference.reconciliation_revision != claimed_revision:
        if commit:
            db.commit()
        _event(
            "coach_reconciled",
            user=_opaque(user_id),
            source=source,
            outcome="stale_aborted",
            itemCount=0,
            durationMs=int((_now() - started).total_seconds() * 1000),
        )
        return 0
    active_keys = {candidate["dedupe_key"] for candidate in candidates}
    existing = (
        db.query(CoachFeedItem)
        .filter(CoachFeedItem.user_id == user_id, CoachFeedItem.expires_at > now)
        .all()
    )
    stale_item_ids = []
    for item in existing:
        if item.kind in FEED_KINDS and item.dedupe_key not in active_keys:
            item.expires_at = now
            item.invalidated_at = now
            item.invalidation_reason = "candidate_absent"
            stale_item_ids.append(item.id)
    if stale_item_ids:
        db.query(CoachNotificationJob).filter(
            CoachNotificationJob.feed_item_id.in_(stale_item_ids),
            CoachNotificationJob.status.in_(["pending", "retry", "processing"]),
        ).update({"status": "cancelled", "locked_at": None}, synchronize_session=False)

    eligible = _is_coach_eligible(db, user_id)
    _sync_ai_eligibility_rows(existing, eligible)
    for candidate in candidates:
        ai_status = "not_eligible"
        if eligible:
            ai_status = (
                "pending"
                if candidate["kind"] in AI_SAFE_KINDS
                else "fallback"
            )
        statement = insert(CoachFeedItem).values(
            id=str(uuid.uuid4()),
            user_id=user_id,
            ai_status=ai_status,
            **candidate,
        )
        may_restore = and_(
            CoachFeedItem.dismissed_at.is_(None),
            CoachFeedItem.invalidation_reason == "source_mutated",
            CoachFeedItem.invalidated_at <= started,
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_coach_feed_items_user_dedupe",
            set_={
                "priority": statement.excluded.priority,
                "title": statement.excluded.title,
                "body": statement.excluded.body,
                "detail": statement.excluded.detail,
                "target_data": statement.excluded.target_data,
                "protected_facts": statement.excluded.protected_facts,
                "evidence_data": statement.excluded.evidence_data,
                "expires_at": case(
                    (may_restore, statement.excluded.expires_at),
                    else_=CoachFeedItem.expires_at,
                ),
                "invalidated_at": case(
                    (may_restore, None), else_=CoachFeedItem.invalidated_at
                ),
                "invalidation_reason": case(
                    (may_restore, None), else_=CoachFeedItem.invalidation_reason
                ),
                "updated_at": now,
            },
        )
        db.execute(statement)
    preference.last_reconciled_at = now
    preference.reconciliation_requested_at = None
    preference.reconciled_revision = claimed_revision
    if commit:
        db.commit()
    _event(
        "coach_reconciled",
        user=_opaque(user_id),
        source=source,
        outcome="success",
        itemCount=len(candidates),
        durationMs=int((_now() - started).total_seconds() * 1000),
    )
    return len(candidates)


def reconcile_after_mutation(
    db: Session,
    user_id: str,
    *,
    metric_session_ids: Optional[list[str]] = None,
    status_session_ids: Optional[list[str]] = None,
    exercise_ids: Optional[list[str]] = None,
    workout_day_ids: Optional[list[str]] = None,
    activation_ids: Optional[list[str]] = None,
    template_ids: Optional[list[str]] = None,
    invalidate_plan_items: bool = False,
    invalidate_program_items: bool = False,
) -> int:
    """Mark reconciliation first, then expire only dependent projections."""
    requested_at = _now()
    sources = {
        "metricSessionIds": set(metric_session_ids or []),
        "statusSessionIds": set(status_session_ids or []),
        "exerciseIds": set(exercise_ids or []),
        "workoutDayIds": set(workout_day_ids or []),
        "activationIds": set(activation_ids or []),
        "templateIds": set(template_ids or []),
    }
    try:
        with db.no_autoflush:
            db.execute(
                insert(CoachPreference)
                .values(user_id=user_id)
                .on_conflict_do_nothing(index_elements=[CoachPreference.user_id])
            )
            preference = (
                db.query(CoachPreference)
                .filter(CoachPreference.user_id == user_id)
                .with_for_update()
                .one()
            )
        preference.reconciliation_requested_at = requested_at
        preference.reconciliation_revision += 1
        db.flush([preference])

        rows = (
            db.query(CoachFeedItem)
            .filter(
                CoachFeedItem.user_id == user_id,
                CoachFeedItem.expires_at > requested_at,
            )
            .all()
        )
        stale_ids = []
        for item in rows:
            evidence = item.evidence_data or {}
            dependent = any(
                source_values
                and source_values.intersection(evidence.get(key) or [])
                for key, source_values in sources.items()
            )
            if invalidate_plan_items and item.kind in {
                "progression",
                "missed_workout",
                "upcoming_workout",
            }:
                dependent = True
            if invalidate_program_items and item.kind == "program_review":
                dependent = True
            if dependent:
                item.expires_at = requested_at
                item.invalidated_at = requested_at
                item.invalidation_reason = "source_mutated"
                stale_ids.append(item.id)
        if stale_ids:
            db.query(CoachNotificationJob).filter(
                CoachNotificationJob.feed_item_id.in_(stale_ids),
                CoachNotificationJob.status.in_(["pending", "retry", "processing"]),
            ).update(
                {"status": "cancelled", "locked_at": None},
                synchronize_session=False,
            )
        db.commit()
        return 1
    except Exception as error:
        db.rollback()
        marker_saved = False
        try:
            with db.no_autoflush:
                db.execute(
                    insert(CoachPreference)
                    .values(user_id=user_id)
                    .on_conflict_do_nothing(index_elements=[CoachPreference.user_id])
                )
                preference = (
                    db.query(CoachPreference)
                    .filter(CoachPreference.user_id == user_id)
                    .with_for_update()
                    .one()
                )
            preference.reconciliation_requested_at = requested_at
            preference.reconciliation_revision += 1
            db.commit()
            marker_saved = True
        except Exception:
            db.rollback()
        _event(
            "coach_reconciliation_requested",
            user=_opaque(user_id),
            source="domain_mutation",
            outcome="invalidation_failed_marker_saved" if marker_saved else "failed",
            reason=type(error).__name__,
        )
        return 1 if marker_saved else 0


def _sync_ai_eligibility_rows(items: list[CoachFeedItem], eligible: bool) -> None:
    for item in items:
        if not eligible:
            item.ai_title = None
            item.ai_body = None
            item.ai_detail = None
            item.ai_locked_at = None
            item.ai_claim_token = None
            if item.ai_status != "fallback":
                item.ai_status = "not_eligible"
        elif not _ai_safe_item(item):
            item.ai_title = None
            item.ai_body = None
            item.ai_detail = None
            item.ai_locked_at = None
            item.ai_claim_token = None
            item.ai_status = "fallback"
        elif item.ai_status == "not_eligible":
            item.ai_status = "pending"


def _contains_numeric_fact(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, dict):
        return any(_contains_numeric_fact(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_numeric_fact(item) for item in value)
    return bool(re.search(r"\d", str(value)))


def _ai_safe_item(item: CoachFeedItem) -> bool:
    return item.kind in AI_SAFE_KINDS


def sync_ai_eligibility(db: Session, user_id: str) -> bool:
    eligible = _is_coach_eligible(db, user_id)
    rows = db.query(CoachFeedItem).filter(CoachFeedItem.user_id == user_id).all()
    _sync_ai_eligibility_rows(rows, eligible)
    db.commit()
    return eligible


def _encode_cursor(item: CoachFeedItem) -> str:
    payload = [item.priority, item.created_at.isoformat(), item.id]
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[int, datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        priority, created_at, item_id = json.loads(raw)
        return int(priority), datetime.fromisoformat(created_at), str(item_id)
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid cursor.") from error


def _item_out(item: CoachFeedItem, *, ai_eligible: bool) -> CoachFeedItemOut:
    ai = ai_eligible and item.ai_status == "enriched" and bool(item.ai_title and item.ai_body)
    return CoachFeedItemOut(
        id=item.id,
        kind=item.kind,
        priority=item.priority,
        title=item.ai_title if ai else item.title,
        body=item.ai_body if ai else item.body,
        detail=item.ai_detail if ai else item.detail,
        target=TARGET_ADAPTER.validate_python(item.target_data),
        weightData=public_weight_data(item),
        isAiAssisted=ai,
        readAt=item.read_at,
        createdAt=item.created_at,
        expiresAt=item.expires_at,
    )


def get_feed(
    db: Session,
    user_id: str,
    local_date: date,
    *,
    view: Literal["now", "yesterday", "last7Days"] = "now",
    time_zone: str = "UTC",
    cursor: Optional[str],
    limit: int,
) -> CoachFeedOut:
    if view not in {"now", "yesterday", "last7Days"}:
        raise ValueError("Invalid Coach feed view.")
    try:
        zone = ZoneInfo(time_zone)
    except Exception as error:
        raise ValueError("timeZone must be a valid IANA timezone") from error
    if cursor is None and view == "now":
        reconcile_feed(db, user_id, local_date)
    now = _now()
    query = db.query(CoachFeedItem).filter(
        CoachFeedItem.user_id == user_id,
        CoachFeedItem.available_at <= now,
        CoachFeedItem.dismissed_at.is_(None),
        CoachFeedItem.kind != "upcoming_workout",
    )
    if view == "now":
        query = query.filter(
            CoachFeedItem.expires_at > now,
            CoachFeedItem.invalidated_at.is_(None),
        )
    else:
        start_date = (
            local_date - timedelta(days=HISTORY_DAYS)
            if view == "last7Days"
            else local_date - timedelta(days=1)
        )
        start = datetime.combine(start_date, time.min, tzinfo=zone).astimezone(timezone.utc)
        end = datetime.combine(local_date, time.min, tzinfo=zone).astimezone(
            timezone.utc
        )
        query = query.filter(
            CoachFeedItem.created_at >= start,
            CoachFeedItem.created_at < end,
        )
    if cursor:
        priority, created_at, item_id = _decode_cursor(cursor)
        query = query.filter(
            or_(
                CoachFeedItem.priority < priority,
                and_(CoachFeedItem.priority == priority, CoachFeedItem.created_at < created_at),
                and_(
                    CoachFeedItem.priority == priority,
                    CoachFeedItem.created_at == created_at,
                    CoachFeedItem.id < item_id,
                ),
            )
        )
    rows = (
        query.order_by(
            CoachFeedItem.priority.desc(),
            CoachFeedItem.created_at.desc(),
            CoachFeedItem.id.desc(),
        )
        .limit(limit + 1)
        .all()
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    _event("coach_feed_read", user=_opaque(user_id), pageSize=len(page), hasMore=has_more)
    ai_eligible = _is_coach_eligible(db, user_id)
    next_action = (
        build_next_action(db, user_id, local_date)
        if view == "now"
        else CoachNextActionOut(
            type="caught_up",
            title="Recent guidance",
            body="Historical Coach guidance is read-only.",
            target={"type": "none"},
        )
    )
    return CoachFeedOut(
        nextAction=next_action,
        items=[_item_out(item, ai_eligible=ai_eligible) for item in page],
        nextCursor=_encode_cursor(page[-1]) if has_more and page else None,
        hasMore=has_more,
        generatedAt=now,
    )


def get_item(db: Session, user_id: str, item_id: str) -> Optional[CoachFeedItemOut]:
    item = (
        db.query(CoachFeedItem)
        .filter(
            CoachFeedItem.id == item_id,
            CoachFeedItem.user_id == user_id,
            CoachFeedItem.expires_at > _now(),
            CoachFeedItem.dismissed_at.is_(None),
        )
        .first()
    )
    if item is None or not is_item_current(db, item):
        if item is not None:
            expire_feed_items(
                db,
                user_id,
                {item.kind},
                item_ids={item.id},
                reason="stale_target",
            )
        return None
    return _item_out(item, ai_eligible=_is_coach_eligible(db, user_id))


def is_item_current(db: Session, item: CoachFeedItem) -> bool:
    target = item.target_data or {"type": "none"}
    target_type = target.get("type")
    if target_type == "active_workout":
        return (
            db.query(WorkoutSession.id)
            .filter(
                WorkoutSession.id == target.get("sessionId"),
                WorkoutSession.user_id == item.user_id,
                WorkoutSession.status == "in_progress",
            )
            .first()
            is not None
        )
    if target_type == "completed_session":
        return (
            db.query(WorkoutSession.id)
            .filter(
                WorkoutSession.id == target.get("sessionId"),
                WorkoutSession.user_id == item.user_id,
                WorkoutSession.status == "completed",
            )
            .first()
            is not None
        )
    if target_type == "planned_workout":
        workout_id = target.get("workoutDayId")
        exists = any(
            workout.get("workoutDayId") == workout_id
            for _, workout in _all_planned_workouts(db, item.user_id)
        )
        if not exists:
            return False
        return workout_id not in _completed_day_ids(db, item.user_id)
    if target_type == "program_review":
        return (
            db.query(UserProgramActivation.id)
            .filter(
                UserProgramActivation.id == target.get("activationId"),
                UserProgramActivation.user_id == item.user_id,
                UserProgramActivation.status.in_(["active", "scheduled"]),
                UserProgramActivation.requires_review.is_(True),
            )
            .first()
            is not None
        )
    if target_type == "program_detail":
        return target.get("templateId") is not None
    return target_type in {"none", "settings"}


def mark_read(db: Session, user_id: str, item_id: str, reason: str) -> Optional[CoachFeedItemOut]:
    item = (
        db.query(CoachFeedItem)
        .filter(CoachFeedItem.id == item_id, CoachFeedItem.user_id == user_id)
        .with_for_update()
        .first()
    )
    if (
        item is None
        or item.dismissed_at is not None
        or item.expires_at <= _now()
        or not is_item_current(db, item)
    ):
        return None
    if item.read_at is None:
        item.read_at = _now()
        db.commit()
        db.refresh(item)
    _event("coach_item_read", user=_opaque(user_id), kind=item.kind, reason=reason)
    return _item_out(item, ai_eligible=_is_coach_eligible(db, user_id))


def dismiss_item(db: Session, user_id: str, item_id: str) -> bool:
    item = (
        db.query(CoachFeedItem)
        .filter(CoachFeedItem.id == item_id, CoachFeedItem.user_id == user_id)
        .with_for_update()
        .first()
    )
    if item is None:
        return False
    if item.dismissed_at is None:
        item.dismissed_at = _now()
        db.query(CoachNotificationJob).filter(
            CoachNotificationJob.feed_item_id == item.id,
            CoachNotificationJob.status.in_(["pending", "retry", "processing"]),
        ).update({"status": "cancelled"}, synchronize_session=False)
        db.commit()
    _event("coach_item_dismissed", user=_opaque(user_id), kind=item.kind)
    return True


def get_preferences(db: Session, user_id: str) -> CoachPreferenceOut:
    row = db.get(CoachPreference, user_id)
    if row is None:
        db.execute(
            insert(CoachPreference)
            .values(user_id=user_id)
            .on_conflict_do_nothing(index_elements=[CoachPreference.user_id])
        )
        db.commit()
        row = db.get(CoachPreference, user_id)
    return CoachPreferenceOut(
        notificationsEnabled=row.notifications_enabled,
        reminderTime=row.reminder_time,
        timeZone=row.time_zone,
    )


def update_preferences(
    db: Session, user_id: str, payload: CoachPreferenceUpdate
) -> CoachPreferenceOut:
    statement = insert(CoachPreference).values(
        user_id=user_id,
        notifications_enabled=payload.notifications_enabled,
        reminder_time=payload.reminder_time,
        time_zone=payload.time_zone,
    )
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[CoachPreference.user_id],
            set_={
                "notifications_enabled": statement.excluded.notifications_enabled,
                "reminder_time": statement.excluded.reminder_time,
                "time_zone": statement.excluded.time_zone,
                "updated_at": _now(),
            },
        )
    )
    if not payload.notifications_enabled:
        db.query(CoachNotificationJob).filter(
            CoachNotificationJob.user_id == user_id,
            CoachNotificationJob.status.in_(["pending", "retry", "processing"]),
        ).update({"status": "cancelled"}, synchronize_session=False)
    db.commit()
    reconcile_after_mutation(db, user_id)
    row = db.get(CoachPreference, user_id)
    return CoachPreferenceOut(
        notificationsEnabled=row.notifications_enabled,
        reminderTime=row.reminder_time,
        timeZone=row.time_zone,
    )


def enrich_pending_items(db: Session, user_id: str, *, limit: int = AI_BATCH_LIMIT) -> int:
    if not _is_coach_eligible(db, user_id):
        sync_ai_eligibility(db, user_id)
        return 0
    started = _now()
    now = _now()
    stale_lock = now - timedelta(minutes=10)
    items = (
        db.query(CoachFeedItem)
        .filter(
            CoachFeedItem.user_id == user_id,
            or_(
                CoachFeedItem.ai_status == "pending",
                and_(
                    CoachFeedItem.ai_status == "processing",
                    CoachFeedItem.ai_locked_at < stale_lock,
                ),
            ),
            CoachFeedItem.ai_attempt_count < AI_ATTEMPT_LIMIT,
            CoachFeedItem.expires_at > now,
            CoachFeedItem.kind.in_(AI_SAFE_KINDS),
        )
        .order_by(CoachFeedItem.priority.desc(), CoachFeedItem.created_at.desc())
        .with_for_update(skip_locked=True)
        .limit(min(AI_BATCH_LIMIT, max(1, limit)))
        .all()
    )
    if not items or not settings.ANTHROPIC_API_KEY:
        return 0
    claim_token = str(uuid.uuid4())
    for item in items:
        item.ai_status = "processing"
        item.ai_locked_at = now
        item.ai_claim_token = claim_token
        item.ai_attempt_count += 1
    db.commit()
    choices = [
        {
            "id": item.id,
            "kind": item.kind,
            "availableVariants": list(AI_STYLE_VARIANTS),
        }
        for item in items
    ]
    try:
        response = _call_coach_feed_ai(choices)
        if isinstance(response, dict):
            response = CoachAISelectionBatch.model_validate(response)
        by_id = {entry.id: entry for entry in response.items}
        if not _is_coach_eligible(db, user_id):
            sync_ai_eligibility(db, user_id)
            _event(
                "coach_ai_batch",
                user=_opaque(user_id),
                source="anthropic",
                outcome="discarded_ineligible",
                itemCount=len(items),
                durationMs=int((_now() - started).total_seconds() * 1000),
            )
            return len(items)
        for claimed in items:
            item = (
                db.query(CoachFeedItem)
                .filter(
                    CoachFeedItem.id == claimed.id,
                    CoachFeedItem.ai_status == "processing",
                    CoachFeedItem.ai_claim_token == claim_token,
                )
                .first()
            )
            if item is None:
                continue
            entry = by_id.get(item.id)
            if not _valid_ai_entry(entry, item):
                item.ai_status = (
                    "fallback" if item.ai_attempt_count >= AI_ATTEMPT_LIMIT else "pending"
                )
                item.ai_locked_at = None
                item.ai_claim_token = None
                continue
            item.ai_title, item.ai_body, item.ai_detail = _render_ai_variant(
                item, entry.variant
            )
            item.ai_status = "enriched"
            item.ai_locked_at = None
            item.ai_claim_token = None
        db.commit()
        _event(
            "coach_ai_batch",
            user=_opaque(user_id),
            source="anthropic",
            outcome="success",
            itemCount=len(items),
            attempt=max(item.ai_attempt_count for item in items),
            durationMs=int((_now() - started).total_seconds() * 1000),
        )
    except Exception as error:
        claimed_rows = (
            db.query(CoachFeedItem)
            .filter(
                CoachFeedItem.user_id == user_id,
                CoachFeedItem.ai_status == "processing",
                CoachFeedItem.ai_claim_token == claim_token,
            )
            .all()
        )
        for item in claimed_rows:
            item.ai_status = (
                "fallback" if item.ai_attempt_count >= AI_ATTEMPT_LIMIT else "pending"
            )
            item.ai_locked_at = None
            item.ai_claim_token = None
        db.commit()
        _event(
            "coach_ai_batch",
            user=_opaque(user_id),
            source="anthropic",
            outcome="fallback",
            reason=type(error).__name__,
            itemCount=len(claimed_rows),
            durationMs=int((_now() - started).total_seconds() * 1000),
        )
    return len(items)


def _valid_ai_entry(
    entry: Optional[CoachAISelection], item: CoachFeedItem
) -> bool:
    return (
        entry is not None
        and entry.id == item.id
        and item.kind in AI_VARIANT_TITLES
        and entry.variant in AI_VARIANT_TITLES[item.kind]
    )


def _render_ai_variant(
    item: CoachFeedItem, variant: str
) -> tuple[str, str, Optional[str]]:
    title = AI_VARIANT_TITLES[item.kind][variant]
    if item.kind == "progression":
        facts = item.protected_facts or {}
        action = (
            "Move up"
            if facts.get("recommendation") == "increase"
            else "Use a lighter load"
        )
        title = title.format(
            action=action,
            exercise_name=facts.get("exerciseName") or "this exercise",
        )
    return title, item.body, item.detail


def _call_coach_feed_ai(items: list[dict]) -> CoachAISelectionBatch:
    import anthropic

    for item in items:
        if (
            item.get("kind") not in AI_SAFE_KINDS
            or item.get("availableVariants") != list(AI_STYLE_VARIANTS)
            or set(item) != {"id", "kind", "availableVariants"}
        ):
            raise ValueError("Unsafe Coach AI payload")
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=10.0)
    message = client.messages.parse(
        model=settings.ANTHROPIC_QA_MODEL,
        max_tokens=1800,
        temperature=0.2,
        system=(
            "Choose exactly one available wording variant for each categorical PrimeRep card. "
            "Do not generate coaching copy, recommendations, facts, or additional fields."
        ),
        messages=[{"role": "user", "content": json.dumps({"items": items})}],
        output_format=CoachAISelectionBatch,
    )
    result = getattr(message, "parsed_output", None)
    if not isinstance(result, CoachAISelectionBatch):
        raise ValueError("Malformed Coach AI response")
    return result


def purge_expired(db: Session, *, limit: int = 500) -> tuple[int, int]:
    cutoff = _now()
    delivery_cutoff = cutoff - timedelta(days=RETENTION_DAYS)
    item_cutoff = cutoff - timedelta(days=RETENTION_DAYS)
    from app.models.coach import CoachNotificationDelivery

    capped = max(1, min(limit, 500))
    delivery_ids = [
        row[0]
        for row in db.query(CoachNotificationDelivery.id)
        .filter(CoachNotificationDelivery.created_at < delivery_cutoff)
        .order_by(CoachNotificationDelivery.created_at.asc())
        .limit(capped)
        .all()
    ]
    deliveries = (
        db.query(CoachNotificationDelivery)
        .filter(CoachNotificationDelivery.id.in_(delivery_ids))
        .delete(synchronize_session=False)
        if delivery_ids
        else 0
    )
    item_ids = [
        row[0]
        for row in db.query(CoachFeedItem.id)
        .filter(
            CoachFeedItem.expires_at <= cutoff,
            CoachFeedItem.created_at <= item_cutoff,
            or_(
                CoachFeedItem.invalidation_reason != "source_mutated",
                CoachFeedItem.invalidation_reason.is_(None),
                CoachFeedItem.invalidated_at <= delivery_cutoff,
            ),
        )
        .order_by(CoachFeedItem.expires_at.asc())
        .limit(capped)
        .all()
    ]
    items = (
        db.query(CoachFeedItem)
        .filter(CoachFeedItem.id.in_(item_ids))
        .delete(synchronize_session=False)
        if item_ids
        else 0
    )
    db.commit()
    _event(
        "coach_purged",
        source="worker",
        outcome="success",
        itemCount=items,
        deliveryCount=deliveries,
    )
    return items, deliveries


def expire_feed_items(
    db: Session,
    user_id: str,
    kinds: set[str],
    *,
    item_ids: Optional[set[str]] = None,
    reason: str = "candidate_absent",
) -> int:
    """Hide a stale projection immediately; the next reconciliation may replace it."""
    if not kinds:
        return 0
    now = _now()
    query = db.query(CoachFeedItem).filter(
            CoachFeedItem.user_id == user_id,
            CoachFeedItem.kind.in_(kinds),
            CoachFeedItem.expires_at > now,
        )
    if item_ids is not None:
        query = query.filter(CoachFeedItem.id.in_(item_ids))
    ids = [row[0] for row in query.with_entities(CoachFeedItem.id).all()]
    count = query.update(
        {
            "expires_at": now,
            "invalidated_at": now,
            "invalidation_reason": reason,
        },
        synchronize_session=False,
    )
    if ids:
        db.query(CoachNotificationJob).filter(
            CoachNotificationJob.feed_item_id.in_(ids),
            CoachNotificationJob.status.in_(["pending", "retry", "processing"]),
        ).update({"status": "cancelled", "locked_at": None}, synchronize_session=False)
    db.commit()
    if count:
        _event("coach_items_invalidated", user=_opaque(user_id), itemCount=count)
    return count


def local_reminder_utc(local_date: date, reminder: time, time_zone: str) -> datetime:
    local = datetime.combine(local_date, reminder, tzinfo=ZoneInfo(time_zone))
    return local.astimezone(timezone.utc)
