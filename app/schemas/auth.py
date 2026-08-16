from typing import Optional, Dict, Any
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.equipment_weights import EquipmentWeightsPayload

class SignUpRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    preferred_name: str = Field(min_length=1, max_length=50)
    last_name: Optional[str] = Field(default=None, max_length=50)
    onboarding: Optional[Dict[str, Any]] = None

    @field_validator("onboarding")
    @classmethod
    def validate_onboarding_weights(
        cls, onboarding: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        if onboarding is not None and (
            "dumbbellWeights" in onboarding or "plateWeights" in onboarding
        ):
            EquipmentWeightsPayload(
                dumbbell_weights=onboarding.get("dumbbellWeights", []),
                plate_weights=onboarding.get("plateWeights", []),
            )
        return onboarding

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
