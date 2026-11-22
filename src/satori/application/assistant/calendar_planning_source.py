"""Reuse CalendarAccessCoordinator grants for a Qt-free planning source adapter."""

from collections.abc import Callable
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from satori.application.assistant.calendar import CalendarAccessCoordinator
from satori.application.assistant.calendar_actions_contracts import (
    CalendarActionCatalog,
    CalendarActionScope,
)
from satori.application.assistant.calendar_admission import require_calendar_host_admission
from satori.application.assistant.calendar_planning_contracts import (
    CalendarObservedEvent,
    CalendarPlanningError,
    CalendarPlanningRequest,
    CalendarPlanningSnapshot,
    context_digest,
)
from satori.application.assistant.contracts import CalendarReadRequest, CalendarReadResult
from satori.application.assistant.dialogue_contracts import _unambiguous_midnight


class CalendarPlanningSource(Protocol):
    def require(self, scope: CalendarActionScope, *, read_grant_id: UUID | None = None) -> None: ...

    async def fetch(self, request: CalendarPlanningRequest) -> CalendarPlanningSnapshot: ...

    def catalog(self, scope: CalendarActionScope) -> CalendarActionCatalog: ...


class NativeCalendarRead(Protocol):
    async def __call__(
        self, request: CalendarReadRequest, *, before_read: Callable[[], None]
    ) -> CalendarReadResult: ...


class GrantedCalendarPlanningSource:
    """No second read-grant owner. Selection and expiry remain with the coordinator."""

    def __init__(
        self,
        coordinator: CalendarAccessCoordinator,
        native_read: NativeCalendarRead,
        *,
        current_scope: Callable[[], CalendarActionScope],
    ) -> None:
        self._owner = coordinator
        self._read = native_read
        self._current_scope = current_scope

    def require(self, scope: CalendarActionScope, *, read_grant_id: UUID | None = None) -> None:
        require_calendar_host_admission()
        grant = self._owner.grant
        if (
            scope != self._current_scope()
            or scope.provider not in {"macos_eventkit", "caldav"}
            or grant is None
            or (read_grant_id is not None and grant.grant_id != read_grant_id)
            or scope.profile_id != self._owner.principal.profile_id
            or not any(r.calendar_id == scope.calendar_id for r in grant.resources)
            or any(r.account_id != scope.account_id for r in grant.resources)
            or any(r.provider != scope.provider for r in grant.resources)
        ):
            raise CalendarPlanningError("calendar_read_scope_unavailable")

    async def fetch(self, request: CalendarPlanningRequest) -> CalendarPlanningSnapshot:
        self.require(request.scope)
        read = self._owner.prepare_read(
            request.window_start, request.window_end, request.scope.timezone, max_events=100
        )
        try:
            result = await self._read(
                read, before_read=lambda: self.require(request.scope, read_grant_id=read.grant_id)
            )
            self.require(request.scope, read_grant_id=read.grant_id)
            accepted = self._owner.accept_result(read, result)
        except Exception:
            raise CalendarPlanningError("calendar_native_read_unavailable") from None
        events = []
        for occurrence in accepted.occurrences:
            start, end = occurrence.starts_at, occurrence.ends_at
            if start is None or end is None:
                assert occurrence.all_day_start is not None
                assert occurrence.all_day_end is not None
                zone = ZoneInfo(occurrence.timezone)
                start = _unambiguous_midnight(occurrence.all_day_start, zone)
                end = _unambiguous_midnight(occurrence.all_day_end, zone)
            events.append(
                CalendarObservedEvent(
                    event_ref=context_digest(
                        [
                            occurrence.source.calendar_id,
                            occurrence.event_id,
                            occurrence.occurrence_id,
                            occurrence.revision,
                        ]
                    )[:32],
                    calendar_id=occurrence.source.calendar_id,
                    title=occurrence.title,
                    starts_at=start,
                    ends_at=end,
                    status=occurrence.status,
                )
            )
        return CalendarPlanningSnapshot(
            scope=request.scope,
            read_grant_id=read.grant_id,
            selected_calendar_ids=tuple(resource.calendar_id for resource in read.resources),
            observed_at=accepted.observed_at,
            expires_at=accepted.expires_at,
            window_start=read.starts_at,
            window_end=read.ends_at,
            complete=accepted.status == "ok",
            events=tuple(events),
        )

    def catalog(self, scope: CalendarActionScope) -> CalendarActionCatalog:
        self.require(scope)
        diagnostic = self._owner.catalog_diagnostics
        if diagnostic.state != "fresh" or diagnostic.observed_at is None:
            raise CalendarPlanningError("calendar_write_catalog_refresh_required")
        selected = next(
            (c for c in self._owner.granted_calendars if c.source.calendar_id == scope.calendar_id),
            None,
        )
        if selected is None:
            raise CalendarPlanningError("calendar_target_not_selected")
        return CalendarActionCatalog(
            scope=scope,
            revision=UUID(hex=context_digest(selected.model_dump(mode="json"))[:32]),
            observed_at=diagnostic.observed_at,
            full_access=True,
            writable=selected.write_capability == "writable",
            supports_events=selected.supports_events is True,
            subscribed=selected.source_kind in {"subscribed", "birthdays"},
            calendar_title=selected.title,
            account_title=selected.account_title,
        )
