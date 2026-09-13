from typing import Optional
from pydantic import BaseModel, ConfigDict, EmailStr, Field, StrictBool
from app.schemas.onboarding import OnboardingData


class LegalAcceptanceRequest(BaseModel):
    accepted: StrictBool
    terms_version: str = Field(alias="termsVersion")
    privacy_version: str = Field(alias="privacyVersion")

    model_config = ConfigDict(populate_by_name=True)

class SignUpRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    preferred_name: str = Field(min_length=1, max_length=50)
    last_name: Optional[str] = Field(default=None, max_length=50)
    onboarding: Optional[OnboardingData] = None
    legal_acceptance: LegalAcceptanceRequest = Field(alias="legalAcceptance")

    model_config = ConfigDict(populate_by_name=True)

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
    refresh_token: str
    token_type: str = "bearer"


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)
    new_password: str = Field(alias="newPassword", min_length=8, max_length=128)

    model_config = ConfigDict(populate_by_name=True)


class PasswordResetAcceptedResponse(BaseModel):
    message: str
