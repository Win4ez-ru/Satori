"""Trusted host composition for a portable calendar; no endpoint or consent inference."""

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from satori.application.assistant.calendar import CalendarAccessCoordinator
from satori.application.assistant.calendar_actions import CalendarActionOwner
from satori.application.assistant.calendar_actions_contracts import (
    CalendarActionScope,
    CalendarActionStore,
)
from satori.application.assistant.calendar_admission import require_calendar_host_admission
from satori.application.assistant.calendar_planning_contracts import CalendarPlanningError
from satori.application.assistant.calendar_planning_source import GrantedCalendarPlanningSource
from satori.application.assistant.contracts import (
    CalendarDescriptor,
    CalendarSourceRef,
    LocalPrincipal,
    require_identifier,
    require_timezone,
    require_utc,
)
from satori.infrastructure.assistant.caldav import ICloudCalDAVAdapter
from satori.infrastructure.assistant.caldav_transport import CalDAVHTTP
from satori.server.calendar_backend import CalendarRuntimeBindings


class ICloudCalendarHost:
    """Reuse one provided coordinator and existing action owner in one headless Core.

    ``begin_scope`` is a trusted foreground-host operation, never an HTTP field or
    an implicit grant from a chat message. It authorizes selected reads only; exact
    reviewed action approval and the default-off real-write switch remain separate.
    """

    def __init__(
        self,
        coordinator: CalendarAccessCoordinator,
        http: CalDAVHTTP,
        *,
        account_id: str,
        principal: LocalPrincipal,
        action_store: CalendarActionStore,
        clock: Callable[[], datetime],
        real_writes_enabled: bool = False,
    ) -> None:
        if principal != coordinator.principal:
            raise CalendarPlanningError("calendar_read_scope_unavailable")
        self.coordinator = coordinator
        self.principal = principal
        self._account_id = require_identifier(account_id)
        self._clock = clock
        self._scope: CalendarActionScope | None = None
        self._original_grant_id: UUID | None = None
        self._closed = False
        self._discovering = False
        self._epoch = uuid4()
        self.adapter = ICloudCalDAVAdapter(
            account_id, http, clock=self.now, read_admission=self._admit_create_read
        )
        self.source = GrantedCalendarPlanningSource(
            coordinator, self.adapter.read_events, current_scope=self.live_scope
        )
        self.actions = CalendarActionOwner(
            action_store, self.adapter, clock=self.now, real_writes_enabled=real_writes_enabled
        )

    def now(self) -> datetime:
        return require_utc(self._clock())

    def _require_host(self) -> None:
        if self._closed:
            raise CalendarPlanningError("calendar_service_closed")
        require_calendar_host_admission()

    def live_scope(self) -> CalendarActionScope:
        self._require_host()
        grant = self.coordinator.grant
        scope = self._scope
        if (
            scope is None
            or grant is None
            or self._original_grant_id is None
            or grant.grant_id != self._original_grant_id
            or scope.generation != self._original_grant_id
            or grant.principal != self.principal
        ):
            raise CalendarPlanningError("calendar_read_scope_unavailable")
        return scope

    def _admit_create_read(self, actual_scope: CalendarActionScope) -> None:
        self._require_host()
        original = self._original_grant_id
        if original is None:
            raise CalendarPlanningError("calendar_read_scope_unavailable")
        self.source.require(actual_scope, read_grant_id=original)

    async def discover(
        self, request_id: UUID, *, before_read: Callable[[], None]
    ) -> tuple[CalendarDescriptor, ...]:
        self._require_host()
        if self._discovering:
            raise CalendarPlanningError("calendar_read_scope_unavailable")
        self.revoke()
        epoch = self._epoch
        self._discovering = True

        def require() -> None:
            self._require_host()
            if epoch != self._epoch:
                raise CalendarPlanningError("calendar_read_scope_unavailable")
            before_read()

        try:
            catalog = await self.adapter.discover(request_id, before_read=require)
            require()
            return self.coordinator.accept_catalog(catalog)
        finally:
            self._discovering = False

    def begin_scope(
        self,
        *,
        identity_id: UUID,
        canonical_session_id: UUID,
        selected_refs: tuple[CalendarSourceRef, ...],
        target: CalendarSourceRef,
        timezone: str,
        explicit_request: bool,
    ) -> CalendarActionScope:
        self._require_host()
        if (
            explicit_request is not True
            or self._discovering
            or not isinstance(identity_id, UUID)
            or not identity_id.int
            or not isinstance(canonical_session_id, UUID)
            or not canonical_session_id.int
            or not isinstance(selected_refs, tuple)
            or not selected_refs
            or not isinstance(target, CalendarSourceRef)
            or target not in selected_refs
            or any(
                not isinstance(ref, CalendarSourceRef)
                or ref.provider != "caldav"
                or ref.account_id != self._account_id
                for ref in selected_refs
            )
        ):
            raise CalendarPlanningError("calendar_read_scope_unavailable")
        timezone = require_timezone(timezone)
        self.actions.revoke()
        self._scope = None
        self._original_grant_id = None
        grant = self.coordinator.issue(selected_refs, lifetime=timedelta(seconds=120))
        scope = CalendarActionScope(
            identity_id=identity_id,
            profile_id=self.principal.profile_id,
            session_id=canonical_session_id,
            generation=grant.grant_id,
            provider="caldav",
            account_id=target.account_id,
            calendar_id=target.calendar_id,
            timezone=timezone,
        )
        self._scope, self._original_grant_id = scope, grant.grant_id
        self.source.require(scope, read_grant_id=grant.grant_id)
        return scope

    def bindings(self) -> CalendarRuntimeBindings:
        self._require_host()
        return CalendarRuntimeBindings(
            source=self.source, actions=self.actions, clock=self.now, live_scope=self.live_scope
        )

    def revoke(self) -> None:
        self._epoch = uuid4()
        self._scope, self._original_grant_id = None, None
        self.coordinator.revoke()
        self.actions.revoke()

    def close(self) -> None:
        if not self._closed:
            self.revoke()
            self._closed = True
            self.actions.close()
