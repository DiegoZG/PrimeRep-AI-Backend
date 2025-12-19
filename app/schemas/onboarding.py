from datetime import datetime
from typing import Any, Dict

from pydantic import BaseModel, ConfigDict


class OnboardingUpsertRequest(BaseModel):
    data: Dict[str, Any]
    is_complete: bool = False


class OnboardingResponse(BaseModel):
    user_id: str
    data: Dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

