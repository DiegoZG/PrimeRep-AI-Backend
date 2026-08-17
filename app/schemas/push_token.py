from typing import Literal

from pydantic import BaseModel, field_validator


class PushTokenDeletePayload(BaseModel):
    token: str

    @field_validator("token")
    @classmethod
    def token_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("token must not be blank")
        return value


class PushTokenPayload(PushTokenDeletePayload):
    platform: Literal["ios", "android"]


class PushTokenResponse(BaseModel):
    token: str
    platform: Literal["ios", "android"]
