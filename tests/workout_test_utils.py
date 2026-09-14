import copy
import datetime
import uuid
from typing import Optional

from sqlalchemy.orm.attributes import flag_modified

from app.core.database import SessionLocal
from app.models.workout_week_plan import WorkoutWeekPlan


def install_startable_workout(
    client,
    token: str,
    *,
    workout_day_id: Optional[str] = None,
    workout_date: Optional[datetime.date] = None,
    day_type: str = "upper",
    exercise_ids: tuple[str, ...] = ("push_up",),
) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    workout_date = workout_date or datetime.date.today()
    week_start = workout_date - datetime.timedelta(days=workout_date.weekday())
    week = client.get(
        "/v1/workouts/week",
        params={"weekStart": week_start.isoformat()},
        headers=headers,
    )
    assert week.status_code == 200
    workout = copy.deepcopy(week.json()["workouts"][0])
    workout["workoutDayId"] = workout_day_id or str(uuid.uuid4())
    workout["date"] = workout_date.isoformat()
    workout["dayType"] = day_type
    workout["title"] = f"Test {day_type.title()}"
    items = []
    for exercise_id in exercise_ids:
        exercise = client.get(f"/v1/exercises/{exercise_id}", headers=headers)
        assert exercise.status_code == 200
        items.append(
            {
                "exercise": exercise.json(),
                "prescription": {
                    "sets": 5,
                    "repsMin": 1,
                    "repsMax": 100,
                    "restSeconds": 60,
                },
                "exerciseRationale": None,
            }
        )
    workout["exerciseBlocks"] = [{"blockType": "main", "items": items}]

    user = client.get("/v1/users/me", headers=headers)
    assert user.status_code == 200
    with SessionLocal() as db:
        plan = db.query(WorkoutWeekPlan).filter_by(
            user_id=user.json()["id"], week_start_date=week_start
        ).one()
        data = dict(plan.plan_json)
        data["workouts"] = [workout]
        data["daysPerWeek"] = 1
        plan.days_per_week = 1
        plan.plan_json = data
        flag_modified(plan, "plan_json")
        db.commit()
    return workout
