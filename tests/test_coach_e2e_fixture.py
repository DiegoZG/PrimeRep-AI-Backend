from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models.onboarding_profile import OnboardingProfile
from app.models.user import User
from app.models.workout_template import WorkoutTemplate
from scripts.seed_coach_e2e_fixture import (
    CONSISTENCY_ITEM_ID,
    FIXTURE_EMAIL,
    FIXTURE_MARKER,
    FIXTURE_PASSWORD,
    FIXTURE_TIME_ZONE,
    FIXTURE_USER_ID,
    FIXTURE_WORKOUT_ID,
    OLDER_ITEM_ID,
    PROGRESSION_ITEM_ID,
    assert_local_environment,
    seed_fixture,
    teardown_fixture,
)


client = TestClient(app)


@pytest.fixture(autouse=True)
def _local_fixture_environment(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")


def _remove_reserved_users() -> None:
    with SessionLocal() as db:
        users = (
            db.query(User)
            .filter((User.id == FIXTURE_USER_ID) | (User.email == FIXTURE_EMAIL))
            .all()
        )
        for user in users:
            db.delete(user)
        db.commit()


def _add_collision_user(*, user_id: str, email: str, marked: bool = True) -> None:
    with SessionLocal() as db:
        user = User(
            id=user_id,
            email=email,
            preferred_name="Collision",
            password_hash="unused",
        )
        db.add(user)
        db.flush()
        db.add(
            OnboardingProfile(
                user_id=user.id,
                data={"_fixture": FIXTURE_MARKER} if marked else {"legitimate": True},
            )
        )
        db.commit()


def test_fixture_refuses_unset_and_production_for_direct_calls(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    with pytest.raises(RuntimeError, match="local/test"):
        assert_local_environment()
    with SessionLocal() as db:
        with pytest.raises(RuntimeError, match="local/test"):
            seed_fixture(db)
        with pytest.raises(RuntimeError, match="local/test"):
            teardown_fixture(db)

    monkeypatch.setenv("APP_ENV", "production")
    with SessionLocal() as db:
        with pytest.raises(RuntimeError, match="local/test"):
            seed_fixture(db)
        with pytest.raises(RuntimeError, match="local/test"):
            teardown_fixture(db)


@pytest.mark.parametrize(
    ("users", "message"),
    [
        ([(FIXTURE_USER_ID, "id-collision@example.com", True)], "collides"),
        ([("email-collision-user", FIXTURE_EMAIL, True)], "collides"),
        (
            [
                (FIXTURE_USER_ID, "split-id@example.com", True),
                ("split-email-user", FIXTURE_EMAIL, True),
            ],
            "collides",
        ),
        ([(FIXTURE_USER_ID, FIXTURE_EMAIL, False)], "not owned"),
    ],
)
def test_fixture_identity_collisions_abort_without_deleting(users, message):
    _remove_reserved_users()
    try:
        for user_id, email, marked in users:
            _add_collision_user(user_id=user_id, email=email, marked=marked)
        with SessionLocal() as db:
            before = {(user.id, user.email) for user in db.query(User).all()}
            with pytest.raises(RuntimeError, match=message):
                seed_fixture(db, local_date=date.today())
            db.rollback()
            after = {(user.id, user.email) for user in db.query(User).all()}
            assert after == before
            with pytest.raises(RuntimeError, match=message):
                teardown_fixture(db)
            db.rollback()
            assert {(user.id, user.email) for user in db.query(User).all()} == before
    finally:
        _remove_reserved_users()


def test_fixture_rerun_and_teardown_preserve_unrelated_user():
    _remove_reserved_users()
    unrelated_id = "coach-fixture-unrelated-user"
    unrelated_email = "coach-fixture-unrelated@example.com"
    try:
        _add_collision_user(
            user_id=unrelated_id,
            email=unrelated_email,
            marked=False,
        )
        with SessionLocal() as db:
            assert seed_fixture(db, local_date=date.today()) == FIXTURE_USER_ID
            assert seed_fixture(db, local_date=date.today()) == FIXTURE_USER_ID
            assert db.query(User).filter(User.id == unrelated_id).count() == 1
            assert teardown_fixture(db) is True
            assert teardown_fixture(db) is False
            assert db.query(User).filter(User.id == unrelated_id).count() == 1
    finally:
        with SessionLocal() as db:
            user = db.get(User, unrelated_id)
            if user is not None:
                db.delete(user)
                db.commit()
        _remove_reserved_users()


def test_fixture_validates_dependencies_before_replacing_existing_fixture():
    _remove_reserved_users()
    try:
        with SessionLocal() as db:
            seed_fixture(db, local_date=date.today())
            templates = (
                db.query(WorkoutTemplate)
                .filter(WorkoutTemplate.scope == "system")
                .all()
            )
            for template in templates:
                template.is_archived = True
            db.flush()
            with pytest.raises(RuntimeError, match="canonical seeds"):
                seed_fixture(db, local_date=date.today())
            db.rollback()
            assert db.get(User, FIXTURE_USER_ID) is not None
    finally:
        with SessionLocal() as db:
            teardown_fixture(db)


def test_seeded_coach_fixture_feed_actions_and_safe_rerun():
    local_date = date.today()
    monday = local_date - timedelta(days=local_date.weekday())
    try:
        with SessionLocal() as db:
            assert seed_fixture(db, local_date=local_date)
            assert seed_fixture(db, local_date=local_date)
            assert db.query(User).filter(User.email == FIXTURE_EMAIL).count() == 1

        login = client.post(
            "/v1/auth/login",
            json={"email": FIXTURE_EMAIL, "password": FIXTURE_PASSWORD},
        )
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        first_page = client.get(
            "/v1/coach/feed",
            headers=headers,
            params={"localDate": local_date.isoformat(), "limit": 20},
        )
        assert first_page.status_code == 200, first_page.text
        feed = first_page.json()
        assert len(feed["items"]) == 20
        assert feed["hasMore"] is True
        assert feed["nextCursor"]
        assert {
            "progression",
            "recovery",
            "missed_workout",
            "personal_record",
            "consistency",
            "program_review",
        } <= {item["kind"] for item in feed["items"]}
        assert feed["nextAction"]["target"]["workoutDayId"] == FIXTURE_WORKOUT_ID

        progression = next(
            item for item in feed["items"] if item["id"] == PROGRESSION_ITEM_ID
        )
        assert progression["kind"] == "progression"
        assert progression["detail"]
        assert progression["isAiAssisted"] is True
        assert progression["target"]["type"] == "planned_workout"
        assert progression["target"]["workoutDayId"] == FIXTURE_WORKOUT_ID

        week = client.get(
            "/v1/workouts/week",
            headers=headers,
            params={"weekStart": monday.isoformat()},
        )
        assert week.status_code == 200, week.text
        assert FIXTURE_WORKOUT_ID in {
            workout["workoutDayId"] for workout in week.json()["workouts"]
        }

        read = client.post(
            f"/v1/coach/items/{PROGRESSION_ITEM_ID}/read",
            headers=headers,
            json={"reason": "expanded"},
        )
        assert read.status_code == 200, read.text
        assert read.json()["readAt"] is not None

        second_page = client.get(
            "/v1/coach/feed",
            headers=headers,
            params={
                "localDate": local_date.isoformat(),
                "limit": 20,
                "cursor": feed["nextCursor"],
            },
        )
        assert second_page.status_code == 200, second_page.text
        assert [item["id"] for item in second_page.json()["items"]] == [OLDER_ITEM_ID]

        dismissed = client.post(
            f"/v1/coach/items/{CONSISTENCY_ITEM_ID}/dismiss",
            headers=headers,
        )
        assert dismissed.status_code == 204, dismissed.text
        assert client.get(
            f"/v1/coach/items/{CONSISTENCY_ITEM_ID}", headers=headers
        ).status_code == 404

        preferences = client.get("/v1/users/me/coach-preferences", headers=headers)
        assert preferences.status_code == 200, preferences.text
        assert preferences.json() == {
            "notificationsEnabled": True,
            "reminderTime": "08:00:00",
            "timeZone": FIXTURE_TIME_ZONE,
        }
    finally:
        with SessionLocal() as db:
            teardown_fixture(db)
