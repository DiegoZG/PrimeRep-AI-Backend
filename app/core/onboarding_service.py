from typing import Optional, Dict, Any

from sqlalchemy.orm import Session

from app.models.onboarding_profile import OnboardingProfile
from app.models.user import User


def canonical_onboarding_data(profile: OnboardingProfile, user: User) -> Dict[str, Any]:
    return {
        **profile.data,
        "preferredName": user.preferred_name,
        "lastName": user.last_name,
        "email": user.email,
    }


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
    db.flush()
    user = db.query(User).filter(User.id == user_id).with_for_update().populate_existing().one()
    existing = db.query(OnboardingProfile).filter(
        OnboardingProfile.user_id == user_id
    ).populate_existing().first()
    protected = set(existing.profile_managed_fields or []) if existing else set()
    data = {key: value for key, value in data.items() if key not in protected}
    data.update(preferredName=user.preferred_name, lastName=user.last_name, email=user.email)

    if existing:
        # Partial saves must retain fields from newer clients that this server
        # does not yet understand.
        existing.data = {**existing.data, **data}
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
