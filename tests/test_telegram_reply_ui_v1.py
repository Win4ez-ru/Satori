"""Offline Qt reply capability and exact-review presentation checks."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication

from satori.desktop.telegram_port import Message, WireRejected, WireState, schema_from
from satori.desktop.telegram_reply_ui import TelegramReplyPanel
from satori.desktop.telegram_reply_wire import reply_qualification_for
from satori.desktop.telegram_runtime import TelegramRuntimeController, worker_arguments
from satori.desktop.telegram_ui import NativeTelegramPanel
from satori.desktop.ui import DesktopApplication


@pytest.fixture(scope="session")
def app() -> QApplication:
    instance = QApplication.instance() or DesktopApplication([])
    assert isinstance(instance, QApplication)
    return instance


class NoChildProcess(QProcess):
    def state(self) -> QProcess.ProcessState:
        return QProcess.ProcessState.Running


@pytest.fixture
def controller(app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    c = TelegramRuntimeController(tmp_path, clock=lambda: 100.0)
    assert c.configure_reviewed_replies(reply_qualification_for(c.principal))
    schema = (
        Path(__file__).resolve().parents[1] / "src/satori/resources/telegram/backend/schema.json"
    )
    c.state = WireState(schema_from(schema.read_bytes()))
    c.state.confirmed, c.state.session_deadline = True, 1000
    c.state.selected, c.state.selection_deadline = uuid4().hex, 900
    c.state.page_id, c.state.page_deadline = uuid4().hex, 400
    c.state.selected_title = "PUBLIC CHAT"
    c.state.messages = (Message(uuid4().hex, "PUBLIC PERSON", "PUBLIC ORIGINAL", 1),)
    c.visible, c.process = True, NoChildProcess(c)
    commands: list[dict[str, Any]] = []

    def flush(_process: QProcess, _generation: int) -> None:
        for payload in c._pending_writes:
            commands.append(json.loads(payload))
        c._pending_writes.clear()

    monkeypatch.setattr(c, "_flush_writes", flush)
    yield c, commands
    c._timer.stop()
    c.process = None


def response(c: TelegramRuntimeController, status: str, **fields: Any) -> None:
    assert c.state is not None
    assert c._reply_pending is not None
    ident = c._reply_pending[0]
    c._receive(
        {
            "v": 2,
            "capability": "reviewed_reply_v1",
            "event_seq": c.state.sequence + 1,
            "type": "reply",
            "request_id": ident,
            "status": status,
            **fields,
        }
    )


def to_approved(c: TelegramRuntimeController, commands: list[dict[str, Any]]) -> None:
    assert c.state is not None
    assert c.preview_reply(c.state.messages[0].handle, "PUBLIC REPLY")
    command = commands[-1]
    response(
        c,
        "preview",
        action_id=command["action_id"],
        page_id=c.state.page_id,
        message_handle=c.state.messages[0].handle,
        text="PUBLIC REPLY",
        chat_title="PUBLIC CHAT",
        payload_digest="a" * 64,
        expires_at=(datetime.now(UTC) + timedelta(seconds=60)).isoformat(),
    )
    assert c.approve_reply()
    response(
        c,
        "approved",
        action_id=command["action_id"],
        approval_id=str(uuid4()),
        payload_digest="a" * 64,
        expires_at=(datetime.now(UTC) + timedelta(seconds=60)).isoformat(),
    )


def test_default_old_worker_arguments_and_separate_root_opt_in(
    app: QApplication, tmp_path: Path
) -> None:
    c = TelegramRuntimeController(tmp_path)
    assert not c.reviewed_reply_enabled
    assert not c.reply_ready
    old = worker_arguments(
        Path("/public/sdk"), "0" * 64, Path("/public/telegram-temporary"), frozen=True
    )
    assert old[0] == "--telegram-worker"
    assert "--reply-principal" not in old
    assert c.configure_reviewed_replies(reply_qualification_for(c.principal))
    new = worker_arguments(
        Path("/public/sdk"),
        "0" * 64,
        Path("/public/telegram-temporary"),
        frozen=True,
        reply_principal=c.principal,
        reply_generation=1,
    )
    assert new[0] == "--telegram-reply-worker"
    assert "--reply-principal" in new
    assert not c.reply_ready  # Qualified code is not a selected source or send grant.
    c._timer.stop()


def test_exact_approval_one_send_and_explicit_reconcile(controller: Any) -> None:
    c, commands = controller
    to_approved(c, commands)
    assert c.reply_view.text == "PUBLIC REPLY"
    assert c.send_reply()
    assert commands[-1]["command"] == "reply_send"
    assert not c.send_reply()
    response(c, "unknown", action_id=str(c.reply_view.action_id), code="native_pending")
    assert c.reply_view.state == "unknown"
    assert c.reconcile_reply()
    assert commands[-1]["command"] == "reply_reconcile"
    response(c, "sent", action_id=str(c.reply_view.action_id), code="native_sent")
    assert c.reply_view.state == "sent"
    assert sum(q["command"] == "reply_send" for q in commands) == 1
    assert not c.send_reply()
    assert not c.reconcile_reply()
    assert all("chat_id" not in row and "message_id" not in row for row in commands)


@pytest.mark.parametrize("change", ["page", "hide", "epoch", "principal"])
def test_late_sent_frame_after_source_revoke_cannot_publish_success(
    controller: Any, change: str
) -> None:
    c, commands = controller
    to_approved(c, commands)
    assert c.send_reply()
    action = str(c.reply_view.action_id)
    if change == "page":
        c.state.page_id = uuid4().hex
    elif change == "hide":
        c.visible = False
    elif change == "epoch":
        c.state.epoch += 1
    else:
        c.principal = c.principal.model_copy(update={"session_id": uuid4()})
    response(c, "sent", action_id=action, code="native_sent")
    assert c.reply_view.state == "unknown"
    assert not c.reply_view.text
    assert not c.reply_view.chat_title
    assert not c.send_reply()


def test_editing_ui_invalidates_approval_and_conceal_clears_text(controller: Any) -> None:
    c, commands = controller
    panel = TelegramReplyPanel(c)
    panel.target = c.state.page_id, c.state.messages[0].handle, c.state.messages[0].text
    panel._changing = True
    panel.editor.setPlainText("PUBLIC REPLY")
    panel._changing = False
    to_approved(c, commands)
    panel.refresh()
    assert panel.send_button.isEnabled()
    panel.editor.setPlainText("PUBLIC DIFFERENT")
    assert not panel.send_button.isEnabled()
    assert c.reply_view.approval_id is None
    assert commands[-1]["command"] == "reply_discard"
    response(c, "discarded")
    c.visible = False
    panel.refresh()
    assert not panel.editor.toPlainText()
    assert not panel.target_text.toPlainText()
    panel.deleteLater()


def test_foreign_preview_text_is_rejected(controller: Any) -> None:
    c, commands = controller
    assert c.preview_reply(c.state.messages[0].handle, "PUBLIC REPLY")
    command = commands[-1]
    with pytest.raises(WireRejected):
        response(
            c,
            "preview",
            action_id=command["action_id"],
            page_id=c.state.page_id,
            message_handle=c.state.messages[0].handle,
            text="PUBLIC ALTERED",
            chat_title="PUBLIC CHAT",
            payload_digest="a" * 64,
            expires_at=(datetime.now(UTC) + timedelta(seconds=60)).isoformat(),
        )
    assert c.reply_view.state != "preview"


def test_capability_details_distinguish_read_permission_from_reply(controller: Any) -> None:
    c, _ = controller
    panel = NativeTelegramPanel(c.data_directory, controller=c)
    assert "Отправки нет." not in panel.details_text.text()
    assert "Право чтения не разрешает отправку." in panel.details_text.text()
    assert panel.scroll_area.isAncestorOf(panel.reply_panel)
    panel.timer.stop()
    panel.deleteLater()


def test_failed_pipe_write_keeps_unknown_instead_of_overwriting_it_as_sending(
    controller: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    c, commands = controller
    to_approved(c, commands)
    monkeypatch.setattr(c, "_send", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(c, "_flush_writes", lambda *_args: c._fail("WRITE_FAILED"))
    c.send_reply()
    assert c.reply_view.state == "unknown"


def test_reply_frame_applies_existing_page_expiry_before_publication(
    controller: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    c, commands = controller
    c.state.page_deadline = 105.0
    to_approved(c, commands)
    assert c.send_reply()
    action = str(c.reply_view.action_id)
    monkeypatch.setattr(c, "clock", lambda: 106.0)
    response(c, "sent", action_id=action, code="native_sent")
    assert c.reply_view.state == "unknown"
    assert c.state.page_id is None
    assert c.state.messages == ()
