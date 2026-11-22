"""Production chat routing with public synthetic source responses."""

# ruff: noqa: RUF001 - intentional Russian test phrases.

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from satori.application.assistant.source_answer_contracts import SourceSearchPlan
from satori.application.assistant.source_task_reads import source_task_window
from satori.desktop.telegram_port import WireState, schema_from
from satori.desktop.telegram_runtime import qualification_for
from tests.test_calendar_controller import NOW, connect, wait_until
from tests.test_calendar_controller import helper as helper
from tests.test_source_answer_desktop import NOW as MAIL_NOW
from tests.test_source_answer_desktop import Port, batch
from tests.test_telegram_chat_handoff import NoChildProcess
from tests.test_telegram_chat_handoff import app as app
from tests.test_telegram_chat_handoff import window as window


@pytest.mark.parametrize(
    ("mode", "status", "has_event"),
    [("normal", "ok", True), ("empty", "empty", False), ("partial", "partial", True)],
)
def test_chat_sunday_question_reads_selected_calendar(
    app: QApplication,
    window: Any,
    helper: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    status: str,
    has_event: bool,
) -> None:
    desktop, requests = window
    calendar = desktop.calendar_panel.controller
    monkeypatch.setattr(desktop.assistant, "_clock", lambda: NOW)
    monkeypatch.setattr(calendar, "_clock", lambda: NOW)
    monkeypatch.setattr(calendar, "_coordinator", calendar._new_coordinator(helper.parent))
    monkeypatch.setattr(calendar, "_helper_command", lambda: (sys.executable, str(helper), mode))
    connect(app, calendar)
    assert calendar.use_calendars((calendar.catalog[0].source,))
    calendar.bind_dialogue(uuid4())

    desktop.editor.setPlainText("В воскресенье у меня есть что-то или нет?")
    desktop.send()
    assert desktop.tabs.currentWidget() is desktop.conversation_page
    assert requests == []
    assert desktop.editor.toPlainText() == ""
    wait_until(app, lambda: not calendar.busy)

    answer = calendar.dialogue_answer
    assert answer is not None
    assert answer.proposal.window.starts_at == datetime(2026, 10, 24, 21, tzinfo=UTC)
    assert answer.proposal.window.ends_at == datetime(2026, 10, 25, 21, tzinfo=UTC)
    assert answer.receipt.status == status
    assert desktop.assistant.card is not None
    assert ("PUBLIC EVENT <b>literal</b>" in desktop.assistant.card.body) is has_event
    assert desktop.transcript.toPlainText() == ""

    app.sendEvent(desktop, QEvent(QEvent.Type.WindowDeactivate))
    assert desktop.assistant.card is None
    assert calendar.dialogue_answer is None


def test_chat_calendar_reports_missing_selection_without_model(window: Any) -> None:
    desktop, requests = window
    desktop.editor.setPlainText("У меня есть планы в воскресенье?")
    desktop.send()

    assert requests == []
    assert desktop.editor.toPlainText() == ""
    assert desktop.assistant.card is not None
    assert desktop.assistant.card.status == "connection_required"
    assert desktop.assistant.card.action == "calendar"


@pytest.mark.parametrize("connected", [True, False])
def test_chat_inbox_question_uses_gmail_without_model(
    window: Any, monkeypatch: pytest.MonkeyPatch, connected: bool
) -> None:
    desktop, requests = window
    desktop._calendar_identity = str(uuid4())
    desktop._calendar_provider = "PUBLIC-provider:model"
    controller = desktop.source_answer
    monkeypatch.setattr(controller, "_clock", lambda: MAIL_NOW)
    mail, telegram = Port("mail"), Port("telegram")
    if not connected:
        mail.task_account_binding = None
    controller._ports = {"mail": mail, "telegram": telegram}

    desktop.editor.setPlainText("Есть у меня новые письма?")
    desktop.send()
    assert desktop.tabs.currentWidget() is desktop.conversation_page
    assert desktop.editor.toPlainText() == ""
    assert requests == []
    assert telegram.calls == []
    assert controller._lease is None
    if connected:
        assert len(mail.calls) == 1
        plan = controller._plan
        assert plan is not None
        assert plan.mail_headers_only
        assert plan.mail_query == "in:inbox"
        receipt = batch(mail.calls[0], plan, "mail")
        item = receipt.items[0].model_copy(update={"text": "", "completeness": "metadata_only"})
        assert mail.finished is not None
        mail.finished(receipt.model_copy(update={"items": (item,)}))
        assert controller.card is not None
        assert "PUBLIC Собеседование" in controller.card.text
    else:
        assert mail.calls == []
        assert controller.card is not None
        assert "Gmail сейчас не подключён" in controller.card.text
    assert desktop.transcript.toPlainText() == ""


def test_chat_uses_confirmed_telegram_after_leaving_settings(
    window: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    desktop, requests = window
    desktop._calendar_identity = str(uuid4())
    desktop._calendar_provider = "PUBLIC-provider:model"
    desktop.supports_source_answers = True
    desktop.process = NoChildProcess(desktop)
    monkeypatch.setattr(desktop.process, "write", lambda raw: len(raw))
    telegram = desktop.telegram_panel.controller
    resources = Path(__file__).parents[1] / "src/satori/resources"
    telegram.resources = resources
    telegram.qualification = qualification_for(telegram.principal)
    state = WireState(schema_from((resources / "telegram/backend/schema.json").read_bytes()))
    state.confirmed, state.session_started, state.session_deadline = True, 100, 1000
    telegram.state = state
    telegram.process = NoChildProcess(telegram)
    monkeypatch.setattr(telegram, "_flush_writes", lambda *_: None)

    desktop._open_settings_section("telegram")
    assert desktop.tabs.currentWidget() is desktop.telegram_panel
    telegram.set_foreground(True)
    binding = telegram.task_account_binding
    assert binding is not None
    desktop._open_conversation()
    assert not telegram.visible
    assert telegram.task_account_binding == binding
    assert telegram._pending_writes == []

    desktop.editor.setPlainText("В тг посмотри, писал ли мне Алексей сегодня?")
    desktop.send()
    task = desktop.source_answer._request
    assert task is not None
    assert task.sources == ("telegram",)
    assert [name for name, _ in requests] == ["source_answer"]
    start, end = source_task_window(task)
    plan = SourceSearchPlan(
        status="search",
        telegram_query="Алексей",
        telegram_match="peer",
        window_start=start,
        window_end=end,
    )
    desktop.source_answer.accept_result(
        str(task.request_id),
        "plan",
        {
            "request_id": str(task.request_id),
            "status": "planned",
            "plan": plan.model_dump(mode="json"),
        },
    )
    assert len(telegram._pending_writes) == 1
    packet = json.loads(telegram._pending_writes[0])
    assert packet["command"] == "task_search"
    assert packet["plan"]["telegram_query"] == "Алексей"
    assert state.confirmed
