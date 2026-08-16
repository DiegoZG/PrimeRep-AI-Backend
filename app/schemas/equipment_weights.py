import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EquipmentWeightsPayload(BaseModel):
    dumbbell_weights: list[float] = Field(alias="dumbbellWeights")
    plate_weights: list[float] = Field(alias="plateWeights")

    @field_validator("dumbbell_weights", "plate_weights", mode="before")
    @classmethod
    def validate_weights(cls, values: Any) -> Any:
        if not isinstance(values, list):
            raise ValueError("weights must be arrays")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            for value in values
        ):
            raise ValueError("weights must be non-negative numeric values")
        return values

    model_config = ConfigDict(populate_by_name=True)


class EquipmentWeightsResponse(EquipmentWeightsPayload):
    pass
