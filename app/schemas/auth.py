from typing import Optional
from pydantic import BaseModel, EmailStr, Field

class SignUpRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    preferred_name: str = Field(min_length=1, max_length=50)
    last_name: Optional[str] = Field(default=None, max_length=50)

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
