"""Independent public negative review cases; no shared fixture/account/provider/network."""

import asyncio
from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

from satori.application.assistant.calendar import CalendarAccessCoordinator
from satori.application.assistant.calendar_actions import CalendarActionOwner
from satori.application.assistant.calendar_actions_contracts import CalendarActionScope
from satori.application.assistant.calendar_disclosure import CalendarDisclosureOwner
from satori.application.assistant.calendar_planning_contracts import (
    CalendarPlanningError,
    CalendarPlanningRequest,
    CalendarPlanningSnapshot,
    available_slots,
)
from satori.application.assistant.calendar_planning_source import GrantedCalendarPlanningSource
from satori.application.assistant.calendar_reasoning import CoreCalendarReasoner
from satori.application.assistant.calendar_service import CalendarAssistantService
from satori.application.assistant.contracts import (
    CalendarCatalog,
    CalendarDescriptor,
    CalendarReadRequest,
    CalendarReadResult,
    CalendarSourceRef,
    LocalPrincipal,
)
from satori.application.source_policy.dispatch import (
    admit_provider_dispatch,
    operation_dispatch_admission,
    source_dispatch_admission,
)

NOW = datetime(2026, 11, 3, 9, tzinfo=UTC)
PROVIDER = "openai:gpt-6-terra"


def public_source() -> tuple[
    CalendarAccessCoordinator,
    GrantedCalendarPlanningSource,
    CalendarPlanningRequest,
    CalendarPlanningSnapshot,
    CalendarSourceRef,
]:
    principal = LocalPrincipal(os_uid=501, profile_id=uuid4(), session_id=uuid4())
    coordinator = CalendarAccessCoordinator(principal, clock=lambda: NOW)
    resource = CalendarSourceRef(account_id="public-account", calendar_id="public-work")
    descriptor = CalendarDescriptor(
        source=resource,
        title="PUBLIC WORK",
        account_title="PUBLIC ACCOUNT",
        writable=True,
        allows_modification=True,
        supports_events=True,
        source_kind="caldav",
    )
    coordinator.accept_catalog(
        CalendarCatalog(request_id=uuid4(), observed_at=NOW, calendars=(descriptor,))
    )
    coordinator.issue((resource,))
    scope = CalendarActionScope(
        identity_id=uuid4(),
        profile_id=principal.profile_id,
        session_id=uuid4(),
        generation=uuid4(),
        provider="macos_eventkit",
        account_id=resource.account_id,
        calendar_id=resource.calendar_id,
        timezone="Europe/Moscow",
    )

    async def native_read(
        request: CalendarReadRequest, *, before_read: Callable[[], None]
    ) -> CalendarReadResult:
        before_read()
        return CalendarReadResult(request_id=request.request_id, observed_at=NOW)

    source = GrantedCalendarPlanningSource(coordinator, native_read, current_scope=lambda: scope)
    request = CalendarPlanningRequest(
        request_id=uuid4(),
        scope=scope,
        user_text="PUBLIC request",
        title="PUBLIC event",
        window_start=NOW + timedelta(hours=1),
        window_end=NOW + timedelta(hours=3),
        duration_minutes=30,
    )
    snapshot = asyncio.run(source.fetch(request))
    return coordinator, source, request, snapshot, resource


def disclosure_owner(
    source: GrantedCalendarPlanningSource, request: CalendarPlanningRequest
) -> CalendarDisclosureOwner:
    return CalendarDisclosureOwner(
        clock=lambda: NOW,
        live_guard=lambda: source.require(request.scope),
        source_guard=lambda snapshot: source.require(
            snapshot.scope, read_grant_id=snapshot.read_grant_id
        ),
    )


def test_provider_disclosure_does_not_survive_read_grant_reissue() -> None:
    coordinator, source, request, snapshot, resource = public_source()
    disclosure = disclosure_owner(source, request)
    permit = disclosure.approve(request, snapshot, provider=PROVIDER, explicit_disclosure=True)
    old_grant_id = snapshot.read_grant_id
    # Same app/identity/session/resource scope deliberately remains unchanged.
    # An independent explicit new read grant cannot revive old disclosure.
    coordinator.suspend_selection()
    renewed = coordinator.issue((resource,))
    assert renewed.grant_id != old_grant_id
    source.require(request.scope)
    with pytest.raises(CalendarPlanningError, match="calendar_read_scope_unavailable"):
        disclosure.require(permit, request, snapshot, provider=PROVIDER)


def test_cached_preview_does_not_revive_after_read_grant_rotation() -> None:
    coordinator, source, request, _, resource = public_source()
    # This test exercises only public preview, with inert session-check ports;
    # no canonical Core/provider/action store is constructed or dispatched.
    reasoner = cast(
        CoreCalendarReasoner,
        SimpleNamespace(require_session=lambda _request: None, require_scope=lambda _scope: None),
    )
    service = CalendarAssistantService(
        reasoner=reasoner,
        source=source,
        actions=cast(CalendarActionOwner, SimpleNamespace()),
        clock=lambda: NOW,
        live_scope=lambda: request.scope,
    )
    first = asyncio.run(service.preview(request))
    coordinator.suspend_selection()
    renewed = coordinator.issue((resource,))
    assert renewed.grant_id != first.read_grant_id
    with pytest.raises(CalendarPlanningError, match="calendar_read_scope_unavailable"):
        asyncio.run(service.preview(request))


@pytest.mark.parametrize("complete", ["partial", 1, None])
def test_constructed_partial_flag_cannot_be_disclosed(complete: object) -> None:
    _, source, request, snapshot, _ = public_source()
    malformed = snapshot.model_copy(update={"complete": complete})
    disclosure = disclosure_owner(source, request)
    with pytest.raises(CalendarPlanningError, match="calendar_context_invalid"):
        disclosure.approve(request, malformed, provider=PROVIDER, explicit_disclosure=True)


@pytest.mark.parametrize(
    "updates", [{"duration_minutes": 0}, {"user_text": ""}, {"window_end": NOW}]
)
def test_constructed_invalid_request_cannot_be_disclosed(updates: dict[str, object]) -> None:
    _, source, request, snapshot, _ = public_source()
    malformed = request.model_copy(update=updates)
    disclosure = disclosure_owner(source, request)
    with pytest.raises(CalendarPlanningError, match="calendar_context_invalid"):
        disclosure.approve(malformed, snapshot, provider=PROVIDER, explicit_disclosure=True)


def test_partial_snapshot_cannot_create_direct_gap_candidates() -> None:
    _, _, request, snapshot, _ = public_source()
    partial = snapshot.model_copy(update={"complete": False})
    with pytest.raises(CalendarPlanningError, match="calendar_context_partial"):
        available_slots(request, partial)


def test_direct_slot_helper_rejects_cross_scope_observations() -> None:
    _, _, request, snapshot, _ = public_source()
    other = request.scope.model_copy(update={"session_id": uuid4()})
    foreign = snapshot.model_copy(update={"scope": other})
    with pytest.raises(CalendarPlanningError, match="calendar_context_scope_mismatch"):
        available_slots(request, foreign)


def test_nested_source_dispatch_keeps_revocable_operation_guard() -> None:
    calls: list[str] = []
    revoked = False

    def operation_guard() -> None:
        calls.append("operation")
        if revoked:
            raise CalendarPlanningError("calendar_disclosure_revoked")

    unit = SimpleNamespace(
        source_admission=SimpleNamespace(dispatch=lambda _id: calls.append("source"))
    )
    with (
        operation_dispatch_admission(operation_guard),
        source_dispatch_admission(lambda: nullcontext(unit), "public-input"),
    ):
        admit_provider_dispatch()
        assert calls == ["operation", "source"]
        revoked = True
        with pytest.raises(CalendarPlanningError, match="calendar_disclosure_revoked"):
            admit_provider_dispatch()
        assert calls == ["operation", "source", "operation"]
    admit_provider_dispatch()
    assert calls == ["operation", "source", "operation"]


def test_nested_operation_guards_do_not_replace_outer_authority() -> None:
    calls: list[str] = []

    def outer() -> None:
        calls.append("outer")
        raise CalendarPlanningError("outer_revoked")

    def inner() -> None:
        calls.append("inner")

    with (
        operation_dispatch_admission(outer),
        operation_dispatch_admission(inner),
        pytest.raises(CalendarPlanningError, match="outer_revoked"),
    ):
        admit_provider_dispatch()
    assert calls == ["outer"]
