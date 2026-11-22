"""Strict, ephemeral contracts for selected-calendar reads.

These records are transport data, not canonical user messages or learning evidence.
Calendar text is intentionally absent from audit records and model representations.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_CALENDARS = 100
MAX_EVENTS = 100
MAX_RESULT_BYTES = 512 * 1024
MAX_READ_WINDOW = timedelta(days=31)
MAX_GRANT_LIFETIME = timedelta(hours=1)
MAX_SNAPSHOT_AGE = timedelta(minutes=5)
MAX_READ_LIFETIME = timedelta(seconds=30)

CalendarFailureReason = Literal[
    "permission_not_determined",
    "permission_denied",
    "permission_restricted",
    "provider_unavailable",
    "provider_error",
    "invalid_response",
    "limit_reached",
    "unsupported_projection",
    "catalog_too_large",
]
CalendarAccessCode = Literal[
    "unsupported_projection",
    "invalid_catalog",
    "catalog_expired",
    "selection_invalid",
    "grant_required",
    "grant_expired",
    "request_expired",
    "stale_request",
    "scope_mismatch",
    "source_mismatch",
    "result_invalid",
    "result_too_large",
    "snapshot_expired",
    "audit_unavailable",
    "clock_invalid",
    "permission_not_determined",
    "permission_denied",
    "permission_restricted",
    "provider_unavailable",
    "provider_error",
    "invalid_response",
    "limit_reached",
    "catalog_too_large",
]
CalendarAuditName = Literal[
    "catalog_accepted",
    "grant_issued",
    "grant_revoked",
    "read_prepared",
    "read_accepted",
    "snapshot_expired",
    "access_denied",
]


def require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("utc_datetime_required")
    return value.astimezone(UTC)


def require_timezone(value: str) -> str:
    if len(value) > 128 or not value or value.strip() != value:
        raise ValueError("iana_timezone_required")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("iana_timezone_required") from error
    return value


def require_identifier(value: str) -> str:
    if (
        not value
        or value.strip() != value
        or "*" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("explicit_identifier_required")
    return value


def require_display_text(value: str) -> str:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("single_line_text_required")
    return value


class _CalendarDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class LocalPrincipal(_CalendarDTO):
    """A local owning process/profile session; not the conversational counterparty."""

    os_uid: int = Field(ge=0, le=4_294_967_294)
    profile_id: UUID
    session_id: UUID

    @field_validator("profile_id", "session_id")
    @classmethod
    def nonzero_uuid(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("nonzero_uuid_required")
        return value


class CalendarSourceRef(_CalendarDTO):
    provider: Literal["macos_eventkit", "caldav"] = "macos_eventkit"
    account_id: str = Field(min_length=1, max_length=512, repr=False)
    calendar_id: str = Field(min_length=1, max_length=512, repr=False)

    _identifiers = field_validator("account_id", "calendar_id")(require_identifier)


class CalendarDescriptor(_CalendarDTO):
    source: CalendarSourceRef = Field(repr=False)
    title: str = Field(min_length=1, max_length=512, repr=False)
    account_title: str = Field(min_length=1, max_length=512, repr=False)
    writable: bool | None = None
    allows_modification: bool | None = None
    supports_events: bool | None = None
    source_kind: Literal[
        "local", "exchange", "caldav", "mobileme", "subscribed", "birthdays", "unknown"
    ] = "unknown"

    _text = field_validator("title", "account_title")(require_display_text)

    @model_validator(mode="after")
    def coherent_write_capability(self) -> "CalendarDescriptor":
        if self.writable is True and (
            self.allows_modification is False or self.supports_events is False
        ):
            raise ValueError("calendar_capability_invalid")
        if (
            self.writable is False
            and self.allows_modification is True
            and self.supports_events is True
        ):
            raise ValueError("calendar_capability_invalid")
        return self

    @property
    def write_capability(
        self,
    ) -> Literal["writable", "read_only", "not_reported", "unsupported_entity"]:
        if self.writable is None:
            return "not_reported"
        if self.writable is True:
            return "writable"
        return "unsupported_entity" if self.supports_events is False else "read_only"


class CalendarCatalogDiagnostics(_CalendarDTO):
    """Bounded catalog metadata only; no resource IDs, names or event contents."""

    state: Literal["never_loaded", "fresh", "stale", "loading", "error", "disconnected"]
    total_count: int = Field(default=0, ge=0, le=MAX_CALENDARS)
    writable_count: int = Field(default=0, ge=0, le=MAX_CALENDARS)
    read_only_count: int = Field(default=0, ge=0, le=MAX_CALENDARS)
    unreported_count: int = Field(default=0, ge=0, le=MAX_CALENDARS)
    unsupported_count: int = Field(default=0, ge=0, le=MAX_CALENDARS)
    observed_at: datetime | None = None
    age_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @field_validator("observed_at")
    @classmethod
    def optional_utc(cls, value: datetime | None) -> datetime | None:
        return require_utc(value) if value is not None else None

    @model_validator(mode="after")
    def coherent_counts(self) -> "CalendarCatalogDiagnostics":
        if self.total_count != (
            self.writable_count
            + self.read_only_count
            + self.unreported_count
            + self.unsupported_count
        ):
            raise ValueError("calendar_catalog_counts_invalid")
        if (self.observed_at is None) != (self.age_seconds is None):
            raise ValueError("calendar_catalog_age_invalid")
        if self.state in {"fresh", "stale"} and self.observed_at is None:
            raise ValueError("calendar_catalog_age_required")
        return self


def _validate_outcome(
    status: str, reason: CalendarFailureReason | None, *, allow_partial: bool = False
) -> None:
    reasons: dict[str, set[str | None]] = {
        "ok": {None},
        "permission_denied": {
            "permission_not_determined",
            "permission_denied",
            "permission_restricted",
        },
        "unavailable": {"provider_unavailable"},
        "error": {"provider_error", "invalid_response", "catalog_too_large"},
    }
    if allow_partial:
        reasons["partial"] = {"limit_reached", "unsupported_projection"}
    if reason not in reasons.get(status, set()):
        raise ValueError("incoherent_calendar_outcome")


class CalendarCatalog(_CalendarDTO):
    request_id: UUID
    observed_at: datetime
    status: Literal["ok", "permission_denied", "unavailable", "error"] = "ok"
    calendars: tuple[CalendarDescriptor, ...] = Field(
        default=(), max_length=MAX_CALENDARS, repr=False
    )
    reason: CalendarFailureReason | None = None

    _utc = field_validator("observed_at")(require_utc)

    @model_validator(mode="after")
    def coherent_catalog(self) -> "CalendarCatalog":
        _validate_outcome(self.status, self.reason)
        if self.status != "ok" and self.calendars:
            raise ValueError("failed_catalog_has_no_payload")
        sources = tuple(item.source for item in self.calendars)
        if len(sources) != len(set(sources)):
            raise ValueError("duplicate_calendar_source")
        return self


class CalendarGrant(_CalendarDTO):
    principal: LocalPrincipal
    grant_id: UUID
    generation: int = Field(ge=1)
    operation: Literal["calendar_read"] = "calendar_read"
    resources: tuple[CalendarSourceRef, ...] = Field(
        min_length=1, max_length=MAX_CALENDARS, repr=False
    )
    issued_at: datetime
    expires_at: datetime

    _utc = field_validator("issued_at", "expires_at")(require_utc)

    @model_validator(mode="after")
    def bounded_grant(self) -> "CalendarGrant":
        if not timedelta(0) < self.expires_at - self.issued_at <= MAX_GRANT_LIFETIME:
            raise ValueError("grant_lifetime_out_of_bounds")
        if len(self.resources) != len(set(self.resources)):
            raise ValueError("duplicate_calendar_source")
        return self


class CalendarReadRequest(_CalendarDTO):
    request_id: UUID
    principal: LocalPrincipal
    grant_id: UUID
    generation: int = Field(ge=1)
    resources: tuple[CalendarSourceRef, ...] = Field(
        min_length=1, max_length=MAX_CALENDARS, repr=False
    )
    starts_at: datetime
    ends_at: datetime
    timezone: str
    max_events: int = Field(default=MAX_EVENTS, ge=1, le=MAX_EVENTS)
    max_bytes: int = Field(default=MAX_RESULT_BYTES, ge=1024, le=MAX_RESULT_BYTES)
    created_at: datetime
    expires_at: datetime

    _utc = field_validator("starts_at", "ends_at", "created_at", "expires_at")(require_utc)
    _timezone = field_validator("timezone")(require_timezone)

    @model_validator(mode="after")
    def bounded_request(self) -> "CalendarReadRequest":
        if not timedelta(0) < self.ends_at - self.starts_at <= MAX_READ_WINDOW:
            raise ValueError("calendar_window_out_of_bounds")
        if not timedelta(0) < self.expires_at - self.created_at <= MAX_READ_LIFETIME:
            raise ValueError("calendar_request_lifetime_out_of_bounds")
        if len(self.resources) != len(set(self.resources)):
            raise ValueError("duplicate_calendar_source")
        return self


class CalendarOccurrence(_CalendarDTO):
    """A native occurrence snapshot, never a canonical interaction or an instruction."""

    source: CalendarSourceRef = Field(repr=False)
    event_id: str = Field(min_length=1, max_length=512, repr=False)
    occurrence_id: str = Field(min_length=1, max_length=512, repr=False)
    title: str = Field(max_length=512, repr=False)
    starts_at: datetime | None = Field(default=None, repr=False)
    ends_at: datetime | None = Field(default=None, repr=False)
    all_day_start: date | None = Field(default=None, repr=False)
    all_day_end: date | None = Field(default=None, repr=False)
    timezone: str
    status: Literal["confirmed", "tentative", "cancelled"] = "confirmed"
    revision: str | None = Field(default=None, min_length=1, max_length=512, repr=False)

    _identifiers = field_validator("event_id", "occurrence_id")(require_identifier)
    _text = field_validator("title")(require_display_text)
    _timezone = field_validator("timezone")(require_timezone)

    @field_validator("starts_at", "ends_at")
    @classmethod
    def optional_utc(cls, value: datetime | None) -> datetime | None:
        return require_utc(value) if value is not None else None

    @field_validator("revision")
    @classmethod
    def optional_identifier(cls, value: str | None) -> str | None:
        return require_identifier(value) if value is not None else None

    @model_validator(mode="after")
    def coherent_occurrence(self) -> "CalendarOccurrence":
        timed = self.starts_at is not None or self.ends_at is not None
        all_day = self.all_day_start is not None or self.all_day_end is not None
        if timed == all_day:
            raise ValueError("exactly_one_calendar_time_representation_required")
        if timed:
            if self.starts_at is None or self.ends_at is None or self.starts_at >= self.ends_at:
                raise ValueError("positive_timed_occurrence_required")
        elif (
            self.all_day_start is None
            or self.all_day_end is None
            or self.all_day_start >= self.all_day_end
        ):
            raise ValueError("exclusive_all_day_interval_required")
        return self


class CalendarReadResult(_CalendarDTO):
    request_id: UUID
    observed_at: datetime
    status: Literal["ok", "partial", "permission_denied", "unavailable", "error"] = "ok"
    occurrences: tuple[CalendarOccurrence, ...] = Field(
        default=(), max_length=MAX_EVENTS, repr=False
    )
    reason: CalendarFailureReason | None = None

    _utc = field_validator("observed_at")(require_utc)

    @model_validator(mode="after")
    def coherent_result(self) -> "CalendarReadResult":
        _validate_outcome(self.status, self.reason, allow_partial=True)
        if self.status not in {"ok", "partial"} and self.occurrences:
            raise ValueError("failed_read_has_no_payload")
        return self


class CalendarSnapshot(_CalendarDTO):
    request: CalendarReadRequest = Field(repr=False)
    observed_at: datetime
    expires_at: datetime
    calendars: tuple[CalendarDescriptor, ...] = Field(max_length=MAX_CALENDARS, repr=False)
    occurrences: tuple[CalendarOccurrence, ...] = Field(max_length=MAX_EVENTS, repr=False)
    status: Literal["ok", "partial"] = "ok"
    reason: CalendarFailureReason | None = None

    _utc = field_validator("observed_at", "expires_at")(require_utc)

    @model_validator(mode="after")
    def bounded_snapshot(self) -> "CalendarSnapshot":
        _validate_outcome(self.status, self.reason, allow_partial=True)
        if not timedelta(0) < self.expires_at - self.observed_at <= MAX_SNAPSHOT_AGE:
            raise ValueError("snapshot_lifetime_out_of_bounds")
        return self


class CalendarAuditEvent(_CalendarDTO):
    """The only audit callback payload: closed metadata, never calendar bodies."""

    event: CalendarAuditName
    occurred_at: datetime
    generation: int = Field(ge=0)
    grant_id: UUID | None = None
    request_id: UUID | None = None
    resource_count: int = Field(default=0, ge=0, le=MAX_CALENDARS)
    event_count: int = Field(default=0, ge=0, le=MAX_EVENTS)
    reason: CalendarAccessCode | None = None

    _utc = field_validator("occurred_at")(require_utc)
