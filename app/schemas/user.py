from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr


class UserResponse(BaseModel):
    id: str
    email: EmailStr
    preferred_name: str
    last_name: Optional[str] = None
    has_completed_onboarding: bool
    subscription_tier: Literal["free", "premium"] = "free"
    coach_insights_enabled: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserPreferencesRequest(BaseModel):
    coach_insights_enabled: bool

