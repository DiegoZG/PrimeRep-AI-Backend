import math


def _weight(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0 else None


def public_weight_data(item):
    facts = item.protected_facts or {}
    if item.kind not in {"progression", "personal_record"}:
        return None
    name = facts.get("exerciseName")
    target = item.kind == "progression"
    weight = _weight(facts.get("targetWeightKg" if target else "recordWeightKg"))
    if not isinstance(name, str) or not name.strip() or weight is None:
        return None
    equipment = facts.get("equipmentIds")
    profile = facts.get("loadProfile")
    load_profile = None
    if isinstance(profile, dict):
        basis = profile.get("basis")
        count = profile.get("implement_count", 1)
        if isinstance(basis, str) and basis in {"per_implement", "total", "machine", "bodyweight", "added", "assistance"} and type(count) is int and count in (1, 2):
            load_profile = {"basis": basis, "implement_count": count}
    result = {
        "context": "target" if target else "record",
        "exerciseName": name,
        "weightKg": weight,
        "equipmentIds": [value for value in equipment if isinstance(value, str)] if isinstance(equipment, list) else [],
        "loadProfile": load_profile,
    }
    if target:
        recommendation = facts.get("recommendation")
        result.update(
            currentWeightKg=_weight(facts.get("currentWeightKg")),
            recommendation=recommendation if isinstance(recommendation, str) and recommendation in {"increase", "hold", "deload"} else None,
        )
    return result
