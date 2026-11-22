"""Independent public command-authority scenarios; no Core, model or native account."""

# ruff: noqa: RUF001 - intentional Russian conversation fixtures.

import asyncio
import json
import os
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast
from uuid import UUID, uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from satori.application.assistant.calendar_actions import CalendarActionOwner
from satori.application.assistant.calendar_actions_contracts import (
    CalendarActionCatalog,
    CalendarActionScope,
    CalendarCreateAttempt,
    CalendarCreateInput,
    CalendarLookupReceipt,
    CalendarProviderReceipt,
)
from satori.application.assistant.calendar_admission import calendar_host_admission
from satori.application.assistant.calendar_planning_contracts import CalendarPlanningRequest
from satori.application.assistant.calendar_planning_source import GrantedCalendarPlanningSource
from satori.application.assistant.calendar_reasoning import CoreCalendarReasoner
from satori.application.assistant.calendar_service import CalendarAssistantService
from satori.application.assistant.contracts import (
    CalendarCatalog,
    CalendarDescriptor,
    CalendarOccurrence,
    CalendarReadRequest,
    CalendarReadResult,
    CalendarSourceRef,
)
from satori.desktop.calendar_chat_controller import CalendarChatController
from satori.desktop.calendar_controller import CalendarController
from satori.desktop.calendar_conversation import CALENDAR_TASK_SECONDS
from satori.infrastructure.assistant.calendar_actions_store import SQLiteCalendarActionStore
from satori.server.calendar_backend import CalendarBackendCommand, execute_calendar_command
from tests.calendar_actions_fixture import FixtureCalendarActionProvider

NOW = datetime(2026, 11, 4, 9, tzinfo=UTC)
EVENTS = CalendarSourceRef(account_id="public-icloud", calendar_id="public-events")
WORK = CalendarSourceRef(account_id="public-icloud", calendar_id="public-work")
MISSING = "Мы завтра планируем созвон в 8 вечера, занеси его в календарь события"
COMPLETE = "Запиши созвон завтра в 8 вечера на час в календарь События"


class Clock:
    def __init__(self) -> None:
        self.now, self.tick = NOW, 1000.0

    def __call__(self) -> datetime:
        return self.now

    def monotonic(self) -> float:
        return self.tick


def descriptor(source: CalendarSourceRef, title: str, **changes: object) -> CalendarDescriptor:
    values: dict[str, object] = {
        "source": source,
        "title": title,
        "account_title": "PUBLIC iCloud",
        "writable": True,
        "allows_modification": True,
        "supports_events": True,
        "source_kind": "caldav",
        **changes,
    }
    return CalendarDescriptor.model_validate(values)


class PublicCalendar(CalendarController):
    """Keep the actual sole grant owner; replace only native listing/read transport."""

    def __init__(self, root: Path, clock: Clock) -> None:
        super().__init__(root, clock=clock, helper_command=lambda: None)
        self.clock = clock
        self.refreshes = 0
        self.selections: list[tuple[CalendarSourceRef, ...]] = []
        self.permission_status = "full_access"

    def refresh_catalog(self) -> None:
        self.refreshes += 1
        self._reset_calendar("connecting")

    def complete_catalog(self, calendars: tuple[CalendarDescriptor, ...]) -> None:
        self._coordinator.accept_catalog(
            CalendarCatalog(request_id=uuid4(), observed_at=self.clock(), calendars=calendars)
        )
        self._catalog_phase = "active"
        self.changed.emit()
        for _ in range(4):
            QApplication.processEvents()

    def use_calendars(self, selected: tuple[CalendarSourceRef, ...]) -> bool:
        self.selections.append(selected)
        return super().use_calendars(selected)


class NeverReasoner:
    """Exact commands must not request inference or even construct a Core."""

    provider_id = "public-never-model"

    def __init__(self) -> None:
        self.calls = 0

    def require_scope(self, scope: CalendarActionScope) -> None:
        del scope

    def require_session(self, request: CalendarPlanningRequest) -> None:
        del request

    async def choose(self, *args: object) -> None:
        del args
        self.calls += 1
        raise AssertionError("An exact user command must never call a model")


class PublicEffectPort:
    """Retag public synthetic receipts; no native helper is discovered or invoked."""

    provider_kind: Literal["macos_eventkit"] = "macos_eventkit"
    external_effects = False

    def __init__(self, clock: Clock) -> None:
        self.fixture = FixtureCalendarActionProvider(clock)

    async def create(
        self, attempt: CalendarCreateAttempt, *, before_effect: Callable[[], None]
    ) -> CalendarProviderReceipt:
        result = await self.fixture.create(attempt, before_effect=before_effect)
        return CalendarProviderReceipt.model_validate(
            result.model_copy(update={"provider": "macos_eventkit"})
        )

    async def lookup(
        self,
        attempt: CalendarCreateAttempt,
        scope: CalendarActionScope,
        *,
        before_read: Callable[[], None],
    ) -> CalendarLookupReceipt:
        result = await self.fixture.lookup(attempt, scope, before_read=before_read)
        return CalendarLookupReceipt.model_validate(
            result.model_copy(update={"provider": "macos_eventkit"})
        )


class AuthorityCase:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.chmod(0o700)
        self.clock = Clock()
        self.calendar = PublicCalendar(self.root, self.clock)
        self.chat = CalendarChatController(
            self.calendar,
            self.root,
            clock=self.clock,
            monotonic=self.clock.monotonic,
            conversational=True,
            foreground=lambda: True,
        )
        self.identity, self.session = uuid4(), uuid4()
        self.chat.bind_context(str(self.identity), str(self.session), NeverReasoner.provider_id)
        self.sent: list[dict[str, object]] = []
        self.chat.command_requested.connect(self.sent.append)
        self.cursor = 0
        self.current_scope: CalendarActionScope | None = None
        self.reads: list[CalendarReadRequest] = []
        self.conflict = False
        self.partial = False
        self.port = PublicEffectPort(self.clock)
        self.reasoner = NeverReasoner()
        self.actions = CalendarActionOwner(
            SQLiteCalendarActionStore(self.root / "actions" / "actions.sqlite3"),
            self.port,
            clock=self.clock,
        )
        self.source = GrantedCalendarPlanningSource(
            self.calendar._coordinator,
            self.native_read,
            current_scope=self.scope,
        )
        self.service = CalendarAssistantService(
            reasoner=cast(CoreCalendarReasoner, self.reasoner),
            source=self.source,
            actions=self.actions,
            clock=self.clock,
            live_scope=self.scope,
        )

    def scope(self) -> CalendarActionScope:
        assert self.current_scope is not None
        return self.current_scope

    def require_current(self) -> None:
        self.chat._require_current()

    async def native_read(
        self, request: CalendarReadRequest, *, before_read: Callable[[], None]
    ) -> CalendarReadResult:
        before_read()
        self.reads.append(request)
        return CalendarReadResult(
            request_id=request.request_id,
            observed_at=self.clock(),
            status="partial" if self.partial else "ok",
            reason="limit_reached" if self.partial else None,
            occurrences=(
                CalendarOccurrence(
                    source=EVENTS,
                    event_id="public-busy",
                    occurrence_id="public-busy-1",
                    title="PUBLIC conflicting event",
                    starts_at=request.starts_at,
                    ends_at=request.ends_at,
                    timezone=request.timezone,
                ),
            )
            if self.conflict
            else (),
        )

    def begin(self, text: str = COMPLETE) -> None:
        assert self.chat.submit(text)
        assert self.calendar.refreshes == 1
        assert self.sent == []
        self.calendar.complete_catalog((descriptor(WORK, "Работа"), descriptor(EVENTS, "События")))
        assert len(self.sent) == 1

    def wire(self, expected: str) -> tuple[CalendarBackendCommand, UUID]:
        value = self.sent[self.cursor]
        command = CalendarBackendCommand.model_validate_json(json.dumps(value["calendar"]))
        assert command.operation == expected
        if command.request is not None:
            self.current_scope = command.request.scope
        return command, UUID(str(value["request_id"]))

    async def execute(self, expected: str, *, deliver: bool = True) -> dict[str, object]:
        command, request_id = self.wire(expected)
        self.cursor += 1
        with calendar_host_admission(self.require_current):
            result = await execute_calendar_command(
                self.service,
                command,
                request_id=request_id,
                session_id=self.session,
            )
        if deliver:
            self.chat.accept_result(str(request_id), expected, result)
        return result

    async def finish(self) -> None:
        first = await self.execute("exact_preview")
        assert first["model_called"] is False
        assert self.port.fixture.effects == 0
        assert self.chat.card is not None
        assert self.chat.card.status == "proposed"
        assert (await self.execute("approve"))["state"] == "approved"
        assert self.port.fixture.effects == 0
        assert (await self.execute("create"))["state"] == "created"

    def close(self) -> None:
        self.service.close()
        self.chat.close()
        self.calendar.close()
        self.chat.deleteLater()
        self.calendar.deleteLater()
        QApplication.processEvents()


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


@pytest.fixture
def case(application: QApplication, tmp_path: Path) -> Iterator[AuthorityCase]:
    del application
    value = AuthorityCase(tmp_path)
    yield value
    value.close()


def test_direct_command_selects_unique_named_calendar_and_creates_once(case: AuthorityCase) -> None:
    case.begin()
    authority = case.chat.conversation_authority
    assert authority is not None
    assert authority.request.scope.calendar_id == EVENTS.calendar_id
    assert case.calendar.selections == [(EVENTS,)]
    asyncio.run(case.finish())
    assert len(case.reads) >= 3
    assert case.reasoner.calls == 0
    assert case.port.fixture.create_calls == case.port.fixture.effects == 1
    assert case.chat.card is not None
    assert case.chat.card.status == "created"
    assert "События" in case.chat.card.message
    assert "20:00–21:00" in case.chat.card.message
    assert [cast(dict[str, object], item["calendar"])["operation"] for item in case.sent] == [
        "exact_preview",
        "approve",
        "create",
    ]


@pytest.mark.parametrize("answer", ["на час", "до 9", "до девяти"])
def test_missing_duration_is_bound_conversation_not_an_extra_approval(
    case: AuthorityCase,
    answer: str,
) -> None:
    assert case.chat.submit(MISSING)
    pending = case.chat.pending_clarification
    assert pending is not None
    assert pending.waiting_for == "end"
    assert case.calendar.refreshes == 0
    assert case.sent == []
    assert case.chat.answer("да")
    assert case.calendar.refreshes == 0
    assert case.chat.conversation_authority is None
    assert case.chat.answer(answer)
    assert case.calendar.refreshes == 1
    case.calendar.complete_catalog((descriptor(EVENTS, "События"),))
    authority = case.chat.conversation_authority
    assert authority is not None
    assert authority.task_id == pending.task_id
    assert authority.request.window_start == datetime(2026, 11, 5, 17, tzinfo=UTC)
    assert authority.request.window_end == datetime(2026, 11, 5, 18, tzinfo=UTC)
    asyncio.run(case.finish())
    assert case.port.fixture.effects == 1


def test_followup_after_midnight_uses_original_tomorrow_and_expiry(case: AuthorityCase) -> None:
    case.clock.now = datetime(2026, 11, 4, 20, 59, tzinfo=UTC)
    assert case.chat.submit(MISSING)
    pending = case.chat.pending_clarification
    assert pending is not None
    case.clock.now += timedelta(minutes=2)
    case.clock.tick += 120
    assert case.chat.answer("на час")
    case.calendar.complete_catalog((descriptor(EVENTS, "События"),))
    authority = case.chat.conversation_authority
    assert authority is not None
    assert authority.expires_monotonic <= pending.expires_at_monotonic
    assert case.chat._task is not None
    assert case.chat._task.expires_monotonic == pending.expires_at_monotonic
    assert authority.request.window_start == datetime(2026, 11, 5, 17, tzinfo=UTC)


@pytest.mark.parametrize("kind", ["missing", "duplicate", "read_only", "subscribed", "no_events"])
def test_unresolved_target_never_falls_back_to_work(case: AuthorityCase, kind: str) -> None:
    calendars = [descriptor(WORK, "Работа")]
    if kind == "duplicate":
        calendars.extend(
            [
                descriptor(EVENTS, "События"),
                descriptor(
                    CalendarSourceRef(account_id=EVENTS.account_id, calendar_id="other-events"),
                    "События",
                ),
            ]
        )
    elif kind != "missing":
        changes: dict[str, object]
        if kind == "read_only":
            changes = {"writable": False, "allows_modification": False}
        elif kind == "subscribed":
            changes = {"source_kind": "subscribed"}
        else:
            changes = {"supports_events": False, "writable": False, "allows_modification": False}
        calendars.append(descriptor(EVENTS, "События", **changes))
    assert case.chat.submit(COMPLETE)
    case.calendar.complete_catalog(tuple(calendars))
    assert case.calendar.selections == []
    assert case.sent == []
    assert case.chat.conversation_authority is None
    assert case.chat.card is not None
    assert case.chat.card.status == "clarification"
    assert case.port.fixture.effects == 0


@pytest.mark.parametrize(
    "text",
    [
        "Не записывай созвон завтра в 8 вечера на час в календарь События",
        "Объясни фразу «запиши созвон завтра в 8 вечера на час в календарь События»",
        "Предложи время для созвона завтра с 18:00 до 22:00 на час в календаре События",
        "Запиши созвон завтра около 8 вечера на час в календарь События",
        "Запиши созвон завтра в 8 вечера и в 21:00 на час в календарь События",
        "Запиши созвон завтра в 8 вечера в 21 на час в календарь События",
        "Запиши созвон завтра с 20:00 до 21:00 в 22 на час в календарь События",
        "Запиши созвон завтра в 8 вечера на час каждый день в календарь События",
        "Запиши созвон завтра в 8 вечера на час и пригласи бабушку в календарь События",
    ],
)
def test_non_authorizing_or_unsupported_semantics_never_auto_create(
    case: AuthorityCase,
    text: str,
) -> None:
    case.chat.submit(text)
    if case.calendar.refreshes:
        case.calendar.complete_catalog((descriptor(EVENTS, "События"),))
    assert case.chat.conversation_authority is None
    operations = [cast(dict[str, object], value["calendar"])["operation"] for value in case.sent]
    assert "approve" not in operations
    assert "create" not in operations
    assert case.port.fixture.effects == 0
    case.chat.answer("да")
    assert case.chat.conversation_authority is None
    assert len(case.sent) == len(operations)


def test_absent_os_permission_does_not_grant_or_start_preview(case: AuthorityCase) -> None:
    case.calendar.permission_status = "denied"
    assert case.chat.submit(COMPLETE)
    case.calendar.changed.emit()
    QApplication.processEvents()
    assert case.calendar.selections == []
    assert case.sent == []
    assert case.chat.conversation_authority is None
    assert case.chat.card is not None
    assert case.chat.card.status == "connection_required"


@pytest.mark.parametrize("change", ["title", "start", "zone", "target", "session", "catalog_title"])
def test_valid_but_wrong_proposal_never_becomes_approval(case: AuthorityCase, change: str) -> None:
    case.begin()
    result = asyncio.run(case.execute("exact_preview", deliver=False))
    authority = case.chat.conversation_authority
    assert authority is not None
    original = case.actions.get(authority.request.request_id, scope=authority.request.scope)
    assert original is not None
    scope_values, command_values = (
        original.proposal.scope.model_dump(),
        original.proposal.command.model_dump(),
    )
    if change == "title":
        command_values["title"] = "PUBLIC substituted title"
    elif change == "start":
        command_values["starts_at"] += timedelta(minutes=5)
        command_values["ends_at"] += timedelta(minutes=5)
    elif change == "zone":
        command_values["timezone"] = scope_values["timezone"] = "Europe/Paris"
    elif change == "target":
        scope_values["calendar_id"] = WORK.calendar_id
    elif change == "session":
        scope_values["session_id"] = uuid4()
    scope = CalendarActionScope.model_validate(scope_values)
    catalog = CalendarActionCatalog(
        scope=scope,
        revision=uuid4(),
        observed_at=case.clock(),
        full_access=True,
        writable=True,
        supports_events=True,
        calendar_title="PUBLIC wrong calendar" if change == "catalog_title" else "События",
        account_title="PUBLIC iCloud",
    )
    # A second fixture owner issues a fully valid, digest-consistent but unwanted proposal.
    other = CalendarActionOwner(
        SQLiteCalendarActionStore(case.root / "other-actions" / "actions.sqlite3"),
        case.port,
        clock=case.clock,
    )
    other.accept_catalog(catalog)
    wrong = other.propose(
        CalendarCreateInput.model_validate(command_values), scope=scope, catalog=catalog
    )
    other.close()
    result["proposal"] = wrong.proposal.model_dump(mode="json")
    case.chat.accept_result(str(authority.request.request_id), "exact_preview", result)
    assert len(case.sent) == 1
    assert case.port.fixture.effects == 0
    assert case.chat.card is None or case.chat.card.status != "created"


def test_changed_source_before_auto_approval_never_dispatches(case: AuthorityCase) -> None:
    case.begin()
    asyncio.run(case.execute("exact_preview"))
    case.conflict = True
    result = asyncio.run(case.execute("approve"))
    assert result["status"] == "unavailable"
    assert len(case.sent) == 2
    assert case.port.fixture.create_calls == 0
    assert case.chat.card is not None
    assert case.chat.card.status != "created"


def test_incomplete_source_does_not_become_available_or_created(case: AuthorityCase) -> None:
    case.begin()
    case.partial = True
    assert asyncio.run(case.execute("exact_preview"))["status"] == "unavailable"
    assert len(case.sent) == 1
    assert case.port.fixture.effects == 0
    assert case.chat.card is not None
    assert case.chat.card.status != "created"


def test_focus_loss_retains_only_authored_draft_and_requires_new_input(case: AuthorityCase) -> None:
    case.begin()
    old_request = case.chat.active_request_id
    old_result = asyncio.run(case.execute("exact_preview", deliver=False))
    case.chat.set_foreground(False)
    assert case.chat.card is not None
    assert case.chat.card.status == "paused"
    assert case.chat.conversation_authority is None
    assert case.chat.active_request_id is None
    case.chat.set_foreground(True)
    QApplication.processEvents()
    assert len(case.sent) == 1
    assert case.calendar.refreshes == 1
    assert old_request is not None
    case.chat.accept_result(old_request, "exact_preview", old_result)
    assert len(case.sent) == 1
    assert case.chat.answer("продолжи")
    assert case.calendar.refreshes == 1
    assert len(case.sent) == 2
    assert case.chat.active_request_id != old_request
    reads_before_resume = len(case.reads)
    asyncio.run(case.execute("exact_preview"))
    assert len(case.reads) > reads_before_resume
    assert case.port.fixture.effects == 0


def test_queued_create_cannot_be_rearmed_after_focus_loss(case: AuthorityCase) -> None:
    case.begin()
    asyncio.run(case.execute("exact_preview"))
    asyncio.run(case.execute("approve"))
    assert len(case.sent) == 3
    case.chat.set_foreground(False)
    case.chat.set_foreground(True)
    assert case.chat.card is not None
    assert case.chat.card.status == "unknown"
    assert case.chat.answer("продолжи")
    assert len(case.sent) == 3
    assert case.calendar.refreshes == 1
    assert case.port.fixture.effects == 0


def test_queued_create_full_repeat_is_blocked_before_any_receipt(case: AuthorityCase) -> None:
    case.begin()
    asyncio.run(case.execute("exact_preview"))
    asyncio.run(case.execute("approve"))
    original = case.chat.active_request_id
    case.chat.submit(COMPLETE)
    QApplication.processEvents()
    assert case.chat.active_request_id == original
    assert len(case.sent) == 3
    assert case.calendar.refreshes == 1
    assert case.port.fixture.create_calls == 0


def test_duplicate_review_callback_during_create_keeps_original_attempt(
    case: AuthorityCase,
) -> None:
    case.begin()
    asyncio.run(case.execute("exact_preview"))
    asyncio.run(case.execute("approve"))
    original = case.chat.active_request_id
    case.chat.create_reviewed()
    assert case.chat.active_request_id == original
    assert len(case.sent) == 3
    assert case.port.fixture.create_calls == 0


def test_failed_reconcile_after_focus_loss_keeps_uncertain_attempt_fence(
    case: AuthorityCase,
) -> None:
    case.begin()
    asyncio.run(case.execute("exact_preview"))
    asyncio.run(case.execute("approve"))
    case.chat.set_foreground(False)
    case.chat.set_foreground(True)
    case.chat.reconcile()
    assert case.chat.card is not None
    assert case.chat.card.status == "unknown"
    case.chat.submit(COMPLETE)
    QApplication.processEvents()
    assert len(case.sent) == 3
    assert case.calendar.refreshes == 1
    assert case.port.fixture.create_calls == 0


def test_unknown_effect_receipt_never_replays_on_answer(case: AuthorityCase) -> None:
    case.begin()
    asyncio.run(case.execute("exact_preview"))
    asyncio.run(case.execute("approve"))
    case.port.fixture.fail_after_create = True
    result = asyncio.run(case.execute("create"))
    assert result["state"] == "unknown"
    assert case.port.fixture.effects == 1
    assert case.chat.card is not None
    assert case.chat.card.status == "unknown"
    assert case.chat.answer("продолжи")
    assert case.chat.answer("да")
    assert len(case.sent) == 3
    assert case.port.fixture.create_calls == 1


def test_focus_loss_after_provider_effect_never_publishes_false_success_or_replays(
    case: AuthorityCase,
) -> None:
    case.begin()
    asyncio.run(case.execute("exact_preview"))
    asyncio.run(case.execute("approve"))
    case.port.fixture.after_effect = lambda: case.chat.set_foreground(False)
    result = asyncio.run(case.execute("create"))
    assert result.get("status") == "unknown" or result.get("state") == "unknown"
    assert case.port.fixture.effects == 1
    assert case.chat.card is not None
    assert case.chat.card.status == "unknown"
    case.chat.set_foreground(True)
    case.chat.submit(COMPLETE)
    QApplication.processEvents()
    assert len(case.sent) == 3
    assert case.port.fixture.create_calls == 1


def test_repeating_full_command_cannot_replace_uncertain_attempt(case: AuthorityCase) -> None:
    case.begin()
    asyncio.run(case.execute("exact_preview"))
    asyncio.run(case.execute("approve"))
    case.port.fixture.fail_after_create = True
    assert asyncio.run(case.execute("create"))["state"] == "unknown"
    old_request = case.chat.active_request_id
    case.chat.submit(COMPLETE)
    QApplication.processEvents()
    assert len(case.sent) == 3
    assert case.calendar.refreshes == 1
    assert case.chat.card is not None
    assert case.chat.card.status == "unknown"
    assert case.chat.active_request_id == old_request
    assert case.port.fixture.create_calls == case.port.fixture.effects == 1


def test_existing_same_scope_grant_is_reused_without_selection_reset(case: AuthorityCase) -> None:
    case.calendar.complete_catalog((descriptor(WORK, "Работа"), descriptor(EVENTS, "События")))
    assert case.calendar.use_calendars((WORK, EVENTS))
    grant = case.calendar.grant
    generation = case.calendar.generation
    assert grant is not None
    case.calendar.selections.clear()
    assert case.chat.submit(COMPLETE)
    assert case.calendar.refreshes == 0
    assert case.calendar.grant == grant
    assert case.calendar.generation == generation
    assert case.calendar.selections == []
    assert len(case.sent) == 1
    assert case.chat.conversation_authority is not None
    assert case.chat.conversation_authority.request.scope.calendar_id == EVENTS.calendar_id
    case.chat.clear()
    assert case.calendar.grant == grant


def test_task_only_grant_is_revoked_on_clear_without_restoring_old_permission(
    case: AuthorityCase,
) -> None:
    case.begin()
    assert case.calendar.grant is not None
    assert case.calendar.grant.resources == (EVENTS,)
    case.chat.clear()
    assert case.calendar.grant is None
    assert case.chat.conversation_authority is None
    assert case.calendar.refreshes == 1
    assert case.port.fixture.effects == 0


def test_verified_success_survives_focus_without_rearming(case: AuthorityCase) -> None:
    case.begin()
    asyncio.run(case.finish())
    assert case.chat.card is not None
    final = case.chat.card
    case.chat.set_foreground(False)
    assert case.chat.card is not None
    assert case.chat.card.status == final.status == "created"
    assert case.chat.card.message == final.message
    assert case.chat.conversation_authority is None
    assert case.chat.active_request_id is None
    assert case.chat.card.proposal is None
    case.chat.set_foreground(True)
    QApplication.processEvents()
    assert len(case.sent) == 3
    assert case.port.fixture.effects == 1


def test_clarification_expiry_clears_even_without_another_input(case: AuthorityCase) -> None:
    assert case.chat.submit(MISSING)
    case.clock.tick += CALENDAR_TASK_SECONDS + 1
    case.chat.tick()
    assert case.chat.pending_clarification is None
    assert case.chat.card is None
    assert not case.chat.answer("на час")
    assert case.sent == []
    assert case.port.fixture.effects == 0


@pytest.mark.parametrize("boundary", ["session", "profile", "close", "cancel"])
def test_pending_answer_never_crosses_lifecycle_boundary(
    case: AuthorityCase, boundary: str
) -> None:
    assert case.chat.submit(MISSING)
    if boundary == "session":
        case.chat.bind_context(str(case.identity), str(uuid4()), NeverReasoner.provider_id)
    elif boundary == "profile":
        profile = case.root / "new-profile"
        profile.mkdir(mode=0o700)
        case.chat.set_profile(profile)
    elif boundary == "close":
        case.chat.close()
    else:
        assert case.chat.answer("не надо")
    assert not case.chat.answer("на час")
    assert case.chat.conversation_authority is None
    assert case.sent == []
    assert case.calendar.refreshes == 0


def test_unrelated_answer_falls_through_without_changing_pending_authority(
    case: AuthorityCase,
) -> None:
    assert case.chat.submit(MISSING)
    pending = case.chat.pending_clarification
    assert pending is not None
    assert not case.chat.answer("Расскажи, что такое календари")
    assert case.chat.pending_clarification == pending
    assert not case.chat.answer("Я сегодня работаю над другим проектом")
    assert case.chat.pending_clarification == pending
    assert case.sent == []
    assert case.chat.conversation_authority is None


def test_authority_revision_and_payload_are_not_interchangeable(case: AuthorityCase) -> None:
    case.begin()
    result = asyncio.run(case.execute("exact_preview", deliver=False))
    authority = case.chat.conversation_authority
    task = case.chat._task
    assert authority is not None
    assert task is not None
    record = case.actions.get(authority.request.request_id, scope=authority.request.scope)
    assert record is not None
    assert not authority.admits(
        replace(task, revision=uuid4()),
        case.chat.fields,
        task.context,
        authority.request,
        record.proposal,
        case.clock.monotonic(),
    )
    case.chat.clear()
    case.chat.accept_result(str(authority.request.request_id), "exact_preview", result)
    assert len(case.sent) == 1
    assert case.port.fixture.effects == 0
