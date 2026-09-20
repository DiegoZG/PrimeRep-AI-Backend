from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from app.schemas.exercise import ExerciseType, MuscleGroup, PrimaryMuscle


class LoadProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    basis: Literal["per_implement", "total", "machine", "bodyweight", "added", "assistance"]
    implement_count: Literal[1, 2] = 1


class StructuredContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: list[str] = Field(min_length=1, max_length=20)
    cues: list[str] = Field(min_length=1, max_length=12)
    mistakes: list[str] = Field(min_length=1, max_length=12)
    benefits: str = Field(min_length=1, max_length=4000)
    beginner_guidance: str = Field(min_length=1, max_length=4000)


class ContentSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1)
    url: HttpUrl
    accessed_at: date
    claims: list[str] = Field(min_length=1)


class CatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,119}$")
    name: str = Field(min_length=1, max_length=120)
    exercise_type: ExerciseType
    primary_muscle: PrimaryMuscle
    secondary_muscles: list[MuscleGroup] = Field(default_factory=list)
    equipment_ids: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    movement_pattern: Literal["horizontal_push", "vertical_push", "horizontal_pull", "vertical_pull", "squat", "hinge", "lunge", "carry", "rotation", "anti_rotation", "flexion", "extension", "abduction", "adduction", "plantar_flexion", "isolation", "locomotion"]
    resistance_modality: Literal["bodyweight", "dumbbell", "barbell", "kettlebell", "cable", "machine", "band", "other"]
    difficulty: Literal["no-experience", "beginner", "intermediate", "advanced"]
    laterality: Literal["bilateral", "unilateral", "alternating"]
    load_profile: LoadProfile
    structured_content: StructuredContent
    generation_eligible: bool = False
    tracking_mode: Literal["reps", "duration", "distance"] = "reps"
    sources: list[ContentSource] = Field(min_length=1)
    review_notes: str = ""

    @model_validator(mode="after")
    def validate_lists(self):
        for field in ("equipment_ids", "secondary_muscles", "aliases"):
            values = getattr(self, field)
            if len(values) != len(set(v.strip().casefold() for v in values)):
                raise ValueError(f"Duplicate {field}")
        for value in [self.name, *self.aliases, *self.structured_content.steps,
                      *self.structured_content.cues, *self.structured_content.mistakes,
                      self.structured_content.benefits, self.structured_content.beginner_guidance,
                      *(source.title for source in self.sources),
                      *(claim for source in self.sources for claim in source.claims)]:
            if not value.strip():
                raise ValueError("Content must not be blank")
        if self.primary_muscle in self.secondary_muscles:
            raise ValueError("Primary muscle cannot be secondary")
        if self.generation_eligible and (self.exercise_type == "cardio" or self.tracking_mode != "reps"):
            raise ValueError("Timed/distance exercises are not generation eligible")
        return self


class CatalogManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    exercises: list[CatalogEntry]

    @model_validator(mode="after")
    def unique_exercises(self):
        ids = [entry.id for entry in self.exercises]
        names = [entry.name.strip().casefold() for entry in self.exercises]
        if len(ids) != len(set(ids)) or len(names) != len(set(names)):
            raise ValueError("Duplicate exercise identity")
        return self


class ReviewArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    subject_type: Literal["content", "media"]
    subject_id: str = Field(min_length=1)
    subject_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewer: str = Field(min_length=1)
    role: Literal["trainer", "publisher"]
    decision: Literal["approved", "rejected"]
    comments: str = ""
    reviewed_at: datetime

    @model_validator(mode="after")
    def validate_review(self):
        if not self.reviewer.strip() or not self.subject_id.strip():
            raise ValueError("An explicit reviewer and subject are required")
        if self.reviewed_at.tzinfo is None or self.reviewed_at > datetime.now(timezone.utc):
            raise ValueError("Review time must include its timezone and cannot be in the future")
        return self
