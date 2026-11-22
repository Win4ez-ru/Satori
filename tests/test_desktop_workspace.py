"""Chat/settings navigation with synthetic profiles, no account or audio operations."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication

from satori.desktop.checklist_ui import CheckListWidget
from satori.desktop.settings import DesktopPreferences
from satori.desktop.ui import DesktopApplication, DesktopWindow


@pytest.fixture
def surface(tmp_path: Path) -> Iterator[DesktopWindow]:
    app = QApplication.instance() or DesktopApplication([])
    window = DesktopWindow(
        DesktopPreferences(
            data_directory=tmp_path,
            repository_directory=tmp_path,
            voice_model_directory=tmp_path / "no-stt",
            speech_model_directory=tmp_path / "no-tts",
        )
    )
    window.poll.stop()
    window.show()
    app.processEvents()
    yield window
    window.quitting = True
    window.tray.hide()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_default_chat_keeps_advanced_controls_in_persistent_settings(
    surface: DesktopWindow,
) -> None:
    assert surface.tabs.currentWidget() is surface.conversation_page
    assert surface.tabs.tabBar().isHidden()
    assert surface.workspace.sidebar_pages.currentIndex() == 0
    voice_content = surface.voice_settings_page.widget()
    assert voice_content is not None
    assert voice_content.isAncestorOf(surface.voice_panel.settings_controls)
    assert not surface.conversation_page.isAncestorOf(surface.voice_panel.settings_controls)
    assert surface.voice_panel.controller.parent() is surface.voice_panel
    assert surface.process.state() == QProcess.ProcessState.NotRunning
    assert surface.telegram_panel.controller.process is None
    assert surface.mail_panel.controller.account is None
    assert surface.calendar_panel.controller.grant is None
    assert isinstance(surface.calendar_panel.calendars, CheckListWidget)
    assert isinstance(surface.mail_panel.messages, CheckListWidget)


def test_repeated_settings_routes_preserve_owners_draft_and_session(surface: DesktopWindow) -> None:
    owners = (
        surface.voice_panel.controller,
        surface.calendar_panel.controller,
        surface.mail_panel.controller,
        surface.telegram_panel.controller,
    )
    surface.editor.setPlainText("SYNTHETIC PRIVATE DRAFT")
    surface.session_id = "synthetic-session"
    for _repeat in range(3):
        for key, page in surface._workspace_routes.items():
            surface._open_settings_section(key)
            assert surface.tabs.currentWidget() is page
            assert surface.workspace.sidebar_pages.currentIndex() == 1
        surface._open_conversation()
    assert owners == (
        surface.voice_panel.controller,
        surface.calendar_panel.controller,
        surface.mail_panel.controller,
        surface.telegram_panel.controller,
    )
    assert surface.editor.toPlainText() == "SYNTHETIC PRIVATE DRAFT"
    assert surface.session_id == "synthetic-session"
    assert surface.workspace.sidebar_pages.currentIndex() == 0
    assert surface.telegram_panel.controller.process is None
    assert surface.calendar_panel.controller.grant is None
    assert surface.mail_panel.controller.account is None
    assert not list(surface.preferences.data_directory.rglob("*.db"))


def test_open_settings_is_navigation_and_does_not_open_profile_editor(
    surface: DesktopWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(surface, "edit_profile_preferences", lambda: calls.append(True))
    surface.edit_settings()
    assert surface.tabs.currentWidget() is surface._workspace_routes["general"]
    assert calls == []
    assert surface.process.state() == QProcess.ProcessState.NotRunning


def test_navigation_cancels_voice_and_disables_hidden_chat_capture(
    surface: DesktopWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(surface.voice_panel, "cancel", lambda: calls.append(True))
    monkeypatch.setattr(surface.process, "state", lambda: QProcess.ProcessState.Running)
    surface.activated = True
    assert surface._voice_context().ready
    surface._open_settings_section("voice")
    assert calls
    assert not surface._voice_context().ready
    surface._open_conversation()
    assert surface._voice_context().ready
    monkeypatch.undo()


def test_chat_empty_state_never_becomes_canonical_history(surface: DesktopWindow) -> None:
    assert surface.history_stack.currentIndex() == 0
    assert surface.transcript.toPlainText() == ""
    assert surface.editor.toPlainText() == ""
    text = '<img src="https://example.invalid/tracker"> SYNTHETIC <script>unsafe</script>'
    surface.transcript.setPlainText(text)
    assert surface.history_stack.currentIndex() == 1
    assert surface.transcript.toPlainText() == text
    assert "<img src=" not in surface.transcript.toHtml()
    surface.transcript.clear()
    assert surface.history_stack.currentIndex() == 0


def test_chat_and_all_settings_routes_fit_720px(surface: DesktopWindow) -> None:
    app = QApplication.instance()
    assert app is not None
    initial = surface.height()
    assert initial <= 720
    for key in surface._workspace_routes:
        surface._open_settings_section(key)
        app.processEvents()
        assert surface.height() == initial
    surface._open_conversation()
    app.processEvents()
    assert surface.editor.isVisible()
    assert surface.voice_panel.microphone.isVisible()
    assert surface.send_button.isVisible()
    assert surface.height() == initial


def test_settings_navigation_clears_transient_calendar_card(surface: DesktopWindow) -> None:
    surface.calendar_action.setChecked(True)
    assert not surface.calendar_dialogue_scroll.isHidden()
    surface._open_settings_section("connections")
    assert not surface.calendar_action.isChecked()
    assert surface.calendar_dialogue_scroll.isHidden()


def test_compact_status_keeps_background_failure_visible(surface: DesktopWindow) -> None:
    surface._background_failures = 1
    surface._set_state("Диалог: готова · память в работе: 0 · ошибок обработки: 1")
    assert "Есть ошибки обработки" in surface.workspace.status.text()
    assert "ошибок обработки: 1" in surface.state.text()


def test_history_format_preserves_plain_text_and_hides_opaque_sidebar_ids(
    surface: DesktopWindow,
) -> None:
    surface.session_id = "opaque-synthetic-session"
    text = '<img src="https://example.invalid/no"> 😀 Сатори:\nSYNTHETIC'
    surface._show_history(
        {
            "sessions": [
                {
                    "session_id": surface.session_id,
                    "status": "open",
                    "started_at": "2026-10-30T12:30:00Z",
                }
            ],
            "interactions": [
                {
                    "session_id": surface.session_id,
                    "status": "completed",
                    "user_text": text,
                    "assistant_text": text,
                }
            ],
        }
    )
    assert surface.transcript.toPlainText() == "\n\n".join((f"Ты:\n{text}", f"Сатори:\n{text}"))
    assert "<img src=" not in surface.transcript.toHtml()
    assert surface.sessions.item(0).text() == "2026-10-30 · 12:30"
    assert surface.session_ids == ["opaque-synthetic-session"]


def test_wide_window_keeps_reading_and_input_column_bounded(surface: DesktopWindow) -> None:
    app = QApplication.instance()
    assert app is not None
    surface.resize(1440, 900)
    app.processEvents()
    assert surface.editor.width() <= 780
    assert surface.transcript.width() <= 780
