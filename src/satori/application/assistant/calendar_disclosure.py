"""One-use provider disclosure, separate from calendar read and write authority."""

from collections.abc import Callable
from datetime import datetime, timedelta
from threading import RLock
from time import monotonic
from uuid import UUID, uuid4

from pydantic import Field

from satori.application.assistant.calendar_actions_contracts import CalendarActionDTO
from satori.application.assistant.calendar_planning_contracts import (
    CalendarPlanningError,
    CalendarPlanningRequest,
    CalendarPlanningSnapshot,
)
from satori.application.assistant.contracts import require_utc


class CalendarDisclosurePermit(CalendarActionDTO):
    permit_id: UUID
    request_id: UUID
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    issued_at: datetime
    expires_at: datetime
    provider: str = Field(min_length=1, max_length=100)


class CalendarDisclosureOwner:
    """Ephemeral consent. A caller must supply a live trusted source/session guard."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime],
        live_guard: Callable[[], None],
        monotonic_clock: Callable[[], float] = monotonic,
        source_guard: Callable[[CalendarPlanningSnapshot], None] | None = None,
    ) -> None:
        self._clock = clock
        self._guard = live_guard
        self._source_guard = source_guard
        self._monotonic = monotonic_clock
        self._lock = RLock()
        self._permits: dict[UUID, CalendarDisclosurePermit] = {}
        self._claimed: set[UUID] = set()
        self._deadlines: dict[UUID, float] = {}
        self._revoked = False

    def approve(
        self,
        request: CalendarPlanningRequest,
        snapshot: CalendarPlanningSnapshot,
        *,
        provider: str,
        explicit_disclosure: bool = False,
    ) -> CalendarDisclosurePermit:
        """Called only by the explicit disclosure UI/host action, never by a model."""

        self._guard()
        if self._source_guard is not None:
            self._source_guard(snapshot)
        now = require_utc(self._clock())
        snapshot.require_for(request, now)
        if explicit_disclosure is not True:
            raise CalendarPlanningError("calendar_disclosure_required")
        with self._lock:
            if self._revoked:
                raise CalendarPlanningError("calendar_disclosure_revoked")
            if len(self._permits) >= 128:
                raise CalendarPlanningError("calendar_disclosure_capacity")
            if any(p.request_id == request.request_id for p in self._permits.values()):
                raise CalendarPlanningError("calendar_disclosure_request_already_seen")
            permit = CalendarDisclosurePermit(
                permit_id=uuid4(),
                request_id=request.request_id,
                request_digest=request.digest,
                snapshot_digest=snapshot.digest,
                issued_at=now,
                expires_at=min(snapshot.expires_at, now + timedelta(minutes=2)),
                provider=provider,
            )
            self._permits[permit.permit_id] = permit
            self._deadlines[permit.permit_id] = (
                self._monotonic() + (permit.expires_at - now).total_seconds()
            )
            return permit

    def require(
        self,
        permit: CalendarDisclosurePermit,
        request: CalendarPlanningRequest,
        snapshot: CalendarPlanningSnapshot,
        *,
        provider: str,
    ) -> None:
        self._guard()
        if self._source_guard is not None:
            self._source_guard(snapshot)
        now = require_utc(self._clock())
        snapshot.require_for(request, now)
        with self._lock:
            if (
                self._revoked
                or self._permits.get(permit.permit_id) != permit
                or not permit.issued_at <= now < permit.expires_at
                or permit.request_id != request.request_id
                or permit.request_digest != request.digest
                or permit.snapshot_digest != snapshot.digest
                or permit.provider != provider
                or self._monotonic() >= self._deadlines.get(permit.permit_id, float("-inf"))
            ):
                raise CalendarPlanningError("calendar_disclosure_not_current")

    def claim(
        self,
        permit: CalendarDisclosurePermit,
        request: CalendarPlanningRequest,
        snapshot: CalendarPlanningSnapshot,
        *,
        provider: str,
    ) -> None:
        self.require(permit, request, snapshot, provider=provider)
        with self._lock:
            if permit.permit_id in self._claimed:
                raise CalendarPlanningError("calendar_disclosure_already_consumed")
            self._claimed.add(permit.permit_id)

    def revoke(self) -> None:
        with self._lock:
            self._revoked = True
            self._permits.clear()
            self._deadlines.clear()
