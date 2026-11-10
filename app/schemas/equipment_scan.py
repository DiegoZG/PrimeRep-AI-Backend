"""Owned scan/discovery v1 DTOs; provider output never crosses this boundary."""
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from app.schemas.exercise_catalog import LoadProfile, StructuredContent


class ScanDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScanVersions(ScanDTO):
    catalog_version: str = Field(min_length=1, max_length=128)
    equipment_revision: str = Field(min_length=1, max_length=128)
    mapping_revision: str = Field(min_length=1, max_length=128)
    resolver_version: str = Field(min_length=1, max_length=64)


class EquipmentSummary(ScanDTO):
    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=160)


class ExerciseOption(ScanDTO):
    exercise_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=160)
    compatibility: Literal["compatible", "needs_equipment_confirmation"]
    required_equipment: list[EquipmentSummary] = Field(max_length=32)
    missing_equipment: list[EquipmentSummary] = Field(max_length=32)
    missing_equipment_ids: list[str] = Field(max_length=32)
    guidance_available: StrictBool
    approved_video_available: StrictBool
    tracking_mode: Optional[Literal["reps", "duration", "distance"]] = None
    load_profile: Optional[LoadProfile] = None

    @model_validator(mode="after")
    def compatible_requirements(self):
        missing = [item.id for item in self.missing_equipment]
        if missing != self.missing_equipment_ids or len(set(missing)) != len(missing):
            raise ValueError("Missing requirement identifiers must match their presentation fields")
        if not set(missing) <= {item.id for item in self.required_equipment}:
            raise ValueError("Missing requirements must be required equipment")
        if (self.compatibility == "compatible") != (not missing):
            raise ValueError("Compatibility must match missing requirements")
        return self


class ScanConfidence(ScanDTO):
    level: Literal["high", "medium", "low", "unknown"] = "unknown"
    score: Optional[float] = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class ScanCandidate(ScanDTO):
    equipment: EquipmentSummary
    confidence: ScanConfidence
    exercise_candidates: list[ExerciseOption] = Field(max_length=3)


class ScanResult(ScanDTO):
    schema_version: Literal[1] = 1
    scan_id: UUID
    status: Literal["candidates", "no_match"]
    requires_confirmation: StrictBool
    versions: ScanVersions
    candidates: list[ScanCandidate] = Field(max_length=5)
    reason: Optional[Literal["not_equipment", "multiple_subjects", "image_unclear", "unsupported_equipment", "uncertain"]] = None
    manual_search_available: Literal[True] = True

    @model_validator(mode="after")
    def result_shape(self):
        if self.status == "candidates":
            if not self.candidates or self.reason is not None or not self.requires_confirmation:
                raise ValueError("Candidates require explicit confirmation and no no-match reason")
            ids = [item.equipment.id for item in self.candidates]
            if len(ids) != len(set(ids)):
                raise ValueError("Equipment candidates must be unique")
        elif self.candidates or self.reason is None or self.requires_confirmation:
            raise ValueError("No-match requires a reason, no candidates and no confirmation")
        return self


class EquipmentSearchPage(ScanDTO):
    schema_version: Literal[1] = 1
    versions: ScanVersions
    items: list[EquipmentSummary] = Field(max_length=20)
    next_cursor: Optional[str] = Field(default=None, max_length=2048)


class ExerciseOptionsPage(ScanDTO):
    schema_version: Literal[1] = 1
    equipment: EquipmentSummary
    versions: ScanVersions
    items: list[ExerciseOption] = Field(max_length=20)
    next_cursor: Optional[str] = Field(default=None, max_length=2048)


class PublishedInstructions(ScanDTO):
    status: Literal["published", "instructions_unavailable"]
    content_version: Optional[str] = Field(default=None, max_length=128)
    content: Optional[StructuredContent] = None

    @model_validator(mode="after")
    def publication_shape(self):
        if (self.status == "published") != (self.content is not None and self.content_version is not None):
            raise ValueError("Published instructions require verified versioned content")
        if self.status == "instructions_unavailable" and (self.content is not None or self.content_version is not None):
            raise ValueError("Unavailable instructions cannot expose unreviewed content")
        return self


class ApprovedVideo(ScanDTO):
    status: Literal["approved", "video_unavailable"]
    asset_id: Optional[str] = Field(default=None, max_length=120)
    url: Optional[str] = Field(default=None, max_length=2048)
    mime_type: Optional[Literal["video/mp4", "video/webm"]] = None
    poster_url: Optional[str] = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def media_shape(self):
        if self.status == "approved":
            if not self.asset_id or not self.url or not self.mime_type or not self.url.startswith("https://"):
                raise ValueError("Approved video requires a verified HTTPS descriptor")
            if self.poster_url and not self.poster_url.startswith("https://"):
                raise ValueError("Approved posters require HTTPS")
        elif any(value is not None for value in (self.asset_id, self.url, self.mime_type, self.poster_url)):
            raise ValueError("Unavailable video cannot expose legacy descriptors")
        return self


class ExerciseGuidance(ScanDTO):
    schema_version: Literal[1] = 1
    exercise_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=160)
    versions: ScanVersions
    tracking_mode: Optional[Literal["reps", "duration", "distance"]] = None
    load_profile: Optional[LoadProfile] = None
    instructions: PublishedInstructions
    video: ApprovedVideo


ScanErrorCode = Literal[
    "scan_authentication_required", "scan_disabled", "scan_access_required", "scan_access_unknown", "scan_in_progress",
    "scan_result_stale", "scan_idempotency_conflict", "scan_attempt_not_found",
    "scan_image_too_large", "scan_media_type_unsupported", "scan_image_invalid",
    "scan_image_not_allowed", "scan_quota_exceeded", "scan_budget_exceeded",
    "scan_provider_unavailable", "scan_provider_timeout", "scan_provider_outcome_unknown",
    "scan_temporarily_unavailable", "scan_invalid_request", "scan_not_found",
]


class ScanErrorDetail(ScanDTO):
    code: ScanErrorCode
    detail: str = Field(min_length=1, max_length=240)
    request_id: UUID
    retry_after_seconds: Optional[int] = Field(default=None, ge=1, le=86400)
    attempt_key: Optional[UUID] = None


class ScanErrorResponse(ScanDTO):
    schema_version: Literal[1] = 1
    error: ScanErrorDetail


class ScanAttemptStatus(ScanDTO):
    schema_version: Literal[1] = 1
    attempt_key: UUID
    status: Literal["processing", "completed", "failed", "unknown"]
    retry_after_seconds: Optional[int] = Field(default=None, ge=1, le=86400)
    result: Optional[ScanResult] = None
    error: Optional[ScanErrorDetail] = None

    @model_validator(mode="after")
    def attempt_shape(self):
        if self.status == "completed" and (self.result is None or self.error is not None):
            raise ValueError("Completed attempts require a result")
        if self.status != "completed" and self.result is not None:
            raise ValueError("Unfinished attempts cannot contain a result")
        if self.status in {"failed", "unknown"} and self.error is None:
            raise ValueError("Unsuccessful attempts require a bounded error")
        if self.status == "processing" and self.error is not None:
            raise ValueError("Processing attempts do not contain a result error")
        return self
