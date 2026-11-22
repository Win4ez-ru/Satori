"""Ephemeral local source cards; never canonical messages or learning evidence."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from satori.application.assistant.contracts import (
    CalendarAccessCode,
    CalendarDescriptor,
    CalendarOccurrence,
    CalendarReadRequest,
    CalendarSourceRef,
    LocalPrincipal,
    require_timezone,
    require_utc,
)


class DialogueDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class SourceInterval(DialogueDTO):
    starts_at: datetime
    ends_at: datetime

    _utc = field_validator("starts_at", "ends_at")(require_utc)

    @model_validator(mode="after")
    def positive(self) -> "SourceInterval":
        if not self.starts_at < self.ends_at:
            raise ValueError("positive_interval_required")
        return self


class CalendarReadProposal(DialogueDTO):
    schema_version: Literal[1] = 1
    proposal_id: UUID
    principal: LocalPrincipal
    chat_id: UUID
    turn_id: UUID
    kind: Literal["agenda", "unoccupied_intervals"]
    day: date
    timezone: str
    window: SourceInterval
    # Computation is explicit and request-local, never a learned working-hours preference.
    minimum_minutes: int = Field(default=30, ge=1, le=1440)

    _timezone = field_validator("timezone")(require_timezone)

    @model_validator(mode="after")
    def same_day(self) -> "CalendarReadProposal":
        zone = ZoneInfo(self.timezone)
        try:
            start = _unambiguous_midnight(self.day, zone)
            end = _unambiguous_midnight(self.day + timedelta(days=1), zone)
        except (OverflowError, ValueError) as error:
            raise ValueError("unsupported_civil_day_boundary") from error
        if not start <= self.window.starts_at < self.window.ends_at <= end:
            raise ValueError("window_outside_selected_day")
        return self


def _unambiguous_midnight(day: date, zone: ZoneInfo) -> datetime:
    local = datetime.combine(day, time.min, zone)
    utc = local.astimezone(UTC)
    if (
        utc.astimezone(zone).replace(tzinfo=None) != local.replace(tzinfo=None)
        or local.replace(fold=1).utcoffset() != local.utcoffset()
    ):
        raise ValueError("ambiguous_or_nonexistent_day_boundary")
    return utc


class DisclosurePermit(DialogueDTO):
    """Minted and retained by the session owner only after an exact UI consent action."""

    permit_id: UUID
    proposal: CalendarReadProposal = Field(repr=False)
    read_grant_id: UUID
    read_generation: int = Field(ge=1)
    resources: tuple[CalendarSourceRef, ...] = Field(min_length=1, max_length=100, repr=False)
    channel: Literal["local_ui"] = "local_ui"
    issued_at: datetime
    expires_at: datetime

    _utc = field_validator("issued_at", "expires_at")(require_utc)

    @model_validator(mode="after")
    def bounded_lifetime(self) -> "DisclosurePermit":
        if not timedelta(0) < self.expires_at - self.issued_at <= timedelta(minutes=5):
            raise ValueError("disclosure_lifetime_out_of_bounds")
        if len(self.resources) != len(set(self.resources)):
            raise ValueError("duplicate_calendar_source")
        return self


class PendingCalendarRead(DialogueDTO):
    permit: DisclosurePermit = Field(repr=False)
    request: CalendarReadRequest = Field(repr=False)


class CalendarSourceReceipt(DialogueDTO):
    """Correlated read receipt, not an event, user report, or evidence root."""

    schema_version: Literal[1] = 1
    receipt_id: UUID
    proposal_id: UUID
    turn_id: UUID
    chat_id: UUID
    request_id: UUID
    permit_id: UUID
    grant_id: UUID
    generation: int = Field(ge=1)
    source_kind: Literal["macos_eventkit"] = "macos_eventkit"
    principal: LocalPrincipal = Field(repr=False)
    read_window: SourceInterval = Field(repr=False)
    timezone: str
    resources: tuple[CalendarSourceRef, ...] = Field(min_length=1, max_length=100, repr=False)
    status: Literal["ok", "empty", "partial", "denied", "unavailable", "error"]
    reason: CalendarAccessCode | None = None
    observed_at: datetime | None = None
    received_at: datetime
    fresh_until: datetime | None = None
    coverage: Literal["selected_calendars_only"] = "selected_calendars_only"
    revision_kind: Literal["snapshot_only_no_server_sync_claim"] = (
        "snapshot_only_no_server_sync_claim"
    )
    retention: Literal["transient_no_canonical_or_learning"] = "transient_no_canonical_or_learning"

    _utc = field_validator("received_at")(require_utc)
    _zone = field_validator("timezone")(require_timezone)

    @field_validator("observed_at", "fresh_until")
    @classmethod
    def optional_utc(cls, value: datetime | None) -> datetime | None:
        return require_utc(value) if value is not None else None

    @model_validator(mode="after")
    def coherent(self) -> "CalendarSourceReceipt":
        if self.status in {"ok", "empty", "partial"}:
            if self.observed_at is None or self.fresh_until is None:
                raise ValueError("successful_read_requires_freshness")
            if not self.observed_at < self.fresh_until:
                raise ValueError("positive_freshness_required")
            if (self.status == "partial") != (self.reason == "limit_reached"):
                raise ValueError("incoherent_partial_receipt")
            if self.status != "partial" and self.reason is not None:
                raise ValueError("successful_read_has_no_failure_reason")
        elif self.reason is None or self.observed_at is not None or self.fresh_until is not None:
            raise ValueError("negative_receipt_has_no_source_observation")
        return self


class CalendarDialogueAudit(DialogueDTO):
    event: Literal[
        "consent_issued",
        "consent_cleared",
        "read_prepared",
        "receipt_accepted",
    ]
    occurred_at: datetime
    permit_id: UUID | None = None
    request_id: UUID | None = None
    receipt_id: UUID | None = None
    outcome: Literal["ok", "empty", "partial", "denied", "unavailable", "error"] | None = None
    count: int = Field(default=0, ge=0, le=101)

    _utc = field_validator("occurred_at")(require_utc)


class GroundedCalendarAnswer(DialogueDTO):
    receipt: CalendarSourceReceipt
    proposal: CalendarReadProposal = Field(repr=False)
    calendars: tuple[CalendarDescriptor, ...] = Field(default=(), max_length=100, repr=False)
    agenda: tuple[CalendarOccurrence, ...] = Field(default=(), max_length=100, repr=False)
    unoccupied: tuple[SourceInterval, ...] = Field(default=(), max_length=101, repr=False)
    answer_kind: Literal["agenda", "unoccupied_intervals", "incomplete", "unavailable"]
    # The application presents these facts as a transient card, never SatoriReply.
    authoritative_free_busy: Literal[False] = False
    tts_eligible: Literal[False] = False

    @model_validator(mode="after")
    def coherent_card(self) -> "GroundedCalendarAnswer":
        receipt, proposal = self.receipt, self.proposal
        if (
            receipt.proposal_id != proposal.proposal_id
            or receipt.turn_id != proposal.turn_id
            or receipt.chat_id != proposal.chat_id
            or receipt.principal != proposal.principal
            or receipt.timezone != proposal.timezone
            or receipt.read_window != proposal.window
        ):
            raise ValueError("answer_proposal_correlation_required")
        if receipt.status in {"denied", "unavailable", "error"}:
            if (
                self.answer_kind != "unavailable"
                or self.agenda
                or self.unoccupied
                or self.calendars
            ):
                raise ValueError("failed_answer_has_no_source_payload")
            return self
        if tuple(item.source for item in self.calendars) != receipt.resources:
            raise ValueError("answer_requires_exact_source_coverage")
        if any(item.source not in receipt.resources for item in self.agenda):
            raise ValueError("agenda_source_outside_receipt")
        if receipt.status == "partial":
            if self.answer_kind != "incomplete" or self.unoccupied:
                raise ValueError("partial_answer_cannot_claim_gaps")
        elif self.answer_kind != proposal.kind:
            raise ValueError("answer_kind_mismatch")
        if proposal.kind == "agenda" and self.unoccupied:
            raise ValueError("agenda_answer_has_no_gap_payload")
        if receipt.status == "empty" and self.agenda:
            raise ValueError("empty_answer_has_no_events")
        previous_end = proposal.window.starts_at
        minimum = timedelta(minutes=proposal.minimum_minutes)
        for interval in self.unoccupied:
            if (
                interval.starts_at < previous_end
                or interval.ends_at > proposal.window.ends_at
                or interval.ends_at - interval.starts_at < minimum
            ):
                raise ValueError("gap_outside_disclosed_bounds")
            previous_end = interval.ends_at
        return self
