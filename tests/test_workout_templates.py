from datetime import date, datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from threading import Event
import uuid
from typing import Optional
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.v1.workout_templates import router as workout_template_router
from app.core import workout_template_service
from app.core.database import SessionLocal
from app.main import app
from app.models.user import User
from app.models.workout_session import WorkoutSession
from app.models.workout_template import ExerciseAlias, UserProgramActivation
from conftest import LEGAL_ACCEPTANCE


client = TestClient(app)


def _freeze_template_service_today(value: date):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            current = cls(value.year, value.month, value.day, 12, tzinfo=timezone.utc)
            return current if tz is None else current.astimezone(tz)

    return patch("app.core.workout_template_service.datetime", FrozenDateTime)


def _signup(prefix: str, *, equipment: Optional[list[str]] = None) -> tuple[dict[str, str], str]:
    email = f"templates_{prefix}_{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/v1/auth/signup",
        json={
            "email": email,
            "password": "StrongPass123",
            "preferred_name": "Program",
            "legalAcceptance": LEGAL_ACCEPTANCE,
        },
    )
    assert response.status_code == 201
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    if equipment is not None:
        onboarding = client.post(
            "/v1/onboarding/me",
            headers=headers,
            json={"data": {"selectedEquipment": equipment}, "is_complete": True},
        )
        assert onboarding.status_code == 200
    return headers, email


def _payload(name: str = "My Program", exercise_id: str = "push_up") -> dict:
    return {
        "name": name,
        "description": "A private repeating program.",
        "goal": "general-fitness",
        "level": "beginner",
        "durationMinutes": 30,
        "aliases": ["home plan"],
        "days": [
            {
                "title": "Day One",
                "dayType": "full_body",
                "exercises": [
                    {
                        "exerciseId": exercise_id,
                        "blockType": "main",
                        "sets": 3,
                        "repsMin": 8,
                        "repsMax": 12,
                        "restSeconds": 60,
                    }
                ],
            }
        ],
    }


def _three_day_payload(name: str = "Three Day Program") -> dict:
    payload = _payload(name)
    payload["days"] = [
        {
            **payload["days"][0],
            "title": title,
            "dayType": f"day_{position + 1}",
        }
        for position, title in enumerate(("First Day", "Middle Day", "Final Day"))
    ]
    return payload


def test_template_catalog_requires_auth_and_contains_deterministic_system_programs():
    assert client.get("/v1/workout-templates").status_code == 401
    headers, _ = _signup("catalog")
    response = client.get("/v1/workout-templates?scope=system", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert {item["id"] for item in body["items"]} == {
        "system-full-body-foundation",
        "system-upper-lower-strength",
        "system-push-pull-legs",
        "system-beginner-strength",
        "system-minimal-equipment",
    }
    assert all(item["frequency"] >= 1 and item["equipmentIds"] is not None for item in body["items"])
    assert all("missingEquipmentIds" in item for item in body["items"])
    assert all(
        item["equipmentCompatible"] == (not item["missingEquipmentIds"])
        for item in body["items"]
    )
    detail = client.get(
        "/v1/workout-templates/system-minimal-equipment", headers=headers
    )
    assert detail.status_code == 200
    assert "missingEquipmentIds" in detail.json()
    assert "equipmentCompatible" in detail.json()
    search = client.get(
        "/v1/explore/search", headers=headers, params={"query": "minimal"}
    )
    assert search.status_code == 200
    assert all(
        "missingEquipmentIds" in item and "equipmentCompatible" in item
        for item in search.json()["programs"]
    )


def test_private_template_crud_is_owned_versioned_and_system_templates_are_read_only():
    owner, _ = _signup("private_owner")
    other, _ = _signup("private_other")
    created = client.post("/v1/workout-templates", headers=owner, json=_payload())
    assert created.status_code == 201
    template = created.json()
    assert template["scope"] == "private"
    assert template["frequency"] == 1
    assert client.get(f"/v1/workout-templates/{template['id']}", headers=other).status_code == 404

    update = _payload("Renamed Program") | {"version": template["version"]}
    changed = client.put(
        f"/v1/workout-templates/{template['id']}", headers=owner, json=update
    )
    assert changed.status_code == 200
    assert changed.json()["name"] == "Renamed Program"
    assert changed.json()["version"] == 2
    assert client.put(
        f"/v1/workout-templates/{template['id']}", headers=owner, json=update
    ).status_code == 409
    assert client.delete(
        "/v1/workout-templates/system-full-body-foundation", headers=owner
    ).status_code == 409
    assert client.delete(
        f"/v1/workout-templates/{template['id']}", headers=owner
    ).status_code == 204
    assert client.get(f"/v1/workout-templates/{template['id']}", headers=owner).status_code == 404


def test_template_and_custom_exercise_names_reject_whitespace_only_values():
    headers, _ = _signup("blank_names")
    blank_program = _payload()
    blank_program["name"] = "   "
    assert client.post(
        "/v1/workout-templates", headers=headers, json=blank_program
    ).status_code == 422


def test_custom_exercises_use_the_canonical_type_and_muscle_vocabulary():
    headers, _ = _signup("exercise_vocab")
    base = {
        "name": "Vocabulary Test",
        "exerciseType": "strength",
        "primaryMuscle": "chest",
        "secondaryMuscles": ["triceps"],
        "equipmentIds": [],
    }
    assert client.post(
        "/v1/users/me/exercises", headers=headers, json=base
    ).status_code == 201
    assert client.post(
        "/v1/users/me/exercises",
        headers=headers,
        json=base | {"exerciseType": "mobility"},
    ).status_code == 422
    assert client.post(
        "/v1/users/me/exercises",
        headers=headers,
        json=base | {"primaryMuscle": "upper-body"},
    ).status_code == 422
    assert client.post(
        "/v1/users/me/exercises",
        headers=headers,
        json=base | {"secondaryMuscles": ["triceps", "triceps"]},
    ).status_code == 422
    blank_day = _payload()
    blank_day["days"][0]["title"] = "   "
    assert client.post(
        "/v1/workout-templates", headers=headers, json=blank_day
    ).status_code == 422
    blank_alias = _payload()
    blank_alias["aliases"] = ["   "]
    assert client.post(
        "/v1/workout-templates", headers=headers, json=blank_alias
    ).status_code == 422
    assert client.post(
        "/v1/users/me/exercises",
        headers=headers,
        json={
            "name": "   ",
            "exerciseType": "strength",
            "primaryMuscle": "chest",
            "secondaryMuscles": [],
            "equipmentIds": [],
        },
    ).status_code == 422


def test_clone_is_an_independent_private_copy():
    headers, _ = _signup("clone")
    cloned = client.post(
        "/v1/workout-templates/system-minimal-equipment/clone", headers=headers
    )
    assert cloned.status_code == 201
    body = cloned.json()
    assert body["scope"] == "private"
    assert body["id"] != "system-minimal-equipment"
    assert body["name"].endswith("Copy")
    assert body["days"][0]["id"] != "system-minimal-equipment-day-0"


def test_template_rejects_duplicate_and_cross_account_custom_exercises():
    owner, _ = _signup("custom_owner")
    other, _ = _signup("custom_other")
    custom = client.post(
        "/v1/users/me/exercises",
        headers=owner,
        json={
            "name": "Private Press",
            "exerciseType": "bodyweight",
            "primaryMuscle": "chest",
            "secondaryMuscles": ["triceps"],
            "equipmentIds": [],
            "instructions": "Press safely.",
        },
    )
    assert custom.status_code == 201
    exercise_id = custom.json()["id"]
    assert client.get(f"/v1/exercises/{exercise_id}", headers=other).status_code == 404
    assert client.post(
        "/v1/workout-templates", headers=other, json=_payload(exercise_id=exercise_id)
    ).status_code == 422
    duplicate = _payload()
    duplicate["days"][0]["exercises"].append(dict(duplicate["days"][0]["exercises"][0]))
    assert client.post("/v1/workout-templates", headers=owner, json=duplicate).status_code == 422


def test_custom_exercise_crud_search_and_account_isolation():
    owner, _ = _signup("exercise_owner")
    other, _ = _signup("exercise_other")
    payload = {
        "name": "Zegarra Press",
        "exerciseType": "strength",
        "primaryMuscle": "chest",
        "secondaryMuscles": ["triceps"],
        "equipmentIds": [],
        "instructions": "Controlled reps.",
    }
    created = client.post("/v1/users/me/exercises", headers=owner, json=payload)
    assert created.status_code == 201
    exercise_id = created.json()["id"]
    assert created.json()["source"] == "custom"
    assert created.json()["is_editable"] is True
    assert any(
        item["id"] == exercise_id
        for item in client.get("/v1/explore/search?query=zegara", headers=owner).json()["exercises"]
    )
    assert not any(
        item["id"] == exercise_id
        for item in client.get("/v1/explore/search?query=zegara", headers=other).json()["exercises"]
    )
    changed = client.put(
        f"/v1/users/me/exercises/{exercise_id}",
        headers=owner,
        json=payload | {"name": "Zegarra Row", "primaryMuscle": "back"},
    )
    assert changed.status_code == 200
    assert changed.json()["name"] == "Zegarra Row"
    assert client.delete(f"/v1/users/me/exercises/{exercise_id}", headers=owner).status_code == 204
    assert client.get(f"/v1/exercises/{exercise_id}", headers=owner).status_code == 404


def test_combined_search_supports_aliases_and_typo_tolerance():
    headers, _ = _signup("search")
    alias = client.get("/v1/explore/search?query=rdl", headers=headers)
    assert alias.status_code == 200
    assert any(item["id"] == "romanian_deadlift" for item in alias.json()["exercises"])
    exact_alias = client.get("/v1/explore/search?query=bench", headers=headers)
    assert exact_alias.status_code == 200
    assert exact_alias.json()["exercises"][0]["id"] == "bench_press"
    typo = client.get("/v1/explore/search?query=benhc", headers=headers)
    assert typo.status_code == 200
    assert typo.json()["exercises"][0]["id"] == "bench_press"
    assert client.get("/v1/explore/search?query=x", headers=headers).status_code == 422


def test_exercise_search_applies_exact_prefix_and_substring_tiers_to_aliases():
    headers, _ = _signup("exercise_alias_rank")
    term = f"rank{uuid.uuid4().hex[:10]}"
    with SessionLocal() as db:
        db.add_all(
            [
                ExerciseAlias(
                    exercise_id="bench_press", alias=term
                ),
                ExerciseAlias(
                    exercise_id="overhead_press", alias=f"{term} progression"
                ),
                ExerciseAlias(
                    exercise_id="squat", alias=f"my {term} movement"
                ),
            ]
        )
        db.commit()
    response = client.get(
        "/v1/explore/search", headers=headers, params={"query": term}
    )
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["exercises"][:3]] == [
        "bench_press",
        "overhead_press",
        "squat",
    ]


def test_catalog_filters_and_recommendations_use_training_preferences():
    headers, _ = _signup("filters")
    preferences = client.patch(
        "/v1/users/me/training-preferences",
        headers=headers,
        json={
            "fitnessGoal": "get-stronger",
            "experienceLevel": "intermediate",
            "workoutFrequency": "4-days",
        },
    )
    assert preferences.status_code == 200
    filtered = client.get(
        "/v1/workout-templates?goal=get-stronger&level=intermediate&frequency=4&maxDuration=45",
        headers=headers,
    )
    assert filtered.status_code == 200
    assert [item["id"] for item in filtered.json()["items"]] == [
        "system-upper-lower-strength"
    ]
    recommended = client.get(
        "/v1/workout-templates?recommended=true", headers=headers
    )
    assert recommended.status_code == 200
    assert recommended.json()["items"][0]["id"] == "system-upper-lower-strength"
    assert "Matches your training goal" in recommended.json()["items"][0]["matchReasons"]


def test_catalog_filters_counts_orders_and_paginates_before_serialization():
    headers, _ = _signup("catalog_page")
    first_page = client.get(
        "/v1/workout-templates?scope=system&limit=2&offset=0", headers=headers
    )
    second_page = client.get(
        "/v1/workout-templates?scope=system&limit=2&offset=2", headers=headers
    )
    assert first_page.status_code == second_page.status_code == 200
    assert first_page.json()["total"] == second_page.json()["total"] == 5
    assert len(first_page.json()["items"]) == len(second_page.json()["items"]) == 2
    assert {item["id"] for item in first_page.json()["items"]}.isdisjoint(
        item["id"] for item in second_page.json()["items"]
    )

    compatible = client.get(
        "/v1/workout-templates?scope=system&equipment=dumbbells&limit=100",
        headers=headers,
    )
    assert compatible.status_code == 200
    assert compatible.json()["total"] == len(compatible.json()["items"])
    assert all(
        set(item["equipmentIds"]) <= {"dumbbells"}
        for item in compatible.json()["items"]
    )


def test_program_search_ranks_exact_alias_before_prefix_and_substring():
    headers, _ = _signup("program_search_rank")
    exact_alias = _payload("A Distant Name")
    exact_alias["aliases"] = ["power plan"]
    prefix = _payload("Power Planner")
    prefix["aliases"] = []
    substring = _payload("My Power Plan")
    substring["aliases"] = []
    for payload in [substring, prefix, exact_alias]:
        assert client.post(
            "/v1/workout-templates", headers=headers, json=payload
        ).status_code == 201
    results = client.get(
        "/v1/workout-templates?scope=private&query=power%20plan", headers=headers
    )
    assert results.status_code == 200
    assert [item["name"] for item in results.json()["items"][:3]] == [
        "A Distant Name",
        "Power Planner",
        "My Power Plan",
    ]


def test_archived_custom_exercise_makes_referencing_template_non_activatable():
    headers, _ = _signup("archived_reference")
    custom = client.post(
        "/v1/users/me/exercises",
        headers=headers,
        json={
            "name": "Archived Move",
            "exerciseType": "bodyweight",
            "primaryMuscle": "chest",
            "secondaryMuscles": [],
            "equipmentIds": [],
        },
    ).json()
    template = client.post(
        "/v1/workout-templates", headers=headers, json=_payload(exercise_id=custom["id"])
    )
    assert template.status_code == 201
    assert client.delete(
        f"/v1/users/me/exercises/{custom['id']}", headers=headers
    ).status_code == 204
    monday = date.today() - timedelta(days=date.today().weekday())
    preview = client.post(
        f"/v1/workout-templates/{template.json()['id']}/activation-preview",
        headers=headers,
        json={
            "weekStart": monday.isoformat(),
            "effectiveDate": monday.isoformat(),
            "weekdays": [0],
        },
    )
    assert preview.status_code == 422
    assert "no longer available" in preview.json()["detail"]


def _activate(headers: dict[str, str], template_id: str = "system-minimal-equipment"):
    equipment = client.post(
        "/v1/onboarding/me",
        headers=headers,
        json={
            "data": {"selectedEquipment": ["dumbbells", "pull_up_bar", "dip_bar"]},
            "is_complete": True,
        },
    )
    assert equipment.status_code == 200
    monday = date.today() - timedelta(days=date.today().weekday())
    preview_payload = {
        "weekStart": monday.isoformat(),
        "effectiveDate": monday.isoformat(),
        "weekdays": [0, 2, 4],
    }
    preview = client.post(
        f"/v1/workout-templates/{template_id}/activation-preview",
        headers=headers,
        json=preview_payload,
    )
    assert preview.status_code == 200
    substitutions = {
        item["templateExerciseId"]: item["suggestedExerciseId"]
        for item in preview.json()["equipmentConflicts"]
        if item["suggestedExerciseId"]
    }
    operation_id = str(uuid.uuid4())
    body = preview_payload | {
        "templateVersion": 1,
        "substitutions": substitutions,
        "applyMode": "now",
        "clientOperationId": operation_id,
    }
    return body, client.post(
        f"/v1/workout-templates/{template_id}/activate", headers=headers, json=body
    )


def _preview_substitutions(headers: dict[str, str], template_id: str, payload: dict) -> dict:
    preview = client.post(
        f"/v1/workout-templates/{template_id}/activation-preview",
        headers=headers,
        json=payload,
    )
    assert preview.status_code == 200
    return {
        item["templateExerciseId"]: item["suggestedExerciseId"]
        for item in preview.json()["equipmentConflicts"]
        if item["suggestedExerciseId"]
    }


def test_activation_is_idempotent_stamps_week_and_locks_duration():
    headers, _ = _signup("activate")
    body, first = _activate(headers)
    assert first.status_code == 200
    result = first.json()
    activation_id = result["activeProgram"]["id"]
    assert result["weekPlan"]["programActivationId"] == activation_id
    assert all(
        workout["programActivationId"] == activation_id
        and workout["templateDayId"]
        for workout in result["weekPlan"]["workouts"]
    )
    replay = client.post(
        "/v1/workout-templates/system-minimal-equipment/activate",
        headers=headers,
        json=body,
    )
    assert replay.status_code == 200
    assert replay.json()["activeProgram"]["id"] == activation_id
    workout = replay.json()["weekPlan"]["workouts"][0]
    duration = client.patch(
        "/v1/workouts/week/duration",
        headers=headers,
        json={
            "workoutDayId": workout["workoutDayId"],
            "durationMinutes": 35,
            "weekStart": replay.json()["weekPlan"]["weekStart"],
        },
    )
    assert duration.status_code == 409
    assert duration.json()["detail"]["code"] == "program_duration_locked"


def test_activation_operation_id_is_bound_to_the_canonical_request():
    headers, _ = _signup("operation_fingerprint")
    body, first = _activate(headers)
    assert first.status_code == 200
    original = first.json()
    monday = date.fromisoformat(body["weekStart"])
    changes = [
        (
            "system-push-pull-legs",
            body,
        ),
        (
            "system-minimal-equipment",
            body | {"templateVersion": body["templateVersion"] + 1},
        ),
        (
            "system-minimal-equipment",
            body | {"weekdays": [1, 3, 5]},
        ),
        (
            "system-minimal-equipment",
            body | {"substitutions": {"different-occurrence": "push_up"}},
        ),
        (
            "system-minimal-equipment",
            body | {"effectiveDate": (monday + timedelta(days=1)).isoformat()},
        ),
        (
            "system-minimal-equipment",
            body
            | {
                "effectiveDate": (monday + timedelta(days=7)).isoformat(),
                "applyMode": "next-week",
            },
        ),
    ]
    for template_id, changed in changes:
        response = client.post(
            f"/v1/workout-templates/{template_id}/activate",
            headers=headers,
            json=changed,
        )
        assert response.status_code == 409
        assert "different activation request" in response.json()["detail"]

    exact = client.post(
        "/v1/workout-templates/system-minimal-equipment/activate",
        headers=headers,
        json=body,
    )
    assert exact.status_code == 200
    assert exact.json() == original


def test_concurrent_duplicate_activation_operation_replays_one_canonical_result():
    headers, _ = _signup("activation_race", equipment=[])
    template_one = client.post(
        "/v1/workout-templates", headers=headers, json=_payload("Race One")
    ).json()
    template_two = client.post(
        "/v1/workout-templates", headers=headers, json=_payload("Race Two")
    ).json()
    monday = date.today() - timedelta(days=date.today().weekday())
    operation_id = str(uuid.uuid4())

    def activate(template: dict):
        return client.post(
            f"/v1/workout-templates/{template['id']}/activate",
            headers=headers,
            json={
                "weekStart": monday.isoformat(),
                "effectiveDate": monday.isoformat(),
                "weekdays": [0],
                "templateVersion": template["version"],
                "substitutions": {},
                "applyMode": "now",
                "clientOperationId": operation_id,
            },
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(activate, (template_one, template_two)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert "different activation request" in conflict.json()["detail"]


def test_preview_uses_effective_week_and_excludes_occupied_dates():
    headers, _ = _signup("preview_effective")
    _, activated = _activate(headers)
    monday = date.fromisoformat(activated.json()["weekPlan"]["weekStart"])
    next_monday = monday + timedelta(days=7)
    next_week = client.get(
        "/v1/workouts/week",
        headers=headers,
        params={"weekStart": next_monday.isoformat()},
    ).json()
    occupied = next_week["workouts"][0]
    started = client.post(
        "/v1/workouts/sessions",
        headers=headers,
        json={
            "clientSessionId": str(uuid.uuid4()),
            "workoutDayId": occupied["workoutDayId"],
            "workoutDate": occupied["date"],
            "dayType": occupied["dayType"],
        },
    )
    assert started.status_code == 201
    preview = client.post(
        "/v1/workout-templates/system-minimal-equipment/activation-preview",
        headers=headers,
        json={
            "weekStart": monday.isoformat(),
            "effectiveDate": next_monday.isoformat(),
            "weekdays": [0, 2, 4],
        },
    )
    assert preview.status_code == 200
    impact = preview.json()["scheduleImpact"]
    assert occupied["workoutDayId"] in impact["preservedWorkoutDayIds"]
    assert occupied["date"] not in impact["scheduledDates"]
    assert impact["scheduledDates"] == [
        (next_monday + timedelta(days=2)).isoformat(),
        (next_monday + timedelta(days=4)).isoformat(),
    ]
    installed = client.post(
        "/v1/workout-templates/system-minimal-equipment/activate",
        headers=headers,
        json={
            "weekStart": monday.isoformat(),
            "effectiveDate": next_monday.isoformat(),
            "weekdays": [0, 2, 4],
            "templateVersion": 1,
            "substitutions": {},
            "applyMode": "next-week",
            "clientOperationId": str(uuid.uuid4()),
        },
    )
    assert installed.status_code == 200
    installed_body = installed.json()
    activation_id = installed_body["activeProgram"]["id"]
    assert [
        workout["date"]
        for workout in installed_body["weekPlan"]["workouts"]
        if workout.get("programActivationId") == activation_id
    ] == impact["scheduledDates"]


def test_preview_hydrates_alternatives_and_activation_rejects_tampered_substitutions():
    headers, _ = _signup("substitution_validation", equipment=[])
    template = client.post(
        "/v1/workout-templates",
        headers=headers,
        json=_payload(exercise_id="hanging_leg_raise"),
    ).json()
    monday = date.today() - timedelta(days=date.today().weekday())
    request = {
        "weekStart": monday.isoformat(),
        "effectiveDate": monday.isoformat(),
        "weekdays": [0],
    }
    preview = client.post(
        f"/v1/workout-templates/{template['id']}/activation-preview",
        headers=headers,
        json=request,
    )
    assert preview.status_code == 200
    conflict = preview.json()["equipmentConflicts"][0]
    assert conflict["suggestedExercise"] is not None
    assert conflict["suggestedExercise"]["id"] == conflict["suggestedExerciseId"]
    assert [item["id"] for item in conflict["alternatives"]] == conflict[
        "alternativeExerciseIds"
    ]
    occurrence_id = conflict["templateExerciseId"]
    common = request | {
        "templateVersion": template["version"],
        "applyMode": "now",
        "clientOperationId": str(uuid.uuid4()),
    }
    extra = client.post(
        f"/v1/workout-templates/{template['id']}/activate",
        headers=headers,
        json=common
        | {
            "substitutions": {
                occurrence_id: conflict["suggestedExerciseId"],
                "not-a-preview-conflict": "push_up",
            }
        },
    )
    assert extra.status_code == 422
    assert "only target" in extra.json()["detail"]
    wrong_muscle = client.post(
        f"/v1/workout-templates/{template['id']}/activate",
        headers=headers,
        json=common
        | {
            "clientOperationId": str(uuid.uuid4()),
            "substitutions": {occurrence_id: "push_up"},
        },
    )
    assert wrong_muscle.status_code == 422
    assert "primary muscle" in wrong_muscle.json()["detail"]

    duplicate_payload = _payload(exercise_id="hanging_leg_raise")
    duplicate_payload["days"][0]["exercises"].append(
        {
            "exerciseId": "plank",
            "blockType": "accessory",
            "sets": 3,
            "repsMin": 20,
            "repsMax": 40,
            "restSeconds": 60,
        }
    )
    duplicate_template = client.post(
        "/v1/workout-templates", headers=headers, json=duplicate_payload
    ).json()
    duplicate_preview = client.post(
        f"/v1/workout-templates/{duplicate_template['id']}/activation-preview",
        headers=headers,
        json=request,
    ).json()
    duplicate_conflict_id = duplicate_preview["equipmentConflicts"][0][
        "templateExerciseId"
    ]
    duplicate = client.post(
        f"/v1/workout-templates/{duplicate_template['id']}/activate",
        headers=headers,
        json=common
        | {
            "templateVersion": duplicate_template["version"],
            "clientOperationId": str(uuid.uuid4()),
            "substitutions": {duplicate_conflict_id: "plank"},
        },
    )
    assert duplicate.status_code == 422
    assert "duplicate" in duplicate.json()["detail"]


def test_program_skip_rotates_template_day_and_deactivation_restores_generator():
    headers, _ = _signup("skip")
    _, activated = _activate(headers)
    week = activated.json()["weekPlan"]
    first = week["workouts"][0]
    skipped = client.post(
        "/v1/workouts/week/skip",
        headers=headers,
        json={"workoutDayId": first["workoutDayId"], "weekStart": week["weekStart"]},
    )
    assert skipped.status_code == 200
    assert len(skipped.json()["workouts"]) == len(week["workouts"])
    assert [date.fromisoformat(item["date"]).weekday() for item in skipped.json()["workouts"]] == [
        0,
        2,
        4,
    ]
    assert skipped.json()["workouts"][-1]["templateDayId"] == first["templateDayId"]
    deactivated = client.delete(
        "/v1/workout-templates/active",
        headers=headers,
        params={"weekStart": week["weekStart"], "effectiveDate": date.today().isoformat()},
    )
    assert deactivated.status_code == 200
    assert deactivated.json().get("programActivationId") is None
    assert client.get("/v1/workout-templates/active", headers=headers).json() == {
        "current": None,
        "scheduled": None,
    }


def test_activation_preserves_an_in_progress_workout_snapshot():
    headers, _ = _signup("preserve")
    week = client.get("/v1/workouts/week", headers=headers).json()
    workout = week["workouts"][0]
    session = client.post(
        "/v1/workouts/sessions",
        headers=headers,
        json={
            "clientSessionId": str(uuid.uuid4()),
            "workoutDayId": workout["workoutDayId"],
            "workoutDate": workout["date"],
            "dayType": workout["dayType"],
        },
    )
    assert session.status_code == 201
    _, activated = _activate(headers)
    assert activated.status_code == 200
    program_workouts = {
        item["workoutDayId"]: item
        for item in activated.json()["weekPlan"]["workouts"]
        if item.get("programActivationId")
    }
    assert workout["workoutDayId"] in {
        item["workoutDayId"] for item in activated.json()["weekPlan"]["workouts"]
    }
    active = client.get("/v1/workouts/sessions/active", headers=headers).json()
    assert active["id"] == session.json()["id"]
    assert active["workoutSnapshot"]["workoutDayId"] == workout["workoutDayId"]
    changed = client.patch(
        "/v1/workouts/week/duration",
        headers=headers,
        json={
            "workoutDayId": workout["workoutDayId"],
            "durationMinutes": 25,
            "weekStart": week["weekStart"],
        },
    )
    assert changed.status_code == 200
    changed_program_workouts = {
        item["workoutDayId"]: item
        for item in changed.json()["workouts"]
        if item.get("programActivationId")
    }
    assert changed_program_workouts == program_workouts


def test_next_week_activation_prebuilds_future_week_without_ending_current_program():
    headers, email = _signup("next_week")
    _, current = _activate(headers)
    assert current.status_code == 200
    current_id = current.json()["activeProgram"]["id"]
    monday = date.today() - timedelta(days=date.today().weekday())
    next_monday = monday + timedelta(days=7)
    preview_payload = {
        "weekStart": monday.isoformat(),
        "effectiveDate": next_monday.isoformat(),
        "weekdays": [0, 2, 4],
    }
    scheduled = client.post(
        "/v1/workout-templates/system-minimal-equipment/activate",
        headers=headers,
        json=preview_payload
        | {
            "templateVersion": 1,
            "substitutions": {},
            "applyMode": "next-week",
            "clientOperationId": str(uuid.uuid4()),
        },
    )
    assert scheduled.status_code == 200
    scheduled_id = scheduled.json()["activeProgram"]["id"]
    assert scheduled.json()["activeProgram"]["status"] == "scheduled"
    status = client.get("/v1/workout-templates/active", headers=headers).json()
    assert status["current"]["id"] == current_id
    assert status["scheduled"]["id"] == scheduled_id

    next_week = client.get(
        f"/v1/workouts/week?weekStart={next_monday.isoformat()}", headers=headers
    )
    assert next_week.status_code == 200
    assert next_week.json()["programActivationId"] == scheduled_id
    active = client.get("/v1/workout-templates/active", headers=headers).json()
    assert active["current"]["id"] == current_id
    assert active["current"]["status"] == "active"
    assert active["scheduled"]["id"] == scheduled_id

    with SessionLocal() as db:
        user_id = db.query(User.id).filter(User.email == email).scalar()
        stored_before = {
            row.id: row.status
            for row in db.query(UserProgramActivation).filter_by(user_id=user_id)
        }
    sunday = next_monday - timedelta(days=1)
    with _freeze_template_service_today(sunday):
        early = client.get(
            f"/v1/workout-templates/active?effectiveDate={sunday.isoformat()}",
            headers=headers,
        )
        promoted = client.get(
            f"/v1/workout-templates/active?effectiveDate={next_monday.isoformat()}",
            headers=headers,
        )
    assert early.status_code == 200
    assert early.json()["current"]["id"] == current_id
    assert early.json()["scheduled"]["id"] == scheduled_id
    assert promoted.status_code == 200
    assert promoted.json()["current"]["id"] == scheduled_id
    assert promoted.json()["current"]["status"] == "active"
    assert promoted.json()["scheduled"] is None
    with SessionLocal() as db:
        stored_after = {
            row.id: row.status
            for row in db.query(UserProgramActivation).filter_by(user_id=user_id)
        }
    assert stored_after == stored_before == {
        current_id: "active",
        scheduled_id: "scheduled",
    }


def test_supersession_and_deactivation_preserve_stored_historical_program_week():
    headers, _ = _signup("historical_week")
    current_body, current = _activate(headers)
    current_week = current.json()["weekPlan"]
    current_activation_id = current.json()["activeProgram"]["id"]
    monday = date.fromisoformat(current_body["weekStart"])
    next_monday = monday + timedelta(days=7)
    scheduled = client.post(
        "/v1/workout-templates/system-minimal-equipment/activate",
        headers=headers,
        json={
            **current_body,
            "effectiveDate": next_monday.isoformat(),
            "applyMode": "next-week",
            "clientOperationId": str(uuid.uuid4()),
        },
    )
    assert scheduled.status_code == 200
    with _freeze_template_service_today(next_monday):
        promoted = client.get(
            "/v1/workout-templates/active",
            headers=headers,
            params={"effectiveDate": next_monday.isoformat()},
        )
    assert promoted.status_code == 200
    assert promoted.json()["current"]["id"] != current_activation_id

    historical = client.get(
        "/v1/workouts/week",
        headers=headers,
        params={"weekStart": monday.isoformat()},
    )
    assert historical.status_code == 200
    assert historical.json() == current_week

    stopped = client.delete(
        "/v1/workout-templates/active",
        headers=headers,
        params={
            "weekStart": next_monday.isoformat(),
            "effectiveDate": next_monday.isoformat(),
        },
    )
    assert stopped.status_code == 422
    with _freeze_template_service_today(next_monday):
        stopped = client.delete(
            "/v1/workout-templates/active",
            headers=headers,
            params={
                "weekStart": next_monday.isoformat(),
                "effectiveDate": next_monday.isoformat(),
            },
        )
    assert stopped.status_code == 200
    historical_after_stop = client.get(
        "/v1/workouts/week",
        headers=headers,
        params={"weekStart": monday.isoformat()},
    )
    assert historical_after_stop.json() == current_week


def test_preview_and_activation_share_the_same_base_week_validation():
    headers, _ = _signup("schedule_contract")
    monday = date.today() - timedelta(days=date.today().weekday())
    common = {
        "weekStart": monday.isoformat(),
        "weekdays": [0, 2, 4],
    }
    invalid_next_week = {
        **common,
        "effectiveDate": monday.isoformat(),
        "applyMode": "next-week",
    }
    preview = client.post(
        "/v1/workout-templates/system-minimal-equipment/activation-preview",
        headers=headers,
        json=invalid_next_week,
    )
    assert preview.status_code == 422
    activate = client.post(
        "/v1/workout-templates/system-minimal-equipment/activate",
        headers=headers,
        json=invalid_next_week
        | {
            "templateVersion": 1,
            "substitutions": {},
            "clientOperationId": str(uuid.uuid4()),
        },
    )
    assert activate.status_code == 422
    assert activate.json()["detail"] == preview.json()["detail"]

    next_week = {
        **common,
        "effectiveDate": (monday + timedelta(days=7)).isoformat(),
        "applyMode": "next-week",
    }
    assert client.post(
        "/v1/workout-templates/system-minimal-equipment/activation-preview",
        headers=headers,
        json=next_week,
    ).status_code == 200


def test_concurrent_distinct_scheduled_activations_leave_one_revision():
    headers, email = _signup("concurrent_scheduled")
    assert client.post(
        "/v1/onboarding/me",
        headers=headers,
        json={
            "data": {"selectedEquipment": ["dumbbells", "pull_up_bar", "dip_bar"]},
            "is_complete": True,
        },
    ).status_code == 200
    monday = date.today() - timedelta(days=date.today().weekday())
    next_monday = monday + timedelta(days=7)
    preview_payload = {
        "weekStart": monday.isoformat(),
        "effectiveDate": next_monday.isoformat(),
        "weekdays": [0, 2, 4],
        "applyMode": "next-week",
    }
    substitutions = _preview_substitutions(
        headers, "system-minimal-equipment", preview_payload
    )

    def activate(operation_id: str):
        return client.post(
            "/v1/workout-templates/system-minimal-equipment/activate",
            headers=headers,
            json={
                "weekStart": monday.isoformat(),
                "effectiveDate": next_monday.isoformat(),
                "weekdays": [0, 2, 4],
                "templateVersion": 1,
                "substitutions": substitutions,
                "applyMode": "next-week",
                "clientOperationId": operation_id,
            },
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(activate, (str(uuid.uuid4()), str(uuid.uuid4()))))
    assert [response.status_code for response in responses] == [200, 200]
    with SessionLocal() as db:
        from app.models.user import User
        from app.models.workout_template import UserProgramActivation

        user = db.query(User).filter(User.email == email).one()
        scheduled = db.query(UserProgramActivation).filter_by(
            user_id=user.id, status="scheduled"
        ).all()
        assert len(scheduled) == 1


def test_activation_locks_and_reloads_template_before_concurrent_edit():
    headers, _ = _signup("activate_edit_lock", equipment=[])
    template = client.post(
        "/v1/workout-templates", headers=headers, json=_payload("Locked Program")
    ).json()
    monday = date.today() - timedelta(days=date.today().weekday())
    entered_preview = Event()
    release_preview = Event()
    edit_started = Event()
    original_preview = workout_template_service.preview_activation
    original_update = workout_template_router.update_template

    def blocking_preview(*args, **kwargs):
        entered_preview.set()
        assert release_preview.wait(5)
        return original_preview(*args, **kwargs)

    def tracked_update(*args, **kwargs):
        edit_started.set()
        return original_update(*args, **kwargs)

    activation_payload = {
        "weekStart": monday.isoformat(),
        "effectiveDate": monday.isoformat(),
        "weekdays": [0],
        "templateVersion": template["version"],
        "substitutions": {},
        "applyMode": "now",
        "clientOperationId": str(uuid.uuid4()),
    }
    edit_payload = _payload("Edited While Activating") | {
        "version": template["version"]
    }
    with patch.object(
        workout_template_service, "preview_activation", blocking_preview
    ), patch.object(workout_template_router, "update_template", tracked_update):
        with ThreadPoolExecutor(max_workers=2) as executor:
            activation_future = executor.submit(
                client.post,
                f"/v1/workout-templates/{template['id']}/activate",
                headers=headers,
                json=activation_payload,
            )
            assert entered_preview.wait(5)
            edit_future = executor.submit(
                client.put,
                f"/v1/workout-templates/{template['id']}",
                headers=headers,
                json=edit_payload,
            )
            assert edit_started.wait(5)
            try:
                edit_future.result(timeout=0.2)
                edit_was_blocked = False
            except FutureTimeoutError:
                edit_was_blocked = True
            release_preview.set()
            activation = activation_future.result(timeout=5)
            edited = edit_future.result(timeout=5)
    assert edit_was_blocked is True
    assert activation.status_code == 200
    assert activation.json()["activeProgram"]["templateVersion"] == 1
    assert edited.status_code == 200
    assert edited.json()["version"] == 2


def test_program_skip_keeps_protected_dates_and_never_duplicates_dates():
    headers, _ = _signup("skip_protected")
    _, activated = _activate(headers)
    week = activated.json()["weekPlan"]
    monday, wednesday, friday = week["workouts"]
    started = client.post(
        "/v1/workouts/sessions",
        headers=headers,
        json={
            "clientSessionId": str(uuid.uuid4()),
            "workoutDayId": wednesday["workoutDayId"],
            "workoutDate": wednesday["date"],
            "dayType": wednesday["dayType"],
        },
    )
    assert started.status_code == 201
    skipped = client.post(
        "/v1/workouts/week/skip",
        headers=headers,
        json={"workoutDayId": monday["workoutDayId"], "weekStart": week["weekStart"]},
    )
    assert skipped.status_code == 200
    workouts = skipped.json()["workouts"]
    dates = [workout["date"] for workout in workouts]
    assert dates == sorted(set(dates))
    protected = next(
        workout for workout in workouts if workout["workoutDayId"] == wednesday["workoutDayId"]
    )
    assert protected["date"] == wednesday["date"]
    assert next(
        workout for workout in workouts if workout["workoutDayId"] == friday["workoutDayId"]
    )["date"] == friday["date"]
    assert len(workouts) == 2
    assert [date.fromisoformat(item["date"]).weekday() for item in workouts] == [2, 4]
    next_monday = date.fromisoformat(week["weekStart"]) + timedelta(days=7)
    next_week = client.get(
        "/v1/workouts/week",
        headers=headers,
        params={"weekStart": next_monday.isoformat()},
    ).json()
    assert next_week["workouts"][0]["templateDayId"] == monday["templateDayId"]


def test_program_skip_carries_shifted_sequence_into_next_week():
    headers, _ = _signup("skip_carry")
    _, activated = _activate(headers)
    week = activated.json()["weekPlan"]
    first, middle, final = week["workouts"]
    skipped = client.post(
        "/v1/workouts/week/skip",
        headers=headers,
        json={"workoutDayId": middle["workoutDayId"], "weekStart": week["weekStart"]},
    )
    assert skipped.status_code == 200
    shifted = skipped.json()["workouts"]
    assert [date.fromisoformat(item["date"]).weekday() for item in shifted] == [0, 2, 4]
    assert [item["templateDayId"] for item in shifted] == [
        first["templateDayId"],
        final["templateDayId"],
        first["templateDayId"],
    ]
    next_monday = date.fromisoformat(week["weekStart"]) + timedelta(days=7)
    next_week = client.get(
        "/v1/workouts/week",
        headers=headers,
        params={"weekStart": next_monday.isoformat()},
    ).json()
    assert [item["templateDayId"] for item in next_week["workouts"]] == [
        middle["templateDayId"],
        final["templateDayId"],
        first["templateDayId"],
    ]


def test_same_template_reactivation_continues_cycle_but_switch_starts_day_one():
    headers, _ = _signup("cycle_lineage")
    body, activated = _activate(headers)
    first_week = activated.json()["weekPlan"]
    first = first_week["workouts"][0]
    assert client.post(
        "/v1/workouts/sessions",
        headers=headers,
        json={
            "clientSessionId": str(uuid.uuid4()),
            "workoutDayId": first["workoutDayId"],
            "workoutDate": first["date"],
            "dayType": first["dayType"],
        },
    ).status_code == 201
    same = client.post(
        "/v1/workout-templates/system-minimal-equipment/activate",
        headers=headers,
        json=body | {"clientOperationId": str(uuid.uuid4())},
    )
    assert same.status_code == 200
    same_generated = [
        workout
        for workout in same.json()["weekPlan"]["workouts"]
        if workout["workoutDayId"] != first["workoutDayId"]
    ]
    assert same_generated[0]["templateDayId"].endswith("day-1")

    other_detail = client.get(
        "/v1/workout-templates/system-push-pull-legs", headers=headers
    ).json()
    assert client.patch(
        "/v1/users/me/training-preferences",
        headers=headers,
        json={"selectedEquipment": other_detail["equipmentIds"]},
    ).status_code == 200
    other = client.post(
        "/v1/workout-templates/system-push-pull-legs/activate",
        headers=headers,
        json=body
        | {
            "templateVersion": 1,
            "substitutions": {},
            "clientOperationId": str(uuid.uuid4()),
        },
    )
    assert other.status_code == 200, other.json()
    other_generated = [
        workout
        for workout in other.json()["weekPlan"]["workouts"]
        if workout["workoutDayId"] != first["workoutDayId"]
    ]
    assert other_generated[0]["templateDayId"].endswith("day-0")


def test_same_template_reactivation_continues_after_middle_day_and_wraps_after_final():
    headers, _ = _signup("cycle_middle_final")
    body, activated = _activate(headers)
    first_week = activated.json()["weekPlan"]
    first, middle, final = first_week["workouts"]
    monday = date.fromisoformat(body["weekStart"])

    after_middle = client.post(
        "/v1/workout-templates/system-minimal-equipment/activate",
        headers=headers,
        json=body
        | {
            "effectiveDate": (monday + timedelta(days=4)).isoformat(),
            "clientOperationId": str(uuid.uuid4()),
        },
    )
    assert after_middle.status_code == 200
    friday = next(
        workout
        for workout in after_middle.json()["weekPlan"]["workouts"]
        if workout["date"] == (monday + timedelta(days=4)).isoformat()
    )
    assert friday["templateDayId"] == final["templateDayId"]

    after_final = client.post(
        "/v1/workout-templates/system-minimal-equipment/activate",
        headers=headers,
        json=body
        | {
            "effectiveDate": (monday + timedelta(days=5)).isoformat(),
            "weekdays": [0, 2, 6],
            "clientOperationId": str(uuid.uuid4()),
        },
    )
    assert after_final.status_code == 200
    sunday = next(
        workout
        for workout in after_final.json()["weekPlan"]["workouts"]
        if workout["date"] == (monday + timedelta(days=6)).isoformat()
    )
    assert sunday["templateDayId"] == first["templateDayId"]
    assert middle["templateDayId"] != final["templateDayId"]


def test_private_template_edits_preserve_cycle_position_across_new_day_ids():
    headers, _ = _signup("private_cycle_positions", equipment=[])
    created = client.post(
        "/v1/workout-templates", headers=headers, json=_three_day_payload()
    ).json()
    monday = date.today() - timedelta(days=date.today().weekday())

    def activate(version: int, effective: date, mode: str):
        return client.post(
            f"/v1/workout-templates/{created['id']}/activate",
            headers=headers,
            json={
                "weekStart": monday.isoformat(),
                "effectiveDate": effective.isoformat(),
                "weekdays": [0, 2, 4],
                "templateVersion": version,
                "substitutions": {},
                "applyMode": mode,
                "clientOperationId": str(uuid.uuid4()),
            },
        )

    original = activate(created["version"], monday, "now")
    assert original.status_code == 200
    original_days = original.json()["weekPlan"]["workouts"]
    assert [item["templateDayPosition"] for item in original_days] == [0, 1, 2]

    edited_payload = _three_day_payload("Edited Apply Now") | {
        "version": created["version"]
    }
    edited = client.put(
        f"/v1/workout-templates/{created['id']}",
        headers=headers,
        json=edited_payload,
    )
    assert edited.status_code == 200
    edited_day_ids = {day["id"] for day in edited.json()["days"]}
    assert edited_day_ids.isdisjoint(
        {item["templateDayId"] for item in original_days}
    )

    friday = monday + timedelta(days=4)
    applied = activate(edited.json()["version"], friday, "now")
    assert applied.status_code == 200
    friday_workout = next(
        item for item in applied.json()["weekPlan"]["workouts"]
        if item["date"] == friday.isoformat()
    )
    assert friday_workout["templateDayPosition"] == 2

    next_edit_payload = _three_day_payload("Edited Next Week") | {
        "version": edited.json()["version"]
    }
    next_edit = client.put(
        f"/v1/workout-templates/{created['id']}",
        headers=headers,
        json=next_edit_payload,
    )
    assert next_edit.status_code == 200
    next_monday = monday + timedelta(days=7)
    scheduled = activate(next_edit.json()["version"], next_monday, "next-week")
    assert scheduled.status_code == 200
    assert [
        item["templateDayPosition"]
        for item in scheduled.json()["weekPlan"]["workouts"]
    ] == [0, 1, 2]


def test_scheduled_only_program_does_not_freeze_current_personalized_week():
    headers, _ = _signup("scheduled_preferences")
    assert client.post(
        "/v1/onboarding/me",
        headers=headers,
        json={
            "data": {"selectedEquipment": ["dumbbells", "pull_up_bar", "dip_bar"]},
            "is_complete": True,
        },
    ).status_code == 200
    monday = date.today() - timedelta(days=date.today().weekday())
    current = client.get(
        "/v1/workouts/week", headers=headers, params={"weekStart": monday.isoformat()}
    ).json()
    next_monday = monday + timedelta(days=7)
    preview_payload = {
        "weekStart": monday.isoformat(),
        "effectiveDate": next_monday.isoformat(),
        "weekdays": [0, 2, 4],
        "applyMode": "next-week",
    }
    substitutions = _preview_substitutions(
        headers, "system-minimal-equipment", preview_payload
    )
    scheduled = client.post(
        "/v1/workout-templates/system-minimal-equipment/activate",
        headers=headers,
        json={
            "weekStart": monday.isoformat(),
            "effectiveDate": next_monday.isoformat(),
            "weekdays": [0, 2, 4],
            "templateVersion": 1,
            "substitutions": substitutions,
            "applyMode": "next-week",
            "clientOperationId": str(uuid.uuid4()),
        },
    )
    assert scheduled.status_code == 200
    scheduled_week = scheduled.json()["weekPlan"]
    changed = client.patch(
        "/v1/users/me/training-preferences",
        headers=headers,
        json={"workoutFrequency": "1-day"},
    )
    assert changed.status_code == 200
    refreshed = client.get(
        "/v1/workouts/week", headers=headers, params={"weekStart": monday.isoformat()}
    ).json()
    assert refreshed["daysPerWeek"] == 1
    assert refreshed["workouts"] != current["workouts"]
    preserved_target = client.get(
        "/v1/workouts/week", headers=headers, params={"weekStart": next_monday.isoformat()}
    ).json()
    assert preserved_target == scheduled_week


def test_deactivation_rejects_arbitrary_historical_and_future_dates():
    headers, _ = _signup("deactivate_date")
    body, _ = _activate(headers)
    monday = date.fromisoformat(body["weekStart"])
    for effective_date in (date.today() - timedelta(days=2), date.today() + timedelta(days=2)):
        response = client.delete(
            "/v1/workout-templates/active",
            headers=headers,
            params={
                "weekStart": monday.isoformat(),
                "effectiveDate": effective_date.isoformat(),
            },
        )
        assert response.status_code == 422
        promotion = client.get(
            "/v1/workout-templates/active",
            headers=headers,
            params={"effectiveDate": effective_date.isoformat()},
        )
        assert promotion.status_code == 422
    assert client.get("/v1/workout-templates/active", headers=headers).json()["current"]


def test_scheduled_replacement_can_be_cancelled_without_stopping_current_program():
    headers, _ = _signup("cancel_scheduled", equipment=[])
    current_template = client.post(
        "/v1/workout-templates", headers=headers, json=_payload("Current Private")
    ).json()
    scheduled_template = client.post(
        "/v1/workout-templates", headers=headers, json=_payload("Scheduled Private")
    ).json()
    monday = date.today() - timedelta(days=date.today().weekday())

    def activate(template: dict, effective: date, mode: str):
        return client.post(
            f"/v1/workout-templates/{template['id']}/activate",
            headers=headers,
            json={
                "weekStart": monday.isoformat(),
                "effectiveDate": effective.isoformat(),
                "weekdays": [0],
                "templateVersion": template["version"],
                "substitutions": {},
                "applyMode": mode,
                "clientOperationId": str(uuid.uuid4()),
            },
        )

    current = activate(current_template, monday, "now")
    assert current.status_code == 200
    next_monday = monday + timedelta(days=7)
    scheduled = activate(scheduled_template, next_monday, "next-week")
    assert scheduled.status_code == 200
    assert client.delete(
        f"/v1/workout-templates/{scheduled_template['id']}", headers=headers
    ).status_code == 409

    cancelled = client.delete(
        "/v1/workout-templates/active/scheduled",
        headers=headers,
        params={"effectiveDate": date.today().isoformat()},
    )
    assert cancelled.status_code == 204
    status = client.get("/v1/workout-templates/active", headers=headers).json()
    assert status["current"]["id"] == current.json()["activeProgram"]["id"]
    assert status["scheduled"] is None
    assert client.delete(
        f"/v1/workout-templates/{scheduled_template['id']}", headers=headers
    ).status_code == 204
    restored_week = client.get(
        "/v1/workouts/week",
        headers=headers,
        params={"weekStart": next_monday.isoformat()},
    ).json()
    assert restored_week["programActivationId"] == current.json()["activeProgram"]["id"]


def test_scheduled_cancel_uses_caller_local_date_at_utc_monday_boundary():
    headers, _ = _signup("cancel_scheduled_boundary", equipment=[])
    current_template = client.post(
        "/v1/workout-templates", headers=headers, json=_payload("Current Boundary")
    ).json()
    scheduled_template = client.post(
        "/v1/workout-templates", headers=headers, json=_payload("Scheduled Boundary")
    ).json()
    monday = date.today() - timedelta(days=date.today().weekday())
    next_monday = monday + timedelta(days=7)

    def activate(template: dict, effective: date, mode: str):
        return client.post(
            f"/v1/workout-templates/{template['id']}/activate",
            headers=headers,
            json={
                "weekStart": monday.isoformat(),
                "effectiveDate": effective.isoformat(),
                "weekdays": [0],
                "templateVersion": template["version"],
                "substitutions": {},
                "applyMode": mode,
                "clientOperationId": str(uuid.uuid4()),
            },
        )

    current = activate(current_template, monday, "now")
    assert current.status_code == 200
    scheduled = activate(scheduled_template, next_monday, "next-week")
    assert scheduled.status_code == 200
    caller_sunday = next_monday - timedelta(days=1)

    with _freeze_template_service_today(next_monday):
        cancelled = client.delete(
            "/v1/workout-templates/active/scheduled",
            headers=headers,
            params={"effectiveDate": caller_sunday.isoformat()},
        )
        replay = client.delete(
            "/v1/workout-templates/active/scheduled",
            headers=headers,
            params={"effectiveDate": caller_sunday.isoformat()},
        )
        status = client.get(
            "/v1/workout-templates/active",
            headers=headers,
            params={"effectiveDate": caller_sunday.isoformat()},
        )

    assert cancelled.status_code == 204
    assert replay.status_code == 204
    assert status.status_code == 200
    assert status.json()["current"]["id"] == current.json()["activeProgram"]["id"]
    assert status.json()["scheduled"] is None


def test_scheduled_cancel_requires_a_valid_caller_local_date():
    headers, _ = _signup("cancel_scheduled_date")
    assert client.delete(
        "/v1/workout-templates/active/scheduled", headers=headers
    ).status_code == 422
    assert client.delete(
        "/v1/workout-templates/active/scheduled",
        headers=headers,
        params={"effectiveDate": (date.today() + timedelta(days=2)).isoformat()},
    ).status_code == 422


def test_apply_now_supersedes_active_and_scheduled_revisions():
    headers, _ = _signup("apply_now_supersedes")
    current_body, current = _activate(headers)
    assert current.status_code == 200
    monday = date.fromisoformat(current_body["weekStart"])
    next_monday = monday + timedelta(days=7)
    scheduled = client.post(
        "/v1/workout-templates/system-minimal-equipment/activate",
        headers=headers,
        json={
            **current_body,
            "effectiveDate": next_monday.isoformat(),
            "applyMode": "next-week",
            "clientOperationId": str(uuid.uuid4()),
        },
    )
    assert scheduled.status_code == 200
    scheduled_id = scheduled.json()["activeProgram"]["id"]
    replacement = client.post(
        "/v1/workout-templates/system-minimal-equipment/activate",
        headers=headers,
        json={
            **current_body,
            "clientOperationId": str(uuid.uuid4()),
        },
    )
    assert replacement.status_code == 200
    replacement_id = replacement.json()["activeProgram"]["id"]
    assert replacement_id != current.json()["activeProgram"]["id"]
    replayed_original = client.post(
        "/v1/workout-templates/system-minimal-equipment/activate",
        headers=headers,
        json=current_body,
    )
    assert replayed_original.status_code == 200
    assert replayed_original.json()["activeProgram"]["id"] == current.json()[
        "activeProgram"
    ]["id"]
    assert replayed_original.json()["weekPlan"] == current.json()["weekPlan"]
    with _freeze_template_service_today(next_monday):
        future = client.get(
            f"/v1/workout-templates/active?effectiveDate={next_monday.isoformat()}",
            headers=headers,
        )
    assert future.status_code == 200
    assert future.json()["current"]["id"] == replacement_id
    assert future.json()["current"]["id"] != scheduled_id
    assert future.json()["scheduled"] is None


def test_active_or_scheduled_private_template_must_be_deactivated_before_archive():
    headers, _ = _signup("archive_activation")
    template = client.post(
        "/v1/workout-templates", headers=headers, json=_payload()
    ).json()
    monday = date.today() - timedelta(days=date.today().weekday())
    activated = client.post(
        f"/v1/workout-templates/{template['id']}/activate",
        headers=headers,
        json={
            "weekStart": monday.isoformat(),
            "effectiveDate": monday.isoformat(),
            "weekdays": [0],
            "templateVersion": template["version"],
            "substitutions": {},
            "applyMode": "now",
            "clientOperationId": str(uuid.uuid4()),
        },
    )
    assert activated.status_code == 200
    blocked = client.delete(
        f"/v1/workout-templates/{template['id']}", headers=headers
    )
    assert blocked.status_code == 409
    assert "Deactivate" in blocked.json()["detail"]
    assert client.delete(
        "/v1/workout-templates/active",
        headers=headers,
        params={"weekStart": monday.isoformat(), "effectiveDate": date.today().isoformat()},
    ).status_code == 200
    assert client.delete(
        f"/v1/workout-templates/{template['id']}", headers=headers
    ).status_code == 204


def test_concurrent_activation_prevents_archiving_template_after_reference_recheck():
    headers, _ = _signup("archive_activation_race", equipment=[])
    template = client.post(
        "/v1/workout-templates", headers=headers, json=_payload("Archive Race")
    ).json()
    monday = date.today() - timedelta(days=date.today().weekday())
    entered_preview = Event()
    release_preview = Event()
    archive_started = Event()
    original_preview = workout_template_service.preview_activation
    original_archive = workout_template_router.archive_template

    def blocking_preview(*args, **kwargs):
        entered_preview.set()
        assert release_preview.wait(5)
        return original_preview(*args, **kwargs)

    def tracked_archive(*args, **kwargs):
        archive_started.set()
        return original_archive(*args, **kwargs)

    activation_payload = {
        "weekStart": monday.isoformat(),
        "effectiveDate": monday.isoformat(),
        "weekdays": [0],
        "templateVersion": template["version"],
        "substitutions": {},
        "applyMode": "now",
        "clientOperationId": str(uuid.uuid4()),
    }
    with patch.object(
        workout_template_service, "preview_activation", blocking_preview
    ), patch.object(workout_template_router, "archive_template", tracked_archive):
        with ThreadPoolExecutor(max_workers=2) as executor:
            activation_future = executor.submit(
                client.post,
                f"/v1/workout-templates/{template['id']}/activate",
                headers=headers,
                json=activation_payload,
            )
            assert entered_preview.wait(5)
            archive_future = executor.submit(
                client.delete,
                f"/v1/workout-templates/{template['id']}",
                headers=headers,
            )
            assert archive_started.wait(5)
            try:
                archive_future.result(timeout=0.2)
                archive_was_blocked = False
            except FutureTimeoutError:
                archive_was_blocked = True
            release_preview.set()
            activation = activation_future.result(timeout=5)
            archived = archive_future.result(timeout=5)

    assert archive_was_blocked is True
    assert activation.status_code == 200
    assert archived.status_code == 409
    assert "Deactivate" in archived.json()["detail"]
    assert client.get(
        f"/v1/workout-templates/{template['id']}", headers=headers
    ).status_code == 200


def test_equipment_change_flags_review_without_rewriting_active_program():
    headers, _ = _signup("equipment_review")
    _, activated = _activate(headers)
    assert activated.status_code == 200
    original = activated.json()["weekPlan"]
    changed = client.patch(
        "/v1/users/me/training-preferences",
        headers=headers,
        json={"selectedEquipment": []},
    )
    assert changed.status_code == 200
    active = client.get("/v1/workout-templates/active", headers=headers)
    assert active.status_code == 200
    assert active.json()["current"]["requiresReview"] is True
    refreshed = client.get(
        f"/v1/workouts/week?weekStart={original['weekStart']}", headers=headers
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["programActivationId"] == original["programActivationId"]
    assert [item["workoutDayId"] for item in refreshed.json()["workouts"]] == [
        item["workoutDayId"] for item in original["workouts"]
    ]


def test_equipment_change_flags_every_active_and_scheduled_revision():
    headers, _ = _signup("equipment_review_all")
    current_body, activated = _activate(headers)
    assert activated.status_code == 200
    monday = date.fromisoformat(current_body["weekStart"])
    next_monday = monday + timedelta(days=7)
    scheduled = client.post(
        "/v1/workout-templates/system-minimal-equipment/activate",
        headers=headers,
        json={
            **current_body,
            "effectiveDate": next_monday.isoformat(),
            "applyMode": "next-week",
            "clientOperationId": str(uuid.uuid4()),
        },
    )
    assert scheduled.status_code == 200
    changed = client.patch(
        "/v1/users/me/training-preferences",
        headers=headers,
        json={"selectedEquipment": []},
    )
    assert changed.status_code == 200
    status = client.get("/v1/workout-templates/active", headers=headers).json()
    assert status["current"]["requiresReview"] is True
    assert status["scheduled"]["requiresReview"] is True
    with _freeze_template_service_today(next_monday):
        promoted = client.get(
            f"/v1/workout-templates/active?effectiveDate={next_monday.isoformat()}",
            headers=headers,
        )
    assert promoted.status_code == 200
    assert promoted.json()["current"]["id"] == scheduled.json()["activeProgram"]["id"]
    assert promoted.json()["current"]["requiresReview"] is True
    assert promoted.json()["scheduled"] is None
