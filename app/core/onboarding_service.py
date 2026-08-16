from typing import Optional, Dict, Any

from sqlalchemy.orm import Session

from app.models.onboarding_profile import OnboardingProfile


def get_onboarding_by_user_id(db: Session, user_id: str) -> Optional[OnboardingProfile]:
    return (
        db.query(OnboardingProfile)
        .filter(OnboardingProfile.user_id == user_id)
        .first()
    )


def upsert_onboarding(
    db: Session,
    user_id: str,
    data: Dict[str, Any],
    *,
    commit: bool = True,
) -> OnboardingProfile:
    existing = get_onboarding_by_user_id(db, user_id)

    if existing:
        existing.data = data
        db.add(existing)
        if commit:
            db.commit()
            db.refresh(existing)
        else:
            db.flush()
        return existing

    profile = OnboardingProfile(user_id=user_id, data=data)
    db.add(profile)
    if commit:
        db.commit()
        db.refresh(profile)
    else:
        db.flush()
    return profile
