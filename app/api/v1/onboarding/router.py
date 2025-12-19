from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security.deps import get_current_user
from app.core.onboarding_service import get_onboarding_by_user_id, upsert_onboarding
from app.models.user import User
from app.schemas.onboarding import OnboardingUpsertRequest, OnboardingResponse

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.get("/me", response_model=OnboardingResponse)
def get_my_onboarding(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    profile = get_onboarding_by_user_id(db, str(current_user.id))
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Onboarding profile not found",
        )
    return profile


@router.post("/me", response_model=OnboardingResponse)
def save_my_onboarding(
    payload: OnboardingUpsertRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    profile = upsert_onboarding(db, str(current_user.id), payload.data)

    if payload.is_complete and not current_user.has_completed_onboarding:
        current_user.has_completed_onboarding = True
        db.add(current_user)
        db.commit()

    db.refresh(profile)
    return profile

