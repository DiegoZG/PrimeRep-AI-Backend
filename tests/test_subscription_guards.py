import copy
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import SessionLocal
from app.core import revenuecat_client, subscription_service
from app.core.settings import settings
from app.core.subscription_service import SubscriptionAccessError
from app.models.feature_flags import FeatureFlagConfig
from app.models.subscription import BillingJob, SubscriptionState
from app.models.user import User
from app.models.workout_session import WorkoutSession
from app.models.workout_template import UserProgramActivation, WorkoutTemplate
from app.models.workout_week_plan import WorkoutWeekPlan
from conftest import LEGAL_ACCEPTANCE

client = TestClient(app)


@pytest.fixture
def account(monkeypatch):
    env = f"guards-{uuid4().hex[:20]}"
    monkeypatch.setattr(settings, "APP_ENV", env)
    monkeypatch.setattr(settings, "REVENUECAT_SECRET_API_KEY", "fixture-key")
    monkeypatch.setattr(settings, "REVENUECAT_PRODUCT_IDS", frozenset({"fixture.monthly"}))
    monkeypatch.setattr(settings, "REVENUECAT_APP_IDS", frozenset({"fixture-app"}))
    monkeypatch.setattr(revenuecat_client, "fetch_customer", lambda _: {"subscriber": {"entitlements": {}}})
    response = client.post("/v1/auth/signup", json={"email": f"guards-{uuid4().hex}@example.com",
        "password": "StrongPass123", "preferred_name": "Billing", "last_name": "Test", "legalAcceptance": LEGAL_ACCEPTANCE})
    assert response.status_code == 201, response.text
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    user_id = client.get("/v1/users/me", headers=headers).json()["id"]
    return headers, user_id, env


def deny(account):
    _, user_id, env = account
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.add(FeatureFlagConfig(environment=env, version=1, subscriptions_purchases_enabled=True,
            subscriptions_enforcement_enabled=True, subscriptions_launch_at=now - timedelta(days=31),
            launch_locked=True, updated_at=now))
        db.add(SubscriptionState(environment=env, user_id=user_id, billing_status="none",
            verification_status="verified", verified_at=now))
        db.commit()


def week(account, offset=0):
    response = client.get("/v1/workouts/week", headers=account[0], params={"weekStart": (date.today() + timedelta(days=7 * offset)).isoformat()})
    assert response.status_code == 200, response.text
    return response.json()


def test_unpaid_signup_and_account_controls_work_but_new_week_is_denied(account):
    deny(account)
    response = client.get("/v1/workouts/week", headers=account[0])
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "subscription_required"
    assert client.get("/v1/users/me", headers=account[0]).status_code == 200
    assert client.get("/v1/onboarding/me", headers=account[0]).status_code == 200
    with SessionLocal() as db:
        assert db.query(WorkoutWeekPlan).filter_by(user_id=account[1]).count() == 0


def test_denied_force_does_not_consume_generation_quota(account, monkeypatch):
    import app.api.v1.workouts.router as routes
    deny(account)
    calls = []
    monkeypatch.setattr(routes, "check_force_regeneration", lambda _: calls.append("quota"))
    monkeypatch.setattr(routes, "_check_and_increment_force_regen", lambda *_: calls.append("daily"))
    for path, method in (("/v1/workouts/week?force=true", client.get), ("/v1/workouts/next?force=true", client.post)):
        response = method(path, headers=account[0])
        assert response.status_code == 403, response.text
    assert calls == []


def test_stale_stored_weeks_and_preference_edits_preserve_data_after_lapse(account):
    week(account)
    week(account, 1)
    with SessionLocal() as db:
        plans = db.query(WorkoutWeekPlan).filter_by(user_id=account[1]).all()
        for plan in plans:
            plan.plan_json = {**plan.plan_json, "preferencesRefreshRequired": True, "onboardingWorkoutContractVersion": -1}
        db.commit()
        before = {str(plan.week_start_date): copy.deepcopy(plan.plan_json) for plan in plans}
    deny(account)
    assert client.get("/v1/workouts/week", headers=account[0]).status_code == 200
    # A real training preference change must save without deleting future weeks.
    response = client.patch("/v1/users/me/training-preferences", headers=account[0], json={"fitnessGoal": "general-fitness"})
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        after = {str(plan.week_start_date): plan.plan_json for plan in db.query(WorkoutWeekPlan).filter_by(user_id=account[1]).all()}
        assert after == before


def test_owned_admitted_session_retry_and_logging_survive_expiry(account):
    workout = week(account)["workouts"][0]
    payload = {"workoutDayId": workout["workoutDayId"], "workoutDate": workout["date"], "dayType": workout["dayType"], "clientSessionId": str(uuid4())}
    admitted = client.post("/v1/workouts/sessions", headers=account[0], json=payload)
    assert admitted.status_code == 201, admitted.text
    deny(account)
    repeated = client.post("/v1/workouts/sessions", headers=account[0], json=payload)
    assert repeated.status_code == 200 and repeated.json()["id"] == admitted.json()["id"], repeated.text
    session_id = admitted.json()["id"]
    exercise = workout["exerciseBlocks"][0]["items"][0]["exercise"]["id"]
    saved = client.post(f"/v1/workouts/sessions/{session_id}/sets", headers=account[0], json={"exerciseId": exercise, "setNumber": 1, "reps": 8, "weightKg": 10, "clientOperationId": str(uuid4())})
    assert saved.status_code == 201, saved.text
    completed = client.patch(f"/v1/workouts/sessions/{session_id}/complete", headers=account[0])
    assert completed.status_code == 200, completed.text
    denied = client.post("/v1/workouts/sessions", headers=account[0], json={**payload, "clientSessionId": str(uuid4())})
    assert denied.status_code == 403, denied.text
    assert client.get(f"/v1/workouts/sessions/{session_id}", headers=account[0]).status_code == 200


def test_unpaid_authoring_and_questions_are_guarded_before_side_effects(account, monkeypatch):
    from app.core import workout_template_service as templates, exercise_qa_service as qa
    deny(account)
    calls = []
    monkeypatch.setattr(qa, "count_questions_today", lambda *_: calls.append("quota") or 0)
    with SessionLocal() as db:
        for action in (lambda: templates.create_template(db, account[1], None),
                       lambda: templates.create_custom_exercise(db, account[1], None),
                       lambda: qa.ask_question(db, db.get(User, account[1]), None, "Question")):
            with pytest.raises(SubscriptionAccessError) as error:
                action()
            assert error.value.code == "subscription_required"
            db.rollback()
    assert not calls


def test_cancellation_after_lapse_only_changes_program_metadata(account, monkeypatch):
    from app.core import workout_template_service as templates
    week(account)
    with SessionLocal() as db:
        template = db.query(WorkoutTemplate).first()
        activation = UserProgramActivation(user_id=account[1], template_id=template.id, template_version=template.version,
            status="active", effective_date=date.today(), weekdays=[0, 2], activation_snapshot={}, apply_mode="now",
            client_operation_id=str(uuid4()), request_fingerprint="fixture")
        db.add(activation)
        db.commit()
        activation_id = activation.id
        before = copy.deepcopy(db.query(WorkoutWeekPlan).filter_by(user_id=account[1]).one().plan_json)
    deny(account)
    monkeypatch.setattr(templates, "_replace_program_week_with_preferences", lambda *_args, **_kwargs: pytest.fail("Cancellation generated a week"))
    today = date.today()
    response = client.delete("/v1/workout-templates/active", headers=account[0], params={"weekStart": (today - timedelta(days=today.weekday())).isoformat(), "effectiveDate": today.isoformat()})
    assert response.status_code == 204, response.text
    with SessionLocal() as db:
        assert db.get(UserProgramActivation, activation_id).status == "deactivated"
        assert db.query(WorkoutWeekPlan).filter_by(user_id=account[1]).one().plan_json == before


def test_coach_preserves_legacy_gate_and_accepts_transition_only_when_enforced(account):
    from app.core.coach_service import _is_coach_eligible
    with SessionLocal() as db:
        user = db.get(User, account[1]); user.coach_insights_enabled = True; db.commit()
        assert _is_coach_eligible(db, user.id) is False
        user.created_at = datetime.now(timezone.utc) - timedelta(days=100)
        now = datetime.now(timezone.utc)
        db.add(FeatureFlagConfig(environment=account[2], version=1, subscriptions_purchases_enabled=True,
            subscriptions_enforcement_enabled=True, subscriptions_launch_at=now - timedelta(days=1), launch_locked=True, updated_at=now))
        db.commit()
        assert _is_coach_eligible(db, user.id) is True
        user.coach_insights_enabled = False; db.commit()
        assert _is_coach_eligible(db, user.id) is False


def test_denied_coach_worker_creates_no_guidance(account, monkeypatch):
    from app.core import coach_feed_service as feed, coach_notification_service as worker
    deny(account)
    monkeypatch.setattr(feed, "_progression_candidates", lambda *_: pytest.fail("Generated unpaid guidance"))
    with SessionLocal() as db:
        assert feed.reconcile_feed(db, account[1], date.today()) == 0
        class Preference:
            user_id = account[1]
            reconciliation_revision = 0
        assert worker._enqueue_for_preference(db, Preference(), datetime.now(timezone.utc)) == 0


def test_deletion_queues_provider_erasure_without_retaining_entitlement(account):
    deny(account)
    response = client.delete("/v1/users/me", headers=account[0])
    assert response.status_code == 204, response.text
    with SessionLocal() as db:
        assert db.get(User, account[1]) is None
        assert db.get(SubscriptionState, (account[2], account[1])) is None
        assert db.get(BillingJob, (account[2], account[1], "erase")) is not None


def test_unknown_verification_preserves_existing_weeks_and_settings(account, monkeypatch):
    original = week(account)
    deny(account)
    with SessionLocal() as db:
        state = db.get(SubscriptionState, (account[2], account[1]))
        state.billing_status = "unknown"
        state.verification_status = "unverified"
        state.verified_at = None
        db.commit()
    monkeypatch.setattr(revenuecat_client, "fetch_customer", lambda _: (_ for _ in ()).throw(revenuecat_client.RevenueCatUnavailable("fixture")))
    assert client.get("/v1/workouts/week", headers=account[0]).json() == original
    response = client.patch("/v1/users/me/training-preferences", headers=account[0], json={"fitnessGoal": "general-fitness"})
    assert response.status_code == 200, response.text
    response = client.get("/v1/workouts/week", headers=account[0], params={"weekStart": (date.today() + timedelta(days=21)).isoformat()})
    assert response.status_code == 503 and response.json()["code"] == "subscription_verification_unavailable"
    assert client.get("/v1/workouts/week", headers=account[0]).json() == original


def test_question_denial_precedes_request_and_daily_quotas(account, monkeypatch):
    from app.core import exercise_qa_service as qa
    deny(account)
    monkeypatch.setattr(qa, "count_questions_today", lambda *_: pytest.fail("Unpaid request consumed daily quota"))
    for _ in range(6):
        response = client.post("/v1/exercises/push_up/ask", headers=account[0], json={"question": "How should I do this?"})
        assert response.status_code == 403, response.text
    with SessionLocal() as db:
        db.get(FeatureFlagConfig, account[2]).subscriptions_enforcement_enabled = False
        db.commit()
    monkeypatch.setattr(qa, "count_questions_today", lambda *_: 0)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "fixture-key")
    monkeypatch.setattr(qa, "_call_ai", lambda *_: "Fixture answer")
    assert client.post("/v1/exercises/push_up/ask", headers=account[0], json={"question": "How should I do this?"}).status_code == 200


def test_expired_account_can_disable_coach_consent(account):
    deny(account)
    with SessionLocal() as db:
        db.get(User, account[1]).coach_insights_enabled = True
        db.commit()
    response = client.patch("/v1/users/me/preferences", headers=account[0], json={"coach_insights_enabled": False})
    assert response.status_code == 200, response.text
    assert response.json()["coach_insights_enabled"] is False
    response = client.patch("/v1/users/me/preferences", headers=account[0], json={"coach_insights_enabled": True})
    assert response.status_code == 403


def test_billing_export_is_owned_and_excludes_internal_metadata(account):
    from app.core.export_service import _collections
    deny(account)
    with SessionLocal() as db:
        state = db.get(SubscriptionState, (account[2], account[1]))
        state.product_id = "fixture.monthly"
        db.commit()
        data = {name: list(rows) for name, rows in _collections(db, account[1])}
        assert len(data["subscriptions"]) == 1
        assert data["subscriptions"][0]["product_id"] == "fixture.monthly"
        assert "provider_snapshot_at" not in data["subscriptions"][0]
        assert "management_url" not in data["subscriptions"][0]
        assert not any(name.startswith("billing_") for name in data)
        assert list(dict(_collections(db, str(uuid4())))["subscriptions"]) == []


def test_notification_is_cancelled_when_access_lapses_after_enqueue(account, monkeypatch):
    from app.core import coach_notification_service as worker
    from app.models.coach import CoachFeedItem, CoachNotificationJob, CoachPreference
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        item = CoachFeedItem(user_id=account[1], kind="program_review", priority=50, dedupe_key=str(uuid4()),
            evidence_fingerprint="fixture", title="Fixture", body="Fixture", expires_at=now + timedelta(days=1))
        db.add(item); db.flush()
        db.add(CoachPreference(user_id=account[1], notifications_enabled=True, time_zone="UTC"))
        job = CoachNotificationJob(user_id=account[1], feed_item_id=item.id, job_type="equipment_review",
            scheduled_at=now, local_delivery_date=now.date())
        db.add(job); db.commit(); job_id = job.id
    deny(account)
    monkeypatch.setattr(worker, "_post_json", lambda *_: pytest.fail("Sent unpaid guidance"))
    with SessionLocal() as db:
        assert worker.send_due_jobs(db, now) == 0
        assert db.get(CoachNotificationJob, job_id).status == "cancelled"


def test_coach_discards_ai_result_when_verified_access_expires_in_flight(account, monkeypatch):
    from app.core import coach_feed_service as feed
    from app.models.coach import CoachFeedItem
    deny(account)
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        state = db.get(SubscriptionState, (account[2], account[1]))
        state.billing_status = "active"; state.expires_at = now + timedelta(days=1)
        db.get(User, account[1]).coach_insights_enabled = True
        item = CoachFeedItem(user_id=account[1], kind="recovery", priority=50, dedupe_key=str(uuid4()),
            evidence_fingerprint="fixture", title="Keep the next session steady", body="Follow the next session as written and skip extra work.",
            detail="This is training guidance, not medical advice.", ai_status="pending", expires_at=now + timedelta(days=1),
            protected_facts={"recommendation": "keep_next_session_as_written", "requiredText": ["next session", "as written"]})
        db.add(item); db.commit(); item_id = item.id
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "fixture-key")
    def expire_then_return(choices):
        with SessionLocal() as other:
            state = other.get(SubscriptionState, (account[2], account[1]))
            state.billing_status = "none"; state.expires_at = None
            state.verified_at = datetime.now(timezone.utc)
            other.commit()
        return {"items": [{"id": choices[0]["id"], "variant": "direct"}]}
    monkeypatch.setattr(feed, "_call_coach_feed_ai", expire_then_return)
    with SessionLocal() as db:
        assert feed.enrich_pending_items(db, account[1]) == 1
        db.expire_all()
        item = db.get(CoachFeedItem, item_id)
        assert item.ai_status == "not_eligible" and item.ai_title is None and item.ai_claim_token is None


@pytest.mark.parametrize("operation", ["session", "week"])
def test_schedule_guard_locks_configuration_before_user(account, operation):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import select, text
    from app.core import workout_logging_service as logging, workout_week_service as weeks
    from app.core.feature_flag_service import lock_feature_flag_configuration
    original = week(account)
    with SessionLocal() as db:
        db.add(FeatureFlagConfig(environment=account[2], version=1, updated_at=datetime.now(timezone.utc)))
        db.commit()
    activity = f"guard-lock-{uuid4().hex}"
    def schedule():
        with SessionLocal() as db:
            db.execute(text("SELECT set_config('application_name', :name, true)"), {"name": activity})
            if operation == "session":
                workout = original["workouts"][0]
                logging.create_session(db, account[1], workout["workoutDayId"], date.fromisoformat(workout["date"]), workout["dayType"], str(uuid4()))
            else:
                weeks.get_or_create_week_plan(db, user_id=account[1])
            db.commit()
    with SessionLocal() as admin, ThreadPoolExecutor(max_workers=1) as pool:
        admin.execute(select(FeatureFlagConfig).where(FeatureFlagConfig.environment == account[2]).with_for_update())
        future = pool.submit(schedule)
        try:
            deadline = time.monotonic() + 5
            with SessionLocal() as observer:
                while time.monotonic() < deadline:
                    waiting = observer.execute(text("SELECT wait_event_type FROM pg_stat_activity WHERE application_name=:name"), {"name": activity}).scalar()
                    observer.rollback()
                    if waiting == "Lock":
                        break
                    time.sleep(0.01)
                else:
                    pytest.fail("Schedule request did not wait for configuration")
            # Admin targeting takes config then a shared user lock. The waiting
            # schedule must not already own the user lock in the reverse order.
            admin.execute(select(User.id).where(User.id == account[1]).with_for_update(read=True, nowait=True)).one()
        finally:
            admin.rollback()
        future.result(timeout=10)


def test_lapse_preserves_enriched_history_but_opt_out_clears_it(account, monkeypatch):
    from app.core import coach_feed_service as feed
    from app.models.coach import CoachFeedItem
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.get(User, account[1]).coach_insights_enabled = True
        item = CoachFeedItem(user_id=account[1], kind="recovery", priority=50, dedupe_key=str(uuid4()),
            evidence_fingerprint="fixture", title="Saved title", body="Saved body", ai_status="enriched",
            ai_title="Enriched history", ai_body="Retained guidance", expires_at=now + timedelta(days=1))
        db.add(item); db.commit(); item_id = item.id
    deny(account)
    monkeypatch.setattr(feed, "_call_coach_feed_ai", lambda *_: pytest.fail("Generated new guidance after lapse"))
    with SessionLocal() as db:
        assert feed.enrich_pending_items(db, account[1]) == 0
        db.expire_all()
        item = db.get(CoachFeedItem, item_id)
        assert item.ai_status == "enriched" and item.ai_body == "Retained guidance"
        assert feed.get_item(db, account[1], item_id).body == "Retained guidance"
        from app.core.export_service import _coach_items
        assert next(value for value in _coach_items(db, account[1]) if value["id"] == item_id)["body"] == "Retained guidance"
    with SessionLocal() as db:
        db.get(FeatureFlagConfig, account[2]).subscriptions_enforcement_enabled = False
        db.commit()
        assert feed.enrich_pending_items(db, account[1]) == 0
        db.expire_all()
        item = db.get(CoachFeedItem, item_id)
        assert item.ai_status == "enriched" and item.ai_body == "Retained guidance"
        assert feed.get_item(db, account[1], item_id).body == "Retained guidance"
        from app.core.export_service import _coach_items
        assert next(value for value in _coach_items(db, account[1]) if value["id"] == item_id)["body"] == "Retained guidance"
    assert client.patch("/v1/users/me/preferences", headers=account[0], json={"coach_insights_enabled": False}).status_code == 200
    with SessionLocal() as db:
        item = db.get(CoachFeedItem, item_id)
        assert item.ai_title is None and item.ai_body is None


def test_program_activation_retry_recovers_after_lapse_without_generating(account):
    from test_workout_templates import _activate
    body, response = _activate(account[0])
    assert response.status_code == 200, response.text
    original = response.json()
    deny(account)
    replay = client.post("/v1/workout-templates/system-minimal-equipment/activate", headers=account[0], json=body)
    assert replay.status_code == 200 and replay.json() == original, replay.text
    denied = client.post("/v1/workout-templates/system-minimal-equipment/activate", headers=account[0], json={**body, "clientOperationId": str(uuid4())})
    assert denied.status_code == 403, denied.text
    assert client.get("/v1/workout-templates/active", headers=account[0]).status_code == 200
    assert client.get("/v1/workouts/week", headers=account[0]).json() == original["weekPlan"]
    missing = client.get("/v1/workouts/week", headers=account[0], params={"weekStart": (date.today() + timedelta(days=21)).isoformat()})
    assert missing.status_code == 403


def test_workout_mutation_routes_deny_without_altering_stored_plan(account):
    original = week(account)
    workout = original["workouts"][0]
    base = {"weekStart": original["weekStart"], "effectiveDate": date.today().isoformat()}
    deny(account)
    actions = [
        (client.post, "/v1/workouts/week/skip", {"workoutDayId": workout["workoutDayId"]}),
        (client.patch, "/v1/workouts/week/duration", {"workoutDayId": workout["workoutDayId"], "durationMinutes": 60}),
        (client.patch, "/v1/workouts/week/planning", {**base, "workoutDayId": workout["workoutDayId"], "planningMode": "time_budget", "timeBudgetMinutes": 60}),
        (client.post, "/v1/workouts/week/apply-planning-preference", base),
        (client.post, "/v1/workouts/week/exercises", {**base, "workoutDayId": workout["workoutDayId"], "exerciseId": "push_up", "expectedExerciseIds": []}),
    ]
    for method, path, payload in actions:
        response = method(path, headers=account[0], json=payload)
        assert response.status_code == 403 and response.json()["code"] == "subscription_required", (path, response.text)
    assert client.get("/v1/workouts/week", headers=account[0]).json() == original


def test_bounded_worker_consumes_denied_dirty_marker_without_starving_next_account(account, monkeypatch):
    from app.core import coach_notification_service as worker
    from app.models.coach import CoachPreference
    deny(account)
    now = datetime.now(timezone.utc)
    response = client.post("/v1/auth/signup", json={"email": f"worker-{uuid4().hex}@example.com", "password": "StrongPass123", "preferred_name": "Worker", "legalAcceptance": LEGAL_ACCEPTANCE})
    assert response.status_code == 201
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    next_user = client.get("/v1/users/me", headers=headers).json()["id"]
    with SessionLocal() as db:
        for user_id, when in [(account[1], now - timedelta(minutes=2)), (next_user, now - timedelta(minutes=1))]:
            db.add(CoachPreference(user_id=user_id, notifications_enabled=False, time_zone="UTC", reconciliation_requested_at=when, reconciliation_revision=1, reconciled_revision=0))
        db.add(SubscriptionState(environment=account[2], user_id=next_user, billing_status="active", verification_status="verified", verified_at=now, expires_at=now + timedelta(days=1)))
        db.commit()
    claim = worker._claim_next_preference
    with SessionLocal() as db:
        unrelated = {row[0] for row in db.query(CoachPreference.user_id) if row[0] not in {account[1], next_user}}
    def claim_accounts(db, **kwargs):
        kwargs["excluded_user_ids"] = kwargs["excluded_user_ids"] | unrelated
        return claim(db, **kwargs)
    monkeypatch.setattr(worker, "_claim_next_preference", claim_accounts)
    seen = []
    def reconcile(db, user_id, *_args, **_kwargs):
        seen.append(user_id)
        pref = db.get(CoachPreference, user_id)
        pref.reconciled_revision = pref.reconciliation_revision
        pref.reconciliation_requested_at = None
    monkeypatch.setattr(worker, "reconcile_feed", reconcile)
    with SessionLocal() as db:
        assert worker.enqueue_due_jobs(db, now, limit=1) == 0
        assert db.get(CoachPreference, account[1]).reconciliation_requested_at is None
        assert worker.enqueue_due_jobs(db, now + timedelta(seconds=1), limit=1) == 0
        assert seen == [next_user]
