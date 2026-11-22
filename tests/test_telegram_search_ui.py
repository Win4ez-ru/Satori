"""Offscreen synthetic source-local search; no account, worker start or native GUI."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QProcess, Qt
from PySide6.QtGui import QHideEvent
from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit, QScrollArea

from satori.desktop.telegram_page_search import TelegramPageSearchQuery
from satori.desktop.telegram_port import Message, WireState, schema_from
from satori.desktop.telegram_runtime import TelegramRuntimeController, qualification_for
from satori.desktop.telegram_ui import NativeTelegramPanel


@dataclass
class Clock:
    now: float = 100.0

    def __call__(self) -> float:
        return self.now


class NoChildProcess(QProcess):
    def state(self) -> QProcess.ProcessState:
        return QProcess.ProcessState.Running


@pytest.fixture(scope="module")
def app() -> QApplication:
    current = QApplication.instance() or QApplication([])
    assert isinstance(current, QApplication)
    return current


@pytest.fixture
def surface(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[NativeTelegramPanel, TelegramRuntimeController, Clock, list[str]]]:
    del app
    clock = Clock()
    controller = TelegramRuntimeController(tmp_path, clock=clock)
    controller._timer.stop()
    controller.qualification = qualification_for(controller.principal)
    controller.visible = True
    controller.process = NoChildProcess(controller)
    schema_path = (
        Path(__file__).resolve().parents[1] / "src/satori/resources/telegram/backend/schema.json"
    )
    state = WireState(schema_from(schema_path.read_bytes()))
    state.confirmed = True
    state.session_deadline, state.selection_deadline, state.page_deadline = 1000, 800, 400
    state.catalog_deadline = 160
    state.selected, state.selected_title = "synthetic-chat", "Synthetic chat"
    state.page_id = "synthetic-page"
    state.known_pages.add("synthetic-page")
    state.last_clock = 100
    state.messages = (
        Message("synthetic-handle", "Synthetic sender", "<b>synthetic needle</b>", 1),
    )
    controller.state = state
    controller._observed_at = datetime(2026, 10, 31, tzinfo=UTC)
    commands: list[str] = []

    def send(
        name: str,
        fields: dict[str, object] | None = None,
        *,
        starting: bool = False,
        track_conceal: bool = False,
    ) -> bool:
        del fields, starting, track_conceal
        commands.append(name)
        return True

    monkeypatch.setattr(controller, "_send", send)
    panel = NativeTelegramPanel(tmp_path, controller=controller)
    panel.timer.stop()
    monkeypatch.setattr(panel, "_foreground", lambda: True)
    panel._render()
    try:
        yield panel, controller, clock, commands
    finally:
        controller.process = None
        panel.shutdown()
        panel.deleteLater()


def run_search(panel: NativeTelegramPanel) -> None:
    panel.set_search_query("needle")
    panel.search_button.click()
    assert panel._search_result is not None


def test_prefill_and_typing_never_search_fetch_or_execute(
    surface: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel, controller, _clock, commands = surface
    seen: list[TelegramPageSearchQuery] = []
    original = controller.search_current_page

    def search(query: TelegramPageSearchQuery) -> Any:
        seen.append(query)
        return original(query)

    monkeypatch.setattr(controller, "search_current_page", search)
    panel.set_search_query("needle")
    panel._render()
    assert seen == []
    assert commands == []
    assert panel.search_result.toPlainText() == ""
    panel.search_button.click()
    assert len(seen) == 1
    assert commands == []
    assert panel._search_result is not None
    panel.search_query.setText("changed")
    assert len(seen) == 1
    assert panel._search_result is None
    assert panel.search_result.toPlainText() == ""


def test_unbound_prefill_survives_hidden_no_source_render(
    surface: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel, controller, _clock, commands = surface
    controller.state = None
    controller.visible = False
    panel._search_source = None
    monkeypatch.setattr(panel, "_foreground", lambda: False)
    panel.set_search_query("needle")
    for _ in range(3):
        panel._render()
    assert panel.search_query.text() == "needle"
    assert not panel.search_button.isEnabled()
    assert panel._search_result is None
    assert commands == []


def test_explicit_search_is_readonly_plain_text_and_names_page_scope(surface: Any) -> None:
    panel, controller, _clock, commands = surface
    assert controller.state is not None
    before = repr(controller.state.__dict__)
    run_search(panel)
    assert isinstance(panel.search_result, QPlainTextEdit)
    assert panel.search_result.isReadOnly()
    text = panel.search_result.toPlainText()
    assert "<b>synthetic needle</b>" in text
    assert "Telegram" in text
    assert any("до 50 сообщений" in label.text() for label in panel.findChildren(QLabel))
    assert "2026" in text
    assert repr(controller.state.__dict__) == before
    assert commands == []


def test_new_message_clears_query_and_hits_without_automatic_read(surface: Any) -> None:
    panel, controller, _clock, commands = surface
    run_search(panel)
    controller._receive(
        {
            "v": 1,
            "event_seq": 1,
            "type": "page_stale",
            "request_id": None,
            "reason": "NEW_MESSAGE",
            "page_id": "synthetic-page",
        }
    )
    assert panel.search_query.text() == ""
    assert panel.search_result.toPlainText() == ""
    assert panel._search_result is None
    assert controller.state is not None
    assert controller.state.selected == "synthetic-chat"
    assert controller.state.selection_deadline == 800
    assert controller.state.session_deadline == 1000
    assert commands == []


@pytest.mark.parametrize("action", ["hide", "deactivate", "conceal", "disconnect", "shutdown"])
def test_existing_privacy_actions_clear_search(surface: Any, action: str) -> None:
    panel, _controller, _clock, commands = surface
    run_search(panel)
    if action == "hide":
        panel.hideEvent(QHideEvent())
    elif action == "deactivate":
        panel._observed = panel
        panel.eventFilter(panel, QEvent(QEvent.Type.WindowDeactivate))
    elif action == "disconnect":
        panel._disconnect()
    else:
        getattr(panel, action)()
    assert panel.search_query.text() == ""
    assert panel.search_result.toPlainText() == ""
    assert panel._search_result is None
    assert "read" not in commands
    assert "catalog" not in commands


@pytest.mark.parametrize("boundary", ["page", "selection", "session", "rollback", "qualification"])
def test_presentation_rechecks_expiry_and_owner_without_waiting_for_poll(
    surface: Any, boundary: str
) -> None:
    panel, controller, clock, commands = surface
    run_search(panel)
    if boundary == "qualification":
        controller.qualification = None
    else:
        clock.now = {"page": 400, "selection": 800, "session": 1000, "rollback": 99}[boundary]
    panel._render()
    assert panel.search_query.text() == ""
    assert panel.search_result.toPlainText() == ""
    assert panel._search_result is None
    assert commands == []


@pytest.mark.parametrize("boundary", ["generation", "epoch", "page", "replacement_read", "profile"])
def test_source_replacement_clears_query_and_result(surface: Any, boundary: str) -> None:
    panel, controller, _clock, _commands = surface
    run_search(panel)
    assert controller.state is not None
    if boundary == "generation":
        controller.generation += 1
    elif boundary == "epoch":
        controller.state.epoch += 1
    elif boundary == "page":
        controller.state.page_id = "replacement-page"
    elif boundary == "replacement_read":
        controller.state.register("replacement-read", "read", 100)
    else:
        controller.principal = controller.principal.model_copy(
            update={"profile_id": controller.principal.session_id}
        )
    controller.changed.emit()
    assert panel.search_query.text() == ""
    assert panel.search_result.toPlainText() == ""
    assert panel._search_result is None


def test_no_match_is_explicitly_only_current_page(surface: Any) -> None:
    panel, _controller, _clock, commands = surface
    panel.set_search_query("absent literal")
    panel.search_button.click()
    assert panel._search_result is not None
    assert panel._search_result.status == "no_match"
    assert "совпадений нет" in panel.search_result.toPlainText()
    assert "текущ" in panel.search_result.toPlainText()
    assert commands == []


def test_periodic_freshness_check_preserves_result_text_selection(surface: Any) -> None:
    panel, _controller, clock, commands = surface
    run_search(panel)
    panel.search_result.selectAll()
    selected = panel.search_result.textCursor().selectedText()
    clock.now += 1
    panel._render()
    assert panel.search_result.textCursor().selectedText() == selected
    assert commands == []


def test_privacy_summary_remains_visible_and_details_are_explicit_plain_text(surface: Any) -> None:
    panel, _controller, _clock, commands = surface
    assert not panel.details_button.isChecked()
    assert panel.details_text.isHidden()
    summary = panel.privacy_summary.text()
    assert "шире выбранного чата" in summary
    assert "до закрытия Сатори" in summary
    assert "24 часа" in summary
    assert "отметки прочтения" in summary
    panel.details_button.click()
    assert not panel.details_text.isHidden()
    assert panel.details_text.textFormat() == Qt.TextFormat.PlainText
    assert not panel.details_text.openExternalLinks()
    assert "до 15 минут" in panel.details_text.text()
    assert "до 5 минут" in panel.details_text.text()
    assert "Logout" in panel.details_text.text()
    panel.details_button.click()
    assert panel.details_text.isHidden()
    for control in (panel.status, panel.acceptance, panel.diagnostics):
        assert not control.isHidden()
    assert commands == []


def test_collapsed_source_panel_fits_570px_content_with_result(
    surface: Any, app: QApplication
) -> None:
    from satori.desktop.workspace_ui import _STYLE

    panel, _controller, _clock, _commands = surface
    panel.setObjectName("SatoriWorkspace")
    panel.setStyleSheet(_STYLE)
    panel.resize(700, 570)
    panel.show()
    run_search(panel)
    app.processEvents()
    assert panel.height() <= 570
    assert panel.width() <= 700
    assert panel.scroll_area.viewport().height() > 0
    for control in (panel.search_query, panel.search_button, panel.scroll_area.viewport()):
        for point in (control.rect().topLeft(), control.rect().bottomRight()):
            assert panel.rect().contains(control.mapTo(panel, point))
    assert panel.findChildren(QScrollArea) == [panel.scroll_area]
    panel.details_button.click()
    app.processEvents()
    assert panel.height() <= 570
    assert panel.width() <= 700
    assert panel.scroll_area.viewport().height() > 0
    panel.hide()


@pytest.mark.parametrize("text", ["x" * 129, "bad\nquery", "bad\u202equery"])
def test_invalid_prefill_is_rejected_without_silent_truncation(surface: Any, text: str) -> None:
    panel, _controller, _clock, commands = surface
    panel.set_search_query(text)
    assert panel.search_query.text() == ""
    assert panel._search_result is None
    assert "128" in panel.search_result.toPlainText()
    assert commands == []


def test_result_header_keeps_one_original_viewport_and_original_body_handles(
    surface: Any, app: QApplication
) -> None:
    panel, controller, _clock, commands = surface
    panel.resize(1000, 720)
    panel.show()
    run_search(panel)
    app.processEvents()
    assert panel.height() <= 720
    assert panel.findChildren(QScrollArea) == [panel.scroll_area]
    assert panel.search_result.height() <= 144
    assert panel.scroll_area.viewport().height() > 0
    assert [handle for handle, _body in panel._bodies] == ["synthetic-handle"]
    assert panel.search_result not in [body for _handle, body in panel._bodies]
    assert controller.state is not None
    assert controller.state.page_deadline == 400
    assert "read" not in commands
    assert panel.search_query.echoMode() == panel.api_id.EchoMode.Normal
    assert panel.search_result.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    panel.hide()
