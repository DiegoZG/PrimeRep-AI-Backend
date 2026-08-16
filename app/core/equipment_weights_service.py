import math
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.onboarding_profile import OnboardingProfile
from app.models.user_equipment_weights import UserEquipmentWeights


def get_equipment_weights(
    db: Session, user_id: str
) -> Optional[UserEquipmentWeights]:
    return (
        db.query(UserEquipmentWeights)
        .filter(UserEquipmentWeights.user_id == user_id)
        .first()
    )


def get_equipment_weight_arrays(
    db: Session, user_id: str
) -> tuple[list[float], list[float]]:
    weights = get_equipment_weights(db, user_id)
    if weights is not None:
        return list(weights.dumbbell_weights), list(weights.plate_weights)

    profile = (
        db.query(OnboardingProfile)
        .filter(OnboardingProfile.user_id == user_id)
        .first()
    )
    if profile is None or not isinstance(profile.data, dict):
        return [], []

    return (
        _sanitize_legacy_weights(profile.data.get("dumbbellWeights")),
        _sanitize_legacy_weights(profile.data.get("plateWeights")),
    )


def _sanitize_legacy_weights(values: Any) -> list[float]:
    if not isinstance(values, list):
        return []
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        for value in values
    ):
        return []
    return [float(value) for value in values]


def upsert_equipment_weights(
    db: Session,
    user_id: str,
    dumbbell_weights: list[float],
    plate_weights: list[float],
    *,
    commit: bool = True,
) -> UserEquipmentWeights:
    weights = get_equipment_weights(db, user_id)
    if weights is None:
        weights = UserEquipmentWeights(user_id=user_id)

    weights.dumbbell_weights = list(dumbbell_weights)
    weights.plate_weights = list(plate_weights)
    db.add(weights)
    if commit:
        db.commit()
        db.refresh(weights)
    else:
        db.flush()
    return weights
