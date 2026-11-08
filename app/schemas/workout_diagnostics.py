from typing import Annotated, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints, field_validator, model_validator


EventType = Literal[
    "local_save", "operation_queued", "sync_attempt", "sync_result", "retry_scheduled",
    "operation_terminal", "queue_snapshot", "finish_result", "diagnostics_dropped",
]
OperationType = Literal[
    "create", "set", "update_set", "delete_set", "restore_set", "update_workout_note",
    "update_exercise_feedback", "update_exercise_note", "order", "replace", "complete", "abandon",
]
Outcome = Literal["success", "failure", "acknowledged", "superseded", "cancelled"]
ErrorCategory = Literal[
    "network", "timeout", "auth", "rate_limited", "server", "validation", "admission_conflict", "storage", "unknown",
]
Count = Annotated[StrictInt, Field(ge=0, le=1_000_000)]
Elapsed = Annotated[StrictInt, Field(ge=0, le=86_400_000)]
QueueAge = Annotated[StrictInt, Field(ge=0, le=31_536_000_000)]
Release = Annotated[str, StringConstraints(strict=True, pattern=r"^(?:unknown|[0-9][0-9A-Za-z.+-]{0,63})$")]


class WorkoutDiagnosticEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    event_type: EventType
    occurred_at_ms: Annotated[StrictInt, Field(ge=0, le=8_640_000_000_000_000)]
    trace_id: Optional[UUID] = None
    operation_type: Optional[OperationType] = None
    outcome: Optional[Outcome] = None
    error_category: Optional[ErrorCategory] = None
    http_status: Optional[Annotated[StrictInt, Field(ge=100, le=599)]] = None
    duration_ms: Optional[Elapsed] = None
    queue_age_ms: Optional[QueueAge] = None
    queue_depth: Optional[Count] = None
    unknown_age_count: Optional[Count] = None
    saved_count: Optional[Count] = None
    dropped_count: Optional[Count] = None
    retry_delay_ms: Optional[Elapsed] = None
    request_attempt: Optional[Count] = None
    acknowledgment_kind: Optional[Literal["direct", "reconciled"]] = None
    recovered_after_restart: Optional[StrictBool] = None
    player_active: Optional[StrictBool] = None
    clock_invalid: Optional[StrictBool] = None
    finish_stage: Optional[Literal["server", "local_cleanup"]] = None

    @model_validator(mode="after")
    def validate_event_fields(self):
        operation_events = {"operation_queued", "sync_attempt", "sync_result", "retry_scheduled", "operation_terminal"}
        if self.event_type in operation_events and (self.trace_id is None or self.operation_type is None):
            raise ValueError("Operation events require trace_id and operation_type")
        if self.event_type == "operation_terminal":
            if self.outcome not in {"acknowledged", "superseded", "cancelled"}:
                raise ValueError("Terminal events require an acknowledged, superseded or cancelled outcome")
            if self.outcome == "acknowledged" and self.acknowledgment_kind is None:
                raise ValueError("Acknowledged operations require acknowledgment_kind")
        elif self.outcome in {"acknowledged", "superseded", "cancelled"}:
            raise ValueError("Terminal outcomes require operation_terminal")
        if self.acknowledgment_kind is not None and not (self.event_type == "operation_terminal" and self.outcome == "acknowledged"):
            raise ValueError("acknowledgment_kind is only valid for acknowledged terminal operations")
        if self.event_type in {"local_save", "sync_result", "finish_result"} and self.outcome not in {"success", "failure"}:
            raise ValueError("Result events require success or failure")
        if self.outcome is not None and self.event_type not in {"local_save", "sync_result", "finish_result", "operation_terminal"}:
            raise ValueError("Only result and terminal events accept outcome")
        if self.error_category is not None and self.outcome != "failure" and self.event_type != "retry_scheduled":
            raise ValueError("Error categories require a failure or scheduled retry")
        if self.event_type == "queue_snapshot" and self.queue_depth is None:
            raise ValueError("Queue snapshots require queue_depth")
        if self.event_type == "diagnostics_dropped" and self.dropped_count is None:
            raise ValueError("Drop events require dropped_count")
        if self.event_type == "finish_result" and self.finish_stage is None:
            raise ValueError("Finish results require finish_stage")
        if self.finish_stage is not None and self.event_type != "finish_result":
            raise ValueError("finish_stage is only valid for finish results")
        if self.unknown_age_count is not None and self.queue_depth is not None and self.unknown_age_count > self.queue_depth:
            raise ValueError("Unknown-age count cannot exceed queue depth")
        if self.clock_invalid and self.queue_age_ms is not None:
            raise ValueError("Invalid clocks cannot report queue age")
        return self


class WorkoutDiagnosticBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    platform: Literal["ios", "android", "web"]
    app_version: Release
    events: Annotated[list[WorkoutDiagnosticEvent], Field(min_length=1, max_length=20)]

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_version_integer(cls, value):
        if type(value) is not int:
            raise ValueError("schema_version must be integer 1")
        return value


class WorkoutDiagnosticReceipt(BaseModel):
    enabled: bool
    accepted: Count
    duplicates: Count
    discarded: Count
