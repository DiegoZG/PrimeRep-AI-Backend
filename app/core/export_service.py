import csv
import json
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.onboarding_service import canonical_onboarding_data
from app.core.profile_service import get_profile
from app.core.coach_weight_data import public_weight_data
from app.models.coach import CoachFeedItem, CoachPreference
from app.models.exercise import Exercise, user_exercise_favorites
from app.models.exercise_question import ExerciseQuestion
from app.models.onboarding_profile import OnboardingProfile
from app.models.set_log import SetLog
from app.models.user import User
from app.models.user_equipment_weights import UserEquipmentWeights
from app.models.user_exercise_note import UserExerciseNote
from app.models.workout_session import WorkoutSession
from app.models.workout_session_exercise_feedback import WorkoutSessionExerciseFeedback
from app.models.workout_template import UserProgramActivation, WorkoutTemplate
from app.models.workout_week_plan import WorkoutWeekPlan
from app.schemas.onboarding import OnboardingData


MAX_EXPORT_BYTES = 256 * 1024 * 1024
BATCH_SIZE = 250
KG_TO_LB = 2.2046226218487757


def fields(names: str) -> dict:
    return dict.fromkeys(names.split())


LOAD = fields("basis implement_count")
EXERCISE = {
    **fields("id name exerciseType primaryMuscle imageUrl demoVideoUrl contentVersion exercise_type primary_muscle image_url demo_video_url content_version movement_pattern resistance_modality difficulty laterality tracking_mode is_active source"),
    "secondaryMuscles": [None], "requiredEquipmentIds": [None],
    "secondary_muscles": [None], "required_equipment_ids": [None],
    "loadProfile": LOAD, "load_profile": LOAD,
}
WORKOUT = {
    **fields("workoutDayId slotIndex date durationMinutes title splitKey dayType estimatedMinutes workoutIntent programActivationId templateId templateVersion templateDayId templateDayPosition"),
    "deferredMuscles": [None], "unavailableMuscles": [None],
    "exerciseBlocks": [{
        "blockType": None,
        "items": [{
            "exercise": EXERCISE,
            "prescription": fields("sets repsMin repsMax restSeconds suggestedWeightKg suggestedPreviousWeightKg suggestedWeightReason"),
            "exerciseRationale": None,
        }],
    }],
}
WEEK = {
    **fields("weekStart daysPerWeek generatedAt programActivationId templateId templateVersion"),
    "workouts": [WORKOUT],
}
ACTIVATION = {
    **fields("id scope name description goal level frequency durationMinutes version"),
    "equipmentIds": [None], "aliases": [None],
    "days": [{
        **fields("id position title dayType"),
        "exercises": [{
            **fields("id position exerciseId originalExerciseId blockType sets repsMin repsMax restSeconds cue"),
            "exercise": EXERCISE,
        }],
    }],
    "installedWeekPlan": WEEK,
}
ONBOARDING = {
    **dict.fromkeys(OnboardingData.model_fields),
    "selectedEquipment": [None], "dumbbellWeights": [None], "plateWeights": [None],
    "customWorkouts": [{**fields("id name type"), "muscleGroups": [None]}],
    **fields("fitness_goal goal experience_level workout_frequency days_per_week workout_split split_preference training_place"),
    "selected_equipment": [None], "equipment_ids": [None],
    "custom_workouts": [{**fields("id name type"), "muscleGroups": [None]}],
}
COACH_TARGET = fields("type sessionId workoutDayId weekStart templateId activationId section")


def project(value, shape):
    if value is None:
        return None
    if isinstance(shape, dict):
        if not isinstance(value, dict):
            return None
        return {key: project(value[key], child) for key, child in shape.items() if key in value}
    if isinstance(shape, list):
        return [project(item, shape[0]) for item in value] if isinstance(value, list) else []
    return value if isinstance(value, (str, int, float, bool)) else None


def record(row, names: str) -> dict:
    return {name: getattr(row, name) for name in names.split()}


def json_default(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    raise TypeError(f"Unsupported export value: {type(value).__name__}")


def safe_csv(value):
    if value is None:
        return ""
    if isinstance(value, str) and (
        value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@"))
    ):
        return "'" + value
    return value


class ExportTooLarge(Exception):
    pass


class Budget:
    def __init__(self):
        self.used = 0

    def consume(self, size):
        self.used += size
        if self.used > MAX_EXPORT_BYTES:
            raise ExportTooLarge("Your export is too large to generate here. Please contact support.")


class ZipText:
    def __init__(self, stream, budget):
        self.stream = stream
        self.budget = budget

    def write(self, value):
        encoded = value.encode("utf-8")
        self.budget.consume(len(encoded))
        self.stream.write(encoded)
        return len(value)


@dataclass
class ExportArchive:
    directory: Path
    path: Path
    filename: str

    def cleanup(self):
        shutil.rmtree(self.directory, ignore_errors=True)


def _rows(db, model, user_id, names, owner="user_id"):
    query = db.query(model).filter(getattr(model, owner) == user_id)
    primary = list(model.__table__.primary_key.columns)
    for row in query.order_by(*primary).yield_per(BATCH_SIZE):
        yield record(row, names)


def _programs(db, user_id):
    for row in db.query(WorkoutTemplate).filter(WorkoutTemplate.owner_user_id == user_id).order_by(WorkoutTemplate.id).yield_per(BATCH_SIZE):
        value = record(row, "id name description goal level frequency duration_minutes version is_archived created_at updated_at")
        value["aliases"] = sorted(alias.alias for alias in row.aliases)
        value["equipment_ids"] = sorted(item.equipment_id for item in row.equipment_requirements)
        value["days"] = [
            {**record(day, "id position title day_type"), "exercises": [
                record(item, "id exercise_id position block_type sets reps_min reps_max rest_seconds cue")
                for item in day.exercises
            ]}
            for day in row.days
        ]
        yield value


def _private_exercises(db, user_id):
    for row in db.query(Exercise).filter(Exercise.owner_user_id == user_id).order_by(Exercise.id).yield_per(BATCH_SIZE):
        value = record(row, "id name exercise_type primary_muscle secondary_muscles how_to why_it_works common_mistakes beginner_notes is_active created_at updated_at")
        value["equipment_ids"] = sorted(item.id for item in row.equipment)
        yield value


def _snapshots(db, model, user_id, names, column, shape):
    for row in db.query(model).filter(model.user_id == user_id).order_by(model.id).yield_per(BATCH_SIZE):
        yield {**record(row, names), column: project(getattr(row, column), shape)}


def _coach_items(db, user_id):
    for row in db.query(CoachFeedItem).filter(CoachFeedItem.user_id == user_id).order_by(CoachFeedItem.created_at, CoachFeedItem.id).yield_per(BATCH_SIZE):
        value = record(row, "id kind title body detail available_at expires_at read_at dismissed_at created_at updated_at")
        if row.ai_status == "enriched":
            value.update(title=row.ai_title or row.title, body=row.ai_body or row.body, detail=row.ai_detail or row.detail)
        value["target"] = project(row.target_data, COACH_TARGET)
        value["weight_data"] = public_weight_data(row)
        yield value


def _collections(db, user_id):
    sessions = db.query(WorkoutSession.id).filter(WorkoutSession.user_id == user_id)
    yield "favorites", (
        dict(row._mapping) for row in db.execute(
            select(user_exercise_favorites.c.exercise_id, user_exercise_favorites.c.created_at)
            .where(user_exercise_favorites.c.user_id == user_id)
            .order_by(user_exercise_favorites.c.exercise_id)
            .execution_options(yield_per=BATCH_SIZE)
        )
    )
    yield "private_exercises", _private_exercises(db, user_id)
    yield "private_programs", _programs(db, user_id)
    yield "program_activations", _snapshots(db, UserProgramActivation, user_id,
        "id template_id template_version status effective_date weekdays apply_mode requires_review created_at deactivated_at", "activation_snapshot", ACTIVATION)
    yield "workout_plans", _snapshots(db, WorkoutWeekPlan, user_id,
        "id week_start_date days_per_week created_at updated_at", "plan_json", WEEK)
    yield "workout_sessions", _snapshots(db, WorkoutSession, user_id,
        "id workout_day_id workout_date day_type status started_at completed_at workout_note created_at", "workout_snapshot", WORKOUT)
    yield "sets", (
        record(row, "id session_id exercise_id set_number reps weight_kg version logged_at updated_at deleted_at")
        for row in db.query(SetLog).filter(SetLog.session_id.in_(sessions)).order_by(SetLog.id).yield_per(BATCH_SIZE)
    )
    yield "exercise_feedback", (
        record(row, "id session_id exercise_id effort rir updated_at")
        for row in db.query(WorkoutSessionExerciseFeedback).filter(WorkoutSessionExerciseFeedback.session_id.in_(sessions)).order_by(WorkoutSessionExerciseFeedback.id).yield_per(BATCH_SIZE)
    )
    yield "exercise_notes", _rows(db, UserExerciseNote, user_id, "id exercise_id note updated_at")
    yield "exercise_questions", _rows(db, ExerciseQuestion, user_id, "id exercise_id question answer content_version created_at")
    yield "coach_items", _coach_items(db, user_id)


def _write_json(archive, budget, db, user, now, unit):
    profile = db.get(OnboardingProfile, user.id)
    equipment = db.get(UserEquipmentWeights, user.id)
    coach = db.get(CoachPreference, user.id)
    metadata = {
        "schema_version": 1, "generated_at": now, "csv_weight_unit": unit,
        "account": record(user, "id email preferred_name last_name has_completed_onboarding subscription_tier coach_insights_enabled terms_accepted_version privacy_accepted_version legal_accepted_at created_at updated_at"),
        "profile": get_profile(db, user).model_dump(mode="json"),
        "onboarding": project(canonical_onboarding_data(profile, user), ONBOARDING) if profile else None,
        "equipment_weights": record(equipment, "dumbbell_weights plate_weights updated_at") if equipment else None,
        "coach_preferences": record(coach, "notifications_enabled reminder_time time_zone") if coach else None,
    }
    encoder = json.JSONEncoder(default=json_default, ensure_ascii=False, allow_nan=False)
    with archive.open("account.json", "w") as stream:
        out = ZipText(stream, budget)
        encoded = encoder.encode(metadata)
        out.write(encoded[:-1])
        for name, values in _collections(db, user.id):
            out.write(f', "{name}": [')
            first = True
            for value in values:
                if not first:
                    out.write(",")
                first = False
                for chunk in encoder.iterencode(value):
                    out.write(chunk)
            out.write("]")
        out.write("}\n")


def _write_csv(archive, budget, db, user_id, unit):
    factor = KG_TO_LB if unit == "LB" else 1
    completed = (WorkoutSession.user_id == user_id, WorkoutSession.status == "completed", WorkoutSession.completed_at.isnot(None))
    totals = (
        db.query(SetLog.session_id.label("session_id"), func.count(SetLog.id).label("set_count"), func.sum(SetLog.weight_kg * SetLog.reps).label("volume"))
        .join(WorkoutSession, WorkoutSession.id == SetLog.session_id)
        .filter(*completed, SetLog.deleted_at.is_(None))
        .group_by(SetLog.session_id).subquery()
    )
    with archive.open("workouts.csv", "w") as stream:
        writer = csv.writer(ZipText(stream, budget))
        writer.writerow(["session_id", "title", "scheduled_date", "started_at", "completed_at", "duration_seconds", "completed_set_count", f"total_volume_{unit.lower()}"])
        query = db.query(WorkoutSession, totals.c.set_count, totals.c.volume).outerjoin(totals, totals.c.session_id == WorkoutSession.id).filter(*completed).order_by(WorkoutSession.completed_at, WorkoutSession.id)
        for row, set_count, volume in query.yield_per(BATCH_SIZE):
            title = (row.workout_snapshot or {}).get("title") or row.day_type.replace("_", " ").title()
            writer.writerow([safe_csv(value) for value in [row.id, title, row.workout_date, row.started_at.isoformat(), row.completed_at.isoformat(), max(0, int((row.completed_at - row.started_at).total_seconds())), set_count or 0, round(float(volume or 0) * factor, 2)]])
    with archive.open("sets.csv", "w") as stream:
        writer = csv.writer(ZipText(stream, budget))
        writer.writerow(["session_id", "set_id", "exercise_id", "exercise_name", "set_number", "reps", f"weight_{unit.lower()}", "logged_at"])
        query = db.query(SetLog, Exercise.name, WorkoutSession.workout_snapshot).join(WorkoutSession, WorkoutSession.id == SetLog.session_id).join(Exercise, Exercise.id == SetLog.exercise_id).filter(*completed, SetLog.deleted_at.is_(None)).order_by(SetLog.session_id, SetLog.exercise_id, SetLog.set_number)
        for row, name, snapshot in query.yield_per(BATCH_SIZE):
            name = next((
                item["exercise"]["name"]
                for block in (snapshot or {}).get("exerciseBlocks", [])
                for item in block.get("items", [])
                if item.get("exercise", {}).get("id") == row.exercise_id and item["exercise"].get("name")
            ), name)
            writer.writerow([safe_csv(value) for value in [row.session_id, row.id, row.exercise_id, name, row.set_number, row.reps, round(row.weight_kg * factor, 3) if row.weight_kg is not None else None, row.logged_at.isoformat()]])


def create_export(bind, user_id: str, auth_version: int, weight_unit: str) -> ExportArchive:
    now = datetime.now(timezone.utc)
    directory = Path(tempfile.mkdtemp(prefix="primerep-export-"))
    artifact = ExportArchive(directory, directory / "archive.zip", f"primerep-data-{now.date().isoformat()}.zip")
    budget = Budget()
    try:
        with bind.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                with Session(bind=connection) as db:
                    user = db.get(User, user_id)
                    if user is None or user.auth_version != auth_version:
                        raise HTTPException(status_code=401, detail="Your session has ended. Please log in again.")
                    with ZipFile(artifact.path, "w", compression=ZIP_DEFLATED) as archive:
                        _write_json(archive, budget, db, user, now, weight_unit)
                        _write_csv(archive, budget, db, user_id, weight_unit)
                        from app.models.user_avatar import UserAvatar

                        avatar = db.get(UserAvatar, user_id)
                        if avatar:
                            budget.consume(len(avatar.data))
                            archive.writestr("profile-photo.jpg", avatar.data)
                        readme = f"""PrimeRep personal data export — schema version 1
Generated: {now.isoformat()}

account.json contains your saved profile, preferences, private programs/exercises,
workout plans and session snapshots, notes, feedback, questions and retained Coach
content. Sets include deleted_at; removed sets remain in JSON for transparency.
Canonical set weights and Coach weight_data values in JSON are kilograms. Coach
weight_data uses the public API's field names and preserves target/record load context.
Other stored weight preferences retain
their original conventions and explicit units. Snapshot fields retain API casing.

workouts.csv includes completed sessions only; duration_seconds is elapsed time.
sets.csv includes current (nonremoved) sets in completed sessions only. CSV weights
and total volume use {weight_unit}. Volume is recorded external weight multiplied by
repetitions; bodyweight is not estimated. Blank weight means no weight was recorded.
Timestamps use ISO 8601 with timezone offsets; scheduled dates are calendar dates.
CSV text beginning with spreadsheet formula characters is prefixed with an apostrophe.

The archive represents server-stored data at generation time. Pending device-only
workout changes are not included. Deleted/expired data no longer retained by the
service cannot be exported. Credentials, push tokens, internal AI prompts, and
operational or catalog-review records are excluded. This archive is for portability;
PrimeRep does not currently support importing it. Keep it somewhere private.
"""
                        budget.consume(len(readme.encode("utf-8")))
                        archive.writestr("README.txt", readme)
        return artifact
    except BaseException:
        artifact.cleanup()
        raise
