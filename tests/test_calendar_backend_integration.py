"""Private headless calendar commands over real owned Core/source-floor installations.

All model/native effects below are explicit public fakes, never account acceptance.
"""

import asyncio
import json
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from satori.application.assistant.calendar_actions import CalendarActionOwner
from satori.application.assistant.calendar_actions_contracts import (
    CalendarActionScope,
    CalendarCreateProposal,
)
from satori.application.assistant.calendar_admission import calendar_host_admission
from satori.application.assistant.calendar_planning_contracts import (
    CalendarPlanningError,
    CalendarPlanningRequest,
)
from satori.application.assistant.calendar_planning_source import GrantedCalendarPlanningSource
from satori.application.assistant.contracts import CalendarReadRequest, CalendarReadResult
from satori.application.source_policy.dispatch import admit_provider_dispatch
from satori.core.conversation import ConversationProviderRequest, ConversationProviderResponse
from satori.desktop.backend import DesktopProviders
from satori.infrastructure.assistant.calendar_actions_store import SQLiteCalendarActionStore
from satori.server.calendar_backend import CalendarBackendCommand, CalendarRuntimeBindings
from satori.server.runtime import HeadlessCommand, HeadlessError, HeadlessRuntime
from tests.calendar_actions_fixture import FixtureCalendarActionProvider
from tests.calendar_planning_fixture import PlanningClock, PlanningSource
from tests.test_calendar_planning_review import NOW, public_source
from tests.test_conversation import skip_episode_provider, success_response
from tests.test_server_runtime import REPOSITORY, prepared


class BackendCalendarModel:
    """Transport-only fake accepting actual builder packets; no semantic quality claim."""

    def __init__(self) -> None:
        self.requests: list[ConversationProviderRequest] = []
        self.wire_sends = 0
        self.entered = asyncio.Event()
        self.release: asyncio.Event | None = None

    async def generate(
        self, request: ConversationProviderRequest, /
    ) -> ConversationProviderResponse:
        admit_provider_dispatch()
        packet = next(
            parsed
            for message in request.messages
            if message.content.startswith("{")
            and isinstance((parsed := json.loads(message.content)), dict)
            and parsed.get("kind") == "calendar_observation"
        )
        self.requests.append(request)
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        # Same last-moment admission as a real queued transport, after its wait.
        admit_provider_dispatch()
        self.wire_sends += 1
        choice = {
            "status": "suggested",
            "slot_id": packet["candidate_slots"][0]["slot_id"],
            "explanation": "PUBLIC synthetic slot from the admitted packet.",
            "used_memory_ids": [],
            "used_event_refs": [event["event_ref"] for event in packet["events"]],
        }
        return replace(
            success_response(json.dumps(choice)),
            provider="ollama",
            model="qwen3:4b-instruct",
        )


class BackendCase:
    def __init__(self, tmp_path: Path, *, enabled: bool = True) -> None:
        self.root, _, self.session, self.other_session = prepared(tmp_path)
        with sqlite3.connect((self.root / "satori.db").as_uri() + "?mode=ro", uri=True) as db:
            identity = db.execute("SELECT identity_id FROM satori_identities").fetchone()[0]
        self.clock = PlanningClock()
        self.model = BackendCalendarModel()
        self.scope = CalendarActionScope(
            identity_id=UUID(identity),
            profile_id=uuid4(),
            session_id=UUID(self.session),
            generation=uuid4(),
            provider="synthetic_fixture",
            account_id="public-account",
            calendar_id="public-work",
            timezone="Europe/Moscow",
        )
        self.source = PlanningSource(self.scope, self.clock)
        self.effects = FixtureCalendarActionProvider(self.clock)
        self.source.effect_provider = self.effects
        self.actions = (
            CalendarActionOwner(
                SQLiteCalendarActionStore(tmp_path / "calendar-private" / "actions.sqlite3"),
                self.effects,
                clock=self.clock,
            )
            if enabled
            else None
        )
        bindings = (
            CalendarRuntimeBindings(
                source=self.source,
                actions=self.actions,
                clock=self.clock,
                live_scope=lambda: self.source.scope,
            )
            if self.actions is not None
            else None
        )
        self.runtime = HeadlessRuntime(
            self.root,
            REPOSITORY,
            providers=DesktopProviders(self.model, skip_episode_provider()),
            calendar_bindings=bindings,
        )
        self.request = CalendarPlanningRequest(
            request_id=uuid4(),
            scope=self.scope,
            user_text="PUBLIC scheduling request",
            title="PUBLIC appointment",
            window_start=self.clock() + timedelta(days=1),
            window_end=self.clock() + timedelta(days=1, hours=3),
            duration_minutes=30,
        )

    def command(self, operation: str, **fields: Any) -> HeadlessCommand:
        return HeadlessCommand(
            operation="calendar",
            session_id=self.session,
            request_id=str(self.request.request_id),
            calendar=CalendarBackendCommand.model_validate({"operation": operation, **fields}),
        )

    async def preview_and_disclose(self) -> None:
        preview = await self.runtime.execute(self.command("preview", request=self.request))
        assert preview["status"] == "disclosure_review_required"
        assert preview["provider"] == "ollama:qwen3:4b-instruct"
        assert self.model.requests == []
        approved = await self.runtime.execute(self.command("disclose", explicit_disclosure=True))
        assert approved["status"] == "disclosure_approved"


def test_calendar_backend_is_default_off_without_effect_or_model(tmp_path: Path) -> None:
    case = BackendCase(tmp_path, enabled=False)

    async def scenario() -> None:
        await case.runtime.start()
        try:
            result = await case.runtime.execute(case.command("plan"))
            assert result["status"] == "unavailable"
            assert result["event_created"] is False
            assert case.model.requests == []
            assert case.effects.effects == 0
        finally:
            await case.runtime.close()

    asyncio.run(scenario())


def test_calendar_backend_no_grant_is_unavailable_not_a_fake_effect(tmp_path: Path) -> None:
    case = BackendCase(tmp_path)
    case.source.active = False

    async def scenario() -> None:
        await case.runtime.start()
        try:
            result = await case.runtime.execute(case.command("preview", request=case.request))
            assert result["status"] == "unavailable"
            assert result["event_created"] is False
            assert case.model.requests == []
            assert case.effects.create_calls == 0
        finally:
            await case.runtime.close()

    asyncio.run(scenario())


def test_preview_text_clarification_does_not_call_model_or_create(tmp_path: Path) -> None:
    case = BackendCase(tmp_path)

    async def scenario() -> None:
        await case.runtime.start()
        try:
            result = await case.runtime.execute(
                case.command("preview_text", text="Поставь встречу завтра")
            )
            assert result["status"] == "clarification"
            missing = result["missing"]
            assert isinstance(missing, list)
            # ADR-0108 asks for the first missing field in ordinary exact authoring.
            # The already supplied literal title/day must not be requested again.
            assert missing == ["exact_time"]
            assert result["message"] == "\u0412\u043e сколько начало?"
            assert case.model.requests == []
            assert case.model.wire_sends == 0
            assert case.effects.create_calls == 0
            assert case.effects.effects == 0
            assert case.effects.events == {}
        finally:
            await case.runtime.close()

    asyncio.run(scenario())


def test_preview_text_missing_fields_then_exact_proposal_never_calls_model_or_creates(
    tmp_path: Path,
) -> None:
    case = BackendCase(tmp_path)

    async def scenario() -> None:
        await case.runtime.start()
        try:
            initial = await case.runtime.execute(
                case.command("preview_text", text="Поставь встречу завтра")
            )
            assert initial["status"] == "clarification"
            assert initial["missing"] == ["exact_time"]

            timed = await case.runtime.execute(
                case.command("preview_text", text="Поставь встречу завтра в восемь вечера")
            )
            assert timed["status"] == "clarification"
            assert timed["missing"] == ["duration"]
            assert case.model.requests == []
            assert case.model.wire_sends == 0
            assert case.effects.create_calls == 0
            assert case.effects.effects == 0
            assert case.effects.events == {}

            result = await case.runtime.execute(
                case.command("preview_text", text="Поставь встречу завтра в восемь вечера на час")
            )
            assert result["status"] == "proposed"
            assert result["mode"] == "user_exact_interval"
            assert result["model_called"] is False
            assert result["event_created"] is False
            proposal = CalendarCreateProposal.model_validate_json(json.dumps(result["proposal"]))
            assert proposal.scope == case.scope
            assert proposal.command.request_id == case.request.request_id
            assert proposal.command.title == "встречу"
            assert proposal.command.starts_at == case.clock() + timedelta(days=1, hours=8)
            assert proposal.command.ends_at == case.clock() + timedelta(days=1, hours=9)
            assert proposal.command.timezone == "Europe/Moscow"
            assert case.model.requests == []
            assert case.model.wire_sends == 0
            assert case.effects.create_calls == 0
            assert case.effects.effects == 0
            assert case.effects.events == {}
        finally:
            await case.runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("unknown_after_effect", [False, True])
def test_private_calendar_chain_has_explicit_review_and_readonly_reconcile(
    tmp_path: Path, unknown_after_effect: bool
) -> None:
    case = BackendCase(tmp_path)
    case.effects.fail_after_create = unknown_after_effect

    async def scenario() -> None:
        await case.runtime.start()
        try:
            await case.preview_and_disclose()
            planned = await case.runtime.execute(case.command("plan"))
            assert planned["status"] == "proposed"
            assert planned["event_created"] is False
            assert case.effects.effects == 0
            proposal = planned["proposal"]
            assert isinstance(proposal, dict)
            refused = await case.runtime.execute(case.command("approve", review_digest="0" * 64))
            assert refused["status"] == "unavailable"
            assert refused["event_created"] is False
            assert case.effects.effects == 0
            approved = await case.runtime.execute(
                case.command("approve", review_digest=proposal["review_digest"])
            )
            assert approved["state"] == "approved"
            approval = approved["approval"]
            assert isinstance(approval, dict)
            receipt = await case.runtime.execute(
                case.command("create", approval_id=UUID(approval["approval_id"]))
            )
            assert receipt["state"] == ("unknown" if unknown_after_effect else "created")
            assert case.effects.effects == 1
            reconciled = await case.runtime.execute(case.command("reconcile"))
            assert reconciled["state"] == "reconciled_present"
            assert case.effects.create_calls == 1
            assert case.effects.lookup_calls == 1
            assert case.model.wire_sends == 1
            history = await case.runtime.execute(
                HeadlessCommand(operation="history", session_id=case.session)
            )
            assert history["interactions"] == []
        finally:
            await case.runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("mismatch", ["session", "request"])
def test_calendar_backend_does_not_cross_session_or_request_ids(
    tmp_path: Path, mismatch: str
) -> None:
    case = BackendCase(tmp_path)
    command = case.command("preview", request=case.request)
    command = command.model_copy(
        update={"session_id": case.other_session}
        if mismatch == "session"
        else {"request_id": str(uuid4())}
    )

    async def scenario() -> None:
        await case.runtime.start()
        try:
            with pytest.raises(HeadlessError, match="command_failed"):
                await case.runtime.execute(command)
            assert case.model.requests == []
            assert case.effects.create_calls == 0
        finally:
            await case.runtime.close()

    asyncio.run(scenario())


def test_calendar_wire_does_not_accept_mixed_permission_fields() -> None:
    with pytest.raises(ValidationError):
        CalendarBackendCommand(operation="plan", explicit_disclosure=True)
    with pytest.raises(ValidationError):
        HeadlessCommand(operation="status", calendar=CalendarBackendCommand(operation="plan"))


def test_cancelled_calendar_wait_retains_shared_owned_lane(tmp_path: Path) -> None:
    case = BackendCase(tmp_path)

    async def scenario() -> None:
        case.model.release = asyncio.Event()
        await case.runtime.start()
        try:
            await case.preview_and_disclose()
            waiter = asyncio.create_task(case.runtime.execute(case.command("plan")))
            await asyncio.wait_for(case.model.entered.wait(), timeout=10)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            with pytest.raises(HeadlessError, match="busy"):
                await case.runtime.execute(HeadlessCommand(operation="status"))
            case.model.release.set()
            closed = await case.runtime.close()
            assert closed["state"] == "closed"
            assert case.model.wire_sends == 1
            assert case.effects.effects == 0
        finally:
            case.model.release.set()
            await case.runtime.close()

    asyncio.run(scenario())


def test_revoke_while_calendar_provider_waits_blocks_actual_dispatch(tmp_path: Path) -> None:
    case = BackendCase(tmp_path)

    async def scenario() -> None:
        case.model.release = asyncio.Event()
        await case.runtime.start()
        try:
            await case.preview_and_disclose()
            waiter = asyncio.create_task(case.runtime.execute(case.command("plan")))
            await asyncio.wait_for(case.model.entered.wait(), timeout=10)
            case.source.active = False
            case.model.release.set()
            failure: HeadlessError | None = None
            result: dict[str, object] | None = None
            try:
                result = await waiter
            except HeadlessError as error:
                failure = error
            if failure is not None:
                assert failure.code == "command_failed"
            else:
                assert result is not None
                assert result["status"] == "unavailable"
            assert case.model.wire_sends == 0
            assert case.effects.create_calls == 0
        finally:
            case.model.release.set()
            await case.runtime.close()

    asyncio.run(scenario())


def test_host_grant_revoke_during_awaited_model_blocks_transport(tmp_path: Path) -> None:
    case = BackendCase(tmp_path)
    live = True

    def host_guard() -> None:
        if not live:
            raise RuntimeError("public synthetic host grant revoked")

    async def scenario() -> None:
        nonlocal live
        case.model.release = asyncio.Event()
        await case.runtime.start()
        try:
            await case.preview_and_disclose()
            waiter = asyncio.create_task(
                case.runtime.execute(case.command("plan"), dispatch_guard=host_guard)
            )
            await asyncio.wait_for(case.model.entered.wait(), timeout=10)
            live = False
            case.model.release.set()
            result = await waiter
            assert result["status"] == "unavailable"
            assert result["event_created"] is False
            # The calendar grant remains valid; only the caller's grant changed.
            assert case.source.active is True
            assert case.model.wire_sends == 0
            assert case.effects.create_calls == 0
        finally:
            case.model.release.set()
            await case.runtime.close()

    asyncio.run(scenario())


def test_host_grant_revoke_immediately_before_effect_never_creates(tmp_path: Path) -> None:
    case = BackendCase(tmp_path)
    live = True

    def host_guard() -> None:
        if not live:
            raise RuntimeError("public synthetic host grant revoked")

    def revoke() -> None:
        nonlocal live
        live = False

    async def scenario() -> None:
        await case.runtime.start()
        try:
            await case.preview_and_disclose()
            planned = await case.runtime.execute(case.command("plan"))
            proposal = planned["proposal"]
            assert isinstance(proposal, dict)
            approved = await case.runtime.execute(
                case.command("approve", review_digest=proposal["review_digest"])
            )
            approval = approved["approval"]
            assert isinstance(approval, dict)
            case.effects.on_ready = revoke
            result = await case.runtime.execute(
                case.command("create", approval_id=UUID(approval["approval_id"])),
                dispatch_guard=host_guard,
            )
            # The one-use effect callback rejected before admission/COMMIT.
            # Do not disclose the proposal after host authority has been revoked;
            # the existing owner still records its exact pre-effect rejection.
            assert result == {
                "status": "unknown",
                "reason": "calendar_operation_unavailable",
                "event_created": None,
                "reconcile_required": True,
            }
            assert case.actions is not None
            durable = case.actions.get(case.request.request_id, scope=case.scope)
            assert durable is not None
            assert durable.state == "rejected"
            assert durable.reason == "calendar_action_revoked"
            assert durable.attempt is not None
            assert case.source.active is True
            assert case.effects.create_calls == 1
            assert case.effects.effects == 0
            assert case.effects.events == {}
        finally:
            await case.runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("revoke", ["host", "source"])
def test_post_effect_revoke_suppresses_receipt_and_keeps_durable_unknown(
    tmp_path: Path, revoke: str
) -> None:
    case = BackendCase(tmp_path)
    live = True

    def host_guard() -> None:
        if not live:
            raise RuntimeError("public synthetic host grant revoked")

    def revoke_after_effect() -> None:
        nonlocal live
        if revoke == "host":
            live = False
        else:
            case.source.active = False

    async def scenario() -> None:
        await case.runtime.start()
        try:
            await case.preview_and_disclose()
            planned = await case.runtime.execute(case.command("plan"))
            proposal = planned["proposal"]
            assert isinstance(proposal, dict)
            approved = await case.runtime.execute(
                case.command("approve", review_digest=proposal["review_digest"])
            )
            approval = approved["approval"]
            assert isinstance(approval, dict)
            case.effects.after_effect = revoke_after_effect
            result = await case.runtime.execute(
                case.command("create", approval_id=UUID(approval["approval_id"])),
                dispatch_guard=host_guard,
            )
            assert result == {
                "status": "unknown",
                "reason": "calendar_operation_unavailable",
                "event_created": None,
                "reconcile_required": True,
            }
            assert case.actions is not None
            durable = case.actions.get(case.request.request_id, scope=case.scope)
            assert durable is not None
            assert durable.state == "unknown"
            assert durable.reason == "calendar_action_unknown_no_replay"
            assert durable.attempt is not None
            assert durable.receipt is None
            assert case.effects.create_calls == 1
            assert case.effects.effects == 1
            assert len(case.effects.events) == 1
            assert case.effects.lookup_calls == 0
        finally:
            await case.runtime.close()

    asyncio.run(scenario())


def test_original_read_grant_rotation_before_effect_rejects_without_creation(
    tmp_path: Path,
) -> None:
    case = BackendCase(tmp_path)
    original_read_grant = case.source.read_grant_id

    def rotate_read_grant() -> None:
        case.source.read_grant_id = uuid4()

    async def scenario() -> None:
        await case.runtime.start()
        try:
            await case.preview_and_disclose()
            planned = await case.runtime.execute(case.command("plan"))
            proposal = planned["proposal"]
            assert isinstance(proposal, dict)
            approved = await case.runtime.execute(
                case.command("approve", review_digest=proposal["review_digest"])
            )
            approval = approved["approval"]
            assert isinstance(approval, dict)
            case.effects.on_ready = rotate_read_grant
            result = await case.runtime.execute(
                case.command("create", approval_id=UUID(approval["approval_id"]))
            )
            assert result == {
                "status": "unknown",
                "reason": "calendar_operation_unavailable",
                "event_created": None,
                "reconcile_required": True,
            }
            assert case.actions is not None
            durable = case.actions.get(case.request.request_id, scope=case.scope)
            assert durable is not None
            assert durable.state == "rejected"
            assert durable.reason == "calendar_action_revoked"
            assert durable.attempt is not None
            assert case.source.active is True
            assert case.source.read_grant_id != original_read_grant
            assert case.effects.create_calls == 1
            assert case.effects.effects == 0
            assert case.effects.events == {}
        finally:
            await case.runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("revoke_host", [False, True])
def test_granted_native_read_checks_host_grant_after_spawn_wait(revoke_host: bool) -> None:
    coordinator, _, request, snapshot, _ = public_source()
    live = True
    read_effects = 0

    def host_guard() -> None:
        if not live:
            raise RuntimeError("public synthetic host grant revoked")

    async def scenario() -> None:
        nonlocal live, read_effects
        entered, release = asyncio.Event(), asyncio.Event()

        async def native_read(
            request: CalendarReadRequest, *, before_read: Callable[[], None]
        ) -> CalendarReadResult:
            nonlocal read_effects
            entered.set()
            await release.wait()
            before_read()
            read_effects += 1
            return CalendarReadResult(request_id=request.request_id, observed_at=NOW)

        source = GrantedCalendarPlanningSource(
            coordinator, native_read, current_scope=lambda: request.scope
        )
        with calendar_host_admission(host_guard):
            waiter = asyncio.create_task(source.fetch(request))
            await asyncio.wait_for(entered.wait(), timeout=10)
            live = not revoke_host
            release.set()
            if revoke_host:
                with pytest.raises(CalendarPlanningError, match="calendar_native_read_unavailable"):
                    await waiter
                assert read_effects == 0
            else:
                result = await waiter
                assert result.complete is True
                assert result.events == ()
                assert read_effects == 1
        # Removing only the ephemeral host guard restores no hidden permissions:
        # the original independent selected Calendar grant was never revoked.
        source.require(request.scope, read_grant_id=snapshot.read_grant_id)
        assert coordinator.grant is not None
        assert coordinator.grant.grant_id == snapshot.read_grant_id

    asyncio.run(scenario())
