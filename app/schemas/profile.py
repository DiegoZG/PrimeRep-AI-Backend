from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator


WeightUnit = Literal["LB", "KG"]
KG_PER_LB = 0.45359237
MAX_WEIGHT_KG = 700 * KG_PER_LB


class ProfileResponse(BaseModel):
    id: str
    preferred_name: str
    last_name: Optional[str]
    email: str
    weight: Optional[float]
    weight_unit: WeightUnit
    age: Optional[int]
    gender: Optional[str]
    avatar_version: Optional[str]
    updated_at: datetime


class ProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    preferred_name: Optional[str] = Field(default=None, max_length=50)
    last_name: Optional[str] = Field(default=None, max_length=50)
    weight: Optional[float] = Field(default=None, gt=0)
    weight_unit: Optional[WeightUnit] = None
    age: Optional[StrictInt] = Field(default=None, ge=13, le=120)
    gender: Optional[str] = None

    @field_validator("preferred_name", "last_name", mode="before")
    @classmethod
    def trim_names(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("weight", mode="before")
    @classmethod
    def reject_boolean_weight(cls, value):
        if isinstance(value, bool):
            raise ValueError("Weight must be a number")
        return value

    @field_validator("gender")
    @classmethod
    def normalize_gender(cls, value):
        if value is None or not value.strip():
            return None
        options = {label.lower(): label for label in ("Male", "Female", "Other", "Prefer not to say")}
        if value.strip().lower() not in options:
            raise ValueError("Choose a supported gender option")
        return options[value.strip().lower()]

    @model_validator(mode="after")
    def validate_fields(self):
        if "preferred_name" in self.model_fields_set and not self.preferred_name:
            raise ValueError("Preferred name is required")
        if "weight_unit" in self.model_fields_set and self.weight_unit is None:
            raise ValueError("Weight unit must be LB or KG")
        if self.weight is not None:
            if self.weight_unit is None:
                raise ValueError("Provide weight_unit with weight")
            kilograms = self.weight * KG_PER_LB if self.weight_unit == "LB" else self.weight
            if kilograms > MAX_WEIGHT_KG + 1e-9:
                raise ValueError("Weight must not exceed 700 lb (317.514659 kg)")
        return self
