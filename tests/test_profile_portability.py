import asyncio
import csv
import io
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.v1.users.portability_router import PrivateExportResponse
from app.core import export_service
from app.core.coach_feed_service import _item_out
from app.core.achievement_service import calculate_achievements, get_achievements, workout_milestones
from app.core.database import SessionLocal, engine
from app.main import app
from app.models.coach import CoachFeedItem, CoachPreference
from app.models.exercise import Exercise, user_exercise_favorites
from app.models.exercise_question import ExerciseQuestion
from app.models.onboarding_profile import OnboardingProfile
from app.models.set_log import SetLog
from app.models.user import User
from app.models.user_avatar import UserAvatar
from app.models.user_equipment_weights import UserEquipmentWeights
from app.models.user_exercise_note import UserExerciseNote
from app.models.workout_session import WorkoutSession
from app.models.workout_session_exercise_feedback import WorkoutSessionExerciseFeedback
from app.models.workout_template import UserProgramActivation, WorkoutTemplate, WorkoutTemplateDay, WorkoutTemplateExercise
from app.models.workout_week_plan import WorkoutWeekPlan
from conftest import LEGAL_ACCEPTANCE


client = TestClient(app)
UTC = timezone.utc


@pytest.fixture
def accounts():
    result = []
    for _ in range(2):
        response = client.post("/v1/auth/signup", json={
            "email": f"part8-export-{uuid.uuid4().hex}@example.com",
            "password": "StrongPass123", "preferred_name": "Exporter",
            "legalAcceptance": LEGAL_ACCEPTANCE,
        })
        assert response.status_code == 201, response.text
        headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
        user_id = client.get("/v1/users/me", headers=headers).json()["id"]
        result.append((user_id, headers))
    yield result
    with SessionLocal() as db:
        for user_id, _ in result:
            db.query(User).filter_by(id=user_id).delete(synchronize_session=False)
        db.commit()


def session(user_id, completed_at, **overrides):
    return WorkoutSession(
        id=str(uuid.uuid4()), user_id=user_id,
        workout_day_id=str(uuid.uuid4()), workout_date=date(2001, 1, 1),
        day_type="full_body", client_session_id=str(uuid.uuid4()),
        status="completed" if completed_at else "in_progress",
        completed_at=completed_at,
        started_at=(completed_at or datetime.now(UTC)) - timedelta(minutes=35),
        **overrides,
    )


def test_milestones_and_current_week_grace_use_completion_date():
    now = datetime(2026, 1, 12, 12, tzinfo=UTC)
    dates = [datetime(2025, 12, 22, 15, tzinfo=UTC), datetime(2025, 12, 29, 15, tzinfo=UTC), datetime(2026, 1, 5, 15, tzinfo=UTC)]
    result = calculate_achievements(dates, time_zone="UTC", now=now)
    assert result["current_active_weeks"] == 3
    assert result["best_active_weeks"] == 3
    assert result["weekly_activity"][-1]["completed_workouts"] == 0
    earned = {item["id"]: item["earned_at"] for item in result["items"]}
    assert earned["active-weeks-2"] == dates[1]
    assert earned["workouts-1"] == dates[0]
    later = calculate_achievements(dates, time_zone="UTC", now=now + timedelta(weeks=1))
    assert later["current_active_weeks"] == 0
    assert later["best_active_weeks"] == 3
    assert next(item for item in later["items"] if item["id"] == "active-weeks-2")["earned_at"] == dates[1]
    assert workout_milestones(153, include_next=True) == [1, 5, 10, 25, 50, 100, 150, 200]


def test_local_monday_dst_and_multiple_sessions():
    dates = [datetime(2026, 3, 2, 5, tzinfo=UTC), datetime(2026, 3, 9, 3, 59, tzinfo=UTC), datetime(2026, 3, 9, 4, tzinfo=UTC)]
    result = calculate_achievements(dates, time_zone="America/New_York", now=datetime(2026, 3, 10, tzinfo=UTC))
    assert result["current_active_weeks"] == 2
    assert result["weekly_activity"][-2]["completed_workouts"] == 2
    assert result["weekly_activity"][-1]["completed_workouts"] == 1
    assert result["total_completed"] == 3
    assert next(item for item in result["items"] if item["id"] == "active-weeks-2")["earned_at"] == dates[2]


def test_achievements_ownership_initial_timezone_and_corrections(accounts):
    (user_id, headers), (other_id, _) = accounts
    with SessionLocal() as db:
        own = session(user_id, datetime(2026, 1, 5, 12, tzinfo=UTC))
        db.add_all([own, session(other_id, datetime(2026, 1, 6, 12, tzinfo=UTC)), session(user_id, None)])
        db.commit()
        own_id = own.id
        db.query(CoachPreference).filter_by(user_id=user_id).delete()
        db.commit()
    response = client.get("/v1/users/me/achievements?time_zone=America/New_York", headers=headers)
    assert response.status_code == 200
    assert response.json()["total_completed"] == 1
    assert response.json()["time_zone"] == "America/New_York"
    assert client.get("/v1/users/me/achievements?time_zone=UTC", headers=headers).json()["time_zone"] == "America/New_York"
    assert client.get("/v1/users/me/achievements?time_zone=bad-zone", headers=headers).status_code == 422
    with SessionLocal() as db:
        own = db.get(WorkoutSession, own_id)
        own.status = "abandoned"
        own.completed_at = None
        db.commit()
        assert get_achievements(db, user_id)["total_completed"] == 0


def populate_export(user_id, other_id):
    now = datetime.now(UTC)
    with SessionLocal() as db:
        private = Exercise(id=f"private-{uuid.uuid4()}", name="=HYPERLINK(secret)", exercise_type="strength", primary_muscle="chest", secondary_muscles=[], source="user", owner_user_id=user_id, how_to="My instructions")
        other = Exercise(id=f"other-{uuid.uuid4()}", name="OTHER_ACCOUNT_PRIVATE", exercise_type="strength", primary_muscle="chest", secondary_muscles=[], source="user", owner_user_id=other_id)
        db.add_all([private, other])
        db.flush()
        snapshot = {"title": "=SUM(1,2)", "workoutDayId": "retained-id", "exerciseBlocks": [{"blockType": "main", "items": [{"exercise": {"id": private.id, "name": "My exercise", "loadProfile": {"basis": "total", "implement_count": 1, "api_key": "NESTED_SECRET"}}, "prescription": {"sets": 3, "repsMin": 5, "repsMax": 8, "restSeconds": 60}, "system_prompt": "NESTED_SECRET"}]}], "system_prompt": "NESTED_SECRET"}
        completed = session(user_id, now, workout_snapshot=snapshot, workout_note="Private workout note")
        active = session(user_id, None, workout_snapshot=snapshot)
        db.add_all([completed, active, session(other_id, now, workout_note="OTHER_ACCOUNT_PRIVATE")])
        db.flush()
        current = SetLog(session_id=completed.id, exercise_id=private.id, set_number=1, reps=10, weight_kg=10, client_operation_id=str(uuid.uuid4()))
        db.add_all([current, SetLog(session_id=completed.id, exercise_id=private.id, set_number=2, reps=100, weight_kg=100, deleted_at=now, client_operation_id=str(uuid.uuid4()))])
        db.add(WorkoutSessionExerciseFeedback(session_id=completed.id, exercise_id=private.id, effort="hard", rir=1))
        db.add(UserExerciseNote(user_id=user_id, exercise_id=private.id, note="My exercise note"))
        db.add(ExerciseQuestion(user_id=user_id, exercise_id=private.id, question="My question", answer="My answer", content_version="reviewed-v1"))
        db.execute(user_exercise_favorites.insert().values(user_id=user_id, exercise_id=private.id))
        db.merge(OnboardingProfile(user_id=user_id, data={"weight": 180, "weightUnit": "LB", "selectedEquipment": [], "password": "ONBOARDING_SECRET", "customWorkouts": [{"id": "home", "name": "My home workout", "type": "custom", "muscleGroups": ["chest"], "token": "NESTED_SECRET"}]}))
        db.merge(UserEquipmentWeights(user_id=user_id, dumbbell_weights=[5, 10], plate_weights=[2.5, 5]))
        db.merge(UserAvatar(user_id=user_id, data=b"jpeg-test-data", version=str(uuid.uuid4()), content_type="image/jpeg"))
        db.add(WorkoutWeekPlan(user_id=user_id, week_start_date=date(2026, 1, 5), days_per_week=1, plan_json={"weekStart": "2026-01-05", "workouts": [snapshot], "seed": "OPERATIONAL_SECRET"}))
        template = WorkoutTemplate(owner_user_id=user_id, scope="private", name="My program", description="My description", goal="build-muscle", level="beginner", frequency=1, duration_minutes=35, version=1)
        day = WorkoutTemplateDay(position=0, title="My day", day_type="full_body")
        day.exercises = [WorkoutTemplateExercise(exercise_id=private.id, position=0, block_type="main", sets=3, reps_min=5, reps_max=8, rest_seconds=60)]
        template.days = [day]
        db.add(template)
        db.flush()
        db.add(UserProgramActivation(user_id=user_id, template_id=template.id, template_version=1, status="active", effective_date=date(2026, 1, 5), weekdays=[0], activation_snapshot={"name": "My program", "installedWeekPlan": {"workouts": [snapshot]}, "system_prompt": "NESTED_SECRET"}, apply_mode="now", client_operation_id=str(uuid.uuid4()), request_fingerprint="OPERATIONAL_SECRET"))
        db.add(CoachFeedItem(user_id=user_id, kind="consistency", dedupe_key=str(uuid.uuid4()), evidence_fingerprint="OPERATIONAL_SECRET", protected_facts={"secret": "PROTECTED_SECRET"}, title="Visible coaching", body="User-facing guidance", ai_status="fallback", ai_claim_token="COACH_SECRET", target_data={"type": "completed_session", "sessionId": completed.id, "secret": "NESTED_SECRET"}, available_at=now, expires_at=now + timedelta(days=30)))
        db.commit()
        return completed.id, current.id


def test_populated_export_allowlist_csv_and_isolation(accounts):
    (user_id, headers), (other_id, _) = accounts
    populate_export(user_id, other_id)
    response = client.get("/v1/users/me/export?weight_unit=LB", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert "attachment" in response.headers["content-disposition"]
    with ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {"account.json", "workouts.csv", "sets.csv", "README.txt", "profile-photo.jpg"}
        raw = archive.read("account.json").decode()
        data = json.loads(raw)
        for forbidden in ["password_hash", "auth_version", "ONBOARDING_SECRET", "NESTED_SECRET", "OPERATIONAL_SECRET", "COACH_SECRET", "PROTECTED_SECRET", "OTHER_ACCOUNT_PRIVATE", "profile_managed_fields"]:
            assert forbidden not in raw
        assert data["schema_version"] == 1
        assert data["profile"]["weight"] == 180
        assert data["profile"]["weight_unit"] == "LB"
        assert data["account"]["terms_accepted_version"] == "2026-09-12"
        assert len(data["workout_sessions"]) == 2
        assert len(data["sets"]) == 2
        assert len(data["private_programs"][0]["days"]) == 1
        assert data["exercise_notes"][0]["note"] == "My exercise note"
        assert data["exercise_questions"][0]["answer"] == "My answer"
        assert data["private_exercises"][0]["how_to"] == "My instructions"
        assert data["program_activations"][0]["activation_snapshot"]["name"] == "My program"
        workouts = list(csv.DictReader(io.StringIO(archive.read("workouts.csv").decode())))
        sets = list(csv.DictReader(io.StringIO(archive.read("sets.csv").decode())))
        assert len(workouts) == len(sets) == 1
        assert workouts[0]["title"].startswith("'=")
        assert sets[0]["exercise_name"] == "My exercise"
        assert workouts[0]["completed_set_count"] == "1"
        assert float(workouts[0]["total_volume_lb"]) == pytest.approx(220.46)
        assert float(sets[0]["weight_lb"]) == pytest.approx(22.046)
        assert archive.read("profile-photo.jpg") == b"jpeg-test-data"


def test_snapshot_does_not_mix_later_mutations(accounts, monkeypatch):
    user_id, _ = accounts[0]
    _, set_id = populate_export(user_id, accounts[1][0])
    original = export_service._write_json

    def write_then_mutate(*args):
        original(*args)
        with SessionLocal() as db:
            db.get(SetLog, set_id).reps = 99
            db.commit()

    monkeypatch.setattr(export_service, "_write_json", write_then_mutate)
    artifact = export_service.create_export(engine, user_id, 0, "KG")
    try:
        with ZipFile(artifact.path) as archive:
            sets = list(csv.DictReader(io.StringIO(archive.read("sets.csv").decode())))
            assert sets[0]["reps"] == "10"
    finally:
        artifact.cleanup()
    assert not artifact.directory.exists()


def test_export_empty_account_rate_limit_is_per_account(accounts):
    (_, headers), (_, other_headers) = accounts
    for _ in range(3):
        response = client.get("/v1/users/me/export", headers=headers)
        assert response.status_code == 200
        with ZipFile(io.BytesIO(response.content)) as archive:
            assert json.loads(archive.read("account.json"))["sets"] == []
    assert client.get("/v1/users/me/export", headers=headers).status_code == 429
    assert client.get("/v1/users/me/export", headers=other_headers).status_code == 200
    assert client.get("/v1/users/me/export").status_code == 401


def test_export_rechecks_revoked_identity_and_cleans_failed_files(accounts, monkeypatch, tmp_path):
    user_id, _ = accounts[0]
    allocated = []
    original = export_service.tempfile.mkdtemp

    def allocate(**kwargs):
        result = original(dir=tmp_path, **kwargs)
        allocated.append(Path(result))
        return result

    monkeypatch.setattr(export_service.tempfile, "mkdtemp", allocate)
    with SessionLocal() as db:
        db.get(User, user_id).auth_version = 1
        db.commit()
    with pytest.raises(HTTPException) as error:
        export_service.create_export(engine, user_id, 0, "KG")
    assert error.value.status_code == 401
    monkeypatch.setattr(export_service, "MAX_EXPORT_BYTES", 1)
    with pytest.raises(export_service.ExportTooLarge):
        export_service.create_export(engine, user_id, 1, "KG")
    assert all(not path.exists() for path in allocated)


def test_response_cleans_archive_on_disconnect(accounts):
    artifact = export_service.create_export(engine, accounts[0][0], 0, "KG")
    response = PrivateExportResponse(artifact)

    async def disconnected_send(message):
        raise OSError("Client disconnected")

    async def receive():
        return {"type": "http.disconnect"}

    with pytest.raises(OSError):
        asyncio.run(response({"type": "http", "method": "GET", "headers": []}, receive, disconnected_send))
    assert not artifact.directory.exists()


def test_export_streams_history_beyond_batch_size(accounts):
    user_id, _ = accounts[0]
    now = datetime.now(UTC)
    with SessionLocal() as db:
        db.add_all(session(user_id, now - timedelta(days=index)) for index in range(501))
        db.commit()
    artifact = export_service.create_export(engine, user_id, 0, "KG")
    try:
        with ZipFile(artifact.path) as archive:
            assert len(json.loads(archive.read("account.json"))["workout_sessions"]) == 501
            assert len(list(csv.DictReader(io.StringIO(archive.read("workouts.csv").decode())))) == 501
    finally:
        artifact.cleanup()


@pytest.mark.parametrize("value", ["=SUM(1,2)", " +CMD()", "-CMD()", "@CMD()", "\tText", "\rText", "\nText"])
def test_spreadsheet_formula_and_control_prefixes_are_neutralized(value):
    assert export_service.safe_csv(value) == "'" + value


@pytest.mark.parametrize("kind,weight_key,context", [
    ("progression", "targetWeightKg", "target"),
    ("personal_record", "recordWeightKg", "record"),
])
def test_export_preserves_public_coach_weight_context_without_internal_facts(accounts, kind, weight_key, context):
    user_id, _ = accounts[0]
    now = datetime.now(UTC)
    with SessionLocal() as db:
        item = CoachFeedItem(
            user_id=user_id, kind=kind, dedupe_key=str(uuid.uuid4()),
            evidence_fingerprint="PRIVATE_FINGERPRINT", title="Your training weight",
            body="See your weight recommendation", ai_status="fallback",
            protected_facts={
                "exerciseName": "Dumbbell press", weight_key: 25,
                "currentWeightKg": 22.5, "recommendation": "increase",
                "equipmentIds": ["dumbbells", {"internal": "NESTED_COACH_SECRET"}],
                "loadProfile": {"basis": "per_implement", "implement_count": 2, "internal": "NESTED_COACH_SECRET"},
                "prompt": "PRIVATE_PROMPT", "requiredText": ["PRIVATE_PROMPT"],
            },
            target_data={"type": "none"}, available_at=now,
            expires_at=now + timedelta(days=30),
        )
        db.add(item)
        db.commit()
        public = _item_out(item, ai_eligible=False).model_dump(by_alias=True)["weightData"]
    artifact = export_service.create_export(engine, user_id, 0, "LB")
    try:
        with ZipFile(artifact.path) as archive:
            raw = archive.read("account.json").decode()
            weight = json.loads(raw)["coach_items"][0]["weight_data"]
            assert weight["context"] == context
            assert weight["weightKg"] == 25
            assert weight["exerciseName"] == "Dumbbell press"
            assert weight["equipmentIds"] == ["dumbbells"]
            assert weight["loadProfile"] == {"basis": "per_implement", "implement_count": 2}
            assert all(public[key] == value for key, value in weight.items())
            if kind == "progression":
                assert weight["currentWeightKg"] == 22.5
                assert weight["recommendation"] == "increase"
            for secret in ("NESTED_COACH_SECRET", "PRIVATE_PROMPT", "PRIVATE_FINGERPRINT", "protected_facts"):
                assert secret not in raw
    finally:
        artifact.cleanup()
