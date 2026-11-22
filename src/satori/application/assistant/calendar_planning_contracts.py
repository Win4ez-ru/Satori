"""Bounded, transient calendar context for one explicitly disclosed Core operation."""

import hashlib
import json
from datetime import datetime, timedelta
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from satori.application.assistant.calendar_actions_contracts import (
    CalendarActionDTO,
    CalendarActionScope,
)
from satori.application.assistant.contracts import require_identifier, require_utc


class CalendarPlanningError(ValueError):
    """Finite diagnostic; source/user/provider content never becomes an error message."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def context_digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class CalendarPlanningRequest(CalendarActionDTO):
    request_id: UUID
    scope: CalendarActionScope = Field(repr=False)
    user_text: str = Field(min_length=1, max_length=8000, repr=False)
    title: str = Field(min_length=1, max_length=500, repr=False)
    window_start: datetime
    window_end: datetime
    duration_minutes: int = Field(ge=5, le=720)
    step_minutes: int = Field(default=30, ge=5, le=120)

    _utc = field_validator("window_start", "window_end")(require_utc)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if not self.request_id.int or not self.user_text.strip() or not self.title.strip():
            raise ValueError("calendar_planning_input_invalid")
        duration = self.window_end - self.window_start
        if not timedelta(minutes=self.duration_minutes) <= duration <= timedelta(days=1):
            raise ValueError("calendar_planning_window_invalid")
        if self.window_start.microsecond or self.window_end.microsecond:
            raise ValueError("calendar_planning_second_precision_required")
        return self

    @property
    def digest(self) -> str:
        return context_digest(self.model_dump(mode="json"))


class CalendarObservedEvent(CalendarActionDTO):
    event_ref: str = Field(min_length=1, max_length=256)
    calendar_id: str = Field(min_length=1, max_length=512, repr=False)
    title: str = Field(max_length=500, repr=False)
    starts_at: datetime
    ends_at: datetime
    status: Literal["confirmed", "tentative", "cancelled", "unknown"] = "unknown"

    _utc = field_validator("starts_at", "ends_at")(require_utc)
    _ids = field_validator("event_ref", "calendar_id")(require_identifier)

    @model_validator(mode="after")
    def positive(self) -> Self:
        if self.ends_at <= self.starts_at:
            raise ValueError("calendar_observation_interval_invalid")
        return self


class CalendarPlanningSnapshot(CalendarActionDTO):
    """Exact selected-calendar observation; completeness is a prerequisite for gaps."""

    scope: CalendarActionScope = Field(repr=False)
    read_grant_id: UUID
    selected_calendar_ids: tuple[str, ...] = Field(min_length=1, max_length=20, repr=False)
    observed_at: datetime
    expires_at: datetime
    window_start: datetime
    window_end: datetime
    complete: bool
    events: tuple[CalendarObservedEvent, ...] = Field(default=(), max_length=100, repr=False)

    _utc = field_validator("observed_at", "expires_at", "window_start", "window_end")(require_utc)

    @model_validator(mode="after")
    def exact(self) -> Self:
        ids = self.selected_calendar_ids
        if not self.read_grant_id.int or len(ids) != len(set(ids)):
            raise ValueError("calendar_observation_scope_invalid")
        for identifier in ids:
            require_identifier(identifier)
        if self.scope.calendar_id not in ids:
            raise ValueError("calendar_target_not_selected")
        if not timedelta(0) < self.expires_at - self.observed_at <= timedelta(minutes=5):
            raise ValueError("calendar_observation_lifetime_invalid")
        if not timedelta(0) < self.window_end - self.window_start <= timedelta(days=1):
            raise ValueError("calendar_observation_window_invalid")
        refs = [event.event_ref for event in self.events]
        if len(refs) != len(set(refs)) or any(
            event.calendar_id not in ids for event in self.events
        ):
            raise ValueError("calendar_observation_event_scope_invalid")
        if any(
            event.ends_at <= self.window_start or event.starts_at >= self.window_end
            for event in self.events
        ):
            raise ValueError("calendar_observation_outside_window")
        return self

    @property
    def digest(self) -> str:
        # Readback time/expiry are not event revisions; refreshing unchanged facts
        # must not manufacture a revision. Grant, scope, window and events are bound.
        material = self.model_dump(mode="json", exclude={"observed_at", "expires_at"})
        material["events"] = sorted(material["events"], key=lambda item: item["event_ref"])
        return context_digest(material)

    def require_for(self, request: CalendarPlanningRequest, now: datetime) -> None:
        try:
            checked = CalendarPlanningSnapshot.model_validate(self)
            command = CalendarPlanningRequest.model_validate(request)
            require_utc(now)
        except ValueError:
            raise CalendarPlanningError("calendar_context_invalid") from None
        if (
            checked.scope != command.scope
            or checked.window_start != command.window_start
            or checked.window_end != command.window_end
        ):
            raise CalendarPlanningError("calendar_context_scope_mismatch")
        if not checked.observed_at <= now < checked.expires_at:
            raise CalendarPlanningError("calendar_context_expired")
        if not checked.complete:
            raise CalendarPlanningError("calendar_context_partial")


class CalendarCandidateSlot(CalendarActionDTO):
    slot_id: str = Field(pattern=r"^[0-9a-f]{24}$")
    starts_at: datetime
    ends_at: datetime


class CalendarModelChoice(CalendarActionDTO):
    """A recommendation, never an effect or approval."""

    status: Literal["suggested", "clarification", "declined"]
    slot_id: str | None = Field(pattern=r"^[0-9a-f]{24}$")
    explanation: str = Field(min_length=1, max_length=2000, repr=False)
    used_memory_ids: tuple[str, ...] = Field(max_length=8)
    used_event_refs: tuple[str, ...] = Field(max_length=100)

    @model_validator(mode="after")
    def selected(self) -> Self:
        if (self.status == "suggested") != (self.slot_id is not None):
            raise ValueError("calendar_model_choice_invalid")
        if len(set(self.used_memory_ids)) != len(self.used_memory_ids):
            raise ValueError("calendar_model_duplicate_memory")
        if len(set(self.used_event_refs)) != len(self.used_event_refs):
            raise ValueError("calendar_model_duplicate_source")
        return self


def available_slots(
    request: CalendarPlanningRequest,
    snapshot: CalendarPlanningSnapshot,
    *,
    not_before: datetime | None = None,
) -> tuple[CalendarCandidateSlot, ...]:
    """Pure UTC interval arithmetic; every non-cancelled selected event blocks time."""

    snapshot.require_for(request, snapshot.observed_at)
    if not_before is not None:
        require_utc(not_before)
    result: list[CalendarCandidateSlot] = []
    start = request.window_start
    duration = timedelta(minutes=request.duration_minutes)
    step = timedelta(minutes=request.step_minutes)
    while start + duration <= request.window_end:
        end = start + duration
        if (not_before is None or start >= not_before) and not any(
            event.status != "cancelled" and event.starts_at < end and start < event.ends_at
            for event in snapshot.events
        ):
            if len(result) == 96:
                raise CalendarPlanningError("calendar_candidate_capacity")
            slot_id = context_digest([snapshot.digest, start.isoformat(), end.isoformat()])[:24]
            result.append(CalendarCandidateSlot(slot_id=slot_id, starts_at=start, ends_at=end))
        start += step
    return tuple(result)
