"""The production composer uses the conversational Calendar surface, without Core/accounts."""

# ruff: noqa: RUF001 - intentional public Russian task fixtures.

import json
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

import pytest
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit

from satori.application.assistant.calendar_planning_contracts import CalendarPlanningRequest
from satori.application.assistant.contracts import (
    CalendarCatalog,
    CalendarDescriptor,
    CalendarSourceRef,
)
from satori.desktop.calendar_chat_controller import CalendarChatCard
from satori.desktop.calendar_chat_ui import CalendarConversationPanel
from satori.desktop.ui import DesktopWindow
from tests import test_calendar_chat_window as window_fixtures

application = window_fixtures.application
window = window_fixtures.window

COMPLETE = "Запиши созвон завтра в 8 вечера на час в календарь События"
MISSING = "Мы завтра планируем созвон в 8 вечера, занеси его в календарь события"
REPORTED = "привет, у меня завтра созвон в 8 вечера, внеси его в календарь события"
EVENTS = CalendarSourceRef(account_id="public-account", calendar_id="public-events")


def settle() -> None:
    for _ in range(5):
        QApplication.processEvents()


def public_catalog(
    window: DesktopWindow, monkeypatch: pytest.MonkeyPatch
) -> list[dict[str, object]]:
    calendar = window.calendar_panel.controller

    def refresh() -> None:
        calendar.permission_status = "full_access"
        calendar._coordinator.accept_catalog(
            CalendarCatalog(
                request_id=uuid4(),
                observed_at=datetime.now(UTC),
                calendars=(
                    CalendarDescriptor(
                        source=EVENTS,
                        title="События",
                        account_title="PUBLIC iCloud",
                        writable=True,
                        allows_modification=True,
                        supports_events=True,
                        source_kind="caldav",
                    ),
                ),
            )
        )
        calendar._catalog_phase = "active"
        calendar.changed.emit()

    monkeypatch.setattr(calendar, "refresh_catalog", refresh)
    window.calendar_chat.command_requested.disconnect(window._calendar_command_requested)
    commands: list[dict[str, object]] = []
    window.calendar_chat.command_requested.connect(commands.append)
    return commands


def test_default_window_complete_request_automatically_prepares_exact_calendar(
    window: DesktopWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = public_catalog(window, monkeypatch)
    window.editor.setPlainText(COMPLETE)
    window.send()
    settle()
    assert isinstance(window.calendar_chat_panel, CalendarConversationPanel)
    assert window.calendar_chat.conversational
    assert window.calendar_chat_panel.findChildren(QLineEdit) == []
    assert window.calendar_chat_panel.findChildren(QComboBox) == []
    assert window.calendar_chat_panel.create_button.isHidden()
    assert len(commands) == 1
    payload = commands[0]["calendar"]
    assert isinstance(payload, dict)
    assert payload["operation"] == "exact_preview"
    request = CalendarPlanningRequest.model_validate_json(json.dumps(payload["request"]))
    assert request.scope.calendar_id == EVENTS.calendar_id
    assert request.title == "созвон"
    assert request.duration_minutes == 60
    assert window.editor.toPlainText() == ""


@pytest.mark.parametrize("reply", ["на час", "21", "до девяти"])
def test_short_composer_answer_completes_only_the_pending_task(
    window: DesktopWindow,
    monkeypatch: pytest.MonkeyPatch,
    reply: str,
) -> None:
    commands = public_catalog(window, monkeypatch)
    window.editor.setPlainText(MISSING)
    window.send()
    pending = window.calendar_chat.pending_clarification
    assert pending is not None
    assert pending.waiting_for == "end"
    assert commands == []
    window.editor.setPlainText(reply)
    window.send()
    settle()
    assert len(commands) == 1
    assert window.calendar_chat.fields.duration_minutes == 60
    assert window.calendar_chat.conversation_authority is not None
    assert window.editor.toPlainText() == ""
    assert window.transcript.toPlainText() == ""


def test_full_composer_repeat_cannot_reset_an_uncertain_attempt(
    window: DesktopWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = public_catalog(window, monkeypatch)
    window.editor.setPlainText(COMPLETE)
    window.send()
    settle()
    assert len(commands) == 1
    # The actual UNKNOWN effect pipeline is independently tested through the real
    # action owner. This controlled seam tests the production window's reset path.
    window.calendar_chat._effect_dispatched = True
    window.calendar_chat._pending = None
    window.calendar_chat._card = CalendarChatCard("unknown", "PUBLIC unknown receipt", COMPLETE)
    old_request = window.calendar_chat.active_request_id
    window.editor.setPlainText(COMPLETE)
    window.send()
    assert len(commands) == 1
    assert window.calendar_chat.active_request_id == old_request
    assert window.calendar_chat.card is not None
    assert window.calendar_chat.card.status == "unknown"


def test_normal_same_session_send_cannot_erase_uncertain_calendar_fence(
    window: DesktopWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = public_catalog(window, monkeypatch)
    window.editor.setPlainText(COMPLETE)
    window.send()
    settle()
    window.calendar_chat._effect_dispatched = True
    window.calendar_chat._pending = None
    window.calendar_chat._card = CalendarChatCard("unknown", "PUBLIC unknown receipt", COMPLETE)
    old_request = window.calendar_chat.active_request_id
    writes: list[Any] = []
    with monkeypatch.context() as patch:
        patch.setattr(window.process, "state", lambda: QProcess.ProcessState.Running)
        patch.setattr(window.process, "write", lambda value: writes.append(value))
        window.editor.setPlainText("Привет")
        window.send()
    assert len(writes) == 1
    assert window.calendar_chat.active_request_id == old_request
    assert window.calendar_chat.card is not None
    assert window.calendar_chat.card.status == "unknown"
    # Complete the unrelated fake transport without executing a Core command.
    window.busy = False
    window.editor.setReadOnly(False)
    window.editor.setPlainText(COMPLETE)
    window.send()
    assert len(commands) == 1
    assert window.calendar_chat.active_request_id == old_request


def test_selecting_current_session_does_not_retire_uncertain_task(
    window: DesktopWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = public_catalog(window, monkeypatch)
    window.editor.setPlainText(COMPLETE)
    window.send()
    settle()
    window.calendar_chat._effect_dispatched = True
    window.calendar_chat._pending = None
    window.calendar_chat._card = CalendarChatCard("unknown", "PUBLIC unknown receipt", COMPLETE)
    old_request = window.calendar_chat.active_request_id
    assert window.session_id is not None
    window.session_ids = [window.session_id]
    window.select_session(0)
    assert window.calendar_chat.active_request_id == old_request
    assert window.calendar_chat.card is not None
    assert window.calendar_chat.card.status == "unknown"
    assert len(commands) == 1


def date_alias(day: str) -> str:
    value = date.fromisoformat(day)
    month = (
        "января",
        "февраля",
        "марта",
        "апреля",
        "мая",
        "июня",
        "июля",
        "августа",
        "сентября",
        "октября",
        "ноября",
        "декабря",
    )[value.month - 1]
    return f"{value.day} {month} {value.year}"


def complete_fake_send(window: DesktopWindow) -> None:
    window.busy = False
    window.editor.setReadOnly(False)
    window.send_button.setEnabled(True)


def test_reported_greeting_and_one_natural_answer_use_the_production_calendar_route(
    window: DesktopWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = public_catalog(window, monkeypatch)
    canonical: list[object] = []
    monkeypatch.setattr(window, "request", lambda *args: canonical.append(args))
    window.editor.setPlainText(REPORTED)
    window.send()
    pending = window.calendar_chat.pending_clarification
    assert pending is not None
    assert pending.waiting_for == "end"
    assert window.calendar_chat.fields.title == "созвон"
    assert window.calendar_chat.fields.starts_at == "20:00"
    assert canonical == []
    window.editor.setPlainText("на час")
    window.send()
    settle()
    assert len(commands) == 1
    assert canonical == []
    assert window.transcript.toPlainText() == ""


def test_related_date_correction_never_falls_through_to_canonical_send(
    window: DesktopWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = public_catalog(window, monkeypatch)
    canonical: list[object] = []
    monkeypatch.setattr(window, "request", lambda *args: canonical.append(args))
    window.editor.setPlainText(REPORTED)
    window.send()
    before = window.calendar_chat.fields
    pending = window.calendar_chat.pending_clarification
    assert pending is not None
    window.editor.setPlainText(f"я же написал, что завтра, {date_alias(before.day)}")
    window.send()
    settle()
    assert canonical == []
    assert commands == []
    assert window.editor.toPlainText() == ""
    assert window.calendar_chat.fields.day == before.day
    assert window.calendar_chat.fields.title == before.title
    assert window.calendar_chat.fields.starts_at == before.starts_at
    after = window.calendar_chat.pending_clarification
    assert after is not None
    assert after.task_id == pending.task_id
    assert after.expires_at_monotonic == pending.expires_at_monotonic
    assert after.waiting_for == "end"
    assert window.transcript.toPlainText() == ""


def test_topic_shift_keeps_draft_but_requires_a_literal_task_reference_to_resume(
    window: DesktopWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = public_catalog(window, monkeypatch)
    writes: list[Any] = []
    monkeypatch.setattr(window.process, "state", lambda: QProcess.ProcessState.Running)
    monkeypatch.setattr(window.process, "write", lambda value: writes.append(value))
    window.editor.setPlainText(REPORTED)
    window.send()
    pending = window.calendar_chat.pending_clarification
    assert pending is not None
    fields = window.calendar_chat.fields
    window.editor.setPlainText("Расскажи о звёздах")
    window.send()
    assert len(writes) == 1
    assert window.calendar_chat.card is not None
    assert window.calendar_chat.card.status == "paused"
    assert window.calendar_chat.fields == fields
    complete_fake_send(window)
    window.editor.setPlainText("на час")
    window.send()
    assert len(writes) == 2
    assert window.calendar_chat.fields.duration_minutes is None
    assert commands == []
    complete_fake_send(window)
    window.editor.setPlainText("к этому событию: на час")
    window.send()
    settle()
    assert len(writes) == 2
    assert len(commands) == 1
    assert window.calendar_chat.fields.duration_minutes == 60
    assert window.calendar_chat.card is not None
    assert window.calendar_chat.card.draft == REPORTED


def test_related_correction_after_topic_shift_stays_operational_without_resuming(
    window: DesktopWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = public_catalog(window, monkeypatch)
    writes: list[Any] = []
    monkeypatch.setattr(window.process, "state", lambda: QProcess.ProcessState.Running)
    monkeypatch.setattr(window.process, "write", lambda value: writes.append(value))
    window.editor.setPlainText(REPORTED)
    window.send()
    fields = window.calendar_chat.fields
    window.editor.setPlainText("Расскажи о звёздах")
    window.send()
    assert len(writes) == 1
    complete_fake_send(window)
    window.editor.setPlainText(f"я же написал, что завтра, {date_alias(fields.day)}")
    window.send()
    settle()
    assert len(writes) == 1
    assert commands == []
    assert window.calendar_chat.fields == fields
    assert window.calendar_chat.card is not None
    assert window.calendar_chat.card.status == "clarification"
    assert window.editor.toPlainText() == ""
    window.editor.setPlainText("на час")
    window.send()
    assert len(writes) == 2
    assert commands == []
