"""Actual Window/composer to current reply owner; public native fixture, no account."""

# ruff: noqa: RUF001 - public synthetic Russian text

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication

from satori.application.assistant.intents import parse_assistant_intent
from satori.desktop.backend import DesktopBackend
from satori.desktop.settings import DesktopPreferences
from satori.desktop.telegram_port import Message, WireState, schema_from
from satori.desktop.telegram_reply_wire import reply_qualification_for
from satori.desktop.ui import DesktopApplication, DesktopWindow
from satori.infrastructure.providers.ollama_http import OllamaHttpClient
from tests.test_telegram_reply_native_v1 import make_engine

PREFIX = "Ответь в Telegram на выбранное сообщение:"
TEXT = "  PUBLIC точный ответ\nВторая строка\t<literal>  "


class NoChildProcess(QProcess):
    def state(self) -> QProcess.ProcessState:
        return QProcess.ProcessState.Running


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or DesktopApplication([])  # type: ignore[return-value]


@pytest.fixture
def window(app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("telegram_handoff_crossed_core_or_provider")

    monkeypatch.setattr(DesktopBackend, "send", forbidden)
    monkeypatch.setattr(OllamaHttpClient, "post_json", forbidden)
    root = tmp_path.resolve()
    root.chmod(0o700)
    result = DesktopWindow(
        DesktopPreferences(
            data_directory=root,
            repository_directory=root,
            voice_model_directory=root / "voice",
            speech_model_directory=root / "speech",
        )
    )
    result.poll.stop()
    result.telegram_panel.timer.stop()
    c = result.telegram_panel.controller
    c._timer.stop()
    c.clock = lambda: 100.0
    assert c.configure_reviewed_replies(reply_qualification_for(c.principal))
    result.activated, result.busy, result.viewing_closed = True, False, False
    result.session_id = str(uuid4())
    requests: list[tuple[str, object]] = []
    monkeypatch.setattr(
        result, "request", lambda command, data=None: requests.append((command, data))
    )
    result.show()
    app.processEvents()
    assert result.isActiveWindow()
    yield result, requests
    c.process = None
    result.quitting = True
    result.tray.hide()
    result.close()
    result.deleteLater()
    app.processEvents()


def submit(window: DesktopWindow, text: str = TEXT) -> None:
    window.editor.setPlainText(PREFIX + text)
    window.send()
    assert window.tabs.currentWidget() is window.telegram_panel
    assert window.telegram_panel.reply_panel.handoff is not None


def current_scope(window: DesktopWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Fresh explicit-selection fixture, using the production native reply engine/owner."""
    c = window.telegram_panel.controller
    engine, native, clock, events = make_engine(tmp_path)
    clock.wall = datetime.now(UTC).timestamp()
    engine.last_wall = clock.wall
    engine.reply_extension.principal = c.principal
    c.generation = engine.reply_extension.generation
    c.state = WireState(
        schema_from(
            (
                Path(__file__).parents[1] / "src/satori/resources/telegram/backend/schema.json"
            ).read_bytes()
        )
    )
    c.state.confirmed, c.state.session_deadline = True, engine.session_until
    c.state.selected, c.state.selection_deadline = engine.selected_token, engine.grant_until
    c.state.page_id, c.state.page_deadline = engine.page_id, engine.page_until
    c.state.selected_title = "PUBLIC CHAT"
    handle = next(iter(engine.messages))
    c.state.messages = (Message(handle, "PUBLIC PERSON", "PUBLIC ORIGINAL", int(clock.wall)),)
    c.visible, c.process = True, NoChildProcess(c)
    commands: list[dict[str, Any]] = []

    def flush(_process: QProcess, _generation: int) -> None:
        batch, c._pending_writes = c._pending_writes, []
        for data in batch:
            query = json.loads(data)
            commands.append(query)
            engine.command(query)
            while events:
                event = events.pop(0)
                assert c.state is not None
                event["event_seq"] = c.state.sequence + 1
                c._receive(event)

    monkeypatch.setattr(c, "_flush_writes", flush)
    c.changed.emit()
    return c, engine, native, commands, handle


def test_window_stages_only_literal_text_then_existing_owner_preview(
    window: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, requests = window
    before = w.transcript.toPlainText()
    submit(w)
    panel = w.telegram_panel.reply_panel
    handoff = panel.handoff
    assert handoff is not None
    assert handoff.text == TEXT == panel.editor.toPlainText()
    assert panel.target is None
    assert not panel.preview_button.isEnabled()
    assert handoff.principal == w.telegram_panel.controller.principal
    assert str(handoff.conversation_id) == w.session_id
    assert handoff.expires_monotonic - handoff.started_monotonic == 120
    assert requests == []
    assert w.transcript.toPlainText() == before
    c, engine, native, commands, handle = current_scope(w, tmp_path, monkeypatch)
    assert not panel.approve_button.isEnabled()
    panel.select_target(handle)
    assert panel.editor.toPlainText() == TEXT
    assert panel.preview_button.isEnabled()
    panel.preview_button.click()
    assert c.reply_view.state == "preview"
    assert c.reply_view.action_id == handoff.request_id
    assert c.reply_view.text == TEXT
    assert engine.reply_extension.draft.text == TEXT
    assert engine.reply_extension.draft.request_id == handoff.request_id
    assert engine.reply_extension.approval is None
    assert native.effects == 0
    assert not any(row["command"] in {"reply_send", "reply_approve"} for row in commands)
    assert not c.send_reply()
    assert requests == []
    assert w.transcript.toPlainText() == before
    engine.reply_extension.close()


@pytest.mark.parametrize("change", ["stale", "page", "epoch", "principal", "generation", "expiry"])
def test_changed_selected_scope_retires_handoff_before_preview(
    window: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    w, requests = window
    submit(w)
    c, engine, native, commands, handle = current_scope(w, tmp_path, monkeypatch)
    panel = w.telegram_panel.reply_panel
    panel.select_target(handle)
    assert panel.handoff is not None
    assert c.state is not None
    if change == "stale":
        c.state.stale = True
    elif change == "page":
        c.state.page_id = uuid4().hex
    elif change == "epoch":
        c.state.epoch += 1
    elif change == "principal":
        c.principal = c.principal.model_copy(update={"session_id": uuid4()})
    elif change == "generation":
        c.generation += 1
    else:
        c.clock = lambda: 221.0
    panel._preview()
    assert panel.handoff is None
    assert not panel.editor.toPlainText()
    assert native.effects == 0
    assert requests == []
    assert not any(row["command"] == "reply_preview" for row in commands)
    engine.reply_extension.close()


@pytest.mark.parametrize("change", ["hide", "conversation", "editor", "profile", "rollback"])
def test_unbound_user_text_is_retired_by_host_context_changes(window: Any, change: str) -> None:
    w, requests = window
    submit(w)
    panel = w.telegram_panel.reply_panel
    if change == "hide":
        w._open_conversation()
    elif change == "conversation":
        w.session_id = str(uuid4())
        panel.refresh()
    elif change == "editor":
        w.editor.setPlainText("NEW PUBLIC DRAFT")
    elif change == "profile":
        w.telegram_panel.controller.principal = w.telegram_panel.controller.principal.model_copy(
            update={"profile_id": uuid4()}
        )
        panel.refresh()
    else:
        w.telegram_panel.controller.clock = lambda: 99.0
        panel.refresh()
    assert panel.handoff is None
    assert not panel.editor.toPlainText()
    assert requests == []


def test_duplicate_request_cannot_replace_text_or_reopen_retired_handoff(window: Any) -> None:
    w, requests = window
    submit(w)
    panel = w.telegram_panel.reply_panel
    handoff = panel.handoff
    assert handoff is not None
    assert not panel.stage_text(
        handoff.request_id, "DIFFERENT", handoff.conversation_id, context_current=lambda: True
    )
    assert panel.editor.toPlainText() == TEXT
    panel.retire_handoff()
    assert not panel.stage_text(
        handoff.request_id, TEXT, handoff.conversation_id, context_current=lambda: True
    )
    assert panel.handoff is None
    assert requests == []


@pytest.mark.parametrize("text", ["", " ", "x" * 4001, "PUBLIC\x00TEXT"])
def test_invalid_explicit_reply_is_local_clarification(window: Any, text: str) -> None:
    w, requests = window
    w.editor.setPlainText(PREFIX + text)
    w.send()
    assert w.assistant.card is not None
    assert w.assistant.card.status == "clarification"
    assert w.telegram_panel.reply_panel.handoff is None
    assert requests == []


def test_literal_parser_preserves_spacing_and_ordinary_conversation() -> None:
    now = datetime.now(UTC)
    intent = parse_assistant_intent(PREFIX + TEXT, now=now, timezone="Europe/Moscow")
    assert intent is not None
    assert intent.reply_text == TEXT
    assert intent.kind == "telegram_reply"
    assert "PUBLIC" not in repr(intent)
    assert (
        parse_assistant_intent("Что ответить другу в Telegram?", now=now, timezone="Europe/Moscow")
        is None
    )


@pytest.mark.parametrize("change", ["expired_approval", "edited_approval"])
def test_existing_approval_is_retired_before_send_on_handoff_change(
    window: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    w, requests = window
    submit(w)
    c, engine, native, commands, handle = current_scope(w, tmp_path, monkeypatch)
    panel = w.telegram_panel.reply_panel
    panel.select_target(handle)
    panel.preview_button.click()
    assert c.reply_view.state == "preview"
    panel.approve_button.click()
    assert c.reply_view.state == "approved"
    assert engine.reply_extension.approval is not None
    if change == "expired_approval":
        c.clock = lambda: 221.0
        panel._send()
    else:
        panel.editor.setPlainText("NEW PUBLIC TEXT")
    assert panel.handoff is None
    assert engine.reply_extension.approval is None
    assert c.reply_view.approval_id is None
    assert native.effects == 0
    assert requests == []
    assert not any(row["command"] == "reply_send" for row in commands)
    engine.reply_extension.close()


def test_intermediate_clock_rollback_cannot_extend_staged_text(window: Any) -> None:
    w, _requests = window
    submit(w)
    panel = w.telegram_panel.reply_panel
    c = w.telegram_panel.controller
    c.clock = lambda: 110.0
    panel.refresh()
    assert panel.handoff is not None
    c.clock = lambda: 105.0
    panel.refresh()
    assert panel.handoff is None


@pytest.mark.parametrize("capabilities", [frozenset({"chat"}), frozenset({"telegram"})])
def test_mobile_dispatch_cannot_invoke_desktop_handoff(
    window: Any, capabilities: frozenset[str]
) -> None:
    w, requests = window
    intent = w.assistant.parse(PREFIX + TEXT, timezone="Europe/Moscow")
    assert intent is not None
    w.assistant.dispatch(intent, capabilities=capabilities)
    assert w.telegram_panel.reply_panel.handoff is None
    assert requests == []


def test_host_refusal_preserves_the_original_user_draft(window: Any) -> None:
    w, requests = window
    w.session_id = None
    w.editor.setPlainText(PREFIX + TEXT)
    w.send()
    assert w.editor.toPlainText() == PREFIX + TEXT
    assert w.tabs.currentWidget() is w.conversation_page
    assert w.telegram_panel.reply_panel.handoff is None
    assert requests == []


def test_explicit_reply_cannot_be_consumed_as_a_calendar_followup(
    window: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, requests = window

    def forbidden(_text: str) -> bool:
        raise AssertionError("telegram_text_entered_calendar_followup")

    monkeypatch.setattr(w.calendar_chat, "answer", forbidden)
    submit(w)
    assert requests == []


def test_chat_handoff_does_not_reuse_a_concealed_target_or_grant(
    window: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, requests = window
    submit(w)
    c, engine, native, _commands, handle = current_scope(w, tmp_path, monkeypatch)
    panel = w.telegram_panel.reply_panel
    panel.select_target(handle)
    assert panel.target is not None
    concealed: list[str] = []

    def send_metadata(operation: str, **fields: object) -> bool:
        concealed.append(operation)
        return True

    monkeypatch.setattr(c, "_send", send_metadata)
    w._open_conversation()
    assert c.state is not None
    assert c.state.confirmed
    assert c.live
    assert c.state.selected is None
    assert c.state.page_id is None
    assert panel.target is None
    assert panel.handoff is None
    assert "conceal" in concealed
    submit(w, "NEW PUBLIC EXACT TEXT")
    assert panel.handoff is not None
    assert panel.handoff.text == "NEW PUBLIC EXACT TEXT"
    assert panel.target is None
    assert not panel.preview_button.isEnabled()
    assert native.effects == 0
    assert requests == []
    engine.reply_extension.close()
