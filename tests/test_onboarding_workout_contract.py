from datetime import date
from types import SimpleNamespace
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.core.security.jwt import decode_access_token
from app.core.workout_service import normalize_onboarding_settings
from app.core.workout_week_service import (
    _get_cycle_start_index,
    _get_template_dates,
    _pick_balanced_full_body_exercises,
)
from app.main import app
from app.models.workout_week_plan import WorkoutWeekPlan


client = TestClient(app)


def _mobile_onboarding(**overrides) -> dict:
    data = {
        "preferredName": "Mobile",
        "lastName": "Tester",
        "age": 31,
        "gender": "male",
        "weight": 190,
        "weightUnit": "LB",
        "reason": "structure",
        "fitnessGoal": "get-stronger",
        "experienceLevel": "intermediate",
        "workoutFrequency": "5-days",
        "workoutSplit": "push-pull-legs-upper-lower",
        "varietyLevel": "consistent",
        "trainingPlace": "garage-gym",
        "selectedEquipment": ["dumbbells"],
        "dumbbellWeights": [10, 20, 30],
        "plateWeights": [],
        "customWorkouts": [],
        "preferredWorkoutTime": "morning",
        "notificationsEnabled": False,
        "benchPress1RM": 185,
        "backSquat1RM": 245,
        "deadlift1RM": 315,
    }
    data.update(overrides)
    return data


def _signup_with_onboarding(onboarding: dict) -> dict[str, str]:
    response = client.post(
        "/v1/auth/signup",
        json={
            "email": f"contract_{uuid.uuid4().hex}@example.com",
            "password": "StrongPass123",
            "preferred_name": "Mobile",
            "last_name": "Tester",
            "onboarding": onboarding,
        },
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.parametrize(
    ("frequency", "days", "offsets"),
    [
        ("1-day", 1, [2]),
        ("2-days", 2, [0, 3]),
        ("3-days", 3, [0, 2, 4]),
        ("4-days", 4, [0, 1, 3, 5]),
        ("5-days", 5, [0, 1, 2, 4, 5]),
        ("6-days", 6, [0, 1, 2, 3, 4, 5]),
        ("every-day", 7, [0, 1, 2, 3, 4, 5, 6]),
    ],
)
def test_mobile_frequency_maps_to_exact_schedule(frequency, days, offsets):
    settings = normalize_onboarding_settings(_mobile_onboarding(workoutFrequency=frequency))
    week_start = date(2026, 9, 7)

    assert settings.days_per_week == days
    assert [
        (workout_date - week_start).days
        for workout_date in _get_template_dates(week_start, days)
    ] == offsets


@pytest.mark.parametrize(
    ("frequency", "expected_weekdays"),
    [
        ("1-day", [2]),
        ("2-days", [0, 3]),
        ("3-days", [0, 2, 4]),
        ("4-days", [0, 1, 3, 5]),
        ("5-days", [0, 1, 2, 4, 5]),
        ("6-days", [0, 1, 2, 3, 4, 5]),
        ("every-day", [0, 1, 2, 3, 4, 5, 6]),
    ],
)
def test_exact_mobile_signup_frequency_drives_week_endpoint(frequency, expected_weekdays):
    headers = _signup_with_onboarding(
        _mobile_onboarding(workoutFrequency=frequency, workoutSplit="ai-optimized")
    )

    response = client.get("/v1/workouts/week?week_start=2026-09-07", headers=headers)

    assert response.status_code == 200
    week = response.json()
    assert week["daysPerWeek"] == len(expected_weekdays)
    assert [date.fromisoformat(day["date"]).weekday() for day in week["workouts"]] == (
        expected_weekdays
    )


@pytest.mark.parametrize(
    ("split_id", "expected_cycle"),
    [
        ("push-pull-legs", ["push", "pull", "legs"]),
        ("upper-lower", ["upper", "lower"]),
        ("push-pull-legs-full-body", ["push", "pull", "legs", "full_body"]),
        ("push-pull-legs-upper-lower", ["push", "pull", "legs", "upper", "lower"]),
        ("upper-lower-full-body", ["upper", "lower", "full_body"]),
        ("full-body", ["full_body"]),
        ("bro-split", ["chest", "back", "legs", "shoulders", "arms"]),
        ("lower-focused-upper", ["lower", "upper", "lower"]),
        ("push-pull-legs-upper-body", ["push", "pull", "legs", "upper"]),
    ],
)
def test_every_mobile_split_has_a_concrete_cycle(split_id, expected_cycle):
    settings = normalize_onboarding_settings(_mobile_onboarding(workoutSplit=split_id))

    assert settings.split_preference == split_id
    assert [day.day_type for day in settings.day_cycle] == expected_cycle


@pytest.mark.parametrize(
    ("frequency", "expected_cycle"),
    [
        ("1-day", ["full_body"]),
        ("3-days", ["full_body"]),
        ("4-days", ["upper", "lower"]),
        ("5-days", ["push", "pull", "legs", "upper", "lower"]),
        ("6-days", ["push", "pull", "legs"]),
        ("every-day", ["push", "pull", "legs"]),
    ],
)
def test_ai_optimized_cycle_depends_on_mobile_frequency(frequency, expected_cycle):
    settings = normalize_onboarding_settings(
        _mobile_onboarding(workoutFrequency=frequency, workoutSplit="ai-optimized")
    )

    assert [day.day_type for day in settings.day_cycle] == expected_cycle


@pytest.mark.parametrize(
    ("mobile_goal", "normalized_goal"),
    [
        ("build-muscle", "hypertrophy"),
        ("general-fitness", "general_fitness"),
        ("conditioning", "conditioning"),
        ("get-stronger", "strength"),
    ],
)
def test_every_mobile_fitness_goal_has_a_normalized_value(mobile_goal, normalized_goal):
    settings = normalize_onboarding_settings(_mobile_onboarding(fitnessGoal=mobile_goal))

    assert settings.goal == normalized_goal


@pytest.mark.parametrize(
    "experience_level",
    ["no-experience", "beginner", "intermediate", "advanced"],
)
def test_every_mobile_experience_level_is_preserved(experience_level):
    settings = normalize_onboarding_settings(
        _mobile_onboarding(experienceLevel=experience_level)
    )

    assert settings.experience_level == experience_level


@pytest.mark.parametrize("variety_level", ["consistent", "balanced", "varied"])
def test_every_mobile_variety_level_is_preserved(variety_level):
    settings = normalize_onboarding_settings(_mobile_onboarding(varietyLevel=variety_level))

    assert settings.variety_level == variety_level


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"fitness_goal": "build_muscle"}, ("hypertrophy", None, None)),
        ({"goal": "strength"}, ("strength", None, None)),
        ({"experience_level": "novice"}, ("hypertrophy", "no-experience", None)),
        ({"variety_level": "balanced"}, ("hypertrophy", None, "balanced")),
    ],
)
def test_legacy_preference_aliases_normalize_to_internal_values(payload, expected):
    settings = normalize_onboarding_settings(payload)

    assert (settings.goal, settings.experience_level, settings.variety_level) == expected


def test_normalized_preferences_are_part_of_deterministic_selection_seed():
    baseline = normalize_onboarding_settings(_mobile_onboarding())

    assert normalize_onboarding_settings(
        _mobile_onboarding(fitnessGoal="build-muscle")
    ).selection_seed != baseline.selection_seed
    assert normalize_onboarding_settings(
        _mobile_onboarding(experienceLevel="advanced")
    ).selection_seed != baseline.selection_seed
    assert normalize_onboarding_settings(
        _mobile_onboarding(varietyLevel="varied")
    ).selection_seed != baseline.selection_seed


def test_equipment_order_and_duplicates_do_not_change_selection_seed():
    first = normalize_onboarding_settings(
        _mobile_onboarding(selectedEquipment=["plates", "dumbbells", "plates"])
    )
    second = normalize_onboarding_settings(
        _mobile_onboarding(selectedEquipment=["dumbbells", "plates"])
    )

    assert first.equipment_ids == ("dumbbells", "plates")
    assert first.selection_seed == second.selection_seed


def test_camel_case_values_take_precedence_and_empty_values_use_legacy_fallbacks():
    canonical = normalize_onboarding_settings(
        {
            "workoutFrequency": "6-days",
            "days_per_week": 2,
            "workoutSplit": "full-body",
            "split_preference": "ppl",
            "fitnessGoal": "conditioning",
            "goal": "hypertrophy",
            "experienceLevel": "advanced",
            "experience_level": "beginner",
            "varietyLevel": "varied",
            "variety_level": "consistent",
            "selectedEquipment": ["dumbbells"],
            "equipment_ids": ["plates"],
        }
    )
    fallback = normalize_onboarding_settings(
        {
            "workoutFrequency": "",
            "days_per_week": 3,
            "workoutSplit": "",
            "split_preference": "ppl",
            "selectedEquipment": [],
            "equipment_ids": ["plates"],
        }
    )

    assert canonical.days_per_week == 6
    assert canonical.split_preference == "full-body"
    assert canonical.goal == "conditioning"
    assert canonical.experience_level == "advanced"
    assert canonical.variety_level == "varied"
    assert canonical.equipment_ids == ("dumbbells",)
    assert fallback.days_per_week == 3
    assert fallback.split_preference == "ppl"
    assert fallback.equipment_ids == ("plates",)


def test_invalid_nonempty_canonical_values_mask_legacy_values():
    settings = normalize_onboarding_settings(
        {
            "workoutFrequency": "not-a-frequency",
            "days_per_week": 2,
            "workoutSplit": "not-a-split",
            "split_preference": "ppl",
            "fitnessGoal": "not-a-goal",
            "goal": "strength",
            "experienceLevel": "not-an-experience",
            "experience_level": "advanced",
            "varietyLevel": "not-a-variety",
            "variety_level": "varied",
            "selectedEquipment": ["dumbbells"],
            "equipment_ids": ["plates"],
        }
    )

    assert settings.days_per_week == 4
    assert settings.split_preference == "upper_lower"
    assert settings.goal == "hypertrophy"
    assert settings.experience_level is None
    assert settings.variety_level is None
    assert settings.equipment_ids == ("dumbbells",)


def test_legacy_custom_cycle_supports_snake_case_muscle_groups():
    settings = normalize_onboarding_settings(
        {
            "workoutSplit": "custom",
            "custom_workouts": [
                {"id": "pull", "name": "Pull First", "muscle_groups": ["back", "biceps"]},
                {"id": "legs", "name": "Legs Second", "muscle_groups": ["quads"]},
            ],
        }
    )

    assert [day.title for day in settings.day_cycle] == ["Pull First", "Legs Second"]
    assert [day.muscles for day in settings.day_cycle] == [
        frozenset({"back", "biceps"}),
        frozenset({"quads"}),
    ]


def test_mobile_only_custom_muscles_map_to_supported_catalog_muscles():
    settings = normalize_onboarding_settings(
        _mobile_onboarding(
            workoutSplit="custom",
            customWorkouts=[
                {
                    "id": "mobile-muscles",
                    "name": "Mobile Muscles",
                    "type": "custom",
                    "muscleGroups": [
                        "lower-back",
                        "adductors",
                        "abductors",
                        "trapezius",
                        "not-a-muscle",
                    ],
                }
            ],
        )
    )

    assert settings.day_cycle[0].muscles == frozenset({"back", "quads", "glutes"})


def test_two_day_ppl_cycle_continues_across_week_boundaries():
    settings = normalize_onboarding_settings(
        _mobile_onboarding(workoutFrequency="2-days", workoutSplit="push-pull-legs")
    )
    first_week = date(2026, 9, 7)
    day_types = [day.day_type for day in settings.day_cycle]
    scheduled_cycle = []

    for week_offset in range(3):
        week_start = first_week.fromordinal(first_week.toordinal() + 7 * week_offset)
        start_index = _get_cycle_start_index(
            week_start,
            settings.days_per_week,
            len(settings.day_cycle),
        )
        scheduled_cycle.extend(
            day_types[(start_index + slot) % len(day_types)]
            for slot in range(settings.days_per_week)
        )

    assert scheduled_cycle == ["push", "pull", "legs", "push", "pull", "legs"]


def test_exact_mobile_signup_payload_drives_week_and_equipment_filtering():
    headers = _signup_with_onboarding(_mobile_onboarding())

    response = client.get("/v1/workouts/week", headers=headers)
    assert response.status_code == 200
    week = response.json()

    assert week["daysPerWeek"] == 5
    assert [date.fromisoformat(day["date"]).weekday() for day in week["workouts"]] == [
        0,
        1,
        2,
        4,
        5,
    ]
    assert [day["dayType"] for day in week["workouts"]] == [
        "push",
        "pull",
        "legs",
        "upper",
        "lower",
    ]
    assert {day["splitKey"] for day in week["workouts"]} == {"push-pull-legs-upper-lower"}

    for workout in week["workouts"]:
        for block in workout["exerciseBlocks"]:
            for item in block["items"]:
                assert set(item["exercise"]["requiredEquipmentIds"]) <= {"dumbbells"}


def test_existing_pre_contract_week_is_refreshed_with_mobile_preferences():
    headers = _signup_with_onboarding(
        _mobile_onboarding(
            workoutFrequency="1-day",
            workoutSplit="full-body",
        )
    )
    week_start = "2026-09-07"
    created = client.get(f"/v1/workouts/week?week_start={week_start}", headers=headers)
    assert created.status_code == 200
    workout_id = created.json()["workouts"][0]["workoutDayId"]
    user_id = decode_access_token(headers["Authorization"].removeprefix("Bearer "))["sub"]

    with SessionLocal() as db:
        plan = db.query(WorkoutWeekPlan).filter_by(
            user_id=user_id,
            week_start_date=date.fromisoformat(week_start),
        ).first()
        assert plan is not None
        plan.plan_json = {
            key: value
            for key, value in plan.plan_json.items()
            if key != "onboardingWorkoutContractVersion"
        }
        plan.days_per_week = 4
        db.commit()

    refreshed = client.get(f"/v1/workouts/week?week_start={week_start}", headers=headers)
    assert refreshed.status_code == 200
    week = refreshed.json()
    assert week["daysPerWeek"] == 1
    assert [workout["dayType"] for workout in week["workouts"]] == ["full_body"]
    assert week["workouts"][0]["workoutDayId"] == workout_id


def test_custom_mobile_workouts_keep_order_and_restrict_muscles():
    headers = _signup_with_onboarding(
        _mobile_onboarding(
            workoutFrequency="3-days",
            workoutSplit="custom",
            selectedEquipment=[
                "dumbbells",
                "flat_bench",
                "olympic_barbell",
                "plates",
                "pull_up_bar",
                "lat_pulldown_cable",
                "row_cable",
            ],
            customWorkouts=[
                {"id": "press", "name": "Press First", "type": "custom", "muscleGroups": ["chest"]},
                {"id": "core", "name": "Core Second", "type": "custom", "muscleGroups": ["abs"]},
                {
                    "id": "pull",
                    "name": "Pull Third",
                    "type": "workout-split",
                    "muscleGroups": ["back"],
                },
            ],
        )
    )

    response = client.get("/v1/workouts/week", headers=headers)
    assert response.status_code == 200
    workouts = response.json()["workouts"]

    assert [day["title"] for day in workouts] == [
        "Press First",
        "Core Second",
        "Pull Third",
    ]
    assert [day["dayType"] for day in workouts] == [
        "custom:press",
        "custom:core",
        "custom:pull",
    ]
    for workout, allowed_muscle in zip(workouts, ("chest", "abs", "back")):
        exercises = [
            item["exercise"]
            for block in workout["exerciseBlocks"]
            for item in block["items"]
        ]
        assert exercises
        assert {exercise["primaryMuscle"] for exercise in exercises} == {
            allowed_muscle
        }
        assert all(
            set(exercise["requiredEquipmentIds"])
            <= {
                "dumbbells",
                "flat_bench",
                "olympic_barbell",
                "plates",
                "pull_up_bar",
                "lat_pulldown_cable",
                "row_cable",
            }
            for exercise in exercises
        )


def test_standard_split_never_fills_a_small_day_with_unrelated_muscles():
    headers = _signup_with_onboarding(
        _mobile_onboarding(
            workoutFrequency="5-days",
            workoutSplit="bro-split",
            selectedEquipment=[],
        )
    )

    response = client.get(
        "/v1/workouts/week?week_start=2026-09-07",
        headers=headers,
    )

    assert response.status_code == 200
    chest_day = response.json()["workouts"][0]
    exercises = [
        item["exercise"]
        for block in chest_day["exerciseBlocks"]
        for item in block["items"]
    ]
    assert exercises
    assert {exercise["primaryMuscle"] for exercise in exercises} == {"chest"}


def test_custom_mobile_muscle_aliases_generate_nonempty_relevant_days():
    equipment = [
        "dumbbells",
        "flat_bench",
        "olympic_barbell",
        "plates",
        "pull_up_bar",
        "lat_pulldown_cable",
        "row_cable",
    ]
    custom_workouts = [
        {
            "id": muscle,
            "name": muscle,
            "type": "custom",
            "muscleGroups": [muscle],
        }
        for muscle in ("lower-back", "adductors", "abductors", "trapezius")
    ]
    headers = _signup_with_onboarding(
        _mobile_onboarding(
            workoutFrequency="4-days",
            workoutSplit="custom",
            selectedEquipment=equipment,
            customWorkouts=custom_workouts,
        )
    )

    response = client.get(
        "/v1/workouts/week?week_start=2026-09-07",
        headers=headers,
    )

    assert response.status_code == 200
    expected_primary_muscles = ("back", "quads", "glutes", "back")
    for workout, expected_muscle in zip(
        response.json()["workouts"], expected_primary_muscles
    ):
        exercises = [
            item["exercise"]
            for block in workout["exerciseBlocks"]
            for item in block["items"]
        ]
        assert exercises
        assert {exercise["primaryMuscle"] for exercise in exercises} == {
            expected_muscle
        }


def test_full_body_picker_covers_foundations_before_duplicate_isolations():
    pool = [
        SimpleNamespace(id="press", primary_muscle="chest", exercise_type="strength"),
        SimpleNamespace(id="row", primary_muscle="back", exercise_type="strength"),
        SimpleNamespace(id="squat", primary_muscle="quads", exercise_type="strength"),
        SimpleNamespace(id="plank", primary_muscle="abs", exercise_type="accessory"),
        SimpleNamespace(id="leg_raise", primary_muscle="abs", exercise_type="bodyweight"),
        SimpleNamespace(id="dumbbell_curl", primary_muscle="biceps", exercise_type="accessory"),
        SimpleNamespace(id="barbell_curl", primary_muscle="biceps", exercise_type="accessory"),
        SimpleNamespace(id="preacher_curl", primary_muscle="biceps", exercise_type="accessory"),
    ]
    tie_breakers = {exercise.id: index for index, exercise in enumerate(pool)}
    main = _pick_balanced_full_body_exercises(
        pool=pool,
        count=2,
        preferred_types={"strength", "bodyweight", "olympic"},
        selected=[],
        tie_breakers=tie_breakers,
    )
    accessories = _pick_balanced_full_body_exercises(
        pool=pool,
        count=5,
        preferred_types={"accessory"},
        selected=main,
        tie_breakers=tie_breakers,
    )

    selected = main + accessories
    primary_muscles = [exercise.primary_muscle for exercise in selected]
    assert {"chest", "back", "quads"} <= set(primary_muscles)
    assert primary_muscles.count("abs") <= 1
    assert primary_muscles.count("biceps") <= 1


def test_full_body_duration_growth_adds_complementary_exercises():
    headers = _signup_with_onboarding(
        _mobile_onboarding(
            workoutFrequency="3-days",
            workoutSplit="ai-optimized",
            selectedEquipment=[
                "dumbbells",
                "flat_bench",
                "olympic_barbell",
                "plates",
                "pull_up_bar",
                "lat_pulldown_cable",
                "row_cable",
                "preacher_curl_bench",
                "short_bar",
                "squat_rack",
                "ab_wheel",
            ],
        )
    )
    week = client.get("/v1/workouts/week", headers=headers)
    assert week.status_code == 200
    workout = week.json()["workouts"][0]
    workout_id = workout["workoutDayId"]

    previous_ids: set[str] = set()
    for duration, expected_count in ((35, 4), (45, 6), (60, 7)):
        if duration != 35:
            response = client.patch(
                "/v1/workouts/week/duration",
                json={"workoutDayId": workout_id, "durationMinutes": duration},
                headers=headers,
            )
            assert response.status_code == 200
            workout = next(
                item
                for item in response.json()["workouts"]
                if item["workoutDayId"] == workout_id
            )

        exercises = [
            item["exercise"]
            for block in workout["exerciseBlocks"]
            for item in block["items"]
        ]
        exercise_ids = {exercise["id"] for exercise in exercises}
        primary_muscles = [exercise["primaryMuscle"] for exercise in exercises]
        assert len(exercises) == expected_count
        assert previous_ids <= exercise_ids
        assert any(muscle in {"chest", "shoulders"} for muscle in primary_muscles)
        assert "back" in primary_muscles
        assert any(muscle in {"quads", "hamstrings", "glutes"} for muscle in primary_muscles)
        assert primary_muscles.count("abs") <= 1
        assert primary_muscles.count("biceps") <= 1
        previous_ids = exercise_ids


def test_skip_uses_actual_position_in_split_with_repeated_day_types():
    headers = _signup_with_onboarding(
        _mobile_onboarding(
            workoutFrequency="3-days",
            workoutSplit="lower-focused-upper",
        )
    )
    initial = client.get("/v1/workouts/week", headers=headers)
    assert initial.status_code == 200
    initial_workouts = initial.json()["workouts"]
    assert [workout["dayType"] for workout in initial_workouts] == [
        "lower",
        "upper",
        "lower",
    ]

    first_skip = client.post(
        "/v1/workouts/week/skip",
        json={"workoutDayId": initial_workouts[0]["workoutDayId"]},
        headers=headers,
    )
    assert first_skip.status_code == 200
    first_skip_workouts = first_skip.json()["workouts"]
    assert [workout["dayType"] for workout in first_skip_workouts] == [
        "upper",
        "lower",
        "lower",
    ]

    second_skip = client.post(
        "/v1/workouts/week/skip",
        json={"workoutDayId": first_skip_workouts[0]["workoutDayId"]},
        headers=headers,
    )
    assert second_skip.status_code == 200
    assert [workout["dayType"] for workout in second_skip.json()["workouts"]] == [
        "lower",
        "lower",
        "upper",
    ]


def test_skip_uses_cycle_position_for_duplicate_custom_day_types():
    headers = _signup_with_onboarding(
        _mobile_onboarding(
            workoutFrequency="3-days",
            workoutSplit="custom",
            customWorkouts=[
                {
                    "id": "duplicate",
                    "name": "Chest First",
                    "type": "custom",
                    "muscleGroups": ["chest"],
                },
                {
                    "id": "middle",
                    "name": "Back Second",
                    "type": "custom",
                    "muscleGroups": ["back"],
                },
                {
                    "id": "duplicate",
                    "name": "Legs Third",
                    "type": "custom",
                    "muscleGroups": ["quads"],
                },
            ],
        )
    )
    initial = client.get("/v1/workouts/week", headers=headers)
    assert initial.status_code == 200
    initial_workouts = initial.json()["workouts"]
    assert [workout["title"] for workout in initial_workouts] == [
        "Chest First",
        "Back Second",
        "Legs Third",
    ]

    skipped = client.post(
        "/v1/workouts/week/skip",
        json={"workoutDayId": initial_workouts[0]["workoutDayId"]},
        headers=headers,
    )

    assert skipped.status_code == 200
    assert [workout["title"] for workout in skipped.json()["workouts"]] == [
        "Back Second",
        "Legs Third",
        "Chest First",
    ]
