from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security.deps import get_current_user
from app.models.user import User
from app.schemas.user import UserPreferencesRequest, UserResponse

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserResponse)
def read_me(
    current_user: User = Depends(get_current_user),
):
    return current_user


@router.patch("/me/preferences", response_model=UserResponse)
def update_preferences(
    body: UserPreferencesRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Toggle AI coach insights. Requires a premium subscription."""
    if current_user.subscription_tier != "premium":
        raise HTTPException(
            status_code=403,
            detail="AI Coach Insights require a Premium subscription.",
        )
    current_user.coach_insights_enabled = body.coach_insights_enabled
    db.commit()
    db.refresh(current_user)
    return current_user

