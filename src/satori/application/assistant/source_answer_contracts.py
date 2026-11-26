"""Finite, RAM-only contracts for an explicit conversational source question."""

import hashlib
import json
import math
from datetime import datetime, timedelta
from typing import Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from satori.application.assistant.contracts import require_utc

SourceName = Literal["mail", "telegram", "calls"]


def monotonic_window_is_current(tick: float, deadline: float, lifetime: timedelta) -> bool:
    # Compare in the same direction as deadline construction. Subtracting the
    # lifetime from a rounded deadline can place the start after the issuing tick.
    return math.isfinite(tick) and tick < deadline <= tick + lifetime.total_seconds()


class SourceAnswerError(ValueError):
    """Content-free failure; never includes source, user or provider text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SourceDTO(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


def source_digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class SourceTaskScope(SourceDTO):
    identity_id: UUID
    profile_id: UUID
    session_id: UUID
    generation: UUID
    timezone: str = Field(min_length=1, max_length=100)

    @field_validator("identity_id", "profile_id", "session_id", "generation")
    @classmethod
    def nonzero(cls, value: UUID) -> UUID:
        if not value.int:
            raise ValueError("source_task_scope_invalid")
        return value

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("source_task_timezone_invalid") from None
        return value


class SourceTaskRequest(SourceDTO):
    request_id: UUID
    scope: SourceTaskScope = Field(repr=False)
    user_text: str = Field(min_length=1, max_length=8000, repr=False)
    sender_literal: str | None = Field(default=None, min_length=1, max_length=200, repr=False)
    sources: tuple[SourceName, ...] = Field(min_length=1, max_length=3)
    created_at: datetime
    expires_at: datetime

    _utc = field_validator("created_at", "expires_at")(require_utc)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if (
            not self.request_id.int
            or not self.user_text.strip()
            or (
                self.sender_literal is not None
                and (
                    not self.sender_literal.strip()
                    or self.sender_literal.casefold() not in self.user_text.casefold()
                )
            )
            or len(set(self.sources)) != len(self.sources)
            or not timedelta(0) < self.expires_at - self.created_at <= timedelta(seconds=300)
        ):
            raise ValueError("source_task_request_invalid")
        return self

    @model_serializer(mode="wrap")
    def _serialize_wire(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        value: dict[str, object] = handler(self)
        if self.sender_literal is None:
            value.pop("sender_literal", None)
        return value

    @property
    def digest(self) -> str:
        return source_digest(self.model_dump(mode="json"))


class SourceSearchPlan(SourceDTO):
    status: Literal["search", "clarify"]
    clarification: str | None = Field(default=None, min_length=1, max_length=1200, repr=False)
    mail_query: str | None = Field(default=None, min_length=1, max_length=440, repr=False)
    mail_headers_only: bool = False
    telegram_query: str | None = Field(default=None, min_length=1, max_length=512, repr=False)
    telegram_match: Literal["text", "peer"] = "text"
    window_start: datetime
    window_end: datetime
    used_context_refs: tuple[str, ...] = Field(default=(), max_length=24, repr=False)

    _utc = field_validator("window_start", "window_end")(require_utc)

    @model_serializer(mode="wrap")
    def _serialize_wire(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        value: dict[str, object] = handler(self)
        if not self.mail_headers_only:
            value.pop("mail_headers_only", None)
        return value

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (
            not timedelta(0) < self.window_end - self.window_start <= timedelta(days=30)
            or len(set(self.used_context_refs)) != len(self.used_context_refs)
            or any(not ref or len(ref) > 256 for ref in self.used_context_refs)
            or (self.status == "clarify") != (self.clarification is not None)
            or (self.status == "clarify" and (self.mail_query or self.telegram_query))
            or (self.mail_headers_only and (not self.mail_query or self.telegram_query))
            or (self.telegram_match == "peer" and self.telegram_query is None)
        ):
            raise ValueError("source_search_plan_invalid")
        return self

    @property
    def digest(self) -> str:
        return source_digest(self.model_dump(mode="json"))


class SourceObservation(SourceDTO):
    source: SourceName
    source_ref: str = Field(min_length=1, max_length=256)
    observed_at: datetime
    event_at: datetime | None = None
    title: str = Field(default="", max_length=500, repr=False)
    sender: str = Field(default="", max_length=500, repr=False)
    text: str = Field(default="", max_length=4000, repr=False)
    completeness: Literal["complete", "partial", "metadata_only"]

    _utc = field_validator("observed_at")(require_utc)

    @field_validator("event_at")
    @classmethod
    def optional_utc(cls, value: datetime | None) -> datetime | None:
        return require_utc(value) if value is not None else None

    @field_validator("source_ref")
    @classmethod
    def safe_ref(cls, value: str) -> str:
        if not value.isascii() or any(ord(char) < 33 or ord(char) > 126 for char in value):
            raise ValueError("source_observation_ref_invalid")
        return value


class SourceCoverage(SourceDTO):
    source: SourceName
    status: Literal["searched", "partial", "unavailable", "error"]
    query: str | None = Field(default=None, max_length=512, repr=False)
    window_start: datetime
    window_end: datetime
    more: bool = False
    detail: str = Field(default="", max_length=256)

    _utc = field_validator("window_start", "window_end")(require_utc)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if not timedelta(0) < self.window_end - self.window_start <= timedelta(days=30):
            raise ValueError("source_coverage_window_invalid")
        if self.source == "calls" and self.status != "unavailable":
            raise ValueError("source_calls_unavailable")
        if self.more and self.status != "partial":
            raise ValueError("source_coverage_more_requires_partial")
        return self


class SourceTaskSnapshot(SourceDTO):
    request_id: UUID
    scope: SourceTaskScope = Field(repr=False)
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: datetime
    items: tuple[SourceObservation, ...] = Field(default=(), max_length=20, repr=False)
    coverage: tuple[SourceCoverage, ...] = Field(min_length=1, max_length=3, repr=False)

    _utc = field_validator("observed_at")(require_utc)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if (
            not self.request_id.int
            or len({item.source_ref for item in self.items}) != len(self.items)
            or len({item.source for item in self.coverage}) != len(self.coverage)
            or any(item.source not in {c.source for c in self.coverage} for item in self.items)
            or any(item.source == "calls" for item in self.items)
            or sum(len(item.text.encode("utf-8")) for item in self.items) > 32768
            or len(self.model_dump_json().encode("utf-8")) > 48 * 1024
        ):
            raise ValueError("source_task_snapshot_invalid")
        return self

    @property
    def digest(self) -> str:
        return source_digest(self.model_dump(mode="json"))


class SourceTaskAnswer(SourceDTO):
    text: str = Field(min_length=1, max_length=6000, repr=False)
    citations: tuple[str, ...] = Field(default=(), max_length=20)
    verdict: Literal["found", "no_match", "partial", "unknown"]

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if not self.text.strip() or len(set(self.citations)) != len(self.citations):
            raise ValueError("source_task_answer_invalid")
        return self


class SourceTaskLease(SourceDTO):
    """Revocable GUI authority mirror, never a source grant owner."""

    schema_version: Literal[1] = 1
    lease_id: UUID
    request_id: UUID
    scope: SourceTaskScope = Field(repr=False)
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    sources: tuple[SourceName, ...] = Field(min_length=1, max_length=3)
    provider_id: str = Field(min_length=1, max_length=256)
    disclosure_authorized: Literal[True]
    issued_at: datetime
    expires_at: datetime
    monotonic_deadline: float
    ui_pid: int = Field(gt=1)
    os_uid: int = Field(ge=0)

    _utc = field_validator("issued_at", "expires_at")(require_utc)

    @field_validator("disclosure_authorized", mode="before")
    @classmethod
    def explicit_disclosure(cls, value: object) -> Literal[True]:
        if value is not True:
            raise ValueError("source_disclosure_required")
        return True

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (
            not self.lease_id.int
            or not self.request_id.int
            or len(set(self.sources)) != len(self.sources)
            or not timedelta(0) < self.expires_at - self.issued_at <= timedelta(seconds=300)
            or not math.isfinite(self.monotonic_deadline)
            or self.monotonic_deadline <= 0
        ):
            raise ValueError("source_task_lease_invalid")
        return self


class SourceTaskCommand(SourceDTO):
    operation: Literal["route", "plan", "answer", "cancel"]
    request: SourceTaskRequest = Field(repr=False)
    snapshot: SourceTaskSnapshot | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (self.operation == "answer") != (self.snapshot is not None):
            raise ValueError("source_task_command_invalid")
        return self
