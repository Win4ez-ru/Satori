"""Isolated assistant dispatch/card checks; no account, EventKit, Core or model calls."""

# ruff: noqa: RUF001 - public synthetic Russian calendar requests

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication

from satori.application.assistant.action_contracts import AssistantIntent
from satori.application.assistant.calendar import CalendarAccessCoordinator
from satori.application.assistant.contracts import (
    CalendarCatalog,
    CalendarDescriptor,
    CalendarOccurrence,
    CalendarReadResult,
    CalendarSourceRef,
    LocalPrincipal,
)
from satori.application.assistant.dialogue_contracts import (
    CalendarReadProposal,
    GroundedCalendarAnswer,
)
from satori.application.assistant.dialogue_tools import CalendarDialogueSession
from satori.desktop.assistant_controller import AssistantController, TaskCard
from satori.desktop.backend import DesktopBackend
from satori.desktop.calendar_controller import CalendarController
from satori.desktop.reminder_controller import ReminderController
from satori.desktop.task_cards_ui import TaskCardView
from satori.infrastructure.providers.ollama_http import OllamaHttpClient

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)
DEFAULT_DEVICE_CAPABILITIES = frozenset({"status", "chat", "history", "reminders"})


@pytest.fixture
def app() -> Iterator[QApplication]:
    current = QApplication.instance() or QApplication([])
    assert isinstance(current, QApplication)
    yield current
    current.processEvents()


@pytest.fixture(autouse=True)
def forbid_core_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("assistant_task_crossed_core_or_provider_boundary")

    for name in ("send", "send_for_session", "history", "new_mobile_session"):
        monkeypatch.setattr(DesktopBackend, name, forbidden)
    monkeypatch.setattr(OllamaHttpClient, "post_json", forbidden)


class CalendarDouble(QObject):
    """Signal-compatible source double with observable source-owner access."""

    changed = Signal()
    dialogue_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.accesses: list[str] = []
        self.proposals: list[CalendarReadProposal] = []
        self.available = True
        self.answer: GroundedCalendarAnswer | None = None
        self.state = "idle"
        self.chat_id: UUID | None = uuid4()
        self.source_principal = LocalPrincipal(os_uid=501, profile_id=uuid4(), session_id=uuid4())

    @property
    def status_projection(self) -> SimpleNamespace:
        self.accesses.append("status_projection")
        return SimpleNamespace(can_query_dialogue=self.available, status_text="Connect Calendar")

    @property
    def dialogue_chat_id(self) -> UUID | None:
        self.accesses.append("dialogue_chat_id")
        return self.chat_id

    @property
    def principal(self) -> LocalPrincipal:
        self.accesses.append("principal")
        return self.source_principal

    @property
    def dialogue_answer(self) -> GroundedCalendarAnswer | None:
        self.accesses.append("dialogue_answer")
        return self.answer

    @property
    def dialogue_status(self) -> str:
        self.accesses.append("dialogue_status")
        return self.state

    def submit_dialogue(self, proposal: CalendarReadProposal) -> None:
        self.accesses.append("submit_dialogue")
        self.proposals.append(proposal)
        self.answer = None
        self.state = "reading"

    def clear_dialogue(self, status: str = "cancelled") -> None:
        self.accesses.append("clear_dialogue")
        self.answer = None
        self.state = status
        self.dialogue_changed.emit()


def dispatcher(calendar: CalendarDouble, reminders: ReminderController) -> AssistantController:
    return AssistantController(cast(CalendarController, calendar), reminders, clock=lambda: NOW)


def intent(kind: str, **fields: object) -> AssistantIntent:
    return AssistantIntent.model_validate(
        {"kind": kind, "request_id": uuid4(), "timezone": "Europe/Moscow", **fields}
    )


def answered(proposal: CalendarReadProposal) -> GroundedCalendarAnswer:
    """Use the real calendar card owner with a wholly synthetic source snapshot."""
    source = CalendarSourceRef(account_id="synthetic-account", calendar_id="synthetic-work")
    coordinator = CalendarAccessCoordinator(proposal.principal, clock=lambda: NOW)
    coordinator.accept_catalog(
        CalendarCatalog(
            request_id=uuid4(),
            observed_at=NOW,
            calendars=(CalendarDescriptor(source=source, title="SYNTHETIC", account_title="TEST"),),
        )
    )
    coordinator.issue((source,))
    session = CalendarDialogueSession(
        coordinator, proposal.chat_id, clock=lambda: NOW, audit=lambda event: None
    )
    pending = session.prepare(session.consent(proposal))
    return session.accept(
        pending,
        CalendarReadResult(
            request_id=pending.request.request_id,
            observed_at=NOW,
            occurrences=(
                CalendarOccurrence(
                    source=source,
                    event_id="synthetic-event",
                    occurrence_id="synthetic-occurrence",
                    title="SYNTHETIC PRIVATE SHIFT <b>literal</b>",
                    starts_at=proposal.window.starts_at + timedelta(hours=10),
                    ends_at=proposal.window.starts_at + timedelta(hours=11),
                    timezone=proposal.timezone,
                ),
            ),
        ),
    )


@pytest.mark.parametrize("kind", ["calendar_agenda", "calendar_gaps"])
def test_default_device_calendar_denial_precedes_every_source_property(
    app: QApplication, tmp_path: Path, kind: str
) -> None:
    calendar = CalendarDouble()
    reminders = ReminderController(tmp_path, clock=lambda: NOW)
    controller = dispatcher(calendar, reminders)
    try:
        controller.dispatch(
            intent(kind, day=date(2026, 11, 1)), capabilities=DEFAULT_DEVICE_CAPABILITIES
        )
        assert controller.card is not None
        assert controller.card.status == "source_grant_required"
        assert calendar.accesses == []
        assert not list(tmp_path.iterdir())
    finally:
        reminders.shutdown()


@pytest.mark.parametrize("source", ["mail", "telegram"])
def test_default_device_selected_lookup_does_not_touch_any_source(
    app: QApplication, tmp_path: Path, source: str
) -> None:
    calendar = CalendarDouble()
    reminders = ReminderController(tmp_path, clock=lambda: NOW)
    controller = dispatcher(calendar, reminders)
    try:
        controller.dispatch(
            intent("selected_lookup", source=source, query="SYNTHETIC QUERY"),
            capabilities=DEFAULT_DEVICE_CAPABILITIES,
        )
        assert controller.card is not None
        assert controller.card.status == "source_grant_required"
        assert calendar.accesses == []
        assert not list(tmp_path.iterdir())
    finally:
        reminders.shutdown()


def test_reminder_capability_denial_cannot_create_store(app: QApplication, tmp_path: Path) -> None:
    calendar = CalendarDouble()
    reminders = ReminderController(tmp_path, clock=lambda: NOW)
    controller = dispatcher(calendar, reminders)
    try:
        controller.dispatch(
            intent("reminder_create", title="SYNTHETIC", due_at=NOW + timedelta(hours=1)),
            capabilities=frozenset({"chat"}),
        )
        assert controller.card is not None
        assert controller.card.status == "source_grant_required"
        assert calendar.accesses == []
        assert not reminders.enabled
        assert not list(tmp_path.iterdir())
    finally:
        reminders.shutdown()


def test_reminder_receipts_replay_and_conflict_are_presented_truthfully(
    app: QApplication, tmp_path: Path
) -> None:
    calendar = CalendarDouble()
    reminders = ReminderController(tmp_path, clock=lambda: NOW)
    controller = dispatcher(calendar, reminders)
    request = intent("reminder_create", title="SYNTHETIC TASK", due_at=NOW + timedelta(hours=1))
    try:
        controller.dispatch(request, capabilities=DEFAULT_DEVICE_CAPABILITIES)
        first_receipt = reminders.last_receipt
        assert first_receipt is not None
        assert first_receipt.status == "created"
        identifier = first_receipt.record.reminder_id
        assert controller.card is not None
        assert controller.card.status == "saved"
        assert "not_requested" in controller.card.body
        assert "Europe/Moscow" in controller.card.body
        controller.dispatch(request, capabilities=DEFAULT_DEVICE_CAPABILITIES)
        replay = reminders.last_receipt
        assert replay is not None
        assert replay.status == "existing"
        assert replay.record.reminder_id == identifier
        assert len(reminders.records) == 1
        controller.dispatch(request.model_copy(update={"title": "DIFFERENT TASK"}))
        assert controller.card is not None
        assert controller.card.status == "error"
        assert len(reminders.records) == 1
        cancel = intent("reminder_cancel", reminder_id=identifier)
        controller.dispatch(cancel, capabilities=DEFAULT_DEVICE_CAPABILITIES)
        assert controller.card.status == "cancelled"
        assert reminders.records[0].state == "cancelled"
        controller.dispatch(cancel, capabilities=DEFAULT_DEVICE_CAPABILITIES)
        repeated_cancel = reminders.last_receipt
        assert repeated_cancel is not None
        assert repeated_cancel.status == "unchanged"
        assert len(reminders.records) == 1
        assert calendar.accesses == []
        assert not (tmp_path / "satori.db").exists()
    finally:
        reminders.shutdown()


def test_calendar_requires_existing_selection_and_current_view(
    app: QApplication, tmp_path: Path
) -> None:
    calendar = CalendarDouble()
    reminders = ReminderController(tmp_path, clock=lambda: NOW)
    controller = dispatcher(calendar, reminders)
    request = intent("calendar_agenda", day=date(2026, 11, 1))
    try:
        calendar.available = False
        controller.dispatch(request)
        assert controller.card is not None
        assert controller.card.status == "connection_required"
        assert calendar.proposals == []
        calendar.available = True
        calendar.chat_id = None
        controller.dispatch(request)
        assert controller.card.status == "context_changed"
        assert calendar.proposals == []
        calendar.chat_id = uuid4()
        controller.dispatch(request, capabilities=frozenset({"calendar"}))
        assert len(calendar.proposals) == 1
        proposal = calendar.proposals[0]
        assert proposal.principal == calendar.source_principal
        assert proposal.chat_id == calendar.chat_id
        assert proposal.proposal_id == request.request_id
        assert proposal.window.starts_at == datetime(2026, 10, 31, 21, tzinfo=UTC)
        assert proposal.window.ends_at == datetime(2026, 11, 1, 21, tzinfo=UTC)
        assert not list(tmp_path.iterdir())
    finally:
        reminders.shutdown()


def test_reminder_list_failure_is_not_presented_as_confirmed_empty(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calendar = CalendarDouble()
    reminders = ReminderController(tmp_path, clock=lambda: NOW)
    controller = dispatcher(calendar, reminders)

    def unreadable_store() -> None:
        reminders.status = "error"
        reminders.diagnostic = "reminder_store_unavailable"

    monkeypatch.setattr(reminders, "refresh", unreadable_store)
    try:
        controller.dispatch(intent("reminder_list"), capabilities=DEFAULT_DEVICE_CAPABILITIES)
        assert controller.card is not None
        assert controller.card.status == "error"
        assert "Активных напоминаний нет" not in controller.card.body
        assert calendar.accesses == []
    finally:
        reminders.shutdown()


@pytest.mark.parametrize("cancelled_first", [True, False])
def test_reminder_list_filters_before_cap_and_discloses_partial_coverage(
    app: QApplication, tmp_path: Path, cancelled_first: bool
) -> None:
    calendar = CalendarDouble()
    reminders = ReminderController(tmp_path, clock=lambda: NOW)
    controller = dispatcher(calendar, reminders)
    try:
        for index in range(51):
            receipt = reminders.create(
                f"SYNTHETIC TASK {index}", NOW + timedelta(hours=1), "Europe/Moscow", uuid4()
            )
            assert receipt is not None
            if cancelled_first and index < 50:
                assert reminders.cancel(receipt.record.reminder_id) is not None
        controller.dispatch(intent("reminder_list"), capabilities=DEFAULT_DEVICE_CAPABILITIES)
        assert controller.card is not None
        assert "Активных напоминаний нет" not in controller.card.body
        if cancelled_first:
            assert "SYNTHETIC TASK 50" in controller.card.body
        else:
            assert controller.card.body.count("SYNTHETIC TASK") == 50
            # A partial status or a visible coverage notice is truthful. The
            # outcome must not silently present the first page as the full list.
            if controller.card.status != "partial":
                notice = controller.card.body.split("\n\n")[-1]
                assert "50" in notice
                assert "первые" in notice.casefold()
    finally:
        reminders.shutdown()


@pytest.mark.parametrize(
    "state", ["stale", "selection_changed", "disconnected", "superseded", "reading"]
)
def test_calendar_source_invalidation_removes_previously_copied_body(
    app: QApplication, tmp_path: Path, state: str
) -> None:
    calendar = CalendarDouble()
    reminders = ReminderController(tmp_path, clock=lambda: NOW)
    controller = dispatcher(calendar, reminders)
    try:
        controller.dispatch(intent("calendar_agenda", day=date(2026, 11, 1)))
        calendar.answer = answered(calendar.proposals[0])
        calendar.state = "ok"
        calendar.dialogue_changed.emit()
        assert controller.card is not None
        assert "SYNTHETIC PRIVATE SHIFT" in controller.card.body
        calendar.answer = None
        calendar.state = state
        calendar.dialogue_changed.emit()
        assert "SYNTHETIC PRIVATE SHIFT" not in controller.card.body
    finally:
        reminders.shutdown()


def test_calendar_expiry_on_general_owner_signal_clears_body(
    app: QApplication, tmp_path: Path
) -> None:
    calendar = CalendarDouble()
    reminders = ReminderController(tmp_path, clock=lambda: NOW)
    controller = dispatcher(calendar, reminders)
    try:
        controller.dispatch(intent("calendar_agenda", day=date(2026, 11, 1)))
        calendar.answer = answered(calendar.proposals[0])
        calendar.state = "ok"
        calendar.dialogue_changed.emit()
        assert controller.card is not None
        assert "SYNTHETIC PRIVATE SHIFT" in controller.card.body
        calendar.answer = None
        calendar.state = "stale"
        # The existing owner emits changed, rather than dialogue_changed, when
        # its answer TTL expires while its selected-calendar grant stays valid.
        calendar.changed.emit()
        assert "SYNTHETIC PRIVATE SHIFT" not in controller.card.body
    finally:
        reminders.shutdown()


def test_cleared_or_replaced_task_cannot_be_restored_by_late_calendar_answer(
    app: QApplication, tmp_path: Path
) -> None:
    calendar = CalendarDouble()
    reminders = ReminderController(tmp_path, clock=lambda: NOW)
    controller = dispatcher(calendar, reminders)
    try:
        controller.dispatch(intent("calendar_agenda", day=date(2026, 11, 1)))
        old = answered(calendar.proposals[0])
        controller.clear()
        calendar.answer = old
        calendar.dialogue_changed.emit()
        assert controller.card is None
        controller.dispatch(intent("calendar_agenda", day=date(2026, 11, 2)))
        calendar.answer = old
        calendar.state = "ok"
        calendar.dialogue_changed.emit()
        assert controller.card is not None
        assert "SYNTHETIC PRIVATE SHIFT" not in controller.card.body
        controller.dispatch(intent("clarification", clarification="Choose an exact time"))
        stable = controller.card
        calendar.dialogue_changed.emit()
        assert controller.card == stable
    finally:
        reminders.shutdown()


def test_leaving_calendar_read_clears_its_answer_without_touching_other_cards(
    app: QApplication, tmp_path: Path
) -> None:
    calendar = CalendarDouble()
    reminders = ReminderController(tmp_path, clock=lambda: NOW)
    controller = dispatcher(calendar, reminders)
    try:
        controller.dispatch(intent("calendar_agenda", day=date(2026, 11, 1)))
        result = answered(calendar.proposals[0])
        calendar.answer = result
        calendar.state = "ok"
        calendar.dialogue_changed.emit()
        assert controller.card is not None
        assert controller.card.title == "Календарь на 01.11.2026"
        assert "SYNTHETIC PRIVATE SHIFT" in controller.card.body.split("\n\n")[0]
        assert "Покрытие: только выбранные календари" in controller.card.body
        controller.clear_calendar_read()
        assert controller.card is None
        calendar.answer = result
        calendar.dialogue_changed.emit()
        assert controller.card is None

        controller.dispatch(intent("clarification", clarification="Choose an exact time"))
        existing_card = controller.card
        accesses = calendar.accesses.copy()
        controller.clear_calendar_read()
        assert controller.card is existing_card
        assert calendar.accesses == accesses
    finally:
        reminders.shutdown()


def test_task_card_keeps_html_like_text_literal_and_clear_removes_every_field(
    app: QApplication, tmp_path: Path
) -> None:
    calendar = CalendarDouble()
    reminders = ReminderController(tmp_path, clock=lambda: NOW)
    controller = dispatcher(calendar, reminders)
    view = TaskCardView(controller)
    try:
        controller.card = TaskCard(
            uuid4(),
            "<b>literal title</b>",
            '<img src="https://example.invalid/tracker">\n<a href="file:///private">literal</a>',
            "partial",
            "<i>literal source</i>",
            "calendar",
        )
        controller.changed.emit()
        assert view.title.textFormat() is Qt.TextFormat.PlainText
        assert view.source.textFormat() is Qt.TextFormat.PlainText
        assert view.body.toPlainText() == controller.card.body
        assert not view.body.acceptRichText()
        assert view.body.isReadOnly()
        actions: list[str] = []
        view.action_requested.connect(actions.append)
        view.action.click()
        assert actions == ["calendar"]
        controller.clear()
        assert view.title.text() == ""
        assert view.source.text() == ""
        assert view.body.toPlainText() == ""
        assert view.isHidden()
    finally:
        reminders.shutdown()
        view.deleteLater()


def test_normal_parse_is_not_an_action_and_ambiguous_time_is_no_write(
    app: QApplication, tmp_path: Path
) -> None:
    calendar = CalendarDouble()
    reminders = ReminderController(tmp_path, clock=lambda: NOW)
    controller = dispatcher(calendar, reminders)
    try:
        assert controller.parse("Привет", timezone="Europe/Moscow") is None
        ambiguous = controller.parse(
            "Напомни завтра вечером написать API", timezone="Europe/Moscow"
        )
        assert ambiguous is not None
        assert ambiguous.kind == "clarification"
        controller.dispatch(ambiguous)
        assert controller.card is not None
        assert controller.card.status == "clarification"
        assert not reminders.enabled
        assert calendar.accesses == []
        assert not list(tmp_path.iterdir())
    finally:
        reminders.shutdown()


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Что у меня запланировано на завтра?", "calendar_agenda"),
        ("Что у меня завтра запланировано?", "calendar_agenda"),
        ("Есть ли у меня свободное время завтра?", "calendar_gaps"),
        ("Есть ли у меня завтра свободные окна?", "calendar_gaps"),
    ],
)
@pytest.mark.parametrize("selected_grant_available", [True, False])
def test_natural_calendar_request_reuses_selected_calendar_owner(
    app: QApplication,
    tmp_path: Path,
    text: str,
    kind: str,
    selected_grant_available: bool,
) -> None:
    calendar = CalendarDouble()
    calendar.available = selected_grant_available
    reminders = ReminderController(tmp_path, clock=lambda: NOW)
    controller = dispatcher(calendar, reminders)
    try:
        request = controller.parse(text, timezone="Europe/Moscow")
        assert request is not None
        assert request.kind == kind
        assert request.day == date(2026, 11, 1)
        controller.dispatch(request)
        assert controller.card is not None
        if selected_grant_available:
            assert len(calendar.proposals) == 1
            proposal = calendar.proposals[0]
            assert proposal.proposal_id == request.request_id
            assert proposal.principal == calendar.source_principal
            assert proposal.chat_id == calendar.chat_id
            assert proposal.window.starts_at == datetime(2026, 10, 31, 21, tzinfo=UTC)
            assert proposal.window.ends_at == datetime(2026, 11, 1, 21, tzinfo=UTC)
        else:
            assert controller.card.status == "connection_required"
            assert not calendar.proposals
        assert not reminders.enabled
        assert not list(tmp_path.iterdir())
    finally:
        reminders.shutdown()
