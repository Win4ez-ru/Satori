"""Trusted host + actual read coordinator/action owner, public fake DAV only."""

import asyncio
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from satori.application.assistant.calendar import CalendarAccessCoordinator
from satori.application.assistant.calendar_actions_contracts import (
    CalendarActionError,
    CalendarCreateInput,
)
from satori.application.assistant.calendar_planning_contracts import (
    CalendarPlanningError,
    CalendarPlanningRequest,
)
from satori.application.assistant.contracts import CalendarSourceRef, LocalPrincipal
from satori.infrastructure.assistant.caldav_transport import CalDAVResponse
from satori.infrastructure.assistant.calendar_actions_store import SQLiteCalendarActionStore
from satori.server.caldav_calendar import ICloudCalendarHost
from tests.test_caldav_adapter import NOW, WORK, Wire, dav, discovery, props, xml


def host(tmp_path: Path, wire: Wire, *, enabled: bool = False) -> ICloudCalendarHost:
    principal = LocalPrincipal(os_uid=501, profile_id=uuid4(), session_id=uuid4())
    coordinator = CalendarAccessCoordinator(principal, clock=lambda: NOW)
    return ICloudCalendarHost(
        coordinator,
        wire,
        account_id="public-account",
        principal=principal,
        action_store=SQLiteCalendarActionStore(tmp_path / "host.sqlite3"),
        clock=lambda: NOW,
        real_writes_enabled=enabled,
    )


async def select(value: ICloudCalendarHost) -> CalendarSourceRef:
    descriptors = await value.discover(uuid4(), before_read=lambda: None)
    target = descriptors[0].source
    value.begin_scope(
        identity_id=uuid4(),
        canonical_session_id=uuid4(),
        selected_refs=(target,),
        target=target,
        timezone="Europe/Moscow",
        explicit_request=True,
    )
    return target


def planning(value: ICloudCalendarHost) -> CalendarPlanningRequest:
    return CalendarPlanningRequest(
        request_id=uuid4(),
        scope=value.live_scope(),
        user_text="PUBLIC SYNTHETIC exact calendar",
        title="PUBLIC SYNTHETIC",
        window_start=NOW + timedelta(days=1),
        window_end=NOW + timedelta(days=1, hours=2),
        duration_minutes=60,
    )


def reviewed(value: ICloudCalendarHost) -> tuple[CalendarCreateInput, UUID]:
    scope = value.live_scope()
    catalog = value.source.catalog(scope)
    command = CalendarCreateInput(
        request_id=uuid4(),
        context_digest="a" * 64,
        title="PUBLIC SYNTHETIC",
        starts_at=NOW + timedelta(days=1),
        ends_at=NOW + timedelta(days=1, hours=1),
        timezone=scope.timezone,
    )
    proposed = value.actions.propose(command, scope=scope, catalog=catalog)
    approved = value.actions.approve(
        command.request_id, proposed.proposal.review_digest, scope=scope, catalog=catalog
    )
    assert approved.approval is not None
    return command, approved.approval.approval_id


def test_same_coordinator_selected_grant_and_runtime_bindings(tmp_path: Path) -> None:
    async def scenario() -> None:
        value = host(tmp_path, Wire([*discovery(), xml()]))
        try:
            target = await select(value)
            grant = value.coordinator.grant
            assert grant is not None
            assert grant.resources == (target,)
            assert grant.expires_at - grant.issued_at == timedelta(seconds=120)
            assert value.live_scope().generation == grant.grant_id
            assert value.live_scope().profile_id == value.principal.profile_id
            snapshot = await value.source.fetch(planning(value))
            assert snapshot.complete
            assert snapshot.read_grant_id == grant.grant_id
            bindings = value.bindings()
            assert bindings.source is value.source
            assert bindings.actions is value.actions
            assert bindings.live_scope() == value.live_scope()
        finally:
            value.close()

    asyncio.run(scenario())


def test_catalog_does_not_issue_grant_and_begin_requires_explicit_foreground_request(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        value = host(tmp_path, Wire(discovery()))
        try:
            descriptors = await value.discover(uuid4(), before_read=lambda: None)
            assert value.coordinator.grant is None
            with pytest.raises(CalendarPlanningError):
                value.begin_scope(
                    identity_id=uuid4(),
                    canonical_session_id=uuid4(),
                    selected_refs=(descriptors[0].source,),
                    target=descriptors[0].source,
                    timezone="Europe/Moscow",
                    explicit_request=False,
                )
            assert value.coordinator.grant is None
        finally:
            value.close()

    asyncio.run(scenario())


def test_default_off_even_after_exact_action_approval(tmp_path: Path) -> None:
    async def scenario() -> None:
        wire = Wire(discovery())
        value = host(tmp_path, wire)
        try:
            await select(value)
            command, approval = reviewed(value)
            with pytest.raises(CalendarActionError, match="calendar_action_real_writes_disabled"):
                await value.actions.dispatch(
                    command.request_id,
                    approval,
                    scope=value.live_scope(),
                    catalog=value.source.catalog(value.live_scope()),
                    precondition_guard=lambda: None,
                )
            assert len(wire.calls) == 3
        finally:
            value.close()

    asyncio.run(scenario())


def test_actual_selected_read_then_durable_create_and_exact_server_readback(tmp_path: Path) -> None:
    async def scenario() -> None:
        wire = Wire(
            [
                *discovery(),
                xml(),
                xml(dav(WORK, props())),
                xml(),
                CalDAVResponse(201),
                CalDAVResponse(200, {}, b"SAVED"),
            ]
        )
        value = host(tmp_path, wire, enabled=True)
        try:
            await select(value)
            snapshot = await value.source.fetch(planning(value))
            assert snapshot.complete
            command, approval = reviewed(value)
            scope, catalog = value.live_scope(), value.source.catalog(value.live_scope())

            def guard() -> None:
                value.source.require(scope, read_grant_id=snapshot.read_grant_id)
                assert value.actions.get(command.request_id, scope=scope) is not None

            created = await value.actions.dispatch(
                command.request_id, approval, scope=scope, catalog=catalog, precondition_guard=guard
            )
            assert created.state == "created"
            assert created.receipt is not None
            assert created.receipt.remote_sync_verified is False
            assert sum(c[0] == "PUT" for c in wire.calls) == 1
        finally:
            value.close()

    asyncio.run(scenario())


def test_original_read_grant_rotation_blocks_preflight_before_private_http(tmp_path: Path) -> None:
    async def scenario() -> None:
        wire = Wire([*discovery(), xml(dav(WORK, props()))])
        value = host(tmp_path, wire, enabled=True)
        try:
            target = await select(value)
            command, approval = reviewed(value)
            scope, catalog = value.live_scope(), value.source.catalog(value.live_scope())
            loop = asyncio.get_running_loop()

            def rotate(_: str) -> None:
                async def on_owner() -> None:
                    value.coordinator.issue((target,), lifetime=timedelta(seconds=120))

                asyncio.run_coroutine_threadsafe(on_owner(), loop).result(timeout=2)

            wire.on_ready = rotate
            result = await value.actions.dispatch(
                command.request_id,
                approval,
                scope=scope,
                catalog=catalog,
                precondition_guard=lambda: None,
            )
            assert result.state == "rejected"
            assert len(wire.calls) == 3
            with pytest.raises(CalendarPlanningError):
                value.live_scope()
        finally:
            value.close()

    asyncio.run(scenario())


def test_wrong_resource_cannot_replace_exact_attempt_scope(tmp_path: Path) -> None:
    async def scenario() -> None:
        wire = Wire(discovery())
        value = host(tmp_path, wire, enabled=True)
        try:
            await select(value)
            wrong = value.live_scope().model_copy(update={"calendar_id": "different-resource"})
            admission = value.adapter._read_admission
            assert admission is not None
            with pytest.raises(CalendarPlanningError):
                admission(wrong)
            assert len(wire.calls) == 3
        finally:
            value.close()

    asyncio.run(scenario())
