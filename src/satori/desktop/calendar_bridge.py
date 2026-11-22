"""Finite Calendar read RPC and same-Core actions in the owned desktop worker.

The GUI retains its sole read coordinator. Only admitted snapshots cross this
private pipe; synchronous last-boundary checks consume its revocable lease file.
"""

import asyncio
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import Field, model_validator

from satori.application.assistant.calendar_actions import CalendarActionOwner
from satori.application.assistant.calendar_actions_contracts import (
    CalendarActionCatalog,
    CalendarActionDTO,
    CalendarActionProvider,
    CalendarActionScope,
)
from satori.application.assistant.calendar_host_lease import CalendarHostLease
from satori.application.assistant.calendar_planning_contracts import (
    CalendarPlanningError,
    CalendarPlanningRequest,
    CalendarPlanningSnapshot,
)
from satori.application.assistant.calendar_service import CalendarAssistantService
from satori.desktop.backend import DesktopBackend
from satori.infrastructure.assistant.calendar_actions_eventkit import EventKitCalendarActionProvider
from satori.infrastructure.assistant.calendar_actions_store import SQLiteCalendarActionStore
from satori.infrastructure.assistant.calendar_lease import CalendarHostLeaseReader
from satori.server.calendar_backend import (
    CalendarBackendCommand,
    CalendarRuntimeBindings,
    execute_calendar_command,
)

MAX_HOST_FRAME_CHARS = 256 * 1024


def packaged_calendar_action_helper(executable: Path) -> Path | None:
    """Resolve only the fixed signed-bundle placements into the strict native port.

    PyInstaller can alias a Resources binary directory to Frameworks. The
    launcher supplies the canonical path; the native port keeps rejecting aliases.
    """
    if not executable.is_absolute():
        return None
    try:
        actual_executable = executable.resolve(strict=True)
        contents = actual_executable.parent.parent
        if (
            not actual_executable.is_file()
            or actual_executable.name != "Satori"
            or actual_executable.parent.name != "MacOS"
            or contents.name != "Contents"
            or contents.parent.suffix != ".app"
        ):
            return None
        relative = Path("calendar-actions-native/SatoriCalendarActions")
        advertised = contents / "Resources" / relative
        resolved = advertised.resolve(strict=True)
        allowed = {contents / placement / relative for placement in ("Resources", "Frameworks")}
        return resolved if resolved in allowed and resolved.is_file() else None
    except (OSError, RuntimeError):
        return None


def _now() -> datetime:
    return datetime.now(UTC)


class CalendarHostResult(CalendarActionDTO):
    protocol_version: Literal[1] = 1
    kind: Literal["calendar_host_result"]
    call_id: UUID
    snapshot: CalendarPlanningSnapshot | None = Field(default=None, repr=False)
    error: Literal["calendar_host_read_unavailable"] | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (self.snapshot is None) == (self.error is None):
            raise ValueError("calendar_host_result_invalid")
        return self


class CalendarReadRPC:
    """No general host executor; one closed selected-Calendar read operation."""

    def __init__(self, emit: Callable[[str, dict[str, object]], None]) -> None:
        self._emit = emit
        self._pending: dict[UUID, asyncio.Future[CalendarPlanningSnapshot]] = {}
        self.closed = False
        self.parent_command: str | None = None

    def accept(self, result: CalendarHostResult) -> None:
        result = CalendarHostResult.model_validate(result)
        future = self._pending.get(result.call_id)
        if future is None or future.done():
            return
        if result.snapshot is None:
            future.set_exception(CalendarPlanningError("calendar_host_read_unavailable"))
        else:
            future.set_result(result.snapshot)

    async def read(self, request: CalendarPlanningRequest) -> CalendarPlanningSnapshot:
        if self.closed or self.parent_command is None or len(self._pending) >= 8:
            raise CalendarPlanningError("calendar_host_unavailable")
        call = uuid4()
        future = asyncio.get_running_loop().create_future()
        self._pending[call] = future
        try:
            self._emit(
                self.parent_command,
                {
                    "call_id": str(call),
                    "request": request.model_dump(mode="json"),
                },
            )
            return await asyncio.wait_for(future, timeout=35)
        except (TimeoutError, asyncio.CancelledError):
            if not future.done():
                future.cancel()
            raise
        finally:
            self._pending.pop(call, None)

    def close(self) -> None:
        self.closed = True
        for future in self._pending.values():
            if not future.done():
                future.set_exception(CalendarPlanningError("calendar_host_unavailable"))


class LeasedCalendarPlanningSource:
    """Read-only consumer of GUI authority; never constructs another coordinator."""

    def __init__(
        self,
        directory: Path,
        lease: CalendarHostLease,
        rpc: CalendarReadRPC,
        *,
        clock: Callable[[], datetime] = _now,
        monotonic_clock: Callable[[], float] = time.monotonic,
        parent_pid: Callable[[], int] = os.getppid,
    ) -> None:
        self.lease = CalendarHostLease.model_validate(lease)
        self._clock = clock
        self._reader = CalendarHostLeaseReader(
            directory, lease, clock=clock, monotonic=monotonic_clock, parent_pid=parent_pid
        )
        self._rpc = rpc

    def require(self, scope: CalendarActionScope, *, read_grant_id: UUID | None = None) -> None:
        if (
            self._rpc.closed
            or scope != self.lease.scope
            or (read_grant_id is not None and read_grant_id != self.lease.read_grant.grant_id)
        ):
            raise CalendarPlanningError("calendar_host_scope_unavailable")
        self._reader.require()

    async def fetch(self, request: CalendarPlanningRequest) -> CalendarPlanningSnapshot:
        self.require(request.scope)
        if request.request_id != self.lease.request_id:
            raise CalendarPlanningError("calendar_host_request_mismatch")
        value = await self._rpc.read(request)
        self.require(request.scope, read_grant_id=value.read_grant_id)
        value.require_for(request, self._clock())
        expected = tuple(r.calendar_id for r in self.lease.read_grant.resources)
        if (
            value.selected_calendar_ids != expected
            or value.expires_at > self.lease.expires_at
            or value.observed_at < self.lease.issued_at
        ):
            raise CalendarPlanningError("calendar_host_snapshot_mismatch")
        return value

    def catalog(self, scope: CalendarActionScope) -> CalendarActionCatalog:
        self.require(scope)
        target = next(c for c in self.lease.calendars if c.source.calendar_id == scope.calendar_id)
        return CalendarActionCatalog(
            scope=scope,
            revision=self.lease.catalog_revision,
            observed_at=self.lease.catalog_observed_at,
            full_access=self.lease.full_access,
            writable=target.writable is True and target.allows_modification is True,
            supports_events=target.supports_events is True,
            subscribed=target.source_kind in {"subscribed", "birthdays"},
            calendar_title=target.title,
            account_title=target.account_title,
        )


class DesktopCalendarOperations:
    """One transient workflow over this worker's existing DesktopBackend/Core."""

    def __init__(
        self,
        backend: DesktopBackend,
        rpc: CalendarReadRPC,
        *,
        action_helper: Path | None = None,
        action_provider: CalendarActionProvider | None = None,
        clock: Callable[[], datetime] = _now,
        monotonic_clock: Callable[[], float] = time.monotonic,
        parent_pid: Callable[[], int] = os.getppid,
    ) -> None:
        self._backend, self._rpc, self._helper = backend, rpc, action_helper
        self._provider = action_provider
        self._clock, self._monotonic, self._parent_pid = clock, monotonic_clock, parent_pid
        self._service: CalendarAssistantService | None = None
        self._request: UUID | None = None
        self._lease: CalendarHostLease | None = None

    def _configure(self, lease: CalendarHostLease, request_id: UUID, session_id: UUID) -> None:
        lease = CalendarHostLease.model_validate(lease)
        if (
            lease.request_id != request_id
            or lease.scope.session_id != session_id
            or lease.scope.profile_id != uuid5(NAMESPACE_URL, str(self._backend.data_directory))
        ):
            raise CalendarPlanningError("calendar_host_request_mismatch")
        source = LeasedCalendarPlanningSource(
            self._backend.data_directory,
            lease,
            self._rpc,
            clock=self._clock,
            monotonic_clock=self._monotonic,
            parent_pid=self._parent_pid,
        )
        source.require(lease.scope)
        if self._lease == lease and self._request == request_id:
            return
        self.close()
        actions = CalendarActionOwner(
            SQLiteCalendarActionStore(
                self._backend.data_directory / "calendar-actions" / "actions.sqlite3"
            ),
            self._provider or EventKitCalendarActionProvider(self._helper, clock=self._clock),
            clock=self._clock,
            real_writes_enabled=self._helper is not None,
        )
        try:
            bindings = CalendarRuntimeBindings(
                source=source,
                actions=actions,
                clock=self._clock,
                live_scope=lambda: lease.scope,
            )
            self._service = self._backend.build_calendar_assistant(bindings)
            self._request, self._lease = request_id, lease
        except BaseException:
            actions.close()
            raise

    async def execute(
        self,
        command: CalendarBackendCommand,
        *,
        request_id: UUID,
        session_id: UUID,
        parent_command: str,
        lease: CalendarHostLease | None = None,
    ) -> dict[str, object]:
        try:
            if lease is not None:
                if command.operation not in {"preview", "exact_preview", "reconcile"}:
                    raise CalendarPlanningError("calendar_host_lease_unexpected")
                self._configure(lease, request_id, session_id)
            if self._service is None or self._request != request_id:
                raise CalendarPlanningError("calendar_host_not_prepared")
            self._rpc.parent_command = parent_command
            return await execute_calendar_command(
                self._service,
                command,
                request_id=request_id,
                session_id=session_id,
            )
        except (CalendarPlanningError, ValueError):
            uncertain = command.operation in {"create", "reconcile"}
            return {
                "status": "unknown" if uncertain else "unavailable",
                "reason": "calendar_operation_unavailable",
                "event_created": None if uncertain else False,
            }
        finally:
            self._rpc.parent_command = None

    def close(self) -> None:
        service = self._service
        self._service, self._request, self._lease = None, None, None
        if service is not None:
            service.close()
