"""Combined public Window seams; no account, provider, Core or external effects."""

# ruff: noqa: RUF001 - intentional literal Russian command and Unicode search text

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QDesktopServices

from satori.application.assistant.intents import parse_assistant_intent
from satori.desktop.backend import DesktopBackend
from satori.infrastructure.gmail import GmailConnector
from tests.test_mail_ui import NOW
from tests.test_telegram_chat_handoff import PREFIX, TEXT
from tests.test_telegram_chat_handoff import app as app
from tests.test_telegram_chat_handoff import window as window


@pytest.fixture(autouse=True)
def forbid_external_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("parallel_handoff_crossed_account_core_or_browser_boundary")

    for name in ("begin_login", "list_inbox", "read_selected"):
        monkeypatch.setattr(GmailConnector, name, forbidden)
    for name in ("send", "send_for_session", "new_mobile_session"):
        monkeypatch.setattr(DesktopBackend, name, forbidden)
    monkeypatch.setattr(QDesktopServices, "openUrl", forbidden)


def command(source: str, *, calendar_word: bool = False) -> str:
    if source == "mail":
        return "Найди\u00a0в\u00a0выбранной\u00a0почте:" + (
            "созвон  ЁЖ Straße  " if calendar_word else "ЁЖ  Straße  "
        )
    return PREFIX + TEXT


def staged(window: Any, source: str) -> object:
    return (
        window.mail_panel._chat_lookup
        if source == "mail"
        else window.telegram_panel.reply_panel.handoff
    )


@pytest.mark.parametrize("source", ["mail", "telegram"])
def test_both_explicit_routes_parse_once_before_calendar_and_keep_exact_text(
    window: Any, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    w, requests = window
    calls: list[str] = []
    original = w.assistant.parse

    def parse(text: str, *, timezone: str) -> Any:
        calls.append(text)
        return original(text, timezone=timezone)

    def forbidden(*args: object, **kwargs: object) -> bool:
        raise AssertionError("explicit_source_task_entered_calendar_pending_field")

    monkeypatch.setattr(w.assistant, "parse", parse)
    monkeypatch.setattr(w.calendar_chat, "answer", forbidden)
    monkeypatch.setattr(w.calendar_chat, "submit", forbidden)
    text = command(source, calendar_word=True)
    before = w.transcript.toPlainText()
    w.editor.setPlainText(text)
    # QTextEdit serializes NBSP as space; preserve the actual composer value at Send.
    submitted = w.editor.toPlainText()
    w.send()
    assert calls == [submitted]
    assert staged(w, source) is not None
    if source == "mail":
        assert w.tabs.currentWidget() is w.mail_panel
        assert w.mail_panel.search_query.text() == "созвон  ЁЖ Straße  "
        assert w.mail_panel.search_results.toPlainText() == ""
        assert w.mail_panel.controller._job is None
        assert w.mail_panel.controller._connector is None
    else:
        assert w.tabs.currentWidget() is w.telegram_panel
        assert w.telegram_panel.reply_panel.editor.toPlainText() == TEXT
        assert w.telegram_panel.reply_panel.target is None
        assert not w.telegram_panel.reply_panel.preview_button.isEnabled()
    assert requests == []
    assert w.transcript.toPlainText() == before


@pytest.mark.parametrize("source", ["mail", "telegram"])
def test_source_clarification_also_precedes_calendar(
    window: Any, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    w, requests = window
    text = "Найди в выбранной почте:" + "x" * 129 if source == "mail" else PREFIX

    def forbidden(*args: object, **kwargs: object) -> bool:
        raise AssertionError("source_clarification_entered_calendar")

    monkeypatch.setattr(w.calendar_chat, "answer", forbidden)
    monkeypatch.setattr(w.calendar_chat, "submit", forbidden)
    intent = parse_assistant_intent(text, now=NOW, timezone="Europe/Moscow")
    assert intent is not None
    assert intent.kind == "clarification"
    assert intent.source == source
    w.editor.setPlainText(text)
    w.send()
    assert w.assistant.card is not None
    assert w.assistant.card.status == "clarification"
    assert staged(w, source) is None
    assert requests == []


@pytest.mark.parametrize("source", ["mail", "telegram"])
def test_host_refusal_restores_exact_command_for_both_sources(window: Any, source: str) -> None:
    w, requests = window
    w.session_id = None
    text = command(source)
    w.editor.setPlainText(text)
    submitted = w.editor.toPlainText()
    w.send()
    assert w.editor.toPlainText() == submitted
    assert w.tabs.currentWidget() is w.conversation_page
    assert staged(w, source) is None
    assert w.assistant.card is not None
    assert w.assistant.card.status == "context_changed"
    assert requests == []


@pytest.mark.parametrize("source", ["mail", "telegram"])
@pytest.mark.parametrize("change", ["editor", "conversation", "profile", "surface", "reset"])
def test_combined_lifecycle_keeps_both_cleanup_hooks(window: Any, source: str, change: str) -> None:
    w, requests = window
    w.editor.setPlainText(command(source))
    w.send()
    assert staged(w, source) is not None
    if change == "editor":
        w.editor.setPlainText("PUBLIC NEW DRAFT")
    elif change == "conversation":
        w.session_id = str(uuid4())
    elif change == "profile":
        w.preferences = w.preferences.model_copy(update={"data_directory": Path("/PUBLIC-OTHER")})
    elif change == "surface":
        w._open_settings_section("telegram" if source == "mail" else "mail")
    else:
        w._reset_calendar_dialogue()
    w.mail_panel._apply_chat_lookup()
    w.telegram_panel.reply_panel.refresh()
    assert w.mail_panel._chat_lookup is None
    assert w.telegram_panel.reply_panel.handoff is None
    assert requests == []


def test_source_to_source_switches_retire_the_previous_exact_draft(window: Any) -> None:
    w, requests = window
    for source in ("mail", "telegram", "mail", "telegram"):
        w._open_conversation()
        w.editor.setPlainText(command(source))
        w.send()
        assert staged(w, source) is not None
        assert staged(w, "mail" if source == "telegram" else "telegram") is None
    assert requests == []


def test_window_deactivation_keeps_mail_cleanup_in_the_combined_event_handler(window: Any) -> None:
    w, requests = window
    w.editor.setPlainText(command("mail"))
    w.send()
    assert w.mail_panel._chat_lookup is not None
    w.event(QEvent(QEvent.Type.WindowDeactivate))
    assert w.mail_panel._chat_lookup is None
    assert w.mail_panel.search_query.text() == ""
    assert requests == []


def test_staged_mail_own_query_is_immediately_visible_plain_and_cleared(
    window: Any, app: Any
) -> None:
    w, requests = window
    # A previous long source visit must not leave the next chat handoff below the viewport.
    w._open_settings_section("mail")
    app.processEvents()
    scroll = w.mail_panel.scroll_area
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    w._open_conversation()
    query = "<b>PUBLIC Счёт  Straße</b>"
    w.editor.setPlainText("Найди в выбранной почте:" + query)
    w.send()
    app.processEvents()
    label = w.mail_panel.chat_lookup_summary
    assert w.size().width() == 1000
    assert w.size().height() == 720
    assert label.isVisible()
    assert label.textFormat() == Qt.TextFormat.PlainText
    assert label.text().startswith("Запрос из чата: " + query + "\n")
    assert "Прочитать выбранные" in label.text()
    assert scroll.verticalScrollBar().value() == 0
    viewport = scroll.viewport()
    assert viewport.rect().contains(label.mapTo(viewport, label.rect().topLeft()))
    assert viewport.rect().contains(label.mapTo(viewport, label.rect().bottomRight()))
    assert w.mail_panel.search_query.text() == query
    assert w.mail_panel.search_results.toPlainText() == ""
    assert w.mail_panel.controller._job is None
    assert requests == []
    w.editor.setPlainText("PUBLIC replacement draft")
    assert label.isHidden()
    assert label.text() == ""
