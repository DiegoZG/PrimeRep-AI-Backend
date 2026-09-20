import uuid
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.core import coach_feed_service, coach_notification_service
from app.core.training_preferences_service import update_training_preferences
from app.core.database import SessionLocal
from app.main import app
from app.models.coach import CoachFeedItem, CoachNotificationJob, CoachPreference
from app.models.coach import CoachNotificationDelivery
from app.models.push_token import PushToken
from app.models.equipment import Equipment
from app.models.user import User
from app.models.set_log import SetLog
from app.models.workout_session import WorkoutSession
from app.models.workout_session_exercise_feedback import WorkoutSessionExerciseFeedback
from app.models.workout_week_plan import WorkoutWeekPlan
from app.models.workout_template import UserProgramActivation, WorkoutTemplate
from conftest import LEGAL_ACCEPTANCE


client = TestClient(app)


def _signup(prefix: str) -> tuple[dict[str, str], str]:
    response = client.post(
        "/v1/auth/signup",
        json={
            "email": f"coach_feed_{prefix}_{uuid.uuid4().hex[:8]}@example.com",
            "password": "StrongPass123",
            "preferred_name": "Coach",
            "last_name": "Tester",
            "legalAcceptance": LEGAL_ACCEPTANCE,
        },
    )
    assert response.status_code == 201
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    user_id = client.get("/v1/users/me", headers=headers).json()["id"]
    return headers, user_id


def _item(user_id: str, index: int, *, kind: str = "consistency") -> CoachFeedItem:
    now = datetime.now(timezone.utc)
    return CoachFeedItem(
        id=str(uuid.uuid4()),
        user_id=user_id,
        kind=kind,
        priority=50,
        dedupe_key=f"manual:{index}:{uuid.uuid4()}",
        evidence_fingerprint=f"evidence-{index}",
        title=f"Item {index}",
        body="Deterministic guidance.",
        detail=None,
        ai_status="not_eligible",
        target_data={"type": "none"},
        available_at=now,
        expires_at=now + timedelta(days=30),
        created_at=now - timedelta(minutes=index),
    )


def _ai_item(user_id: str, index: int) -> CoachFeedItem:
    item = _item(user_id, index, kind="recovery")
    item.title = "Keep the next session steady"
    item.body = "Follow the next session as written and skip extra work."
    item.detail = "This is training guidance, not medical advice."
    item.protected_facts = {
        "recommendation": "keep_next_session_as_written",
        "requiredText": ["next session", "as written"],
    }
    return item


def _patch_feed_candidates(monkeypatch, candidates: list[dict]) -> None:
    by_kind = {
        kind: [candidate for candidate in candidates if candidate["kind"] == kind]
        for kind in coach_feed_service.FEED_KINDS
    }
    monkeypatch.setattr(
        coach_feed_service,
        "_progression_candidates",
        lambda *args: by_kind["progression"],
    )
    monkeypatch.setattr(
        coach_feed_service, "_recovery_candidate", lambda *args: by_kind["recovery"]
    )
    monkeypatch.setattr(
        coach_feed_service,
        "_missed_candidates",
        lambda *args: by_kind["missed_workout"],
    )
    monkeypatch.setattr(
        coach_feed_service,
        "_upcoming_notification_candidates",
        lambda *args: by_kind["upcoming_workout"],
    )
    monkeypatch.setattr(
        coach_feed_service,
        "_pr_candidates",
        lambda *args: by_kind["personal_record"],
    )
    monkeypatch.setattr(
        coach_feed_service,
        "_consistency_candidates",
        lambda *args: by_kind["consistency"],
    )
    monkeypatch.setattr(
        coach_feed_service,
        "_program_review_candidates",
        lambda *args: by_kind["program_review"],
    )


def test_feed_and_preferences_require_authentication():
    assert client.get("/v1/coach/feed", params={"localDate": date.today().isoformat()}).status_code == 401
    assert client.get("/v1/users/me/coach-preferences").status_code == 401


def test_default_preferences_are_private_and_replaceable():
    headers, _ = _signup("preferences")
    default = client.get("/v1/users/me/coach-preferences", headers=headers)
    assert default.status_code == 200
    assert default.json() == {
        "notificationsEnabled": False,
        "reminderTime": "08:00:00",
        "timeZone": "UTC",
    }

    updated = client.put(
        "/v1/users/me/coach-preferences",
        headers=headers,
        json={
            "notificationsEnabled": True,
            "reminderTime": "07:30:00",
            "timeZone": "America/New_York",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["notificationsEnabled"] is True
    assert updated.json()["timeZone"] == "America/New_York"

    invalid = client.put(
        "/v1/users/me/coach-preferences",
        headers=headers,
        json={
            "notificationsEnabled": True,
            "reminderTime": "07:30:00",
            "timeZone": "Not/AZone",
        },
    )
    assert invalid.status_code == 422


def test_empty_feed_returns_canonical_caught_up_action():
    headers, _ = _signup("empty")
    response = client.get(
        "/v1/coach/feed",
        headers=headers,
        params={"localDate": date.today().isoformat()},
    )
    assert response.status_code == 200
    assert response.json()["nextAction"]["type"] == "caught_up"
    assert response.json()["hasMore"] is False


def test_feed_cursor_read_dismiss_and_cross_account_isolation(monkeypatch):
    first_headers, first_id = _signup("first")
    second_headers, _ = _signup("second")
    monkeypatch.setattr(coach_feed_service, "reconcile_feed", lambda *args, **kwargs: 0)
    with SessionLocal() as db:
        db.add_all([_item(first_id, index) for index in range(3)])
        db.commit()

    first_page = client.get(
        "/v1/coach/feed",
        headers=first_headers,
        params={"localDate": date.today().isoformat(), "limit": 2},
    )
    assert first_page.status_code == 200
    assert len(first_page.json()["items"]) == 2
    assert first_page.json()["hasMore"] is True
    cursor = first_page.json()["nextCursor"]
    second_page = client.get(
        "/v1/coach/feed",
        headers=first_headers,
        params={"localDate": date.today().isoformat(), "limit": 2, "cursor": cursor},
    )
    assert len(second_page.json()["items"]) == 1

    item_id = first_page.json()["items"][0]["id"]
    assert client.get(f"/v1/coach/items/{item_id}", headers=second_headers).status_code == 404
    read = client.post(
        f"/v1/coach/items/{item_id}/read",
        headers=first_headers,
        json={"reason": "expanded"},
    )
    assert read.status_code == 200
    assert read.json()["readAt"] is not None
    reread = client.post(
        f"/v1/coach/items/{item_id}/read",
        headers=first_headers,
        json={"reason": "primaryAction"},
    )
    assert reread.json()["readAt"] == read.json()["readAt"]

    dismissed = client.post(f"/v1/coach/items/{item_id}/dismiss", headers=first_headers)
    assert dismissed.status_code == 204
    assert client.post(f"/v1/coach/items/{item_id}/dismiss", headers=first_headers).status_code == 204
    assert client.get(f"/v1/coach/items/{item_id}", headers=first_headers).status_code == 404


def test_reconciliation_keeps_dismissal_and_new_evidence_creates_new_item(monkeypatch):
    headers, user_id = _signup("evidence")
    candidate = coach_feed_service._candidate(
        kind="consistency",
        evidence=["milestone", 5],
        priority=40,
        title="Five workouts",
        body="Keep going.",
        detail=None,
        target={"type": "none"},
    )
    monkeypatch.setattr(coach_feed_service, "_progression_candidates", lambda *args: [])
    monkeypatch.setattr(coach_feed_service, "_recovery_candidate", lambda *args: [])
    monkeypatch.setattr(coach_feed_service, "_missed_candidates", lambda *args: [])
    monkeypatch.setattr(coach_feed_service, "_pr_candidates", lambda *args: [])
    monkeypatch.setattr(coach_feed_service, "_consistency_candidates", lambda *args: [candidate])
    monkeypatch.setattr(coach_feed_service, "_program_review_candidates", lambda *args: [])
    with SessionLocal() as db:
        coach_feed_service.reconcile_feed(db, user_id, date.today())
        row = db.query(CoachFeedItem).filter_by(user_id=user_id).one()
        row.dismissed_at = datetime.now(timezone.utc)
        db.commit()
        coach_feed_service.reconcile_feed(db, user_id, date.today())
        assert db.query(CoachFeedItem).filter_by(user_id=user_id).count() == 1
        assert db.query(CoachFeedItem).filter_by(user_id=user_id).one().dismissed_at is not None

        new_candidate = {**candidate}
        new_candidate["evidence_fingerprint"] = "different"
        new_candidate["dedupe_key"] = "consistency:different"
        monkeypatch.setattr(coach_feed_service, "_consistency_candidates", lambda *args: [new_candidate])
        coach_feed_service.reconcile_feed(db, user_id, date.today())
        assert db.query(CoachFeedItem).filter_by(user_id=user_id).count() == 2


def test_reconciliation_does_not_clear_a_newer_dirty_revision(monkeypatch):
    _, user_id = _signup("dirty_revision_race")
    with SessionLocal() as db:
        assert coach_feed_service.reconcile_after_mutation(db, user_id) == 1
        first_revision = db.get(CoachPreference, user_id).reconciliation_revision

    _patch_feed_candidates(monkeypatch, [])
    interleaved = False

    def mutate_during_reconcile(*args):
        nonlocal interleaved
        if not interleaved:
            interleaved = True
            with SessionLocal() as other:
                assert coach_feed_service.reconcile_after_mutation(other, user_id) == 1
        return []

    monkeypatch.setattr(
        coach_feed_service, "_progression_candidates", mutate_during_reconcile
    )
    with SessionLocal() as first:
        coach_feed_service.reconcile_feed(first, user_id, date.today())

    with SessionLocal() as db:
        preference = db.get(CoachPreference, user_id)
        assert preference.reconciliation_revision == first_revision + 1
        assert preference.reconciled_revision < preference.reconciliation_revision
        assert preference.reconciliation_requested_at is not None


def test_reconciliation_does_not_restore_item_invalidated_after_it_started(monkeypatch):
    _, user_id = _signup("restore_race")
    exercise_id = str(uuid.uuid4())
    candidate = coach_feed_service._candidate(
        kind="progression",
        evidence=["stale-candidate", exercise_id],
        evidence_data={"exerciseIds": [exercise_id]},
        priority=70,
        title="Current guidance",
        body="This candidate was computed before a newer mutation.",
        detail=None,
        target={"type": "none"},
    )
    _patch_feed_candidates(monkeypatch, [candidate])
    with SessionLocal() as db:
        coach_feed_service.reconcile_feed(db, user_id, date.today())
        item_id = db.query(CoachFeedItem).filter_by(user_id=user_id).one().id
        claimed_revision = db.get(CoachPreference, user_id).reconciliation_revision

    interleaved = False

    def invalidate_after_candidate_computation(*args):
        nonlocal interleaved
        if not interleaved:
            interleaved = True
            with SessionLocal() as other:
                assert coach_feed_service.reconcile_after_mutation(
                    other, user_id, exercise_ids=[exercise_id]
                ) == 1
        return []

    monkeypatch.setattr(
        coach_feed_service,
        "_program_review_candidates",
        invalidate_after_candidate_computation,
    )
    with SessionLocal() as first:
        coach_feed_service.reconcile_feed(first, user_id, date.today())

    with SessionLocal() as db:
        item = db.get(CoachFeedItem, item_id)
        preference = db.get(CoachPreference, user_id)
        assert item.expires_at <= datetime.now(timezone.utc)
        assert item.invalidation_reason == "source_mutated"
        assert item.invalidated_at is not None
        assert preference.reconciliation_revision == claimed_revision + 1
        assert preference.reconciled_revision < preference.reconciliation_revision
        assert preference.reconciliation_requested_at is not None


def test_stale_reconciliation_cannot_expire_newer_projection(monkeypatch):
    _, user_id = _signup("stale_writer")
    old_exercise_id = str(uuid.uuid4())
    new_exercise_id = str(uuid.uuid4())
    old_candidate = coach_feed_service._candidate(
        kind="progression",
        evidence=["old", old_exercise_id],
        evidence_data={"exerciseIds": [old_exercise_id]},
        priority=70,
        title="Old candidate",
        body="This projection is superseded.",
        detail=None,
        target={"type": "none"},
    )
    new_candidate = coach_feed_service._candidate(
        kind="progression",
        evidence=["new", new_exercise_id],
        evidence_data={"exerciseIds": [new_exercise_id]},
        priority=70,
        title="New candidate",
        body="This is the canonical newer projection.",
        detail=None,
        target={"type": "none"},
    )
    mode = "old"
    interleaved = False

    monkeypatch.setattr(
        coach_feed_service,
        "_progression_candidates",
        lambda *args: [old_candidate if mode == "old" else new_candidate],
    )
    for name in (
        "_recovery_candidate",
        "_missed_candidates",
        "_upcoming_notification_candidates",
        "_pr_candidates",
        "_consistency_candidates",
    ):
        monkeypatch.setattr(coach_feed_service, name, lambda *args: [])

    with SessionLocal() as db:
        coach_feed_service.reconcile_feed(db, user_id, date.today())
        old_item_id = db.query(CoachFeedItem).filter_by(user_id=user_id).one().id
        initial_revision = db.get(CoachPreference, user_id).reconciliation_revision

    def newer_reconciliation_during_old_candidate_pass(*args):
        nonlocal interleaved, mode
        if interleaved:
            return []
        interleaved = True
        mode = "new"
        with SessionLocal() as newer:
            assert coach_feed_service.reconcile_after_mutation(
                newer, user_id, exercise_ids=[old_exercise_id]
            ) == 1
            assert coach_feed_service.reconcile_feed(
                newer, user_id, date.today(), source="test_newer_pass"
            ) == 1
        return []

    monkeypatch.setattr(
        coach_feed_service,
        "_program_review_candidates",
        newer_reconciliation_during_old_candidate_pass,
    )
    mode = "old"
    with SessionLocal() as stale:
        assert coach_feed_service.reconcile_feed(
            stale, user_id, date.today(), source="test_stale_pass"
        ) == 0

    with SessionLocal() as db:
        old_item = db.get(CoachFeedItem, old_item_id)
        new_item = db.query(CoachFeedItem).filter_by(
            user_id=user_id, dedupe_key=new_candidate["dedupe_key"]
        ).one()
        preference = db.get(CoachPreference, user_id)
        assert old_item.expires_at <= datetime.now(timezone.utc)
        assert new_item.expires_at > datetime.now(timezone.utc)
        assert new_item.title == "New candidate"
        assert preference.reconciliation_revision == initial_revision + 1
        assert preference.reconciled_revision == preference.reconciliation_revision
        assert preference.reconciliation_requested_at is None


def test_simultaneous_mutation_and_reconciliation_use_consistent_lock_order(monkeypatch):
    _, user_id = _signup("lock_order")
    exercise_id = str(uuid.uuid4())
    candidate = coach_feed_service._candidate(
        kind="progression",
        evidence=["lock-order", exercise_id],
        evidence_data={"exerciseIds": [exercise_id]},
        priority=70,
        title="Current guidance",
        body="This item must remain invalidated by the newer mutation.",
        detail=None,
        target={"type": "none"},
    )
    _patch_feed_candidates(monkeypatch, [candidate])
    with SessionLocal() as db:
        coach_feed_service.reconcile_feed(db, user_id, date.today())
        item_id = db.query(CoachFeedItem).filter_by(user_id=user_id).one().id

    barrier = threading.Barrier(2)

    def reconcile():
        with SessionLocal() as db:
            barrier.wait(timeout=5)
            return coach_feed_service.reconcile_feed(db, user_id, date.today())

    def mutate():
        with SessionLocal() as db:
            barrier.wait(timeout=5)
            return coach_feed_service.reconcile_after_mutation(
                db, user_id, exercise_ids=[exercise_id]
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        reconcile_future = pool.submit(reconcile)
        mutation_future = pool.submit(mutate)
        assert reconcile_future.result(timeout=10) in {0, 1}
        assert mutation_future.result(timeout=10) == 1

    with SessionLocal() as db:
        item = db.get(CoachFeedItem, item_id)
        preference = db.get(CoachPreference, user_id)
        assert item.expires_at <= datetime.now(timezone.utc)
        assert item.invalidation_reason == "source_mutated"
        assert preference.reconciliation_requested_at is not None
        assert preference.reconciliation_revision > preference.reconciled_revision


def test_ai_enrichment_preserves_target_and_falls_back(monkeypatch):
    _, user_id = _signup("ai")
    with SessionLocal() as db:
        user = db.get(User, user_id)
        user.subscription_tier = "premium"
        user.coach_insights_enabled = True
        row = _ai_item(user_id, 1)
        row.ai_status = "pending"
        db.add(row)
        db.commit()
        original_target = dict(row.target_data)

        monkeypatch.setattr(coach_feed_service.settings, "ANTHROPIC_API_KEY", "test")
        monkeypatch.setattr(
            coach_feed_service,
            "_call_coach_feed_ai",
            lambda facts: {
                "items": [
                    {
                        "id": facts[0]["id"],
                        "variant": "supportive",
                    }
                ]
            },
        )
        assert coach_feed_service.enrich_pending_items(db, user_id) == 1
        db.refresh(row)
        assert row.ai_status == "enriched"
        assert row.target_data == original_target


def test_ai_copy_hides_on_eligibility_loss_and_requeues_on_opt_in(monkeypatch):
    headers, user_id = _signup("ai_eligibility")
    with SessionLocal() as db:
        row = _ai_item(user_id, 1)
        row.ai_status = "enriched"
        row.ai_title = "AI title"
        row.ai_body = "AI body"
        db.add(row)
        db.commit()
        item_id = row.id

    free_read = client.get(f"/v1/coach/items/{item_id}", headers=headers)
    assert free_read.status_code == 200
    assert free_read.json()["isAiAssisted"] is False
    assert free_read.json()["title"] != "AI title"

    with SessionLocal() as db:
        coach_feed_service.sync_ai_eligibility(db, user_id)
        row = db.get(CoachFeedItem, item_id)
        assert row.ai_status == "not_eligible"
        assert row.ai_title is None and row.ai_body is None
        user = db.get(User, user_id)
        user.subscription_tier = "premium"
        user.coach_insights_enabled = True
        db.commit()
        assert coach_feed_service.sync_ai_eligibility(db, user_id) is True
        db.refresh(row)
        assert row.ai_status == "pending"


def test_ai_malformed_then_timeout_uses_deterministic_fallback(monkeypatch):
    _, user_id = _signup("ai_fallback")
    with SessionLocal() as db:
        user = db.get(User, user_id)
        user.subscription_tier = "premium"
        user.coach_insights_enabled = True
        row = _ai_item(user_id, 1)
        row.ai_status = "pending"
        db.add(row)
        db.commit()
        monkeypatch.setattr(coach_feed_service.settings, "ANTHROPIC_API_KEY", "test")
        monkeypatch.setattr(
            coach_feed_service,
            "_call_coach_feed_ai",
            lambda facts: {
                "items": [
                    {
                        "id": facts[0]["id"],
                        "variant": "focused",
                        "body": "Lower the load and shorten the workout if you feel pain.",
                    }
                ]
            },
        )
        assert coach_feed_service.enrich_pending_items(db, user_id) == 1
        db.refresh(row)
        assert row.ai_status == "pending"
        assert row.ai_body is None
        monkeypatch.setattr(
            coach_feed_service,
            "_call_coach_feed_ai",
            lambda facts: (_ for _ in ()).throw(TimeoutError()),
        )
        assert coach_feed_service.enrich_pending_items(db, user_id) == 1
        db.refresh(row)
        assert row.ai_status == "fallback"
        output = coach_feed_service._item_out(row, ai_eligible=True)
        assert output.is_ai_assisted is False
        assert output.title == "Keep the next session steady"


def test_ai_fallback_is_terminal_for_immutable_evidence(monkeypatch):
    _, user_id = _signup("ai_terminal_fallback")
    calls = []
    with SessionLocal() as db:
        user = db.get(User, user_id)
        user.subscription_tier = "premium"
        user.coach_insights_enabled = True
        row = _ai_item(user_id, 1)
        row.ai_status = "fallback"
        row.ai_attempt_count = coach_feed_service.AI_ATTEMPT_LIMIT
        db.add(row)
        db.commit()

        monkeypatch.setattr(
            coach_feed_service,
            "_call_coach_feed_ai",
            lambda payload: calls.append(payload),
        )
        for _ in range(3):
            coach_feed_service._sync_ai_eligibility_rows([row], True)
            db.commit()
            assert coach_feed_service.enrich_pending_items(db, user_id) == 0
        db.refresh(row)
        assert row.ai_status == "fallback"
        assert row.ai_attempt_count == coach_feed_service.AI_ATTEMPT_LIMIT
        assert calls == []


def test_ai_payload_excludes_exact_training_values(monkeypatch):
    _, user_id = _signup("ai_privacy")
    captured = []
    with SessionLocal() as db:
        user = db.get(User, user_id)
        user.subscription_tier = "premium"
        user.coach_insights_enabled = True
        safe = _ai_item(user_id, 1)
        safe.ai_status = "pending"
        sensitive = _item(user_id, 2, kind="progression")
        sensitive.ai_status = "pending"
        sensitive.title = "Move up to 123.45 kg"
        sensitive.body = "Complete 8 reps with 1 RIR."
        sensitive.protected_facts = {
            "targetWeightKg": 123.45,
            "reps": 8,
            "rir": 1,
        }
        db.add_all([safe, sensitive])
        db.commit()
        monkeypatch.setattr(coach_feed_service.settings, "ANTHROPIC_API_KEY", "test")

        def rewrite(facts):
            captured.extend(facts)
            return {
                "items": [
                    {
                        "id": facts[0]["id"],
                        "variant": "focused",
                    }
                ]
            }

        monkeypatch.setattr(coach_feed_service, "_call_coach_feed_ai", rewrite)
        assert coach_feed_service.enrich_pending_items(db, user_id) == 2
        serialized = str(captured)
        assert {row["kind"] for row in captured} == {"recovery", "progression"}
        assert all(
            set(row) == {"id", "kind", "availableVariants"}
            for row in captured
        )
        assert "123.45" not in serialized
        assert "8 reps" not in serialized
        assert "RIR" not in serialized


def test_ai_contract_rejects_provider_authored_coaching_copy():
    with pytest.raises(ValueError):
        coach_feed_service.CoachAISelectionBatch.model_validate(
            {
                "items": [
                    {
                        "id": "unsafe",
                        "variant": "focused",
                        "body": "Lower the load and shorten the workout if you feel pain.",
                    }
                ]
            }
        )


def test_ai_result_is_discarded_when_user_opts_out_in_flight(monkeypatch):
    _, user_id = _signup("ai_race")
    with SessionLocal() as db:
        user = db.get(User, user_id)
        user.subscription_tier = "premium"
        user.coach_insights_enabled = True
        row = _ai_item(user_id, 1)
        row.ai_status = "pending"
        db.add(row)
        db.commit()
        monkeypatch.setattr(coach_feed_service.settings, "ANTHROPIC_API_KEY", "test")

        def opt_out_then_return(facts):
            with SessionLocal() as other:
                other_user = other.get(User, user_id)
                other_user.coach_insights_enabled = False
                other.commit()
                coach_feed_service.sync_ai_eligibility(other, user_id)
            return {
                "items": [
                    {
                        "id": facts[0]["id"],
                        "variant": "direct",
                    }
                ]
            }

        monkeypatch.setattr(
            coach_feed_service, "_call_coach_feed_ai", opt_out_then_return
        )
        assert coach_feed_service.enrich_pending_items(db, user_id) == 1
        db.expire_all()
        row = db.get(CoachFeedItem, row.id)
        assert row.ai_status == "not_eligible"
        assert row.ai_title is None
        assert row.ai_claim_token is None


def test_notification_daily_cap_and_opt_out_cancels_pending():
    headers, user_id = _signup("daily")
    with SessionLocal() as db:
        preference = CoachPreference(
            user_id=user_id,
            notifications_enabled=True,
            reminder_time=time(8, 0),
            time_zone="UTC",
        )
        first = _item(user_id, 1, kind="missed_workout")
        second = _item(user_id, 2, kind="missed_workout")
        db.add_all([preference, first, second])
        db.commit()
        for item in (first, second):
            coach_notification_service._insert_job(
                db,
                user_id=user_id,
                item=item,
                job_type="scheduled_daily",
                scheduled_at=datetime.now(timezone.utc),
                local_delivery_date=date.today(),
            )
        db.commit()
        assert db.query(CoachNotificationJob).filter_by(user_id=user_id).count() == 1

    response = client.put(
        "/v1/users/me/coach-preferences",
        headers=headers,
        json={
            "notificationsEnabled": False,
            "reminderTime": "08:00:00",
            "timeZone": "UTC",
        },
    )
    assert response.status_code == 200
    with SessionLocal() as db:
        assert db.query(CoachNotificationJob).filter_by(user_id=user_id).one().status == "cancelled"


def test_cancelled_valid_notification_job_can_be_reactivated():
    _, user_id = _signup("reactivate_job")
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        item = _item(user_id, 1, kind="program_review")
        db.add(item)
        db.flush()
        job = CoachNotificationJob(
            id=str(uuid.uuid4()),
            user_id=user_id,
            feed_item_id=item.id,
            job_type="equipment_review",
            scheduled_at=now - timedelta(minutes=1),
            local_delivery_date=now.date(),
            status="cancelled",
            attempts=2,
        )
        db.add(job)
        db.flush()
        failed = CoachNotificationDelivery(
            id=str(uuid.uuid4()),
            job_id=job.id,
            push_token_hash="failed-history",
            status="failed",
            attempts=5,
            error_code="terminal",
        )
        ticketed = CoachNotificationDelivery(
            id=str(uuid.uuid4()),
            job_id=job.id,
            push_token_hash="ticket-history",
            status="ticketed",
            attempts=1,
            expo_ticket_id="ticket-history-id",
            ticketed_at=now - timedelta(minutes=20),
            next_receipt_at=now,
        )
        retry = CoachNotificationDelivery(
            id=str(uuid.uuid4()),
            job_id=job.id,
            push_token_hash="retry-history",
            status="retry",
            attempts=2,
            retry_at=now + timedelta(hours=1),
        )
        db.add_all([failed, ticketed, retry])
        db.commit()
        assert coach_notification_service._insert_job(
            db,
            user_id=user_id,
            item=item,
            job_type="equipment_review",
            scheduled_at=now,
            local_delivery_date=now.date(),
        ) == 1
        db.commit()
        db.refresh(job)
        assert job.status == "pending"
        assert job.attempts == 0
        assert job.scheduled_at == now
        rows = {
            row.push_token_hash: row
            for row in db.query(CoachNotificationDelivery).filter_by(job_id=job.id)
        }
        assert rows["failed-history"].status == "failed"
        assert rows["failed-history"].error_code == "terminal"
        assert rows["ticket-history"].status == "ticketed"
        assert rows["ticket-history"].expo_ticket_id == "ticket-history-id"
        assert rows["retry-history"].status == "retry"
        assert rows["retry-history"].attempts == 2
        assert rows["retry-history"].retry_at == now


def test_upcoming_workout_uses_notification_only_item_and_coach_item_id():
    headers, user_id = _signup("upcoming_push")
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    now = datetime.combine(today, time(12, 0), tzinfo=timezone.utc)
    with SessionLocal() as db:
        db.add(
            WorkoutWeekPlan(
                id=str(uuid.uuid4()),
                user_id=user_id,
                week_start_date=monday,
                days_per_week=1,
                plan_json={
                    "workouts": [
                        {
                            "workoutDayId": "today-ready",
                            "slotIndex": 0,
                            "date": today.isoformat(),
                            "title": "Upper A",
                            "durationMinutes": 35,
                            "exerciseBlocks": [],
                        }
                    ]
                },
            )
        )
        preference = CoachPreference(
            user_id=user_id,
            notifications_enabled=True,
            reminder_time=time(8, 0),
            time_zone="UTC",
        )
        db.add(preference)
        db.commit()
        assert coach_notification_service._enqueue_for_preference(db, preference, now) == 1
        db.commit()
        item = db.query(CoachFeedItem).filter_by(
            user_id=user_id, kind="upcoming_workout"
        ).one()
        job = db.query(CoachNotificationJob).filter_by(feed_item_id=item.id).one()
        assert job.job_type == "scheduled_daily"
        item_id = item.id

    feed = client.get(
        "/v1/coach/feed",
        headers=headers,
        params={"localDate": today.isoformat()},
    )
    assert all(row["kind"] != "upcoming_workout" for row in feed.json()["items"])
    detail = client.get(f"/v1/coach/items/{item_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["kind"] == "upcoming_workout"
    assert detail.json()["target"]["workoutDayId"] == "today-ready"


def test_local_reminder_handles_timezone_boundary():
    result = coach_feed_service.local_reminder_utc(
        date(2026, 9, 19), time(8, 0), "America/New_York"
    )
    assert result == datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


def test_worker_sends_generic_allowlisted_payload_and_records_ticket(monkeypatch):
    _, user_id = _signup("worker")
    captured = []
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.add(
            CoachPreference(
                user_id=user_id,
                notifications_enabled=True,
                reminder_time=time(8, 0),
                time_zone="UTC",
            )
        )
        item = _item(user_id, 1, kind="program_review")
        item_id = item.id
        db.add(item)
        db.flush()
        job_id = str(uuid.uuid4())
        db.add(
            CoachNotificationJob(
                id=job_id,
                user_id=user_id,
                feed_item_id=item.id,
                job_type="equipment_review",
                scheduled_at=now,
                local_delivery_date=now.date(),
            )
        )
        db.add(
            PushToken(
                token=f"ExponentPushToken[{uuid.uuid4().hex}]",
                user_id=user_id,
                platform="ios",
            )
        )
        db.commit()

        def fake_post(url, payload):
            captured.append((url, payload))
            return {"data": [{"status": "ok", "id": "ticket-1"}]}

        monkeypatch.setattr(coach_notification_service, "_post_json", fake_post)
        assert coach_notification_service.send_due_jobs(db, now) >= 1
        delivery = db.query(CoachNotificationDelivery).filter_by(job_id=job_id).one()
        assert delivery.status == "ticketed"
        assert "ExponentPushToken" not in delivery.push_token_hash

    payload = next(
        message
        for _, batch in captured
        for message in batch
        if message["data"].get("coachItemId") == item_id
    )
    assert payload["body"] == "Your program needs an equipment review."
    assert payload["data"] == {
        "version": 1,
        "type": "coach_item",
        "coachItemId": item_id,
    }
    assert "title" not in payload["data"] and "body" not in payload["data"]


def test_worker_retries_transient_failures_with_bounded_backoff(monkeypatch):
    _, user_id = _signup("worker_retry")
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.add(
            CoachPreference(
                user_id=user_id,
                notifications_enabled=True,
                reminder_time=time(8, 0),
                time_zone="UTC",
            )
        )
        item = _item(user_id, 1, kind="program_review")
        db.add(item)
        db.flush()
        job = CoachNotificationJob(
            id=str(uuid.uuid4()),
            user_id=user_id,
            feed_item_id=item.id,
            job_type="equipment_review",
            scheduled_at=now,
            local_delivery_date=now.date(),
        )
        db.add_all(
            [
                job,
                PushToken(
                    token=f"ExponentPushToken[{uuid.uuid4().hex}]",
                    user_id=user_id,
                    platform="ios",
                ),
            ]
        )
        db.commit()

        def fail(*args):
            raise coach_notification_service.ExpoRequestError("network", True)

        monkeypatch.setattr(coach_notification_service, "_post_json", fail)
        assert coach_notification_service.send_due_jobs(db, now) == 0
        db.refresh(job)
        assert job.status == "retry"
        assert job.attempts == 1
        assert job.retry_at > now


def test_worker_retries_only_due_devices_with_independent_attempt_limits(monkeypatch):
    _, user_id = _signup("worker_device_retry")
    now = datetime.now(timezone.utc)
    due_token = f"ExponentPushToken[due-{uuid.uuid4().hex}]"
    future_token = f"ExponentPushToken[future-{uuid.uuid4().hex}]"
    sent_to = []
    with SessionLocal() as db:
        db.query(CoachNotificationJob).filter(
            CoachNotificationJob.status.in_(["pending", "retry", "processing"])
        ).update(
            {"scheduled_at": now + timedelta(days=1)}, synchronize_session=False
        )
        item = _item(user_id, 1, kind="program_review")
        db.add_all(
            [
                CoachPreference(
                    user_id=user_id,
                    notifications_enabled=True,
                    reminder_time=time(8),
                    time_zone="UTC",
                ),
                item,
                PushToken(token=due_token, user_id=user_id, platform="ios"),
                PushToken(token=future_token, user_id=user_id, platform="ios"),
            ]
        )
        db.flush()
        job = CoachNotificationJob(
            id=str(uuid.uuid4()),
            user_id=user_id,
            feed_item_id=item.id,
            job_type="equipment_review",
            scheduled_at=now,
            local_delivery_date=now.date(),
            status="retry",
            attempts=9,
            retry_at=now,
        )
        db.add(job)
        db.flush()
        due_delivery = CoachNotificationDelivery(
            id=str(uuid.uuid4()),
            job_id=job.id,
            push_token_hash=coach_notification_service._token_hash(due_token),
            status="retry",
            attempts=4,
            retry_at=now,
        )
        future_delivery = CoachNotificationDelivery(
            id=str(uuid.uuid4()),
            job_id=job.id,
            push_token_hash=coach_notification_service._token_hash(future_token),
            status="retry",
            attempts=1,
            retry_at=now + timedelta(hours=2),
        )
        db.add_all([due_delivery, future_delivery])
        db.commit()

        def transient_ticket(url, payload):
            sent_to.extend(message["to"] for message in payload)
            return {
                "data": [
                    {
                        "status": "error",
                        "details": {"error": "MessageRateExceeded"},
                    }
                    for _ in payload
                ]
            }

        monkeypatch.setattr(
            coach_notification_service, "_post_json", transient_ticket
        )
        assert coach_notification_service.send_due_jobs(db, now) == 0
        db.refresh(job)
        db.refresh(due_delivery)
        db.refresh(future_delivery)
        assert sent_to == [due_token]
        assert due_delivery.status == "failed"
        assert due_delivery.attempts == 5
        assert future_delivery.status == "retry"
        assert future_delivery.attempts == 1
        assert future_delivery.retry_at == now + timedelta(hours=2)
        assert job.status == "retry"
        assert job.retry_at == future_delivery.retry_at


def test_worker_chunks_all_devices_and_records_every_outcome(monkeypatch):
    _, user_id = _signup("worker_batches")
    now = datetime.now(timezone.utc)
    batch_sizes = []
    with SessionLocal() as db:
        db.add(
            CoachPreference(
                user_id=user_id,
                notifications_enabled=True,
                reminder_time=time(8),
                time_zone="UTC",
            )
        )
        item = _item(user_id, 1, kind="program_review")
        db.add(item)
        db.flush()
        job = CoachNotificationJob(
            id=str(uuid.uuid4()),
            user_id=user_id,
            feed_item_id=item.id,
            job_type="equipment_review",
            scheduled_at=now,
            local_delivery_date=now.date(),
        )
        db.add(job)
        db.add_all(
            [
                PushToken(
                    token=f"ExponentPushToken[batch-{uuid.uuid4().hex}]",
                    user_id=user_id,
                    platform="ios",
                )
                for _ in range(201)
            ]
        )
        db.commit()

        def succeed(url, payload):
            batch_sizes.append(len(payload))
            return {
                "data": [
                    {"status": "ok", "id": f"ticket-{uuid.uuid4().hex}"}
                    for _ in payload
                ]
            }

        monkeypatch.setattr(coach_notification_service, "_post_json", succeed)
        assert coach_notification_service.send_due_jobs(db, now) >= 1
        assert batch_sizes == [100, 100, 1]
        assert db.query(CoachNotificationDelivery).filter_by(job_id=job.id).count() == 201
        db.refresh(job)
        assert job.status == "sent"


def test_worker_partial_ticket_response_retries_every_device(monkeypatch):
    _, user_id = _signup("worker_partial")
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.add(
            CoachPreference(
                user_id=user_id,
                notifications_enabled=True,
                reminder_time=time(8),
                time_zone="UTC",
            )
        )
        item = _item(user_id, 1, kind="program_review")
        db.add(item)
        db.flush()
        job = CoachNotificationJob(
            id=str(uuid.uuid4()),
            user_id=user_id,
            feed_item_id=item.id,
            job_type="equipment_review",
            scheduled_at=now,
            local_delivery_date=now.date(),
        )
        db.add(job)
        db.add_all(
            [
                PushToken(
                    token=f"ExponentPushToken[partial-{uuid.uuid4().hex}]",
                    user_id=user_id,
                    platform="ios",
                )
                for _ in range(2)
            ]
        )
        db.commit()
        monkeypatch.setattr(
            coach_notification_service,
            "_post_json",
            lambda url, payload: {"data": [{"status": "ok", "id": "only-one"}]},
        )
        coach_notification_service.send_due_jobs(db, now)
        deliveries = db.query(CoachNotificationDelivery).filter_by(job_id=job.id).all()
        assert len(deliveries) == 2
        assert {row.status for row in deliveries} == {"retry"}
        db.refresh(job)
        assert job.status == "retry"


def test_worker_recovers_stale_locks_but_not_live_locks():
    _, user_id = _signup("worker_locks")
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        items = [_item(user_id, index, kind="program_review") for index in (1, 2)]
        db.add_all(items)
        db.flush()
        stale = CoachNotificationJob(
            id=str(uuid.uuid4()), user_id=user_id, feed_item_id=items[0].id,
            job_type="equipment_review", scheduled_at=now, local_delivery_date=now.date(),
            status="processing", locked_at=now - timedelta(minutes=11),
        )
        live = CoachNotificationJob(
            id=str(uuid.uuid4()), user_id=user_id, feed_item_id=items[1].id,
            job_type="equipment_review", scheduled_at=now, local_delivery_date=now.date(),
            status="processing", locked_at=now - timedelta(minutes=1),
        )
        db.add_all([stale, live])
        db.commit()
        claimed = coach_notification_service.lock_due_jobs(db, now)
        claimed_ids = {row.id for row in claimed}
        assert stale.id in claimed_ids
        assert live.id not in claimed_ids


def test_receipt_device_not_registered_removes_token(monkeypatch):
    _, user_id = _signup("receipt_stale")
    now = datetime.now(timezone.utc)
    token_value = f"ExponentPushToken[stale-{uuid.uuid4().hex}]"
    with SessionLocal() as db:
        item = _item(user_id, 1, kind="program_review")
        db.add(item)
        db.flush()
        job = CoachNotificationJob(
            id=str(uuid.uuid4()), user_id=user_id, feed_item_id=item.id,
            job_type="equipment_review", scheduled_at=now, local_delivery_date=now.date(),
            status="sent",
        )
        db.add(job)
        db.flush()
        db.add(PushToken(token=token_value, user_id=user_id, platform="ios"))
        db.add(
            CoachNotificationDelivery(
                id=str(uuid.uuid4()), job_id=job.id,
                push_token_hash=coach_notification_service._token_hash(token_value),
                expo_ticket_id="stale-ticket", status="ticketed", attempts=1,
                ticketed_at=now - timedelta(minutes=20),
                next_receipt_at=now,
                created_at=now - timedelta(minutes=20),
            )
        )
        db.commit()
        monkeypatch.setattr(
            coach_notification_service,
            "_post_json",
            lambda url, payload: {
                "data": {
                    "stale-ticket": {
                        "status": "error",
                        "details": {"error": "DeviceNotRegistered"},
                    }
                }
            },
        )
        assert coach_notification_service.poll_receipts(db, now) >= 1
        assert db.get(PushToken, token_value) is None


def test_missing_receipt_is_deferred_with_backoff(monkeypatch):
    _, user_id = _signup("receipt_missing")
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.query(CoachNotificationDelivery).filter(
            CoachNotificationDelivery.status == "ticketed"
        ).update(
            {"next_receipt_at": now + timedelta(days=1)},
            synchronize_session=False,
        )
        item = _item(user_id, 1, kind="program_review")
        db.add(item)
        db.flush()
        job = CoachNotificationJob(
            id=str(uuid.uuid4()),
            user_id=user_id,
            feed_item_id=item.id,
            job_type="equipment_review",
            scheduled_at=now,
            local_delivery_date=now.date(),
            status="sent",
        )
        db.add(job)
        db.flush()
        delivery = CoachNotificationDelivery(
            id=str(uuid.uuid4()),
            job_id=job.id,
            push_token_hash="opaque-hash",
            expo_ticket_id="missing-ticket",
            status="ticketed",
            attempts=1,
            ticketed_at=now - timedelta(minutes=15),
            next_receipt_at=now,
        )
        db.add(delivery)
        db.commit()
        monkeypatch.setattr(
            coach_notification_service,
            "_post_json",
            lambda url, payload: {"data": {}},
        )
        assert coach_notification_service.poll_receipts(db, now) == 0
        db.refresh(delivery)
        assert delivery.status == "ticketed"
        assert delivery.receipt_attempts == 1
        assert delivery.next_receipt_at > now
        assert delivery.error_code == "receipt_missing"


def test_receipt_claim_skips_rows_locked_by_another_worker(monkeypatch):
    _, user_id = _signup("receipt_lock")
    now = datetime.now(timezone.utc)
    with SessionLocal() as first:
        first.query(CoachNotificationDelivery).filter(
            CoachNotificationDelivery.status == "ticketed"
        ).update(
            {"next_receipt_at": now + timedelta(days=1)},
            synchronize_session=False,
        )
        item = _item(user_id, 1, kind="program_review")
        first.add(item)
        first.flush()
        job = CoachNotificationJob(
            id=str(uuid.uuid4()),
            user_id=user_id,
            feed_item_id=item.id,
            job_type="equipment_review",
            scheduled_at=now,
            local_delivery_date=now.date(),
            status="sent",
        )
        first.add(job)
        first.flush()
        delivery = CoachNotificationDelivery(
            id=str(uuid.uuid4()),
            job_id=job.id,
            push_token_hash="locked-hash",
            expo_ticket_id="locked-ticket",
            status="ticketed",
            attempts=1,
            ticketed_at=now - timedelta(minutes=15),
            next_receipt_at=now,
        )
        first.add(delivery)
        first.commit()
        first.query(CoachNotificationDelivery).filter_by(id=delivery.id).with_for_update().one()
        monkeypatch.setattr(
            coach_notification_service,
            "_post_json",
            lambda *args: (_ for _ in ()).throw(AssertionError("locked row sent")),
        )
        with SessionLocal() as second:
            assert coach_notification_service.poll_receipts(second, now) == 0
        first.rollback()


def test_worker_limits_notification_users_and_ai_items(monkeypatch):
    now = datetime.now(timezone.utc)
    user_ids = [_signup(f"bounded_{index}")[1] for index in range(3)]
    with SessionLocal() as db:
        db.query(CoachPreference).update(
            {
                "notifications_enabled": False,
                "reconciliation_requested_at": None,
                "last_reconciled_at": now,
            },
            synchronize_session=False,
        )
        for user_id in user_ids:
            db.add(
                CoachPreference(
                    user_id=user_id,
                    notifications_enabled=True,
                    reminder_time=time(8),
                    time_zone="UTC",
                    reconciliation_requested_at=now,
                )
            )
        db.commit()
        monkeypatch.setattr(
            coach_notification_service,
            "_enqueue_for_preference",
            lambda *args: 0,
        )
        assert coach_notification_service.enqueue_due_jobs(db, now, limit=2) == 0
        refreshed = db.query(CoachPreference).filter(
            CoachPreference.user_id.in_(user_ids),
            CoachPreference.last_reconciled_at == now,
        ).count()
        assert refreshed == 2

    ai_user_id = user_ids[0]
    with SessionLocal() as db:
        db.query(User).filter(User.id != ai_user_id).update(
            {"coach_insights_enabled": False}, synchronize_session=False
        )
        user = db.get(User, ai_user_id)
        user.subscription_tier = "premium"
        user.coach_insights_enabled = True
        rows = [_ai_item(ai_user_id, index) for index in range(12)]
        for row in rows:
            row.ai_status = "pending"
        db.add_all(rows)
        db.commit()
        monkeypatch.setattr(coach_feed_service.settings, "ANTHROPIC_API_KEY", "test")
        monkeypatch.setattr(
            coach_feed_service,
            "_call_coach_feed_ai",
            lambda facts: {
                "items": [
                    {
                        "id": fact["id"],
                        "variant": "supportive",
                    }
                    for fact in facts
                ]
            },
        )
        assert coach_notification_service.process_ai_users(db, limit=5) == 5
        assert db.query(CoachFeedItem).filter_by(
            user_id=ai_user_id, ai_status="enriched"
        ).count() == 5


def test_worker_fairly_interleaves_dirty_and_notification_users(monkeypatch):
    now = datetime.now(timezone.utc)
    processed = []
    with SessionLocal() as db:
        db.query(CoachPreference).delete(synchronize_session=False)
        users = [
            User(
                id=str(uuid.uuid4()),
                email=f"coach_fair_{uuid.uuid4().hex}@example.com",
                preferred_name="Coach",
                password_hash="not-used",
            )
            for _ in range(12)
        ]
        db.add_all(users)
        db.flush()
        dirty_users = [user.id for user in users[:6]]
        notification_users = [user.id for user in users[6:]]
        db.add_all(
            [
                CoachPreference(
                    user_id=user_id,
                    notifications_enabled=False,
                    reminder_time=time(8),
                    time_zone="UTC",
                    reconciliation_requested_at=now - timedelta(minutes=10 - index),
                    reconciliation_revision=1,
                )
                for index, user_id in enumerate(dirty_users)
            ]
            + [
                CoachPreference(
                    user_id=user_id,
                    notifications_enabled=True,
                    reminder_time=time(8),
                    time_zone="UTC",
                    last_reconciled_at=now - timedelta(days=2 - index / 10),
                )
                for index, user_id in enumerate(notification_users)
            ]
        )
        db.commit()
        monkeypatch.setattr(
            coach_notification_service,
            "_enqueue_for_preference",
            lambda db, preference, now: processed.append(preference.user_id) or 0,
        )
        coach_notification_service.enqueue_due_jobs(db, now, limit=4)

    assert len(processed) == 4
    assert len(set(processed) & set(dirty_users)) == 2
    assert len(set(processed) & set(notification_users)) == 2


def test_worker_commits_each_user_before_processing_the_next(monkeypatch):
    now = datetime.now(timezone.utc)
    _, user_a = _signup("worker_commit_a")
    _, user_b = _signup("worker_commit_b")
    _patch_feed_candidates(monkeypatch, [])
    with SessionLocal() as db:
        db.query(CoachPreference).update(
            {
                "notifications_enabled": False,
                "reconciliation_requested_at": None,
            },
            synchronize_session=False,
        )
        db.query(CoachPreference).filter(
            CoachPreference.user_id.in_([user_a, user_b])
        ).delete(synchronize_session=False)
        db.add_all(
            [
                CoachPreference(
                    user_id=user_a,
                    notifications_enabled=False,
                    reconciliation_requested_at=now - timedelta(minutes=2),
                    reconciliation_revision=1,
                ),
                CoachPreference(
                    user_id=user_b,
                    notifications_enabled=False,
                    reconciliation_requested_at=now - timedelta(minutes=1),
                    reconciliation_revision=1,
                ),
            ]
        )
        db.commit()

    original_enqueue = coach_notification_service._enqueue_for_preference
    user_b_started = threading.Event()
    release_user_b = threading.Event()

    def block_second_user(db, preference, worker_now):
        if preference.user_id == user_b:
            user_b_started.set()
            assert release_user_b.wait(timeout=10)
        return original_enqueue(db, preference, worker_now)

    monkeypatch.setattr(
        coach_notification_service, "_enqueue_for_preference", block_second_user
    )

    def run_worker():
        with SessionLocal() as db:
            return coach_notification_service.enqueue_due_jobs(db, now, limit=2)

    def mutate_user_a():
        with SessionLocal() as db:
            return coach_feed_service.reconcile_after_mutation(db, user_a)

    with ThreadPoolExecutor(max_workers=2) as pool:
        worker_future = pool.submit(run_worker)
        assert user_b_started.wait(timeout=10)
        mutation_future = pool.submit(mutate_user_a)
        assert mutation_future.result(timeout=3) == 1
        release_user_b.set()
        assert worker_future.result(timeout=10) == 0

    with SessionLocal() as db:
        preference = db.get(CoachPreference, user_a)
        assert preference.reconciliation_requested_at is not None
        assert preference.reconciliation_revision > preference.reconciled_revision


def test_overlapping_enqueue_workers_do_not_duplicate_jobs(monkeypatch):
    _, user_id = _signup("enqueue_overlap")
    now = datetime.now(timezone.utc) + timedelta(minutes=1)
    activation_id = str(uuid.uuid4())
    candidate = coach_feed_service._candidate(
        kind="program_review",
        evidence=["overlap", activation_id],
        evidence_data={"activationIds": [activation_id]},
        priority=100,
        title="Review equipment",
        body="Review equipment before the program updates.",
        detail=None,
        target={"type": "none"},
    )
    _patch_feed_candidates(monkeypatch, [candidate])
    with SessionLocal() as db:
        db.query(CoachPreference).update(
            {
                "notifications_enabled": False,
                "reconciliation_requested_at": None,
            },
            synchronize_session=False,
        )
        preference = CoachPreference(
            user_id=user_id,
            notifications_enabled=True,
            reminder_time=time(8),
            time_zone="UTC",
        )
        db.add(preference)
        db.commit()

    original_enqueue = coach_notification_service._enqueue_for_preference
    first_worker_started = threading.Event()
    release_first_worker = threading.Event()
    call_lock = threading.Lock()
    call_count = 0

    def block_first_worker(db, preference, worker_now):
        nonlocal call_count
        with call_lock:
            call_count += 1
            is_first = call_count == 1
        if is_first:
            first_worker_started.set()
            assert release_first_worker.wait(timeout=10)
        return original_enqueue(db, preference, worker_now)

    monkeypatch.setattr(
        coach_notification_service, "_enqueue_for_preference", block_first_worker
    )

    def run_worker():
        with SessionLocal() as db:
            return coach_notification_service.enqueue_due_jobs(db, now, limit=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run_worker)
        assert first_worker_started.wait(timeout=10)
        second = pool.submit(run_worker)
        assert second.result(timeout=5) == 0
        release_first_worker.set()
        assert first.result(timeout=10) == 1

    with SessionLocal() as db:
        assert db.query(CoachNotificationJob).filter_by(user_id=user_id).count() == 1


def test_mixed_stale_retry_and_success_tokens_finish_after_retry(monkeypatch):
    _, user_id = _signup("worker_mixed")
    now = datetime.now(timezone.utc)
    tokens = [f"ExponentPushToken[mixed-{uuid.uuid4().hex}]" for _ in range(3)]
    with SessionLocal() as db:
        db.query(CoachNotificationJob).filter(
            CoachNotificationJob.status.in_(["pending", "retry", "processing"])
        ).update(
            {"scheduled_at": now + timedelta(days=1)}, synchronize_session=False
        )
        item = _item(user_id, 1, kind="program_review")
        db.add_all(
            [
                CoachPreference(
                    user_id=user_id,
                    notifications_enabled=True,
                    reminder_time=time(8),
                    time_zone="UTC",
                ),
                item,
            ]
        )
        db.flush()
        job = CoachNotificationJob(
            id=str(uuid.uuid4()),
            user_id=user_id,
            feed_item_id=item.id,
            job_type="equipment_review",
            scheduled_at=now,
            local_delivery_date=now.date(),
        )
        db.add(job)
        db.add_all(
            [PushToken(token=token, user_id=user_id, platform="ios") for token in tokens]
        )
        db.commit()
        responses = iter(
            [
                {
                    "data": [
                        {"status": "error", "details": {"error": "DeviceNotRegistered"}},
                        {"status": "error", "details": {"error": "ExpoServerError"}},
                        {"status": "ok", "id": "mixed-ok"},
                    ]
                },
                {"data": [{"status": "ok", "id": "mixed-retry-ok"}]},
            ]
        )
        monkeypatch.setattr(
            coach_notification_service,
            "_post_json",
            lambda url, payload: next(responses),
        )
        coach_notification_service.send_due_jobs(db, now)
        db.refresh(job)
        assert job.status == "retry"
        retry_at = job.retry_at
        coach_notification_service.send_due_jobs(db, retry_at)
        db.refresh(job)
        assert job.status == "sent"
        assert db.query(PushToken).filter_by(user_id=user_id).count() == 2


def test_next_action_prefers_active_session_over_plan():
    _, user_id = _signup("active_action")
    with SessionLocal() as db:
        session = WorkoutSession(
            id=str(uuid.uuid4()),
            user_id=user_id,
            workout_day_id=str(uuid.uuid4()),
            workout_date=date.today(),
            day_type="upper",
            workout_snapshot={"title": "Upper A", "exerciseBlocks": []},
            client_session_id=str(uuid.uuid4()),
            status="in_progress",
        )
        db.add(session)
        db.commit()
        action = coach_feed_service.build_next_action(db, user_id, date.today())
        assert action.type == "active_workout"
        assert action.body == "Upper A"
        assert action.target.session_id == session.id


def test_progression_and_missed_rules_use_canonical_week_plan():
    _, user_id = _signup("plan_rules")
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    past_date = today - timedelta(days=1)
    future_date = today + timedelta(days=1)
    with SessionLocal() as db:
        prior = WorkoutSession(
            id=str(uuid.uuid4()),
            user_id=user_id,
            workout_day_id=str(uuid.uuid4()),
            workout_date=today - timedelta(days=3),
            day_type="upper",
            workout_snapshot={"title": "Upper", "exerciseBlocks": []},
            client_session_id=str(uuid.uuid4()),
            status="completed",
            completed_at=datetime.now(timezone.utc) - timedelta(days=3),
        )
        db.add(prior)
        db.flush()
        db.add(
            SetLog(
                id=str(uuid.uuid4()),
                session_id=prior.id,
                exercise_id="bench_press",
                set_number=1,
                reps=10,
                weight_kg=60,
                client_operation_id=str(uuid.uuid4()),
            )
        )
        plan = WorkoutWeekPlan(
            id=str(uuid.uuid4()),
            user_id=user_id,
            week_start_date=monday,
            days_per_week=2,
            plan_json={
                "workouts": [
                    {
                        "workoutDayId": "missed-day",
                        "date": past_date.isoformat(),
                        "title": "Missed Upper",
                        "exerciseBlocks": [],
                    },
                    {
                        "workoutDayId": "future-day",
                        "date": future_date.isoformat(),
                        "title": "Future Upper",
                        "exerciseBlocks": [
                            {
                                "items": [
                                    {
                                            "exercise": {
                                                "id": "bench_press",
                                                "name": "Bench Press",
                                                "primaryMuscle": "chest",
                                            },
                                            "prescription": {
                                                "repsMin": 6,
                                                "repsMax": 10,
                                                "suggestedWeightReason": "increase",
                                            "suggestedWeightKg": 62.5,
                                        },
                                    },
                                    {
                                        "exercise": {"id": "push_up", "name": "Push Up"},
                                        "prescription": {
                                            "suggestedWeightReason": "hold",
                                            "suggestedWeightKg": 0,
                                        },
                                    },
                                ]
                            }
                        ],
                    },
                ]
            },
        )
        db.add(plan)
        db.commit()
        missed = coach_feed_service._missed_candidates(db, user_id, today)
        progression = coach_feed_service._progression_candidates(db, user_id, today)
        assert len(missed) == 1
        assert missed[0]["target_data"]["workoutDayId"] == "missed-day"
        assert len(progression) == 1
        assert progression[0]["title"] == "Move up on Bench Press"


def test_recovery_and_weighted_pr_rules_use_effective_session_data():
    _, user_id = _signup("session_rules")
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        first = WorkoutSession(
            id=str(uuid.uuid4()),
            user_id=user_id,
            workout_day_id=str(uuid.uuid4()),
            workout_date=date.today() - timedelta(days=2),
            day_type="upper",
            workout_snapshot={"title": "Upper", "exerciseBlocks": []},
            client_session_id=str(uuid.uuid4()),
            status="completed",
            completed_at=now - timedelta(days=2),
        )
        latest = WorkoutSession(
            id=str(uuid.uuid4()),
            user_id=user_id,
            workout_day_id=str(uuid.uuid4()),
            workout_date=date.today(),
            day_type="upper",
            workout_snapshot={"title": "Upper", "exerciseBlocks": []},
            client_session_id=str(uuid.uuid4()),
            status="completed",
            completed_at=now,
        )
        db.add_all([first, latest])
        db.flush()
        db.add_all(
            [
                SetLog(
                    id=str(uuid.uuid4()),
                    session_id=first.id,
                    exercise_id="bench_press",
                    set_number=1,
                    reps=5,
                    weight_kg=50,
                    client_operation_id=str(uuid.uuid4()),
                ),
                SetLog(
                    id=str(uuid.uuid4()),
                    session_id=latest.id,
                    exercise_id="bench_press",
                    set_number=1,
                    reps=5,
                    weight_kg=55,
                    client_operation_id=str(uuid.uuid4()),
                ),
                SetLog(
                    id=str(uuid.uuid4()),
                    session_id=latest.id,
                    exercise_id="bench_press",
                    set_number=2,
                    reps=3,
                    weight_kg=57.5,
                    client_operation_id=str(uuid.uuid4()),
                ),
                WorkoutSessionExerciseFeedback(
                    id=str(uuid.uuid4()),
                    session_id=latest.id,
                    exercise_id="bench_press",
                    effort="hard",
                    rir=1,
                ),
                WorkoutSessionExerciseFeedback(
                    id=str(uuid.uuid4()),
                    session_id=latest.id,
                    exercise_id="seated_row",
                    effort="hard",
                    rir=0,
                ),
            ]
        )
        db.commit()
        recovery = coach_feed_service._recovery_candidate(db, user_id)
        prs = coach_feed_service._pr_candidates(db, user_id)
        assert len(recovery) == 1
        assert "not medical advice" in recovery[0]["detail"]
        assert len(prs) == 1
        assert prs[0]["target_data"]["sessionId"] == latest.id
        assert "57.5 kg" in prs[0]["body"]


def test_reconcile_does_not_revive_expired_evidence(monkeypatch):
    _, user_id = _signup("retention")
    evidence_at = datetime.now(timezone.utc) - timedelta(days=31)
    candidate = coach_feed_service._candidate(
        kind="consistency",
        evidence=["old", 5],
        evidence_at=evidence_at,
        priority=40,
        title="Five workouts",
        body="Old evidence",
        detail=None,
        target={"type": "none"},
    )
    with SessionLocal() as db:
        original = CoachFeedItem(
            id=str(uuid.uuid4()),
            user_id=user_id,
            ai_status="not_eligible",
            **candidate,
        )
        db.add(original)
        db.commit()
        original_id = original.id
        original_expiry = original.expires_at

        monkeypatch.setattr(coach_feed_service, "_progression_candidates", lambda *args: [])
        monkeypatch.setattr(coach_feed_service, "_recovery_candidate", lambda *args: [])
        monkeypatch.setattr(coach_feed_service, "_missed_candidates", lambda *args: [])
        monkeypatch.setattr(
            coach_feed_service, "_upcoming_notification_candidates", lambda *args: []
        )
        monkeypatch.setattr(coach_feed_service, "_pr_candidates", lambda *args: [])
        monkeypatch.setattr(
            coach_feed_service, "_consistency_candidates", lambda *args: [candidate]
        )
        monkeypatch.setattr(coach_feed_service, "_program_review_candidates", lambda *args: [])

        coach_feed_service.reconcile_feed(db, user_id, date.today())
        rows = db.query(CoachFeedItem).filter_by(user_id=user_id).all()
        assert len(rows) == 1
        assert rows[0].id == original_id
        assert rows[0].expires_at == original_expiry
        assert rows[0].expires_at <= datetime.now(timezone.utc)


@pytest.mark.parametrize("kind", ["progression", "recovery"])
def test_reconcile_restores_same_valid_system_invalidated_candidate(monkeypatch, kind):
    _, user_id = _signup(f"restore_{kind}")
    source_id = str(uuid.uuid4())
    evidence_data = (
        {"exerciseIds": [source_id]}
        if kind == "progression"
        else {"metricSessionIds": [source_id]}
    )
    candidate = coach_feed_service._candidate(
        kind=kind,
        evidence=[kind, source_id],
        evidence_data=evidence_data,
        priority=70,
        title="Current guidance",
        body="The canonical recommendation is still valid.",
        detail=None,
        target={"type": "none"},
    )
    _patch_feed_candidates(monkeypatch, [candidate])
    with SessionLocal() as db:
        coach_feed_service.reconcile_feed(db, user_id, date.today())
        item = db.query(CoachFeedItem).filter_by(user_id=user_id, kind=kind).one()
        item_id = item.id
        kwargs = (
            {"exercise_ids": [source_id]}
            if kind == "progression"
            else {"metric_session_ids": [source_id]}
        )
        assert coach_feed_service.reconcile_after_mutation(db, user_id, **kwargs) == 1
        db.refresh(item)
        assert item.expires_at <= datetime.now(timezone.utc)
        assert item.invalidation_reason == "source_mutated"

        coach_feed_service.reconcile_feed(db, user_id, date.today())
        db.refresh(item)
        assert item.id == item_id
        assert item.expires_at == candidate["expires_at"]
        assert item.invalidated_at is None
        assert item.invalidation_reason is None


def test_reconcile_never_restores_user_dismissed_identical_evidence(monkeypatch):
    _, user_id = _signup("dismissed_terminal")
    exercise_id = str(uuid.uuid4())
    candidate = coach_feed_service._candidate(
        kind="progression",
        evidence=["dismissed", exercise_id],
        evidence_data={"exerciseIds": [exercise_id]},
        priority=70,
        title="Current guidance",
        body="This recommendation was dismissed.",
        detail=None,
        target={"type": "none"},
    )
    _patch_feed_candidates(monkeypatch, [candidate])
    with SessionLocal() as db:
        coach_feed_service.reconcile_feed(db, user_id, date.today())
        item = db.query(CoachFeedItem).filter_by(user_id=user_id).one()
        assert coach_feed_service.dismiss_item(db, user_id, item.id)
        assert coach_feed_service.reconcile_after_mutation(
            db, user_id, exercise_ids=[exercise_id]
        ) == 1
        coach_feed_service.reconcile_feed(db, user_id, date.today())
        db.refresh(item)
        assert item.dismissed_at is not None
        assert item.expires_at <= datetime.now(timezone.utc)
        assert item.invalidation_reason == "source_mutated"


def test_reps_correction_revives_same_weight_pr_after_reconcile():
    headers, user_id = _signup("pr_revival")
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        sessions = []
        for index, weight in enumerate((10, 20)):
            session = WorkoutSession(
                id=str(uuid.uuid4()),
                user_id=user_id,
                workout_day_id=str(uuid.uuid4()),
                workout_date=date.today() - timedelta(days=1 - index),
                day_type="upper",
                workout_snapshot={
                    "title": "Upper",
                    "exerciseBlocks": [
                        {"items": [{"exercise": {"id": "push_up"}}]}
                    ],
                },
                client_session_id=str(uuid.uuid4()),
                status="completed",
                completed_at=now - timedelta(days=1 - index),
            )
            set_log = SetLog(
                id=str(uuid.uuid4()),
                session_id=session.id,
                exercise_id="push_up",
                set_number=1,
                reps=10,
                weight_kg=weight,
                client_operation_id=str(uuid.uuid4()),
                version=1,
            )
            db.add_all([session, set_log])
            sessions.append((session, set_log))
        db.commit()
        coach_feed_service.reconcile_feed(db, user_id, date.today())
        item = db.query(CoachFeedItem).filter_by(
            user_id=user_id, kind="personal_record"
        ).one()
        item_id = item.id
        latest_session_id = sessions[-1][0].id
        latest_set_id = sessions[-1][1].id

    response = client.patch(
        f"/v1/workouts/sessions/{latest_session_id}/sets/{latest_set_id}",
        headers=headers,
        json={
            "reps": 9,
            "expectedVersion": 1,
            "clientOperationId": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 200, response.text
    assert client.get(f"/v1/coach/items/{item_id}", headers=headers).status_code == 404

    with SessionLocal() as db:
        coach_feed_service.reconcile_feed(db, user_id, date.today())
        revived = db.get(CoachFeedItem, item_id)
        assert revived is not None
        assert revived.expires_at > datetime.now(timezone.utc)
        assert revived.invalidation_reason is None
    assert client.get(f"/v1/coach/items/{item_id}", headers=headers).status_code == 200


def test_reconciled_notification_reactivation_preserves_delivery_history(monkeypatch):
    _, user_id = _signup("job_reconcile_revival")
    now = datetime.now(timezone.utc)
    activation_id = str(uuid.uuid4())
    candidate = coach_feed_service._candidate(
        kind="program_review",
        evidence=["review", activation_id],
        evidence_data={"activationIds": [activation_id]},
        priority=100,
        title="Review equipment",
        body="Review equipment before the program updates.",
        detail=None,
        target={"type": "none"},
    )
    _patch_feed_candidates(monkeypatch, [candidate])
    with SessionLocal() as db:
        db.add(
            CoachPreference(
                user_id=user_id,
                notifications_enabled=True,
                reminder_time=time(8),
                time_zone="UTC",
            )
        )
        db.commit()
        coach_feed_service.reconcile_feed(db, user_id, date.today())
        item = db.query(CoachFeedItem).filter_by(user_id=user_id).one()
        assert coach_notification_service._insert_job(
            db,
            user_id=user_id,
            item=item,
            job_type="equipment_review",
            scheduled_at=now,
            local_delivery_date=now.date(),
        ) == 1
        job = db.query(CoachNotificationJob).filter_by(feed_item_id=item.id).one()
        db.add_all(
            [
                CoachNotificationDelivery(
                    id=str(uuid.uuid4()),
                    job_id=job.id,
                    push_token_hash="terminal-history",
                    status="failed",
                    attempts=5,
                ),
                CoachNotificationDelivery(
                    id=str(uuid.uuid4()),
                    job_id=job.id,
                    push_token_hash="retry-history",
                    status="retry",
                    attempts=2,
                    retry_at=now + timedelta(hours=1),
                ),
            ]
        )
        db.commit()
        assert coach_feed_service.reconcile_after_mutation(
            db, user_id, activation_ids=[activation_id]
        ) == 1
        db.refresh(job)
        assert job.status == "cancelled"

        preference = db.get(CoachPreference, user_id)
        assert coach_notification_service._enqueue_for_preference(
            db, preference, now
        ) == 1
        db.commit()
        db.refresh(job)
        assert job.status == "pending"
        deliveries = db.query(CoachNotificationDelivery).filter_by(job_id=job.id).all()
        assert {(row.push_token_hash, row.status, row.attempts) for row in deliveries} == {
            ("terminal-history", "failed", 5),
            ("retry-history", "retry", 2),
        }


def test_stale_direct_target_expires_item_and_cancels_job():
    _, user_id = _signup("stale_target")
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        plan = WorkoutWeekPlan(
            id=str(uuid.uuid4()),
            user_id=user_id,
            week_start_date=monday,
            days_per_week=1,
            plan_json={
                "workouts": [
                    {
                        "workoutDayId": "removed-workout",
                        "date": today.isoformat(),
                        "exerciseBlocks": [],
                    }
                ]
            },
        )
        item = _item(user_id, 1, kind="upcoming_workout")
        item.target_data = {
            "type": "planned_workout",
            "workoutDayId": "removed-workout",
            "weekStart": monday.isoformat(),
        }
        db.add_all(
            [
                plan,
                item,
                CoachPreference(
                    user_id=user_id,
                    notifications_enabled=True,
                    reminder_time=time(8),
                    time_zone="UTC",
                ),
            ]
        )
        db.flush()
        job = CoachNotificationJob(
            id=str(uuid.uuid4()),
            user_id=user_id,
            feed_item_id=item.id,
            job_type="scheduled_daily",
            scheduled_at=now,
            local_delivery_date=today,
        )
        db.add(job)
        db.commit()

        plan.plan_json = {**plan.plan_json, "workouts": []}
        db.commit()
        assert coach_feed_service.get_item(db, user_id, item.id) is None
        db.refresh(item)
        db.refresh(job)
        assert item.expires_at <= datetime.now(timezone.utc)
        assert job.status == "cancelled"


def test_unrelated_set_edit_preserves_valid_pr_and_milestone(monkeypatch):
    headers, user_id = _signup("targeted_set")
    today = date.today()
    candidates = [
        coach_feed_service._candidate(
            kind="personal_record",
            evidence=["stable-pr"],
            priority=60,
            title="A valid personal best",
            body="This unrelated best remains valid.",
            detail=None,
            target={"type": "none"},
        ),
        coach_feed_service._candidate(
            kind="consistency",
            evidence=["stable-milestone"],
            priority=40,
            title="A valid milestone",
            body="This milestone remains valid.",
            detail=None,
            target={"type": "none"},
        ),
    ]
    _patch_feed_candidates(monkeypatch, candidates)
    with SessionLocal() as db:
        session = WorkoutSession(
            id=str(uuid.uuid4()),
            user_id=user_id,
            workout_day_id=str(uuid.uuid4()),
            workout_date=today,
            day_type="upper",
            workout_snapshot={
                "title": "Upper",
                "exerciseBlocks": [
                    {"items": [{"exercise": {"id": "push_up"}}]}
                ],
            },
            client_session_id=str(uuid.uuid4()),
            status="completed",
            completed_at=datetime.now(timezone.utc),
        )
        set_log = SetLog(
            id=str(uuid.uuid4()),
            session_id=session.id,
            exercise_id="push_up",
            set_number=1,
            reps=10,
            weight_kg=20,
            client_operation_id=str(uuid.uuid4()),
            version=1,
        )
        db.add_all([session, set_log])
        db.commit()
        session_id = session.id
        set_log_id = set_log.id
        coach_feed_service.reconcile_feed(db, user_id, today)
        item_ids = {
            row.kind: row.id
            for row in db.query(CoachFeedItem).filter_by(user_id=user_id).all()
        }
        review_item = _item(user_id, 99, kind="program_review")
        db.add(review_item)
        db.flush()
        review_job = CoachNotificationJob(
            id=str(uuid.uuid4()),
            user_id=user_id,
            feed_item_id=review_item.id,
            job_type="equipment_review",
            scheduled_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            local_delivery_date=today,
        )
        db.add(review_job)
        db.commit()
        review_job_id = review_job.id

    monkeypatch.setattr(
        coach_feed_service,
        "reconcile_feed",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("set mutation performed a full Coach reconciliation")
        ),
    )
    response = client.patch(
        f"/v1/workouts/sessions/{session_id}/sets/{set_log_id}",
        headers=headers,
        json={
            "reps": 9,
            "expectedVersion": 1,
            "clientOperationId": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        preference = db.get(CoachPreference, user_id)
        assert preference.reconciliation_requested_at is not None
        preserved = db.query(CoachFeedItem).filter(
            CoachFeedItem.id.in_(item_ids.values())
        ).all()
        assert {row.kind for row in preserved if row.expires_at > datetime.now(timezone.utc)} == {
            "personal_record",
            "consistency",
        }
        assert db.get(CoachNotificationJob, review_job_id).status == "pending"

    monkeypatch.setattr(
        coach_feed_service,
        "insert",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("coach down")),
    )
    response = client.patch(
        f"/v1/workouts/sessions/{session_id}/sets/{set_log_id}",
        headers=headers,
        json={
            "reps": 8,
            "expectedVersion": 2,
            "clientOperationId": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["reps"] == 8


def test_set_correction_immediately_invalidates_only_dependent_guidance():
    headers, user_id = _signup("immediate_set_invalidation")
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        session = WorkoutSession(
            id=str(uuid.uuid4()),
            user_id=user_id,
            workout_day_id=str(uuid.uuid4()),
            workout_date=date.today(),
            day_type="upper",
            workout_snapshot={
                "title": "Upper",
                "exerciseBlocks": [
                    {"items": [{"exercise": {"id": "push_up"}}]}
                ],
            },
            client_session_id=str(uuid.uuid4()),
            status="completed",
            completed_at=now,
        )
        set_log = SetLog(
            id=str(uuid.uuid4()),
            session_id=session.id,
            exercise_id="push_up",
            set_number=1,
            reps=10,
            weight_kg=20,
            client_operation_id=str(uuid.uuid4()),
            version=1,
        )
        dependent = []
        for index, (kind, evidence_data) in enumerate(
            [
                ("personal_record", {"exerciseIds": ["push_up"]}),
                ("progression", {"exerciseIds": ["push_up"]}),
                ("recovery", {"metricSessionIds": [session.id]}),
            ],
            start=1,
        ):
            item = _item(user_id, index, kind=kind)
            item.evidence_data = evidence_data
            dependent.append(item)
        unrelated_pr = _item(user_id, 10, kind="personal_record")
        unrelated_pr.evidence_data = {"exerciseIds": ["squat"]}
        milestone = _item(user_id, 11, kind="consistency")
        milestone.evidence_data = {"statusSessionIds": [session.id]}
        db.add_all([session, set_log, *dependent, unrelated_pr, milestone])
        db.flush()
        recovery_job = CoachNotificationJob(
            id=str(uuid.uuid4()),
            user_id=user_id,
            feed_item_id=dependent[2].id,
            job_type="scheduled_daily",
            scheduled_at=now + timedelta(minutes=5),
            local_delivery_date=now.date(),
        )
        db.add(recovery_job)
        db.commit()
        dependent_ids = [item.id for item in dependent]
        unrelated_ids = [unrelated_pr.id, milestone.id]
        session_id = session.id
        set_log_id = set_log.id
        recovery_job_id = recovery_job.id

    response = client.patch(
        f"/v1/workouts/sessions/{session_id}/sets/{set_log_id}",
        headers=headers,
        json={
            "reps": 9,
            "expectedVersion": 1,
            "clientOperationId": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 200, response.text
    for item_id in dependent_ids:
        assert client.get(f"/v1/coach/items/{item_id}", headers=headers).status_code == 404
    for item_id in unrelated_ids:
        assert client.get(f"/v1/coach/items/{item_id}", headers=headers).status_code == 200
    with SessionLocal() as db:
        assert db.get(CoachNotificationJob, recovery_job_id).status == "cancelled"


def test_duration_change_preserves_unrelated_guidance(monkeypatch):
    headers, user_id = _signup("targeted_duration")
    onboarding = client.post(
        "/v1/onboarding/me",
        headers=headers,
        json={
            "data": {
                "equipment_ids": ["dumbbells", "flat_bench"],
                "days_per_week": 3,
            },
            "is_complete": True,
        },
    )
    assert onboarding.status_code == 200
    week = client.get("/v1/workouts/week", headers=headers)
    assert week.status_code == 200
    workout = week.json()["workouts"][0]
    candidate = coach_feed_service._candidate(
        kind="recovery",
        evidence=["stable-recovery"],
        priority=80,
        title="Keep the next session steady",
        body="Follow the next session as written.",
        detail=None,
        target={"type": "none"},
    )
    _patch_feed_candidates(monkeypatch, [candidate])
    with SessionLocal() as db:
        coach_feed_service.reconcile_feed(db, user_id, date.today())
        item_id = db.query(CoachFeedItem).filter_by(
            user_id=user_id, kind="recovery"
        ).one().id
        progression = _item(user_id, 80, kind="progression")
        progression.evidence_data = {
            "workoutDayIds": [workout["workoutDayId"]]
        }
        program_review = _item(user_id, 81, kind="program_review")
        program_review.evidence_data = {"activationIds": ["unrelated-program"]}
        db.add_all([progression, program_review])
        db.commit()
        progression_id = progression.id
        program_review_id = program_review.id

    duration = 25 if workout["durationMinutes"] != 25 else 35
    response = client.patch(
        "/v1/workouts/week/duration",
        headers=headers,
        json={
            "workoutDayId": workout["workoutDayId"],
            "durationMinutes": duration,
            "weekStart": week.json()["weekStart"],
        },
    )
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        item = db.get(CoachFeedItem, item_id)
        assert item.expires_at > datetime.now(timezone.utc)
    assert client.get(
        f"/v1/coach/items/{progression_id}", headers=headers
    ).status_code == 404
    assert client.get(
        f"/v1/coach/items/{program_review_id}", headers=headers
    ).status_code == 200


def test_material_equipment_changes_create_new_review_evidence():
    _, user_id = _signup("equipment_evidence")
    with SessionLocal() as db:
        template = db.query(WorkoutTemplate).order_by(WorkoutTemplate.id).first()
        equipment = db.query(Equipment).order_by(Equipment.id).limit(2).all()
        assert template is not None and len(equipment) == 2
        activation = UserProgramActivation(
            id=str(uuid.uuid4()),
            user_id=user_id,
            template_id=template.id,
            template_version=template.version,
            status="active",
            effective_date=date.today(),
            weekdays=[date.today().weekday()],
            activation_snapshot={"name": template.name, "days": []},
            apply_mode="now",
            client_operation_id=str(uuid.uuid4()),
            request_fingerprint=str(uuid.uuid4()),
        )
        db.add(activation)
        db.commit()

        update_training_preferences(
            db, user_id, {"selectedEquipment": [equipment[0].id]}
        )
        db.refresh(activation)
        first_fingerprint = activation.activation_snapshot["equipmentReviewFingerprint"]
        coach_feed_service.reconcile_feed(db, user_id, date.today())
        first_item = db.query(CoachFeedItem).filter_by(
            user_id=user_id, kind="program_review"
        ).one()
        assert coach_feed_service.dismiss_item(db, user_id, first_item.id)

        update_training_preferences(
            db, user_id, {"selectedEquipment": [equipment[1].id]}
        )
        db.refresh(activation)
        second_fingerprint = activation.activation_snapshot["equipmentReviewFingerprint"]
        assert second_fingerprint != first_fingerprint
        coach_feed_service.reconcile_feed(db, user_id, date.today())
        items = db.query(CoachFeedItem).filter_by(
            user_id=user_id, kind="program_review"
        ).all()
        assert len(items) == 2
        assert len({row.evidence_fingerprint for row in items}) == 2
        assert sum(row.dismissed_at is None for row in items) == 1

        current = next(row for row in items if row.dismissed_at is None)
        assert coach_feed_service.dismiss_item(db, user_id, current.id)
        update_training_preferences(
            db, user_id, {"selectedEquipment": [equipment[0].id]}
        )
        db.refresh(activation)
        assert activation.activation_snapshot["equipmentReviewRevision"] == 3
        assert (
            activation.activation_snapshot["equipmentReviewFingerprint"]
            != first_fingerprint
        )
        coach_feed_service.reconcile_feed(db, user_id, date.today())
        cycled = db.query(CoachFeedItem).filter_by(
            user_id=user_id, kind="program_review"
        ).all()
        assert len(cycled) == 3
        assert len({row.evidence_fingerprint for row in cycled}) == 3
        assert sum(row.dismissed_at is None for row in cycled) == 1


def test_equipment_change_immediately_invalidates_program_review_source():
    headers, user_id = _signup("program_review_invalidation")
    with SessionLocal() as db:
        template = db.query(WorkoutTemplate).order_by(WorkoutTemplate.id).first()
        equipment = db.query(Equipment).order_by(Equipment.id).first()
        assert template is not None and equipment is not None
        activation = UserProgramActivation(
            id=str(uuid.uuid4()),
            user_id=user_id,
            template_id=template.id,
            template_version=template.version,
            status="active",
            effective_date=date.today(),
            weekdays=[date.today().weekday()],
            activation_snapshot={"name": template.name, "days": []},
            apply_mode="now",
            client_operation_id=str(uuid.uuid4()),
            request_fingerprint=str(uuid.uuid4()),
        )
        review = _item(user_id, 1, kind="program_review")
        review.evidence_data = {
            "activationIds": [activation.id],
            "templateIds": [template.id],
        }
        unrelated = _item(user_id, 2, kind="personal_record")
        unrelated.evidence_data = {"exerciseIds": ["push_up"]}
        db.add_all([activation, review, unrelated])
        db.commit()
        review_id = review.id
        unrelated_id = unrelated.id
        equipment_id = equipment.id

    response = client.patch(
        "/v1/users/me/training-preferences",
        headers=headers,
        json={"selectedEquipment": [equipment_id]},
    )
    assert response.status_code == 200, response.text
    assert client.get(f"/v1/coach/items/{review_id}", headers=headers).status_code == 404
    assert client.get(
        f"/v1/coach/items/{unrelated_id}", headers=headers
    ).status_code == 200


def test_terminal_ticket_failure_does_not_mark_job_sent(monkeypatch):
    _, user_id = _signup("worker_terminal")
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        item = _item(user_id, 1, kind="program_review")
        db.add_all(
            [
                CoachPreference(
                    user_id=user_id,
                    notifications_enabled=True,
                    reminder_time=time(8),
                    time_zone="UTC",
                ),
                item,
            ]
        )
        db.flush()
        job = CoachNotificationJob(
            id=str(uuid.uuid4()),
            user_id=user_id,
            feed_item_id=item.id,
            job_type="equipment_review",
            scheduled_at=now,
            local_delivery_date=now.date(),
        )
        db.add_all(
            [
                job,
                PushToken(
                    token=f"ExponentPushToken[terminal-{uuid.uuid4().hex}]",
                    user_id=user_id,
                    platform="ios",
                ),
            ]
        )
        db.commit()
        monkeypatch.setattr(
            coach_notification_service,
            "_post_json",
            lambda url, payload: {
                "data": [
                    {
                        "status": "error",
                        "details": {"error": "MessageTooBig"},
                    }
                    for _ in payload
                ]
            },
        )
        coach_notification_service.send_due_jobs(db, now)
        db.refresh(job)
        delivery = db.query(CoachNotificationDelivery).filter_by(job_id=job.id).one()
        assert delivery.status == "failed"
        assert job.status == "failed"


def test_feed_and_worker_clear_dirty_marker_using_explicit_local_dates(monkeypatch):
    headers, feed_user_id = _signup("dirty_feed")
    with SessionLocal() as db:
        assert coach_feed_service.reconcile_after_mutation(db, feed_user_id) == 1
        assert db.get(CoachPreference, feed_user_id).reconciliation_requested_at is not None
    response = client.get(
        "/v1/coach/feed",
        headers=headers,
        params={"localDate": "2026-01-11"},
    )
    assert response.status_code == 200
    with SessionLocal() as db:
        preference = db.get(CoachPreference, feed_user_id)
        assert preference.reconciliation_requested_at is None
        assert preference.last_reconciled_at is not None

    _, worker_user_id = _signup("dirty_worker_timezone")
    worker_now = datetime(2026, 1, 2, 1, 0, tzinfo=timezone.utc)
    captured_dates = []
    original_reconcile = coach_feed_service.reconcile_feed
    _patch_feed_candidates(monkeypatch, [])

    def capture_reconcile(db, user_id, local_date, **kwargs):
        captured_dates.append(local_date)
        return original_reconcile(db, user_id, local_date, **kwargs)

    monkeypatch.setattr(coach_notification_service, "reconcile_feed", capture_reconcile)
    with SessionLocal() as db:
        db.query(CoachPreference).update(
            {
                "notifications_enabled": False,
                "reconciliation_requested_at": None,
            },
            synchronize_session=False,
        )
        db.add(
            CoachPreference(
                user_id=worker_user_id,
                notifications_enabled=False,
                reminder_time=time(8),
                time_zone="America/Los_Angeles",
                reconciliation_requested_at=worker_now,
            )
        )
        db.commit()
        coach_notification_service.enqueue_due_jobs(db, worker_now, limit=1)
        preference = db.get(CoachPreference, worker_user_id)
        assert preference.reconciliation_requested_at is None
    assert captured_dates == [date(2026, 1, 1)]


def test_coach_queue_and_retention_indexes_exist():
    with SessionLocal() as db:
        inspector = inspect(db.bind)
        feed_indexes = {row["name"] for row in inspector.get_indexes("coach_feed_items")}
        preference_indexes = {
            row["name"] for row in inspector.get_indexes("coach_preferences")
        }
        delivery_indexes = {
            row["name"]
            for row in inspector.get_indexes("coach_notification_deliveries")
        }
        notification_index_definition = db.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname = current_schema() "
                "AND indexname = 'ix_coach_preferences_notifications'"
            )
        ).scalar_one()
    assert "ix_coach_feed_items_ai_queue" in feed_indexes
    assert "ix_coach_feed_items_expires_at" in feed_indexes
    assert "ix_coach_preferences_dirty" in preference_indexes
    assert "ix_coach_preferences_notifications" in preference_indexes
    assert "last_reconciled_at" in notification_index_definition
    assert "NULLS FIRST" in notification_index_definition
    assert notification_index_definition.index("last_reconciled_at") < notification_index_definition.index("user_id")
    assert "notifications_enabled IS TRUE" in notification_index_definition
    assert "ix_coach_notification_deliveries_created_at" in delivery_indexes


def test_observability_never_logs_copy_training_values_email_or_token(
    monkeypatch, caplog
):
    headers, user_id = _signup("safe_events")
    now = datetime.now(timezone.utc)
    raw_token = f"ExponentPushToken[private-{uuid.uuid4().hex}]"
    with SessionLocal() as db:
        email = db.get(User, user_id).email
        item = _item(user_id, 1, kind="program_review")
        item.title = "Sensitive 123.45 kg marker"
        db.add_all(
            [
                CoachPreference(
                    user_id=user_id,
                    notifications_enabled=True,
                    reminder_time=time(8),
                    time_zone="UTC",
                ),
                item,
            ]
        )
        db.flush()
        db.add_all(
            [
                CoachNotificationJob(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    feed_item_id=item.id,
                    job_type="equipment_review",
                    scheduled_at=now,
                    local_delivery_date=now.date(),
                ),
                PushToken(token=raw_token, user_id=user_id, platform="ios"),
            ]
        )
        db.commit()
        caplog.set_level(logging.INFO, logger="primerep.coach")
        monkeypatch.setattr(
            coach_notification_service,
            "_post_json",
            lambda url, payload: {
                "data": [{"status": "ok", "id": "privacy-safe-ticket"}]
            },
        )
        coach_notification_service.send_due_jobs(db, now)
        coach_feed_service._event(
            "coach_privacy_assertion",
            source="test",
            outcome="success",
            reason="verified",
            pageSize=1,
            queueDepth=0,
        )

    rendered = caplog.text
    assert "coach_notification_send" in rendered
    assert "source" in rendered and "outcome" in rendered
    assert email not in rendered
    assert raw_token not in rendered
    assert "123.45" not in rendered
    assert "Sensitive" not in rendered


def test_account_deletion_cascades_all_coach_records():
    headers, user_id = _signup("cascade")
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        item = _item(user_id, 1, kind="program_review")
        db.add_all(
            [
                CoachPreference(
                    user_id=user_id,
                    notifications_enabled=True,
                    reminder_time=time(8),
                    time_zone="UTC",
                ),
                item,
            ]
        )
        db.flush()
        job = CoachNotificationJob(
            id=str(uuid.uuid4()),
            user_id=user_id,
            feed_item_id=item.id,
            job_type="equipment_review",
            scheduled_at=now,
            local_delivery_date=now.date(),
        )
        db.add(job)
        db.flush()
        db.add(
            CoachNotificationDelivery(
                id=str(uuid.uuid4()),
                job_id=job.id,
                push_token_hash="opaque-cascade-hash",
                status="failed",
                attempts=1,
            )
        )
        db.commit()
        item_id = item.id
        job_id = job.id

    response = client.delete("/v1/users/me", headers=headers)
    assert response.status_code == 204
    with SessionLocal() as db:
        assert db.get(CoachPreference, user_id) is None
        assert db.get(CoachFeedItem, item_id) is None
        assert db.get(CoachNotificationJob, job_id) is None
        assert db.query(CoachNotificationDelivery).filter_by(job_id=job_id).count() == 0
