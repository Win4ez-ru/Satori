"""Owned native Calendar transport for one local profile session; no Core I/O."""

import hashlib
import json
import os
import sys
from collections import deque
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from PySide6.QtCore import QObject, QProcess, QThread, QTimer, Signal

from satori.application.assistant.calendar import CalendarAccessCoordinator, CalendarAccessError
from satori.application.assistant.contracts import (
    MAX_RESULT_BYTES,
    CalendarAuditEvent,
    CalendarCatalog,
    CalendarCatalogDiagnostics,
    CalendarDescriptor,
    CalendarFailureReason,
    CalendarGrant,
    CalendarOccurrence,
    CalendarReadRequest,
    CalendarReadResult,
    CalendarSnapshot,
    CalendarSourceRef,
    LocalPrincipal,
    require_display_text,
    require_identifier,
    require_timezone,
    require_utc,
)
from satori.application.assistant.dialogue_contracts import (
    CalendarDialogueAudit,
    CalendarReadProposal,
    GroundedCalendarAnswer,
    PendingCalendarRead,
)
from satori.application.assistant.dialogue_tools import CalendarDialogueSession
from satori.desktop.calendar_status import (
    CalendarConnection,
    CalendarFreshness,
    CalendarOutcome,
    CalendarStatusProjection,
    calendar_status_text,
)
from satori.desktop.schedule_controller import ScheduleController, ScheduleOperation
from satori.desktop.source_status import (
    ConnectionStatus,
    SnapshotStatus,
    SourceProblem,
    SourceStatus,
)

MAX_NATIVE_REQUEST_BYTES = 256 * 1024
ReadOperation = Literal["status", "request_access", "list_calendars", "read_events"]
Operation = ReadOperation | ScheduleOperation
Access = Literal["not_determined", "restricted", "denied", "write_only", "full_access", "unknown"]
NativeError = Literal[
    "invalid_request",
    "permission_required",
    "permission_denied",
    "permission_restricted",
    "permission_request_failed",
    "calendar_unavailable",
    "calendar_scope_mismatch",
    "catalog_too_large",
    "calendar_data_invalid",
    "calendar_read_failed",
    "response_too_large",
    "operation_timeout",
    "platform_unavailable",
]


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class _Calendar(_WireModel):
    source_id: str = Field(min_length=1, max_length=512, repr=False)
    calendar_id: str = Field(min_length=1, max_length=512, repr=False)
    title: str = Field(min_length=1, max_length=512, repr=False)
    source_title: str = Field(min_length=1, max_length=512, repr=False)
    writable: bool | None = None
    allows_modification: bool | None = None
    supports_events: bool | None = None
    source_kind: Literal[
        "local", "exchange", "caldav", "mobileme", "subscribed", "birthdays", "unknown"
    ] = "unknown"

    _ids = field_validator("source_id", "calendar_id")(require_identifier)
    _text = field_validator("title", "source_title")(require_display_text)


class _Event(_WireModel):
    source_id: str = Field(min_length=1, max_length=512, repr=False)
    calendar_id: str = Field(min_length=1, max_length=512, repr=False)
    event_id: str = Field(min_length=1, max_length=512, repr=False)
    occurrence_start: datetime = Field(repr=False)
    title: str = Field(max_length=512, repr=False)
    start: datetime = Field(repr=False)
    end: datetime = Field(repr=False)
    all_day: bool
    start_date: date | None
    end_date: date | None
    time_zone: str
    status: Literal["confirmed", "tentative"]

    _ids = field_validator("source_id", "calendar_id", "event_id")(require_identifier)
    _text = field_validator("title")(require_display_text)
    _utc = field_validator("occurrence_start", "start", "end")(require_utc)
    _zone = field_validator("time_zone")(require_timezone)

    @model_validator(mode="after")
    def coherent_time(self) -> "_Event":
        if self.end <= self.start:
            raise ValueError("calendar_time_invalid")
        if self.all_day:
            if self.start_date is None or self.end_date is None or self.end_date <= self.start_date:
                raise ValueError("calendar_all_day_invalid")
        elif self.start_date is not None or self.end_date is not None:
            raise ValueError("calendar_time_invalid")
        return self


class _Response(_WireModel):
    schema_version: Literal[1]
    request_id: UUID
    operation: ReadOperation
    status: Literal["ok", "error"]
    access: Access
    error: NativeError | None
    observed_at: datetime
    calendars: tuple[_Calendar, ...] | None = Field(default=None, max_length=100, repr=False)
    events: tuple[_Event, ...] | None = Field(default=None, max_length=100, repr=False)
    partial: bool | None = None

    _utc = field_validator("observed_at")(require_utc)

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_schema_integer(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("calendar_schema_invalid")
        return value

    @model_validator(mode="after")
    def coherent_payload(self) -> "_Response":
        if self.status == "error":
            if self.error is None or any(
                item is not None for item in (self.calendars, self.events, self.partial)
            ):
                raise ValueError("calendar_error_payload_invalid")
        elif self.error is not None:
            raise ValueError("calendar_error_invalid")
        elif self.operation == "list_calendars":
            if self.calendars is None or self.events is not None or self.partial is not None:
                raise ValueError("calendar_catalog_invalid")
        elif self.operation == "read_events":
            if self.events is None or self.partial is None or self.calendars is not None:
                raise ValueError("calendar_events_invalid")
        elif any(item is not None for item in (self.calendars, self.events, self.partial)):
            raise ValueError("calendar_payload_invalid")
        return self


def bundled_helper_command() -> tuple[str, ...] | None:
    """Resolve only the packaged helper; development never silently compiles or prompts."""

    directory = getattr(sys, "_MEIPASS", None)
    if not isinstance(directory, str):
        return None
    helper = Path(directory) / "calendar-native" / "SatoriCalendar"
    return (str(helper),) if helper.is_file() and os.access(helper, os.X_OK) else None


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(eq=False)
class _Job:
    process: QProcess
    timer: QTimer
    generation: int
    principal: LocalPrincipal
    request_id: UUID
    operation: Operation
    request: CalendarReadRequest | None
    payload: bytes = field(repr=False)
    output: bytearray = field(default_factory=bytearray, repr=False)
    disposed: bool = False
    launched: bool = False
    terminal_received: bool = False
    dialogue: PendingCalendarRead | None = field(default=None, repr=False)
    schedule_operation: bool = False
    planning_operation: bool = False
    planning_notified: bool = False


class CalendarController(QObject):
    """Explicit permission/catalog/selected-read lifecycle, fenced from every other owner."""

    changed = Signal()
    status_changed = Signal(str)
    dialogue_changed = Signal()
    shutdown_finished = Signal()
    planning_read_ready = Signal(str, object)
    planning_read_failed = Signal(str, str)

    def __init__(
        self,
        data_directory: Path,
        parent: QObject | None = None,
        *,
        helper_command: Callable[[], Sequence[str] | None] = bundled_helper_command,
        process_factory: Callable[[QObject], QProcess] = QProcess,
        coordinator_factory: Callable[
            [LocalPrincipal, Callable[[], datetime]], CalendarAccessCoordinator
        ]
        | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        super().__init__(parent)
        self._helper_command = helper_command
        self._process_factory = process_factory
        self._coordinator_factory = coordinator_factory
        self._clock = clock
        self._audit: deque[CalendarAuditEvent] = deque(maxlen=256)
        self._dialogue_audit: deque[CalendarDialogueAudit] = deque(maxlen=256)
        self._dialogue_chat_id: UUID | None = None
        self._dialogue: CalendarDialogueSession | None = None
        self._dialogue_status = "idle"
        self._had_dialogue_answer = False
        self._generation = 0
        self._job: _Job | None = None
        self._retired: set[_Job] = set()
        self._closed = False
        self._closing = False
        self._shutdown_notified = False
        self.status = "disconnected"
        self._had_grant = False
        self._had_snapshot = False
        self._show_snapshot = False
        self.permission_status: Access = "unknown"
        self.last_successful_read_at: datetime | None = None
        self._catalog_phase: Literal[
            "never_loaded", "loading", "active", "error", "disconnected"
        ] = "never_loaded"
        self._coordinator = self._new_coordinator(data_directory)
        self.schedule = ScheduleController(self, data_directory=data_directory, clock=self._clock)
        self.poll = QTimer(self)
        self.poll.setInterval(15_000)
        self.poll.timeout.connect(self.tick)
        self.poll.start()
        self._expiry = QTimer(self)
        self._expiry.setSingleShot(True)
        self._expiry.timeout.connect(self.tick)

    def _new_coordinator(self, path: Path) -> CalendarAccessCoordinator:
        profile = path.expanduser().resolve(strict=False)
        principal = LocalPrincipal(
            os_uid=os.getuid(), profile_id=uuid5(NAMESPACE_URL, str(profile)), session_id=uuid4()
        )
        if self._coordinator_factory is not None:
            return self._coordinator_factory(principal, self._clock)
        return CalendarAccessCoordinator(principal, clock=self._clock, audit=self._audit.append)

    @property
    def audit_events(self) -> tuple[CalendarAuditEvent, ...]:
        return tuple(self._audit)

    @property
    def dialogue_audit_events(self) -> tuple[CalendarDialogueAudit, ...]:
        return tuple(self._dialogue_audit)

    @property
    def dialogue_chat_id(self) -> UUID | None:
        return self._dialogue_chat_id

    @property
    def dialogue_status(self) -> str:
        # Inspect freshness even if the consumer requests status before the answer.
        _ = self.dialogue_answer
        return self._dialogue_status

    @property
    def dialogue_answer(self) -> GroundedCalendarAnswer | None:
        answer = self._dialogue.current_answer() if self._dialogue else None
        if answer is None and self._had_dialogue_answer:
            self._dialogue_status = "stale"
        self._had_dialogue_answer = answer is not None
        return answer

    def bind_dialogue(self, chat_id: UUID) -> None:
        """A local view session, separate from the canonical conversation owner."""
        if self._closed:
            return
        self.clear_dialogue("context_changed")
        self._dialogue_chat_id = chat_id
        self._dialogue = CalendarDialogueSession(
            self._coordinator, chat_id, clock=self._clock, audit=self._dialogue_audit.append
        )
        self._dialogue_status = "idle"
        self.dialogue_changed.emit()

    def _invalidate_dialogue(self, status: str) -> None:
        if self._dialogue is not None:
            with suppress(ValueError):
                self._dialogue.clear()
        self._dialogue_status = status
        self._had_dialogue_answer = False
        self.dialogue_changed.emit()

    def clear_dialogue(self, status: str = "cancelled") -> None:
        """Clear only the transient answer; cancel only an owned dialogue read."""
        cancelled = self._job is not None and self._job.dialogue is not None
        if cancelled:
            self._generation += 1
            self._cancel_job()
        self._invalidate_dialogue(status)
        if cancelled:
            self._notify("grant_ready" if self.grant else "grant_required")

    def submit_dialogue(self, proposal: CalendarReadProposal) -> None:
        """Explicit local-display consent precedes one exact native read, outside Core."""
        owner = self._dialogue
        if self._closed or owner is None:
            return
        if proposal.chat_id != self._dialogue_chat_id or proposal.principal != self.principal:
            return  # A stale view must not clear the newer session's work.
        self.clear_dialogue("superseded")
        self._generation += 1
        self._show_snapshot = False
        self._cancel_job()
        try:
            permit = owner.consent(proposal)
            pending = owner.prepare(permit)
        except ValueError:
            self._invalidate_dialogue("grant_required" if self.grant is None else "invalid_query")
            self._notify()
            return
        self._dialogue_status = "reading"
        self._start("read_events", pending.request, dialogue=pending)

    @property
    def principal(self) -> LocalPrincipal:
        return self._coordinator.principal

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def busy(self) -> bool:
        return self._job is not None

    @property
    def shutdown_complete(self) -> bool:
        """True only after close and terminal receipts from every launched helper."""
        return self._closed and not self._closing and self._job is None and not self._retired

    @property
    def catalog(self) -> tuple[CalendarDescriptor, ...]:
        try:
            return self._coordinator.catalog
        except CalendarAccessError as error:
            self.status = error.code
            return ()

    @property
    def catalog_diagnostics(self) -> CalendarCatalogDiagnostics:
        try:
            metadata = self._coordinator.catalog_diagnostics
        except CalendarAccessError:
            return CalendarCatalogDiagnostics(state="error")
        if self._catalog_phase == "active":
            return metadata
        return metadata.model_copy(update={"state": self._catalog_phase})

    @property
    def selected_calendars(self) -> tuple[CalendarDescriptor, ...]:
        """Exact descriptors already bound to the current grant, never a new selection."""
        try:
            return self._coordinator.granted_calendars
        except CalendarAccessError as error:
            self.status = error.code
            return ()

    @property
    def status_projection(self) -> CalendarStatusProjection:
        """Home/assistant metadata from existing owners, with no native or Core I/O."""
        grant, snapshot = self.grant, self.snapshot
        answer = self.dialogue_answer
        catalog_state = self.catalog_diagnostics.state
        selected = self.selected_calendars
        connection: CalendarConnection
        if self.status == "disconnected" or self._closed:
            connection = "disconnected"
        elif self.status == "requesting_access" or self.status == "permission_not_determined":
            connection = "permission_required"
        elif self.status == "connecting":
            connection = "connecting"
        elif self.status in {"permission_denied", "permission_restricted", "denied"}:
            connection = "permission_denied"
        elif self.status in {"provider_unavailable", "unavailable"}:
            connection = "unavailable"
        elif self.status in {"clock_invalid", "audit_unavailable"}:
            connection = "error"
        elif self.status in {
            "select_calendars",
            "catalog_empty",
            "catalog_expired",
            "selection_invalid",
            "grant_required",
        }:
            connection = "needs_selection"
        elif grant is None and (self.status == "grant_expired" or self._had_grant):
            connection = "session_expired"
        elif self.status in {
            "grant_ready",
            "ready",
            "empty",
            "partial",
            "reading",
            "snapshot_expired",
        }:
            connection = "connected" if grant is not None else "needs_selection"
        else:
            connection = "error"
        freshness: CalendarFreshness = "not_read"
        outcome: CalendarOutcome = "none"
        observed_at, fresh_until = None, None
        if snapshot is not None:
            observed_at, fresh_until = snapshot.observed_at, snapshot.expires_at
            freshness = "fresh"
            outcome = (
                "partial"
                if snapshot.status == "partial"
                else "ok"
                if snapshot.occurrences
                else "empty"
            )
        elif answer is not None and answer.receipt.status in {"ok", "empty", "partial"}:
            observed_at, fresh_until = answer.receipt.observed_at, answer.receipt.fresh_until
            freshness = "fresh"
            outcome = (
                "partial"
                if answer.receipt.status == "partial"
                else "empty"
                if answer.receipt.status == "empty"
                else "ok"
            )
        elif (
            self._had_snapshot
            or self.status == "snapshot_expired"
            or self._dialogue_status == "stale"
        ):
            freshness = "stale"
        age_seconds = None
        if observed_at is not None:
            try:
                age_seconds = max(0.0, (require_utc(self._clock()) - observed_at).total_seconds())
            except (ValueError, TypeError, AttributeError):
                connection, freshness, outcome = "error", "not_read", "none"
                observed_at, fresh_until = None, None
        busy = self.busy
        reading = busy and (self.status == "reading" or self._dialogue_status == "reading")
        count = len(grant.resources) if grant is not None else 0
        can_refresh = grant is not None and not busy and not self._closed
        return CalendarStatusProjection(
            connection=connection,
            permission=self.permission_status,
            catalog_state=catalog_state,
            selected_count=count,
            freshness=freshness,
            outcome=outcome,
            busy=busy,
            reading=reading,
            can_connect=not busy and not self._closed,
            can_refresh=can_refresh,
            can_query_dialogue=(
                can_refresh
                and self._dialogue_chat_id is not None
                and tuple(item.source for item in selected) == grant.resources
                if grant is not None
                else False
            ),
            last_successful_read_at=self.last_successful_read_at,
            observed_at=observed_at,
            fresh_until=fresh_until,
            age_seconds=age_seconds,
            status_text=calendar_status_text(
                connection, freshness, outcome, selected_count=count, busy=busy, reading=reading
            ),
        )

    @property
    def source_status(self) -> SourceStatus:
        """Common workspace projection; no new capability or account operation."""
        local = self.status_projection
        connection: ConnectionStatus
        problem: SourceProblem = "none"
        if self._closed:
            connection = "disconnected" if self.shutdown_complete else "closing"
        elif local.connection == "connected":
            connection = "connected"
        elif local.connection == "needs_selection":
            if local.catalog_state == "stale":
                connection, problem = "expired", "expired"
            else:
                connection = "connected" if local.permission == "full_access" else "disconnected"
        elif local.connection == "session_expired":
            connection, problem = "expired", "expired"
        elif local.connection == "connecting":
            connection = "connecting"
        elif local.connection in {"permission_required", "permission_denied"}:
            connection = "connecting" if local.busy else "error"
            problem = "permission"
        elif local.connection == "unavailable":
            connection, problem = "unavailable", "unavailable"
        elif local.connection == "disconnected":
            connection = "disconnected"
        else:
            connection = "error"
            problem = (
                "transport"
                if self.status in {"provider_error", "request_expired"}
                else "rejected"
                if self.status
                in {
                    "invalid_response",
                    "scope_mismatch",
                    "source_mismatch",
                    "result_invalid",
                    "result_too_large",
                    "clock_invalid",
                    "audit_unavailable",
                    "timezone_invalid",
                }
                else "unknown"
            )
        snapshot: SnapshotStatus = (
            "partial"
            if local.freshness == "fresh" and local.outcome == "partial"
            else "fresh"
            if local.freshness == "fresh"
            else "stale"
            if local.freshness == "stale"
            else "unknown"
            if connection in {"error", "unavailable"}
            else "not_read"
        )
        return SourceStatus(
            source="calendar",
            connection=connection,
            selected_count=local.selected_count,
            snapshot=snapshot,
            observed_at=local.observed_at,
            busy=local.busy or connection == "closing",
            problem=problem,
        )

    @property
    def grant(self) -> CalendarGrant | None:
        try:
            return self._coordinator.grant
        except CalendarAccessError as error:
            self.status = error.code
            return None

    @property
    def snapshot(self) -> CalendarSnapshot | None:
        try:
            snapshot = self._coordinator.snapshot
            return snapshot if self._show_snapshot else None
        except CalendarAccessError as error:
            self.status = error.code
            return None

    def _notify(self, status: str | None = None) -> None:
        if status is not None:
            self.status = status
        grant, snapshot = self.grant, self.snapshot
        self._had_grant = grant is not None
        self._had_snapshot = snapshot is not None
        self._expiry.stop()
        if grant is not None:
            deadline = min(grant.expires_at, snapshot.expires_at) if snapshot else grant.expires_at
            answer = self.dialogue_answer
            if answer is not None and answer.receipt.fresh_until is not None:
                deadline = min(deadline, answer.receipt.fresh_until)
            try:
                milliseconds = max(
                    1, int((deadline - require_utc(self._clock())).total_seconds() * 1000)
                )
                self._expiry.start(milliseconds)
            except (ValueError, TypeError, AttributeError):
                self.status = "clock_invalid"
        self.status_changed.emit(self.status)
        self.changed.emit()

    def tick(self) -> None:
        if self._closed:
            return
        grant, snapshot = self.grant, self.snapshot
        if self._had_grant and grant is None:
            self._cancel_job()
            status = (
                self.status
                if self.status in {"clock_invalid", "audit_unavailable"}
                else "grant_expired"
            )
            self._invalidate_dialogue(status)
            self._notify(status)
        elif self._had_snapshot and snapshot is None:
            self._notify("snapshot_expired")
        else:
            self._notify()

    def set_profile(self, data_directory: Path) -> None:
        """A fresh process/profile principal can never inherit the previous selection."""

        self.disconnect_calendar()
        self._audit.clear()
        self._dialogue_audit.clear()
        self._coordinator = self._new_coordinator(data_directory)
        self.schedule.set_profile(data_directory)
        self.permission_status = "unknown"
        self.last_successful_read_at = None
        self._catalog_phase = "never_loaded"
        if self._dialogue_chat_id is not None:
            self.bind_dialogue(self._dialogue_chat_id)
        self._notify("disconnected")

    def disconnect_calendar(self) -> None:
        self._reset_calendar("disconnected")

    def _reset_calendar(self, status: str) -> None:
        self._catalog_phase = "loading" if status == "connecting" else "disconnected"
        self._generation += 1
        self._show_snapshot = False
        self._cancel_job()
        self._invalidate_dialogue(status)
        with suppress(CalendarAccessError):
            self._coordinator.revoke()
        # Revoke clears catalog names/identifiers as well as event payloads.
        self._notify(status)

    def connect_calendar(self) -> None:
        """Only the explicit connect gesture may start an OS permission/catalog operation."""

        if self._closed:
            return
        # Refreshing the catalog revokes all read/write permits, while retaining
        # the user's editable local schedule. Explicit Disconnect clears that draft.
        self._reset_calendar("connecting")
        self._start("status")

    def refresh_catalog(self) -> None:
        """A direct foreground task may refresh metadata, never request OS access."""
        if self._closed or self.busy:
            return
        self._reset_calendar("connecting")
        # list_calendars reports missing access; unlike status it cannot enter
        # the explicit Connect gesture's request_access continuation.
        self._start("list_calendars")

    def selection_changed(self) -> None:
        self._generation += 1
        self._show_snapshot = False
        self._cancel_job()
        self._invalidate_dialogue("selection_changed")
        try:
            self._coordinator.suspend_selection()
        except CalendarAccessError as error:
            self._notify(error.code)
            return
        self._notify("select_calendars")

    def use_calendars(self, selected: tuple[CalendarSourceRef, ...]) -> bool:
        if self._closed:
            return False
        self._generation += 1
        self._show_snapshot = False
        self._cancel_job()
        self._invalidate_dialogue("selection_changed")
        try:
            self._coordinator.issue(selected)
        except CalendarAccessError as error:
            self._notify(error.code)
            return False
        self._notify("grant_ready")
        return True

    def read_day(self, day: date, timezone: str) -> None:
        if self._closed:
            return
        self._generation += 1
        self._show_snapshot = False
        self._cancel_job()
        self._invalidate_dialogue("superseded")
        try:
            request = self._coordinator.prepare_day(day, timezone)
        except (CalendarAccessError, ValueError) as error:
            self._notify(
                error.code if isinstance(error, CalendarAccessError) else "timezone_invalid"
            )
            return
        self._start("read_events", request)

    def planning_catalog(self) -> tuple[UUID, datetime, tuple[CalendarDescriptor, ...]]:
        """GUI-thread projection of existing selected metadata; never grants access."""
        if QThread.currentThread() != self.thread() or self._closed:
            raise ValueError("calendar_planning_context_unavailable")
        diagnostic = self._coordinator.catalog_diagnostics
        observed = diagnostic.observed_at
        selected = self._coordinator.granted_calendars
        if diagnostic.state != "fresh" or observed is None or not selected:
            raise ValueError("calendar_planning_context_unavailable")
        material = json.dumps(
            {
                "observed_at": observed.isoformat(),
                "calendars": [c.model_dump(mode="json") for c in selected],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return uuid5(NAMESPACE_URL, material), observed, selected

    def request_planning_read(self, start: datetime, end: datetime, timezone: str) -> UUID:
        """One explicit planning RPC through the sole GUI-owned read coordinator."""
        identifier = uuid4()
        if QThread.currentThread() != self.thread() or self._closed or self.busy:
            self.planning_read_failed.emit(str(identifier), "calendar_read_unavailable")
            return identifier
        try:
            request = self._coordinator.prepare_read(start, end, timezone)
        except (CalendarAccessError, ValueError):
            self.planning_read_failed.emit(str(identifier), "calendar_read_unavailable")
            return identifier
        self._show_snapshot = False
        self._start_owned("read_events", request, planning_operation=True)
        return request.request_id

    def cancel_planning_read(self) -> None:
        """Cancel only an owned planning helper; retain the independent read grant."""
        if self._job is not None and self._job.planning_operation:
            self._generation += 1
            self._cancel_job()
            self._notify("grant_ready" if self.grant else "grant_required")

    def _start(
        self,
        operation: Operation,
        request: CalendarReadRequest | None = None,
        *,
        dialogue: PendingCalendarRead | None = None,
        schedule_body: dict[str, object] | None = None,
    ) -> None:
        """Keep the existing transport override contract for source components."""
        self._start_owned(operation, request, dialogue=dialogue, schedule_body=schedule_body)

    def _start_owned(
        self,
        operation: Operation,
        request: CalendarReadRequest | None = None,
        *,
        dialogue: PendingCalendarRead | None = None,
        schedule_body: dict[str, object] | None = None,
        planning_operation: bool = False,
    ) -> None:
        if self._closed:
            return

        def failed(code: str) -> None:
            if planning_operation and self._job is None:
                self.planning_read_failed.emit(
                    str(request.request_id) if request is not None else str(uuid4()),
                    "calendar_read_unavailable",
                )
            if schedule_body is not None:
                self.schedule.transport_failed(code)
                self._cancel_job()
                self._notify()
            else:
                self._failure(code)

        try:
            command = self._helper_command()
            if (
                not command
                or len(command) > 16
                or any(
                    not isinstance(arg, str) or not arg or len(arg) > 4096 or "\x00" in arg
                    for arg in command
                )
                or not Path(command[0]).is_absolute()
            ):
                failed("provider_unavailable")
                return
            identifier = request.request_id if request else uuid4()
            body: dict[str, object] = {
                "schema_version": 1,
                "request_id": str(identifier),
                "operation": operation,
            }
            if schedule_body is not None:
                body.update(schedule_body)
            if request is not None:
                body.update(
                    selected_calendars=[
                        {"source_id": item.account_id, "calendar_id": item.calendar_id}
                        for item in request.resources
                    ],
                    start=request.starts_at.isoformat(),
                    end=request.ends_at.isoformat(),
                    time_zone=request.timezone,
                    max_events=request.max_events,
                )
            payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
            if len(payload) > MAX_NATIVE_REQUEST_BYTES:
                failed("result_too_large")
                return
            process = self._process_factory(self)
            timer = QTimer(self)
            timer.setSingleShot(True)
            job = _Job(
                process,
                timer,
                self._generation,
                self.principal,
                identifier,
                operation,
                request,
                payload,
                dialogue=dialogue,
                schedule_operation=schedule_body is not None,
                planning_operation=planning_operation,
            )
            self._job = job
            process.started.connect(lambda: self._started(job))
            process.readyReadStandardOutput.connect(lambda: self._read(job))
            process.readyReadStandardError.connect(lambda: process.readAllStandardError())
            process.finished.connect(lambda code, state: self._finished(job, code, state))
            process.errorOccurred.connect(lambda error: self._process_error(job, error))
            timer.timeout.connect(lambda: self._timed_out(job))
            process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
            process.setProgram(command[0])
            process.setArguments(list(command[1:]))
            # Cold local Vision model startup can exceed the normal read deadline.
            # The native child still owns its independent 89/90-second watchdog.
            timer.start(
                120_000 if operation in {"request_access", "recognize_schedule_image"} else 30_000
            )
            self._notify(
                self.status
                if schedule_body is not None
                else "requesting_access"
                if operation == "request_access"
                else "reading"
                if request
                else "connecting"
            )
            if self._current(job):
                job.launched = True
                try:
                    process.start()
                except Exception:
                    if process.state() == QProcess.ProcessState.NotRunning:
                        job.terminal_received = True
                    raise
        except Exception:
            failed("provider_unavailable")

    def start_schedule(self, operation: ScheduleOperation, body: dict[str, object]) -> bool:
        """Use the same owned helper for a closed, explicitly prepared schedule operation."""
        allowed = (
            {"image_path"}
            if operation == "recognize_schedule_image"
            else {"target", "rows", "plan_fingerprint"}
        )
        if (
            self._closed
            or self.busy
            or operation
            not in {
                "recognize_schedule_image",
                "preflight_schedule",
                "create_schedule",
                "reconcile_schedule",
            }
            or set(body) != allowed
            or not self.schedule.authorize_transport(operation, body)
        ):
            return False
        self._start(operation, schedule_body=body)
        return self._job is not None

    def cancel_schedule(self) -> None:
        """Cancel only the owned import operation; a possible write remains UNKNOWN."""
        if self._job is not None and self._job.schedule_operation:
            self._cancel_job()
            self._notify()

    def observe_permission_loss(self, access: Access) -> None:
        """A correlated denial invalidates cached reads as well as future requests."""
        self.permission_status = access
        if access in {"denied", "restricted", "not_determined", "write_only"}:
            self._failure(
                "permission_restricted"
                if access == "restricted"
                else "permission_not_determined"
                if access == "not_determined"
                else "permission_denied"
            )

    def _current(self, job: _Job) -> bool:
        return (
            not self._closed
            and self._job is job
            and job.generation == self._generation
            and job.principal == self.principal
        )

    def _started(self, job: _Job) -> None:
        if not self._current(job):
            return
        if job.schedule_operation:
            body = json.loads(job.payload)
            for key in ("schema_version", "request_id", "operation"):
                body.pop(key)
            if not self.schedule.authorize_transport(job.operation, body):
                self._failure("stale_request")
                return
        if job.request is not None:
            if job.dialogue is not None and (
                self._dialogue is None or self._dialogue.pending != job.dialogue
            ):
                self._failure("stale_request")
                return
            grant = self.grant
            if grant is None:
                self._failure(
                    self.status
                    if self.status in {"clock_invalid", "audit_unavailable"}
                    else "grant_expired"
                )
                return
            try:
                now = require_utc(self._clock())
            except (ValueError, TypeError, AttributeError):
                self._failure("clock_invalid")
                return
            if now >= job.request.expires_at:
                self._failure("request_expired")
                return
            if (
                grant.principal != job.request.principal
                or grant.grant_id != job.request.grant_id
                or grant.generation != job.request.generation
                or grant.resources != job.request.resources
            ):
                self._failure("stale_request")
                return
        if job.process.write(job.payload) != len(job.payload):
            self._failure("provider_error")
            return
        job.payload = b""
        job.process.closeWriteChannel()

    def _read(self, job: _Job) -> None:
        if not self._current(job):
            return
        chunk = job.process.readAllStandardOutput().data()
        if len(job.output) + len(chunk) > MAX_RESULT_BYTES:
            self._failure("result_too_large")
            return
        job.output.extend(chunk)

    def _process_error(self, job: _Job, error: QProcess.ProcessError) -> None:
        if job.disposed:
            return
        if error == QProcess.ProcessError.FailedToStart:
            job.terminal_received = True
        if not self._current(job):
            # Crashed can arrive while the cancelled process is still Running. Keep
            # ownership until finished; FailedToStart may never emit finished.
            if error == QProcess.ProcessError.FailedToStart:
                self._dispose(job)
        elif error == QProcess.ProcessError.FailedToStart:
            self._failure("provider_unavailable")

    def _timed_out(self, job: _Job) -> None:
        if self._current(job):
            self._failure("request_expired")

    def _finished(self, job: _Job, code: int, state: QProcess.ExitStatus) -> None:
        if job.disposed:
            return
        job.terminal_received = True
        if not self._current(job):
            self._dispose(job)
            return
        self._read(job)
        if not self._current(job):
            return
        if job.schedule_operation:
            data = bytes(job.output)
            self._job = None
            self._dispose(job)
            self.schedule.accept_transport(
                job.operation,
                job.request_id,
                data,
                code,
                state == QProcess.ExitStatus.NormalExit,
            )
            self._notify()
            return
        try:
            result = _Response.model_validate_json(job.output)
            if (
                state != QProcess.ExitStatus.NormalExit
                or code not in {0, 1}
                or (result.status == "ok" and code != 0)
                or result.request_id != job.request_id
                or result.operation != job.operation
            ):
                raise ValueError("calendar_response_invalid")
        except (ValidationError, ValueError):
            self._failure("invalid_response")
            return
        self._job = None
        self._dispose(job)
        try:
            self._accept(job, result)
        except (ValidationError, ValueError) as error:
            if job.planning_operation and not job.planning_notified:
                job.planning_notified = True
                self.planning_read_failed.emit(str(job.request_id), "calendar_read_unavailable")
            self._failure(
                error.code if isinstance(error, CalendarAccessError) else "invalid_response"
            )

    def _accept(self, job: _Job, result: _Response) -> None:
        self.permission_status = result.access
        if job.planning_operation and (result.status == "error" or result.access != "full_access"):
            job.planning_notified = True
            self.planning_read_failed.emit(str(job.request_id), "calendar_read_unavailable")
        if job.dialogue is not None and (
            result.status == "error" or result.access != "full_access"
        ):
            # A validated, correlated negative source response has the same sole admission
            # path as success. Malformed/uncorrelated transport still fails before this point.
            reason: CalendarFailureReason
            if result.error == "permission_required" or result.access == "not_determined":
                reason = "permission_not_determined"
            elif result.error == "permission_restricted" or result.access == "restricted":
                reason = "permission_restricted"
            elif result.error == "permission_denied" or result.access in {"denied", "write_only"}:
                reason = "permission_denied"
            elif result.error in {"platform_unavailable", "calendar_unavailable"}:
                reason = "provider_unavailable"
            elif result.error in {"calendar_data_invalid", "calendar_scope_mismatch"}:
                reason = "invalid_response"
            else:
                reason = "provider_error"
            failed_status: Literal["permission_denied", "unavailable", "error"] = (
                "permission_denied"
                if reason.startswith("permission_")
                else "unavailable"
                if reason == "provider_unavailable"
                else "error"
            )
            self._accept_dialogue(
                job,
                CalendarReadResult(
                    request_id=result.request_id,
                    observed_at=result.observed_at,
                    status=failed_status,
                    reason=reason,
                ),
            )
            return
        if result.status == "error":
            self._failure(
                {
                    "permission_required": "permission_not_determined",
                    "permission_denied": "permission_denied",
                    "permission_restricted": "permission_restricted",
                    "calendar_scope_mismatch": "scope_mismatch",
                    "catalog_too_large": "catalog_too_large",
                    "response_too_large": "result_too_large",
                    "operation_timeout": "request_expired",
                    "platform_unavailable": "provider_unavailable",
                    "calendar_unavailable": "provider_unavailable",
                    "calendar_data_invalid": "invalid_response",
                }.get(result.error or "", "provider_error")
            )
            return
        if result.access != "full_access":
            if job.operation == "status" and result.access in {"not_determined", "write_only"}:
                self._start("request_access")
            else:
                self._failure(
                    "permission_restricted"
                    if result.access == "restricted"
                    else "permission_denied"
                )
            return
        if job.operation in {"status", "request_access"}:
            self._start("list_calendars")
        elif job.operation == "list_calendars":
            descriptors = tuple(
                CalendarDescriptor(
                    source=CalendarSourceRef(
                        account_id=item.source_id, calendar_id=item.calendar_id
                    ),
                    title=item.title,
                    account_title=item.source_title,
                    writable=item.writable,
                    allows_modification=item.allows_modification,
                    supports_events=item.supports_events,
                    source_kind=item.source_kind,
                )
                for item in result.calendars or ()
            )
            self._coordinator.accept_catalog(
                CalendarCatalog(
                    request_id=result.request_id,
                    observed_at=result.observed_at,
                    calendars=descriptors,
                )
            )
            self._catalog_phase = "active"
            self._notify("select_calendars" if descriptors else "catalog_empty")
        else:
            assert job.request is not None
            occurrences = tuple(self._occurrence(item) for item in result.events or ())
            read_result = CalendarReadResult(
                request_id=result.request_id,
                observed_at=result.observed_at,
                status="partial" if result.partial else "ok",
                occurrences=occurrences,
                reason="limit_reached" if result.partial else None,
            )
            if job.dialogue is not None:
                self._accept_dialogue(job, read_result)
                return
            snapshot = self._coordinator.accept_result(job.request, read_result)
            self.last_successful_read_at = snapshot.observed_at
            if job.planning_operation:
                self._show_snapshot = False
                job.planning_notified = True
                self.planning_read_ready.emit(str(job.request_id), snapshot)
                self._notify("grant_ready")
                return
            self._show_snapshot = True
            self._notify(
                "partial"
                if snapshot.status == "partial"
                else "ready"
                if snapshot.occurrences
                else "empty"
            )

    def _accept_dialogue(self, job: _Job, result: CalendarReadResult) -> None:
        if job.dialogue is None or self._dialogue is None or self._dialogue.pending != job.dialogue:
            return
        answer = self._dialogue.accept(job.dialogue, result)
        if answer.receipt.status in {"ok", "empty", "partial"}:
            self.last_successful_read_at = result.observed_at
        self._dialogue_status = (
            answer.receipt.status
            if answer.receipt.status in {"ok", "empty", "partial"}
            else answer.receipt.reason or answer.receipt.status
        )
        self._show_snapshot = False
        self._notify(
            "grant_ready"
            if answer.receipt.status in {"ok", "empty", "partial"}
            else self._dialogue_status
        )
        self.dialogue_changed.emit()

    @staticmethod
    def _occurrence(item: _Event) -> CalendarOccurrence:
        occurrence_key = (
            item.occurrence_start.astimezone(ZoneInfo(item.time_zone)).date().isoformat()
            if item.all_day
            else item.occurrence_start.isoformat()
        )
        identity = json.dumps(
            [item.source_id, item.calendar_id, item.event_id, occurrence_key],
            ensure_ascii=False,
        ).encode()
        return CalendarOccurrence(
            source=CalendarSourceRef(account_id=item.source_id, calendar_id=item.calendar_id),
            event_id=item.event_id,
            occurrence_id=hashlib.sha256(identity).hexdigest(),
            title=item.title,
            starts_at=None if item.all_day else item.start,
            ends_at=None if item.all_day else item.end,
            all_day_start=item.start_date,
            all_day_end=item.end_date,
            timezone=item.time_zone,
            status=item.status,
        )

    def _failure(self, code: str) -> None:
        if (
            self._job is not None
            and self._job.planning_operation
            and not self._job.planning_notified
        ):
            self._job.planning_notified = True
            self.planning_read_failed.emit(str(self._job.request_id), "calendar_read_unavailable")
        if self._job is not None and self._job.schedule_operation:
            self.schedule.transport_failed(code)
            self._cancel_job()
            self._notify()
            return
        self._catalog_phase = "error"
        self._generation += 1
        self._show_snapshot = False
        self._cancel_job()
        self._invalidate_dialogue(code)
        with suppress(CalendarAccessError):
            self._coordinator.revoke()
        self._notify(code)

    def _cancel_job(self) -> None:
        job, self._job = self._job, None
        if job is None:
            return
        if job.planning_operation and not job.planning_notified:
            job.planning_notified = True
            self.planning_read_failed.emit(str(job.request_id), "calendar_read_unavailable")
        if job.schedule_operation:
            self.schedule.transport_failed("cancelled")
        job.timer.stop()
        job.output.clear()
        job.payload = b""
        if job.process.state() == QProcess.ProcessState.NotRunning:
            self._dispose(job)
        else:
            self._retired.add(job)
            job.process.kill()

    def _dispose(self, job: _Job) -> None:
        if job.disposed:
            return
        job.timer.stop()
        job.output.clear()
        job.payload = b""
        if job.process.state() != QProcess.ProcessState.NotRunning or (
            job.launched and not job.terminal_received
        ):
            self._retired.add(job)
            return
        job.disposed = True
        self._retired.discard(job)
        job.timer.deleteLater()
        job.process.deleteLater()
        self._notify_shutdown_finished()

    def _notify_shutdown_finished(self) -> None:
        if self.shutdown_complete and not self._shutdown_notified:
            self._shutdown_notified = True
            self.shutdown_finished.emit()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._closing = True
        self.poll.stop()
        self._expiry.stop()
        self.disconnect_calendar()
        for job in tuple(self._retired):
            job.process.kill()
        self._closing = False
        self._notify_shutdown_finished()
