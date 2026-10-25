"""Deterministic workout volume and time-budget policy."""
import copy
import math

PROFILE_EXERCISE_COUNTS = {"no-experience": (2, 2), "beginner": (2, 2), "intermediate": (3, 3), "advanced": (3, 4)}


class WorkoutTimeBudgetTooShort(Exception):
    def __init__(self, minimum_useful_minutes):
        self.minimum_useful_minutes = minimum_useful_minutes
        super().__init__(f"This workout needs at least {minimum_useful_minutes} minutes. Choose a larger budget or Trainer-led.")


class WorkoutPlanningLocked(Exception):
    pass


def planning_defaults(data):
    mode = data.get("workoutPlanningMode")
    if mode not in {"trainer_led", "time_budget"}:
        mode = "time_budget"
    minutes = data.get("preferredWorkoutMinutes", 35)
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 15 <= minutes <= 120:
        minutes = 35
    return mode, minutes


def estimate_minutes(blocks):
    items = [item for block in blocks for item in block.get("items", [])]
    seconds = 300 + max(0, len(items) - 1) * 60
    for item in items:
        prescription = item["prescription"]
        reps = (prescription["repsMin"] + prescription["repsMax"]) / 2
        sets = prescription["sets"]
        seconds += sets * reps * 4 + max(0, sets - 1) * prescription["restSeconds"]
    return max(5, math.ceil(seconds / 300) * 5)


def normalized_day(day):
    result = copy.deepcopy(day)
    mode = "trainer_led" if (result.get("programActivationId") or result.get("templateId") or result.get("splitKey") == "program") else result.get("planningMode", "time_budget")
    budget = result.get("timeBudgetMinutes", result.get("durationMinutes", 35)) if mode == "time_budget" else None
    result.update(planningMode=mode, timeBudgetMinutes=budget,
                  planningSource=result.get("planningSource", "default"),
                  estimatedMinutes=estimate_minutes(result.get("exerciseBlocks", [])))
    return result


def apply_time_budget(workout, mode, budget, source="default"):
    result = copy.deepcopy(workout)
    rows = [(block["blockType"], item) for block in result["exerciseBlocks"] for item in block["items"]]
    floor = min(2, len(rows))
    # Selection already prioritizes mains and foundation/custom coverage.
    protected = {id(item) for _, item in rows[:floor]}
    def blocks():
        return [{"blockType": role, "items": [item for item_role, item in rows if item_role == role]}
                for role in ("main", "accessory") if any(item_role == role for item_role, _ in rows)]
    if mode == "time_budget":
        while estimate_minutes(blocks()) > budget:
            removable = next((index for index in range(len(rows) - 1, -1, -1)
                              if rows[index][0] == "accessory" and id(rows[index][1]) not in protected), None)
            if removable is None:
                removable = next((index for index in range(len(rows) - 1, -1, -1)
                                  if id(rows[index][1]) not in protected), None)
            if removable is None:
                raise WorkoutTimeBudgetTooShort(estimate_minutes(blocks()))
            rows.pop(removable)
    result["exerciseBlocks"] = blocks()
    estimate = estimate_minutes(result["exerciseBlocks"])
    result.update(planningMode=mode, timeBudgetMinutes=budget if mode == "time_budget" else None,
                  planningSource=source, estimatedMinutes=estimate,
                  durationMinutes=budget if mode == "time_budget" else estimate)
    return result


def planning_summary(day):
    if day.get("programActivationId") or day.get("templateId") or day.get("splitKey") == "program":
        return f"{day.get('durationMinutes', day.get('estimatedMinutes', 0))} min · Program"
    if day.get("planningMode") == "trainer_led":
        count = sum(len(block.get("items", [])) for block in day.get("exerciseBlocks", []))
        return f"{count} exercise{'s' if count != 1 else ''}"
    budget = day.get("timeBudgetMinutes") or day.get("durationMinutes", 35)
    estimate = day.get("estimatedMinutes")
    if estimate is not None and estimate > budget:
        return f"About {estimate} min · {budget} min budget"
    return f"Up to {budget} min"
