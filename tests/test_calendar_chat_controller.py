"""Public Qt card/worker framing; no model, native account, Calendar write or install."""

# ruff: noqa: RUF001 - intentional Russian task fixtures.

import json
import os
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from satori.application.assistant.calendar_actions import CalendarActionOwner
from satori.application.assistant.calendar_actions_contracts import (
    CalendarActionCatalog,
    CalendarCreateInput,
)
from satori.application.assistant.calendar_host_lease import CalendarHostLease
from satori.application.assistant.calendar_planning_contracts import CalendarPlanningRequest
from satori.application.assistant.contracts import (
    CalendarCatalog,
    CalendarDescriptor,
    CalendarOccurrence,
    CalendarReadRequest,
    CalendarReadResult,
    CalendarSourceRef,
)
from satori.desktop.calendar_chat_controller import CalendarChatController
from satori.desktop.calendar_chat_ui import CalendarChatPanel
from satori.desktop.calendar_controller import CalendarController
from satori.infrastructure.assistant.calendar_actions_eventkit import EventKitCalendarActionProvider
from satori.infrastructure.assistant.calendar_actions_store import SQLiteCalendarActionStore
from satori.infrastructure.assistant.calendar_lease import CalendarHostLeaseReader

NOW = datetime(2026, 11, 3, 9, tzinfo=UTC)
TARGET = CalendarSourceRef(account_id="public-account", calendar_id="public-work")
EXACT = "Поставь «<b>PUBLIC appointment</b>» завтра в 15:00 на час"
PLAN = "Подбери «PUBLIC appointment» завтра с 14:00 до 20:00 на час"


class Clock:
    def __init__(self) -> None:
        self.now, self.tick = NOW, 1000.0

    def __call__(self) -> datetime:
        return self.now

    def monotonic(self) -> float:
        return self.tick


class SyntheticCalendar(CalendarController):
    """Sole actual coordinator, with explicit test-only native transport completion."""

    def __init__(self, path: Path, clock: Clock) -> None:
        super().__init__(path, clock=clock, helper_command=lambda: None)
        self.read_requests: list[CalendarReadRequest] = []
        self.fixture_clock = clock

    def ready(self) -> None:
        self._coordinator.accept_catalog(
            CalendarCatalog(
                request_id=uuid4(),
                observed_at=self.fixture_clock(),
                calendars=(
                    CalendarDescriptor(
                        source=TARGET,
                        title="Работа",
                        account_title="PUBLIC iCloud",
                        writable=True,
                        allows_modification=True,
                        supports_events=True,
                        source_kind="caldav",
                    ),
                ),
            )
        )
        self.permission_status = "full_access"
        assert self.use_calendars((TARGET,))

    def request_planning_read(self, start: datetime, end: datetime, timezone: str) -> UUID:
        request = self._coordinator.prepare_read(start, end, timezone)
        self.read_requests.append(request)
        return request.request_id

    def complete_read(self, *, partial: bool = False) -> None:
        request = self.read_requests[-1]
        snapshot = self._coordinator.accept_result(
            request,
            CalendarReadResult(
                request_id=request.request_id,
                observed_at=self.fixture_clock(),
                status="partial" if partial else "ok",
                reason="limit_reached" if partial else None,
                occurrences=(
                    CalendarOccurrence(
                        source=TARGET,
                        event_id="public-event",
                        occurrence_id="public-occurrence",
                        title="PUBLIC SOURCE CONTENT",
                        starts_at=request.starts_at,
                        ends_at=request.starts_at + timedelta(minutes=15),
                        timezone=request.timezone,
                    ),
                ),
            ),
        )
        self.planning_read_ready.emit(str(request.request_id), snapshot)


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


@pytest.fixture
def case(
    app: QApplication, tmp_path: Path
) -> Iterator[
    tuple[CalendarChatController, SyntheticCalendar, Clock, list[dict[str, object]], Path]
]:
    del app
    root = tmp_path.resolve()
    root.chmod(0o700)
    clock = Clock()
    calendar = SyntheticCalendar(root, clock)
    chat = CalendarChatController(calendar, root, clock=clock, monotonic=clock.monotonic)
    chat.bind_context(str(uuid4()), str(uuid4()), "ollama:qwen3:4b-instruct")
    sent: list[dict[str, object]] = []
    chat.command_requested.connect(sent.append)
    yield chat, calendar, clock, sent, root
    chat.close()
    calendar.close()
    chat.deleteLater()
    calendar.deleteLater()
    QApplication.processEvents()


def preview(
    chat: CalendarChatController,
    calendar: SyntheticCalendar,
    sent: list[dict[str, object]],
    text: str = EXACT,
) -> tuple[CalendarHostLease, CalendarPlanningRequest]:
    calendar.ready()
    assert chat.submit(text)
    assert sent == []
    chat.request_preview(TARGET, "Europe/Moscow")
    assert len(sent) == 1
    lease = CalendarHostLease.model_validate_json(json.dumps(sent[-1]["calendar_lease"]))
    command = sent[-1]["calendar"]
    assert isinstance(command, dict)
    request = CalendarPlanningRequest.model_validate_json(json.dumps(command["request"]))
    return lease, request


def test_submit_and_missing_grant_never_read_or_send(
    case: tuple[CalendarChatController, SyntheticCalendar, Clock, list[dict[str, object]], Path],
) -> None:
    chat, calendar, _, sent, root = case
    assert chat.submit(EXACT)
    assert chat.card is not None
    assert chat.card.status == "draft"
    assert not (root / "calendar-host-leases").exists()
    chat.request_preview(TARGET, "Europe/Moscow")
    assert sent == []
    assert calendar.read_requests == []
    assert chat.card is not None
    assert chat.card.status == "unavailable"


@pytest.mark.parametrize(("text", "operation"), [(EXACT, "exact_preview"), (PLAN, "preview")])
def test_exact_and_search_routes_use_one_bound_lease(
    case: tuple[CalendarChatController, SyntheticCalendar, Clock, list[dict[str, object]], Path],
    text: str,
    operation: str,
) -> None:
    chat, calendar, clock, sent, root = case
    lease, request = preview(chat, calendar, sent, text)
    assert lease.request_id == request.request_id
    assert lease.scope == request.scope
    assert lease.read_grant == calendar.grant
    assert calendar.read_requests == []
    command = sent[0]["calendar"]
    assert isinstance(command, dict)
    assert command["operation"] == operation
    CalendarHostLeaseReader(
        root, lease, clock=clock, monotonic=clock.monotonic, parent_pid=os.getpid
    ).require()


@pytest.mark.parametrize("partial", [False, True])
def test_source_rpc_admits_only_the_actual_coordinator_result(
    case: tuple[CalendarChatController, SyntheticCalendar, Clock, list[dict[str, object]], Path],
    partial: bool,
) -> None:
    chat, calendar, _, sent, root = case
    lease, request = preview(chat, calendar, sent)
    replies: list[dict[str, object]] = []
    chat.host_read_result.connect(replies.append)
    call_id = str(uuid4())
    chat.handle_host_read({"call_id": call_id, "request": request.model_dump(mode="json")})
    assert len(calendar.read_requests) == 1
    assert replies == []
    calendar.complete_read(partial=partial)
    assert len(replies) == 1
    assert replies[0]["call_id"] == call_id
    assert replies[0]["error"] == ("calendar_host_read_unavailable" if partial else None)
    assert (replies[0]["snapshot"] is None) == partial
    assert (
        b"PUBLIC SOURCE CONTENT"
        not in (root / "calendar-host-leases" / (lease.lease_id.hex + ".json")).read_bytes()
    )


def test_wrong_host_request_does_not_read(
    case: tuple[CalendarChatController, SyntheticCalendar, Clock, list[dict[str, object]], Path],
) -> None:
    chat, calendar, _, sent, _ = case
    _, request = preview(chat, calendar, sent)
    replies: list[dict[str, object]] = []
    chat.host_read_result.connect(replies.append)
    wrong = request.model_copy(update={"request_id": uuid4()})
    chat.handle_host_read({"call_id": str(uuid4()), "request": wrong.model_dump(mode="json")})
    assert calendar.read_requests == []
    assert len(replies) == 1
    assert replies[0]["snapshot"] is None


@pytest.mark.parametrize("change", ["clear", "selection", "session", "expiry"])
def test_clear_and_context_changes_revoke_file_and_ignore_late_result(
    case: tuple[CalendarChatController, SyntheticCalendar, Clock, list[dict[str, object]], Path],
    change: str,
) -> None:
    chat, calendar, clock, sent, root = case
    lease, request = preview(chat, calendar, sent)
    reader = CalendarHostLeaseReader(
        root, lease, clock=clock, monotonic=clock.monotonic, parent_pid=os.getpid
    )
    reader.require()
    if change == "clear":
        chat.clear()
    elif change == "selection":
        calendar.selection_changed()
    elif change == "session":
        chat.bind_context(str(lease.scope.identity_id), str(uuid4()), "ollama:qwen3:4b-instruct")
    else:
        clock.now = lease.expires_at
        clock.tick = lease.monotonic_deadline
        chat.tick()
    assert chat.card is None
    assert not (root / "calendar-host-leases" / (lease.lease_id.hex + ".json")).exists()
    chat.accept_result(str(request.request_id), "exact_preview", {"status": "proposed"})
    assert chat.card is None
    assert len(sent) == 1


def test_exact_review_keeps_plain_text_and_only_then_sends_approval_and_create(
    case: tuple[CalendarChatController, SyntheticCalendar, Clock, list[dict[str, object]], Path],
) -> None:
    chat, calendar, clock, sent, root = case
    lease, request = preview(chat, calendar, sent)
    owner = CalendarActionOwner(
        SQLiteCalendarActionStore(root / "public-actions" / "actions.sqlite3"),
        EventKitCalendarActionProvider(),
        clock=clock,
    )
    catalog = CalendarActionCatalog(
        scope=lease.scope,
        revision=lease.catalog_revision,
        observed_at=clock(),
        full_access=True,
        writable=True,
        supports_events=True,
        calendar_title="Работа",
        account_title="PUBLIC iCloud",
    )
    record = owner.propose(
        CalendarCreateInput(
            request_id=request.request_id,
            context_digest="a" * 64,
            title=request.title,
            starts_at=request.window_start,
            ends_at=request.window_end,
            timezone=request.scope.timezone,
        ),
        scope=lease.scope,
        catalog=catalog,
    )
    panel = CalendarChatPanel(chat)
    try:
        chat.accept_result(
            str(request.request_id),
            "exact_preview",
            {
                "status": "proposed",
                "request_id": str(request.request_id),
                "proposal": record.proposal.model_dump(mode="json"),
                "mode": "user_exact_interval",
                "model_called": False,
                "event_created": False,
            },
        )
        assert chat.card is not None
        assert chat.card.status == "proposed"
        assert "<b>PUBLIC appointment</b>" in panel.review.toPlainText()
        assert not panel.create_button.isEnabled()
        panel.confirm.setChecked(True)
        assert panel.create_button.isEnabled()
        panel.create_button.click()
        assert len(sent) == 2
        command = sent[-1]["calendar"]
        assert isinstance(command, dict)
        assert command == {"operation": "approve", "review_digest": record.proposal.review_digest}
        approved = owner.approve(
            request.request_id, record.proposal.review_digest, scope=lease.scope, catalog=catalog
        )
        chat.accept_result(str(request.request_id), "approve", approved.model_dump(mode="json"))
        assert len(sent) == 3
        create = sent[-1]["calendar"]
        assert isinstance(create, dict)
        assert approved.approval is not None
        assert create == {"operation": "create", "approval_id": str(approved.approval.approval_id)}
        assert "calendar_lease" not in sent[-1]
        chat.accept_result(
            str(request.request_id),
            "create",
            {
                "status": "unknown",
                "reason": "calendar_operation_unavailable",
                "event_created": None,
                "reconcile_required": True,
            },
        )
        assert chat.card is not None
        assert chat.card.status == "unknown"
        assert not panel.create_button.isVisible()
        chat.reconcile()
        reconcile = sent[-1]["calendar"]
        assert isinstance(reconcile, dict)
        assert reconcile["operation"] == "reconcile"
        assert len(sent) == 4
    finally:
        panel.close()
        panel.deleteLater()
        owner.close()


def test_hiding_card_revokes_scope_without_disconnect(
    case: tuple[CalendarChatController, SyntheticCalendar, Clock, list[dict[str, object]], Path],
) -> None:
    chat, calendar, _, sent, root = case
    lease, _ = preview(chat, calendar, sent)
    panel = CalendarChatPanel(chat)
    panel.show()
    QApplication.processEvents()
    panel.hide()
    assert chat.card is None
    assert calendar.grant is not None
    assert not (root / "calendar-host-leases" / (lease.lease_id.hex + ".json")).exists()
    panel.close()
    panel.deleteLater()


def test_planning_read_uses_owned_native_job_and_the_existing_result_admission(
    app: QApplication, tmp_path: Path
) -> None:
    root, clock = tmp_path.resolve(), Clock()
    helper = root / "public-calendar-read.py"
    helper.write_text(
        "import json,sys\n"
        "r=json.loads(sys.stdin.buffer.read())\n"
        "print(json.dumps({'schema_version':1,'request_id':r['request_id'],"
        "'operation':'read_events','status':'ok','access':'full_access','error':None,"
        "'observed_at':'2026-11-03T09:00:00Z','events':[],'partial':False}))\n"
    )
    calendar = CalendarController(
        root, clock=clock, helper_command=lambda: [sys.executable, str(helper)]
    )
    calendar._coordinator.accept_catalog(
        CalendarCatalog(
            request_id=uuid4(),
            observed_at=clock(),
            calendars=(
                CalendarDescriptor(
                    source=TARGET, title="PUBLIC WORK", account_title="PUBLIC ACCOUNT"
                ),
            ),
        )
    )
    calendar.permission_status = "full_access"
    assert calendar.use_calendars((TARGET,))
    ready: list[tuple[str, object]] = []
    failed: list[tuple[str, str]] = []
    calendar.planning_read_ready.connect(
        lambda identifier, snapshot: ready.append((identifier, snapshot))
    )
    calendar.planning_read_failed.connect(
        lambda identifier, code: failed.append((identifier, code))
    )
    try:
        identifier = calendar.request_planning_read(
            clock() + timedelta(days=1), clock() + timedelta(days=1, hours=1), "UTC"
        )
        deadline = time.monotonic() + 5
        while not ready and not failed and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert failed == []
        assert len(ready) == 1
        assert ready[0][0] == str(identifier)
        assert calendar.grant is not None
        # Planning data is not silently published into My Day's ordinary display.
        assert calendar.snapshot is None
        assert calendar.last_successful_read_at == clock()
    finally:
        calendar.close()
        calendar.deleteLater()
        app.processEvents()


NATURAL_REQUEST = "Мы завтра планируем созвон в 8 вечера, занеси его в календарь события"
NATURAL_COMPLETE = "Мы завтра планируем созвон в 8 вечера на час, занеси его в календарь события"
NATURAL_EVENTS = CalendarSourceRef(account_id="public-account", calendar_id="public-events")
NATURAL_OTHER_EVENTS = CalendarSourceRef(
    account_id="public-other-account", calendar_id="public-other-events"
)
NATURAL_DUPLICATE_EVENTS = CalendarSourceRef(
    account_id="public-account", calendar_id="public-duplicate-events"
)


def _natural_catalog(
    calendar: SyntheticCalendar,
    clock: Clock,
    entries: tuple[tuple[CalendarSourceRef, str, bool], ...],
    *,
    selected: tuple[CalendarSourceRef, ...] | None = None,
) -> None:
    calendar._coordinator.accept_catalog(
        CalendarCatalog(
            request_id=uuid4(),
            observed_at=clock(),
            calendars=tuple(
                CalendarDescriptor(
                    source=source,
                    title=title,
                    account_title="PUBLIC iCloud",
                    writable=writable,
                    allows_modification=writable,
                    supports_events=True,
                    source_kind="caldav",
                )
                for source, title, writable in entries
            ),
        )
    )
    calendar.permission_status = "full_access"
    assert calendar.use_calendars(
        selected if selected is not None else tuple(source for source, _, _ in entries)
    )


def test_natural_duration_clarification_has_a_working_inert_form_and_fresh_read(
    case: tuple[CalendarChatController, SyntheticCalendar, Clock, list[dict[str, object]], Path],
) -> None:
    chat, calendar, clock, sent, root = case
    _natural_catalog(
        calendar,
        clock,
        ((TARGET, "Работа", True), (NATURAL_EVENTS, "События", True)),
    )
    panel = CalendarChatPanel(chat)
    try:
        assert chat.submit(NATURAL_REQUEST)
        assert chat.fields.title.casefold() == "созвон"
        assert chat.fields.starts_at == "20:00"
        assert chat.fields.duration_minutes is None
        assert chat.requested_calendar_title is not None
        assert chat.requested_calendar_title.casefold() == "события"
        assert chat.preferred_calendar == NATURAL_EVENTS
        assert panel.calendar.currentData() == NATURAL_EVENTS
        chat.request_preview(NATURAL_EVENTS, "Europe/Moscow")
        assert chat.card is not None
        assert chat.card.status == "clarification"
        assert sent == []
        assert calendar.read_requests == []
        assert not (root / "calendar-host-leases").exists()

        panel.duration_input.setText("60")
        panel.apply_fields_button.click()
        assert chat.fields.duration_minutes == 60
        assert sent == []
        assert calendar.read_requests == []
        assert not (root / "calendar-host-leases").exists()
        panel.preview_button.click()
        assert len(sent) == 1
        command = sent[0]["calendar"]
        assert isinstance(command, dict)
        assert command["operation"] == "exact_preview"
        request = CalendarPlanningRequest.model_validate_json(json.dumps(command["request"]))
        assert request.user_text == NATURAL_REQUEST
        assert request.title.casefold() == "созвон"
        assert request.scope.calendar_id == NATURAL_EVENTS.calendar_id
        assert request.window_start == datetime(2026, 11, 4, 17, tzinfo=UTC)
        assert request.window_end == datetime(2026, 11, 4, 18, tzinfo=UTC)
        assert request.duration_minutes == 60
        assert calendar.read_requests == []
        replies: list[dict[str, object]] = []
        chat.host_read_result.connect(replies.append)
        call_id = str(uuid4())
        chat.handle_host_read({"call_id": call_id, "request": request.model_dump(mode="json")})
        assert len(calendar.read_requests) == 1
        assert replies == []
        calendar.complete_read()
        assert len(replies) == 1
        assert replies[0]["error"] is None
        assert replies[0]["call_id"] == call_id
        assert len(sent) == 1
    finally:
        panel.close()
        panel.deleteLater()


def test_named_calendar_never_inherits_work_selection_from_previous_card(
    case: tuple[CalendarChatController, SyntheticCalendar, Clock, list[dict[str, object]], Path],
) -> None:
    chat, calendar, clock, sent, _ = case
    _natural_catalog(
        calendar,
        clock,
        ((TARGET, "Работа", True), (NATURAL_EVENTS, "События", True)),
    )
    panel = CalendarChatPanel(chat)
    try:
        assert chat.submit(EXACT)
        panel.calendar.setCurrentIndex(panel.calendar.findData(TARGET))
        assert panel.calendar.currentData() == TARGET
        assert chat.submit(NATURAL_COMPLETE)
        assert chat.preferred_calendar == NATURAL_EVENTS
        assert panel.calendar.currentData() == NATURAL_EVENTS
        assert sent == []
    finally:
        panel.close()
        panel.deleteLater()


@pytest.mark.parametrize("unresolved", ["missing", "duplicate", "unselected", "readonly"])
def test_unresolved_named_calendar_never_falls_back_to_work_or_mints_a_lease(
    case: tuple[CalendarChatController, SyntheticCalendar, Clock, list[dict[str, object]], Path],
    unresolved: str,
) -> None:
    chat, calendar, clock, sent, root = case
    entries: tuple[tuple[CalendarSourceRef, str, bool], ...] = ((TARGET, "Работа", True),)
    selected: tuple[CalendarSourceRef, ...] | None = None
    if unresolved in {"unselected", "readonly", "duplicate"}:
        entries += ((NATURAL_EVENTS, "События", unresolved != "readonly"),)
    if unresolved == "duplicate":
        entries += ((NATURAL_OTHER_EVENTS, "события", True),)
    if unresolved == "unselected":
        selected = (TARGET,)
    _natural_catalog(calendar, clock, entries, selected=selected)
    panel = CalendarChatPanel(chat)
    try:
        assert chat.submit(NATURAL_COMPLETE)
        assert chat.preferred_calendar is None
        assert panel.calendar.currentIndex() == -1
        assert not panel.preview_button.isEnabled()
        chat.request_preview(TARGET, "Europe/Moscow")
        assert sent == []
        assert calendar.read_requests == []
        assert not (root / "calendar-host-leases").exists()
        assert chat.card is not None
        assert chat.card.status != "proposed"
    finally:
        panel.close()
        panel.deleteLater()


def test_duplicate_name_requires_a_deliberate_granted_target_selection(
    case: tuple[CalendarChatController, SyntheticCalendar, Clock, list[dict[str, object]], Path],
) -> None:
    chat, calendar, clock, sent, root = case
    _natural_catalog(
        calendar,
        clock,
        (
            (TARGET, "Работа", True),
            (NATURAL_EVENTS, "События", True),
            (NATURAL_DUPLICATE_EVENTS, "События", True),
        ),
    )
    assert chat.submit(NATURAL_COMPLETE)
    chat.request_preview(NATURAL_EVENTS, "Europe/Moscow")
    assert sent == []
    assert not (root / "calendar-host-leases").exists()
    chat.choose_calendar(NATURAL_EVENTS)
    chat.request_preview(NATURAL_EVENTS, "Europe/Moscow")
    assert len(sent) == 1
    command = sent[0]["calendar"]
    assert isinstance(command, dict)
    assert command["operation"] == "exact_preview"
    request = CalendarPlanningRequest.model_validate_json(json.dumps(command["request"]))
    assert request.scope.calendar_id == NATURAL_EVENTS.calendar_id
    assert request.scope.account_id == NATURAL_EVENTS.account_id
    assert calendar.read_requests == []


@pytest.mark.parametrize("invalid_duration", ["abc", "0", "4", "721"])
def test_invalid_duration_form_cannot_reuse_an_earlier_valid_duration(
    case: tuple[CalendarChatController, SyntheticCalendar, Clock, list[dict[str, object]], Path],
    invalid_duration: str,
) -> None:
    chat, calendar, clock, sent, root = case
    _natural_catalog(calendar, clock, ((NATURAL_EVENTS, "События", True),))
    panel = CalendarChatPanel(chat)
    try:
        assert chat.submit(NATURAL_REQUEST)
        panel.duration_input.setText("60")
        panel.apply_fields_button.click()
        assert chat.fields.duration_minutes == 60
        panel.duration_input.setText(invalid_duration)
        panel.apply_fields_button.click()
        assert chat.card is not None
        assert chat.card.status == "clarification"
        assert chat.fields.duration_minutes is None
        assert not panel.preview_button.isEnabled()
        chat.request_preview(NATURAL_EVENTS, "Europe/Moscow")
        assert sent == []
        assert calendar.read_requests == []
        assert not (root / "calendar-host-leases").exists()
    finally:
        panel.close()
        panel.deleteLater()


def test_later_natural_task_retires_the_previous_lease_and_rejects_its_late_review(
    case: tuple[CalendarChatController, SyntheticCalendar, Clock, list[dict[str, object]], Path],
) -> None:
    chat, calendar, clock, sent, root = case
    lease, request = preview(chat, calendar, sent)
    lease_file = root / "calendar-host-leases" / f"{lease.lease_id.hex}.json"
    assert lease_file.exists()
    owner = CalendarActionOwner(
        SQLiteCalendarActionStore(root / "public-natural-lifecycle" / "actions.sqlite3"),
        EventKitCalendarActionProvider(),
        clock=clock,
    )
    try:
        record = owner.propose(
            CalendarCreateInput(
                request_id=request.request_id,
                context_digest="b" * 64,
                title=request.title,
                starts_at=request.window_start,
                ends_at=request.window_end,
                timezone=request.scope.timezone,
            ),
            scope=lease.scope,
            catalog=CalendarActionCatalog(
                scope=lease.scope,
                revision=lease.catalog_revision,
                observed_at=clock(),
                full_access=True,
                writable=True,
                supports_events=True,
                calendar_title="Работа",
                account_title="PUBLIC iCloud",
            ),
        )
        result = {
            "status": "proposed",
            "request_id": str(request.request_id),
            "proposal": record.proposal.model_dump(mode="json"),
            "mode": "user_exact_interval",
            "model_called": False,
            "event_created": False,
        }
        chat.accept_result(str(request.request_id), "exact_preview", result)
        assert chat.card is not None
        assert chat.card.status == "proposed"
        before = chat.fields
        chat.set_fields(before.model_copy(update={"duration_minutes": 30}))
        assert chat.fields == before
        assert not chat.choose_calendar(NATURAL_EVENTS)
        assert len(sent) == 1

        assert chat.submit(NATURAL_REQUEST)
        assert not lease_file.exists()
        assert chat.active_request_id is None
        assert chat.fields.duration_minutes is None
        chat.accept_result(str(request.request_id), "exact_preview", result)
        chat.create_reviewed()
        assert len(sent) == 1
        assert chat.card is not None
        assert chat.card.draft == NATURAL_REQUEST
        assert chat.card.proposal is None
        assert calendar.read_requests == []
    finally:
        owner.close()


def test_oversized_calendar_hint_clarifies_without_exception_or_work_fallback(
    case: tuple[CalendarChatController, SyntheticCalendar, Clock, list[dict[str, object]], Path],
) -> None:
    chat, calendar, clock, sent, root = case
    _natural_catalog(calendar, clock, ((TARGET, "Работа", True),))
    text = f"Запиши созвон завтра в 8 вечера на час в календарь {'С' * 501}"
    assert len(text) < 8000
    assert chat.submit(text)
    assert chat.card is not None
    assert chat.card.status == "clarification"
    assert chat.card.draft == text
    assert chat.preferred_calendar is None
    chat.request_preview(TARGET, "Europe/Moscow")
    assert sent == []
    assert calendar.read_requests == []
    assert not (root / "calendar-host-leases").exists()


@pytest.mark.parametrize("unsupported", ["каждую неделю", "по времени Амстердама"])
@pytest.mark.parametrize("correction", ["unchanged", "duration_only"])
def test_partial_form_apply_cannot_silently_discard_unrepresented_request_semantics(
    case: tuple[CalendarChatController, SyntheticCalendar, Clock, list[dict[str, object]], Path],
    unsupported: str,
    correction: str,
) -> None:
    chat, calendar, clock, sent, root = case
    _natural_catalog(calendar, clock, ((NATURAL_EVENTS, "События", True),))
    panel = CalendarChatPanel(chat)
    try:
        text = f"Запиши «Созвон» завтра в 8 вечера на час {unsupported} в календарь События"
        assert chat.submit(text)
        if correction == "duration_only":
            panel.duration_input.setText("30")
        panel.apply_fields_button.click()
        chat.request_preview(NATURAL_EVENTS, "Europe/Moscow")
        assert chat.card is not None
        assert chat.card.status == "clarification"
        assert sent == []
        assert calendar.read_requests == []
        assert not (root / "calendar-host-leases").exists()

        # A new complete one-event instruction is a distinct deliberate request.
        assert chat.submit(NATURAL_COMPLETE)
        chat.request_preview(NATURAL_EVENTS, "Europe/Moscow")
        assert len(sent) == 1
        command = sent[0]["calendar"]
        assert isinstance(command, dict)
        assert command["operation"] == "exact_preview"
        assert calendar.read_requests == []
    finally:
        panel.close()
        panel.deleteLater()


def test_explicit_single_event_replacement_is_inert_and_does_not_carry_to_a_new_task(
    case: tuple[CalendarChatController, SyntheticCalendar, Clock, list[dict[str, object]], Path],
) -> None:
    chat, calendar, clock, sent, root = case
    _natural_catalog(calendar, clock, ((NATURAL_EVENTS, "События", True),))
    panel = CalendarChatPanel(chat)
    try:
        text = "Запиши «Созвон» завтра в 8 вечера на час каждую неделю в календарь События"
        assert chat.submit(text)
        panel.start_input.setText("20:00")
        panel.duration_input.setText("60")
        panel.replace_unsupported.setChecked(True)
        panel.apply_fields_button.click()
        assert sent == []
        assert calendar.read_requests == []
        assert not (root / "calendar-host-leases").exists()
        chat.request_preview(NATURAL_EVENTS, "Europe/Moscow")
        assert len(sent) == 1
        command = sent[0]["calendar"]
        assert isinstance(command, dict)
        assert command["operation"] == "exact_preview"
        request = CalendarPlanningRequest.model_validate_json(json.dumps(command["request"]))
        assert request.user_text == text
        assert request.window_start == datetime(2026, 11, 4, 17, tzinfo=UTC)
        assert request.window_end == datetime(2026, 11, 4, 18, tzinfo=UTC)
        assert calendar.read_requests == []

        assert chat.submit(
            "Запиши «Созвон» завтра в 8 вечера на час по времени Амстердама в календарь События"
        )
        assert not panel.replace_unsupported.isChecked()
        panel.apply_fields_button.click()
        chat.request_preview(NATURAL_EVENTS, "Europe/Moscow")
        assert len(sent) == 1
        assert chat.card is not None
        assert chat.card.status == "clarification"
        assert not any((root / "calendar-host-leases").glob("*.json"))
    finally:
        panel.close()
        panel.deleteLater()
