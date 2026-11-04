from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class SubscriptionSnapshot(BaseModel):
    environment: str
    flags_version: int
    flags: dict[str, bool]
    billing_status: Literal["unknown", "none", "trial", "active", "grace", "expired"]
    verification_status: Literal["unverified", "verified", "unavailable", "not_configured"]
    verified_at: Optional[datetime]
    product_id: Optional[str]
    expires_at: Optional[datetime]
    grace_expires_at: Optional[datetime]
    will_renew: Optional[bool]
    management_url: Optional[str]
    has_premium: bool
    can_start_training: Optional[bool]
    access_source: Literal["legacy", "subscription", "transition", "none", "unknown"]
    access_expires_at: Optional[datetime]
    transition_ends_at: Optional[datetime]
    provider_configured: bool
