"""Native presentation regressions; no model, microphone or real user profile access."""

import json
import os
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate, QEvent, QProcess
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QLineEdit,
    QMessageBox,
    QSystemTrayIcon,
)

from satori.application.assistant.contracts import (
    CalendarCatalog,
    CalendarDescriptor,
    CalendarSourceRef,
)
from satori.desktop import settings, ui
from satori.desktop.settings import DesktopPreferences
from satori.desktop.source_status import SourceKey
from satori.desktop.ui import DesktopApplication, DesktopWindow


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or DesktopApplication([])  # type: ignore[return-value]


@pytest.fixture
def window(application: QApplication, tmp_path: Path) -> Iterator[DesktopWindow]:
    del application
    result = DesktopWindow(
        DesktopPreferences(
            data_directory=tmp_path,
            repository_directory=tmp_path,
            voice_model_directory=tmp_path / "weights",
            speech_model_directory=tmp_path / "speech-weights",
        )
    )
    result.poll.stop()
    yield result
    result.quitting = True
    result.tray.hide()
    result.close()
    result.deleteLater()


def test_resume_is_bound_before_first_send_after_worker_restart(
    window: DesktopWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(window, "request", lambda command, data=None: calls.append((command, data)))
    window.session_id = "prior-open"
    window.synchronize_session = True
    window._show_history({"sessions": [{"session_id": "prior-open", "status": "open"}]})
    assert calls == [("resume_session", {"session_id": "prior-open"})]
    assert not window.synchronize_session


def test_closed_history_does_not_become_a_new_send_target(
    window: DesktopWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(window, "request", lambda command, data=None: calls.append(command))
    window.session_ids = ["closed"]
    window.closed_sessions = {"closed"}
    window.activated = True
    window.select_session(0)
    window.editor.setPlainText("Этот текст должен остаться черновиком")
    window.send()
    assert calls == ["history"]
    assert window.viewing_closed
    assert not window.send_button.isEnabled()


def test_historical_and_provider_text_are_displayed_as_plain_text(window: DesktopWindow) -> None:
    window.session_id = "session"
    text = '<img src="https://example.invalid/tracker"> & <script>alert(1)</script>'
    window._show_history(
        {
            "sessions": [{"session_id": "session", "status": "open", "started_at": "2026-10-16"}],
            "interactions": [
                {
                    "session_id": "session",
                    "status": "completed",
                    "user_text": text,
                    "assistant_text": text,
                }
            ],
        }
    )
    assert window.transcript.toPlainText().count(text) == 2
    assert "&lt;script&gt;" in window.transcript.toHtml()


def test_rejected_send_preserves_draft_and_unlocks_editor(
    window: DesktopWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Ok)
    window.pending["operation"] = "send"
    window.editor.setPlainText("Сохрани черновик")
    window.editor.setReadOnly(True)
    window.busy = True
    window._receive(
        {
            "id": "operation",
            "event": "error",
            "error": {"code": "provider_unavailable", "message": "Unavailable"},
        }
    )
    assert window.editor.toPlainText() == "Сохрани черновик"
    assert not window.editor.isReadOnly()
    assert not window.busy


@pytest.mark.parametrize("outcome", ["accept", "cancel", "save_failure"])
def test_cloud_key_is_password_session_only_and_changes_only_after_saved_settings(
    window: DesktopWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    path = tmp_path / "preferences.json"
    monkeypatch.setattr(settings, "preferences_path", lambda: path)
    monkeypatch.setattr(window, "restart", lambda: None)
    warnings: list[tuple[object, ...]] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    observed_fields: list[QLineEdit] = []

    def choose_cloud(dialog: QDialog) -> QDialog.DialogCode:
        backend = dialog.findChild(QComboBox, "speech_backend")
        voice = dialog.findChild(QLineEdit, "elevenlabs_voice_id")
        key = dialog.findChild(QLineEdit, "elevenlabs_api_key")
        assert backend is not None
        assert voice is not None
        assert key is not None
        assert key.echoMode() == QLineEdit.EchoMode.Password
        assert key.text() == ""
        backend.setCurrentIndex(backend.findData("elevenlabs"))
        voice.setText("ymDCYd8puC7gYjxIamPt")
        key.setText("SYNTHETIC-KEY-DO-NOT-PERSIST")
        observed_fields.append(key)
        return QDialog.DialogCode.Rejected if outcome == "cancel" else QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", choose_cloud)
    if outcome == "save_failure":

        def reject_save(_: DesktopPreferences) -> Path:
            raise OSError("synthetic failure")

        monkeypatch.setattr(ui, "save_preferences", reject_save)
    window.edit_profile_preferences()
    assert observed_fields[0].text() == ""
    assert "SYNTHETIC-KEY" not in window.diagnostics.toPlainText()
    assert "SYNTHETIC-KEY" not in "\n".join(window.events)
    assert "SYNTHETIC-KEY" not in str(warnings)
    assert window.voice_panel.controller.has_cloud_credentials == (outcome == "accept")
    if outcome == "accept":
        stored = path.read_text()
        assert "SYNTHETIC-KEY" not in stored
        assert "api_key" not in stored
        assert settings.load_preferences().speech_backend == "elevenlabs"
        assert "только текст" in window.diagnostics.toPlainText()
    else:
        assert not path.exists()
        assert window.preferences.speech_backend == "elevenlabs"


@pytest.mark.parametrize("forget", [False, True])
def test_existing_cloud_key_is_never_prefilled_and_blank_preserves_it_unless_forgotten(
    window: DesktopWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, forget: bool
) -> None:
    monkeypatch.setattr(settings, "preferences_path", lambda: tmp_path / "preferences.json")
    monkeypatch.setattr(window, "restart", lambda: None)
    controller = window.voice_panel.controller
    controller.set_cloud_credentials("SYNTHETIC-EXISTING-KEY", "ymDCYd8puC7gYjxIamPt")

    def accept_blank(dialog: QDialog) -> QDialog.DialogCode:
        backend = dialog.findChild(QComboBox, "speech_backend")
        key = dialog.findChild(QLineEdit, "elevenlabs_api_key")
        forgetting = dialog.findChild(QCheckBox, "forget_elevenlabs_key")
        assert backend is not None
        assert key is not None
        assert forgetting is not None
        assert key.text() == ""
        assert "уже задан" in key.placeholderText()
        backend.setCurrentIndex(backend.findData("elevenlabs"))
        forgetting.setChecked(forget)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", accept_blank)
    window.edit_profile_preferences()
    assert controller.has_cloud_credentials == (not forget)
    assert "SYNTHETIC" not in (tmp_path / "preferences.json").read_text()


@pytest.mark.parametrize("previous_backend", ["system", "neural"])
def test_switching_local_autoplay_to_cloud_requires_a_new_autoplay_opt_in(
    window: DesktopWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    previous_backend: str,
) -> None:
    monkeypatch.setattr(settings, "preferences_path", lambda: tmp_path / "preferences.json")
    monkeypatch.setattr(window, "restart", lambda: None)
    window.preferences = DesktopPreferences.model_validate(
        {
            **window.preferences.model_dump(),
            "speech_backend": previous_backend,
            "speak_replies": True,
        }
    )
    window.voice_panel.set_preferences(window.preferences)

    def select_cloud(dialog: QDialog) -> QDialog.DialogCode:
        backend = dialog.findChild(QComboBox, "speech_backend")
        key = dialog.findChild(QLineEdit, "elevenlabs_api_key")
        assert backend is not None
        assert key is not None
        backend.setCurrentIndex(backend.findData("elevenlabs"))
        key.setText("synthetic-key-0123456789")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", select_cloud)
    window.edit_profile_preferences()
    assert window.preferences.speech_backend == "elevenlabs"
    assert not window.preferences.speak_replies
    assert not window.voice_panel.automatic_speech.isChecked()
    assert window.voice_panel.controller.has_cloud_credentials


def test_shutdown_keeps_background_failure_count_in_diagnostics(window: DesktopWindow) -> None:
    window.pending["stop"] = "shutdown"
    window._receive(
        {
            "id": "stop",
            "event": "result",
            "data": {"closed": True, "post_response_failures": ["satori_positions"]},
        }
    )
    assert "за запуск — 1" in window.events[-1]


def test_native_quit_action_uses_owned_lifecycle(window: DesktopWindow) -> None:
    window.quit_action.trigger()
    assert window.quitting


def test_window_quit_waits_for_mail_worker_shutdown(
    window: DesktopWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    completed = False
    quit_calls: list[bool] = []
    monkeypatch.setattr(QApplication, "quit", lambda: quit_calls.append(True))
    monkeypatch.setattr(
        type(window.mail_panel), "shutdown_complete", property(lambda _panel: completed)
    )
    window.quit_app()
    assert window.quitting
    assert quit_calls == []
    completed = True
    window.mail_panel.shutdown_finished.emit()
    assert quit_calls == [True]


def test_unified_home_navigation_does_not_start_sources_or_core(window: DesktopWindow) -> None:
    assert window.assistant_home is not None
    assert window.tabs.currentWidget() is window.conversation_page
    assert window.process.state() == QProcess.ProcessState.NotRunning
    assert window.telegram_panel.controller.process is None
    sources: tuple[SourceKey, ...] = ("calendar", "mail", "telegram")
    panels = (window.calendar_panel, window.mail_panel, window.telegram_panel)
    for key, panel in zip(sources, panels, strict=True):
        window.assistant_home.open_buttons[key].click()
        assert window.tabs.currentWidget() is panel
    window.assistant_home.conversation_button.click()
    assert window.tabs.currentWidget() is window.conversation_page
    assert window.process.state() == QProcess.ProcessState.NotRunning
    assert window.telegram_panel.controller.process is None
    assert window.mail_panel.controller.account is None
    assert window.calendar_panel.controller.grant is None


def test_quit_waits_for_both_calendar_and_telegram(
    window: DesktopWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    calendar_complete = False
    telegram_complete = False
    quit_calls: list[bool] = []
    monkeypatch.setattr(QApplication, "quit", lambda: quit_calls.append(True))
    monkeypatch.setattr(
        type(window.calendar_panel),
        "shutdown_complete",
        property(lambda _panel: calendar_complete),
    )
    monkeypatch.setattr(
        type(window.telegram_panel),
        "shutdown_complete",
        property(lambda _panel: telegram_complete),
    )
    window.quit_app()
    assert quit_calls == []
    calendar_complete = True
    window.calendar_panel.shutdown_finished.emit()
    assert quit_calls == []
    telegram_complete = True
    window.telegram_panel.shutdown_finished.emit()
    assert quit_calls == [True]


def test_profile_switch_retires_telegram_identity_and_clears_protected_fields(
    window: DesktopWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous = window.preferences.data_directory
    principal = window.telegram_panel.controller.principal
    monkeypatch.setattr(ui, "load_runtime_resources", lambda: SimpleNamespace(root=tmp_path))
    window._telegram_configuration_pending = True
    window._telegram_runtime_changed()
    assert window.telegram_panel.controller.qualification is not None
    assert window.telegram_panel.controller.qualification.principal == principal
    window.telegram_panel.api_id.setText("123456")
    window.telegram_panel.api_hash.setText("a" * 32)
    monkeypatch.setattr(ui, "save_preferences", lambda _value: tmp_path / "preferences.json")
    monkeypatch.setattr(window, "restart", lambda: None)

    def choose_profile(dialog: QDialog) -> QDialog.DialogCode:
        selected = [
            field for field in dialog.findChildren(QLineEdit) if field.text() == str(previous)
        ]
        assert len(selected) == 1
        selected[0].setText(str(tmp_path / "new-profile"))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", choose_profile)
    window.edit_profile_preferences()
    assert window.telegram_panel.controller.principal != principal
    assert window.telegram_panel.controller.qualification is not None
    assert (
        window.telegram_panel.controller.qualification.principal
        == window.telegram_panel.controller.principal
    )
    assert window.telegram_panel.api_id.text() == ""
    assert window.telegram_panel.api_hash.text() == ""
    assert window.telegram_panel.controller.acceptance.status == "NOT_RUN"
    assert window.telegram_panel.controller.process is None


def test_saved_profile_change_rebinds_calendar_but_speech_toggle_does_not(
    window: DesktopWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prior = window.calendar_panel.controller.principal
    prior_mail = window.mail_panel.controller.principal
    previous_directory = window.preferences.data_directory
    new_directory = tmp_path / "another-profile"
    monkeypatch.setattr(ui, "save_preferences", lambda _value: tmp_path / "preferences.json")
    monkeypatch.setattr(window, "restart", lambda: None)

    def choose_profile(dialog: QDialog) -> QDialog.DialogCode:
        fields = dialog.findChildren(QLineEdit)
        selected = [field for field in fields if field.text() == str(previous_directory)]
        assert len(selected) == 1
        selected[0].setText(str(new_directory))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", choose_profile)
    window.edit_profile_preferences()
    current = window.calendar_panel.controller.principal
    assert current.profile_id != prior.profile_id
    assert current.session_id != prior.session_id
    assert window.calendar_panel.controller.grant is None
    assert window.calendar_panel.controller.snapshot is None
    assert window.calendar_panel.controller.catalog == ()
    current_mail = window.mail_panel.controller.principal
    assert current_mail.profile_id != prior_mail.profile_id
    assert current_mail.session_id != prior_mail.session_id
    assert window.mail_panel.controller.snapshot is None
    window._set_speech_enabled(True)
    assert window.calendar_panel.controller.principal == current
    assert window.mail_panel.controller.principal == current_mail


def test_application_activation_reopens_hidden_window(window: DesktopWindow) -> None:
    app = QApplication.instance()
    assert isinstance(app, DesktopApplication)
    app.desktop_window = window
    window.hide()
    app.event(QEvent(QEvent.Type.ApplicationActivate))
    QTest.qWait(20)
    assert window.isVisible()
    app.desktop_window = None


def test_oversized_message_is_not_submitted_or_lost(
    window: DesktopWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Ok)
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(window, "request", lambda *args: calls.append(args))
    window.activated = True
    window.editor.setPlainText("a" * 8001)
    window.send()
    assert not calls
    assert len(window.editor.toPlainText()) == 8001


def test_persistent_diagnostics_do_not_include_conversation_text(
    application: QApplication, tmp_path: Path
) -> None:
    del application
    path = tmp_path / "desktop-events.json"
    result = DesktopWindow(
        DesktopPreferences(
            data_directory=tmp_path,
            repository_directory=tmp_path,
            voice_model_directory=tmp_path / "weights",
            speech_model_directory=tmp_path / "speech-weights",
        ),
        diagnostics_path=path,
    )
    result.poll.stop()
    result.editor.setPlainText("PRIVATE-DRAFT")
    result.log("Проверка метаданных")
    events = json.loads(path.read_text())
    assert events[-1] == "Проверка метаданных"
    assert "PRIVATE-DRAFT" not in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    result.tray.hide()
    result.quitting = True
    result.close()


def test_real_worker_crash_restarts_without_resubmitting_draft(
    window: DesktopWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "sent-count.txt"
    helper = tmp_path / "synthetic_worker.py"
    helper.write_text(
        "import sys,json,os\n"
        "for line in sys.stdin:\n"
        " r=json.loads(line); c=r['command']; data={}\n"
        " if c in ('initialize','status'): data={'activated':True,'post_response_failures':[]}\n"
        " elif c=='history': data={'sessions':[],'interactions':[]}\n"
        " elif c in ('new_session','resume_session'): data={'session_id':'synthetic-session'}\n"
        " elif c=='send':\n"
        "  with open(sys.argv[1],'a') as f: f.write('1')\n"
        "  os._exit(7)\n"
        " print(json.dumps({'id':r['id'],'event':'result','data':data}),flush=True)\n"
        " if c=='shutdown': break\n"
    )
    original = window.process.setArguments
    monkeypatch.setattr(
        window.process, "setArguments", lambda _: original([str(helper), str(marker)])
    )

    def wait_for(predicate: Any) -> None:
        deadline = monotonic() + 10
        while not predicate() and monotonic() < deadline:
            QTest.qWait(10)
        assert predicate()

    window.start()
    try:
        wait_for(lambda: window.activated and not window.busy and window.session_id is not None)
        window.editor.setPlainText("Синтетический черновик для crash test")
        window.send()
        wait_for(lambda: bool(window.crashes) and window.activated and not window.busy)
        assert marker.read_text() == "1"
        assert window.editor.toPlainText() == "Синтетический черновик для crash test"
        assert any("Исход отправки неизвестен" in event for event in window.events)
    finally:
        window.stop()
        wait_for(lambda: window.process.state() == QProcess.ProcessState.NotRunning)


@pytest.fixture
def calendar_source(window: DesktopWindow, tmp_path: Path) -> Path:
    """Real owned process/pipe protocol, with synthetic public calendar contents only."""
    helper = tmp_path / "source-card-helper.py"
    helper.write_text(
        "import datetime as dt,json,sys,time\n"
        "r=json.loads(sys.stdin.buffer.read())\n"
        "if len(sys.argv)>1:time.sleep(30)\n"
        "start=dt.datetime.fromisoformat(r['start'])+dt.timedelta(hours=1)\n"
        "end=start+dt.timedelta(hours=1);source=r['selected_calendars'][0]\n"
        "event={'source_id':source['source_id'],'calendar_id':source['calendar_id'],"
        "'event_id':'synthetic-event','occurrence_start':start.isoformat(),"
        "'title':'SYNTHETIC <img src=x> EVENT','start':start.isoformat(),'end':end.isoformat(),"
        "'all_day':False,'start_date':None,'end_date':None,'time_zone':r['time_zone'],'status':'confirmed'}\n"
        "out={'schema_version':1,'request_id':r['request_id'],'operation':r['operation'],"
        "'status':'ok','access':'full_access','error':None,"
        "'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'events':[event],'partial':False}\n"
        "print(json.dumps(out),flush=True)\n"
    )
    controller = window.calendar_panel.controller
    controller._helper_command = lambda: (sys.executable, str(helper))
    source = CalendarSourceRef(account_id="synthetic-account", calendar_id="synthetic-calendar")
    controller._coordinator.accept_catalog(
        CalendarCatalog(
            request_id=uuid4(),
            observed_at=datetime.now(UTC),
            calendars=(
                CalendarDescriptor(
                    source=source, title="SYNTHETIC CALENDAR", account_title="SYNTHETIC ACCOUNT"
                ),
            ),
        )
    )
    assert controller.use_calendars((source,))
    window.calendar_action.click()
    panel = window.calendar_dialogue_panel
    panel.timezone.setText("UTC")
    panel.day.setDate(QDate(2026, 10, 20))
    return helper


def start_calendar_card(window: DesktopWindow) -> None:
    panel = window.calendar_dialogue_panel
    panel.preview_button.click()
    assert panel.preview.text()
    panel.consent.setChecked(True)
    assert panel.run_button.isEnabled()
    panel.run_button.click()
    assert not panel.consent.isChecked()


def test_calendar_controls_scroll_without_hiding_the_normal_editor_or_voice(
    window: DesktopWindow, application: QApplication
) -> None:
    window.show()
    application.processEvents()
    initial_height = window.height()
    window.calendar_action.click()
    application.processEvents()
    scroll = window.calendar_dialogue_scroll
    assert window.height() == initial_height
    assert window.height() <= 720
    assert scroll.verticalScrollBar().maximum() > 0
    for widget in (window.editor, window.send_button, window.voice_panel):
        assert window.rect().contains(widget.mapTo(window, widget.rect().bottomRight()))
    scroll.ensureWidgetVisible(window.calendar_dialogue_panel.cancel_button)
    application.processEvents()
    target = window.calendar_dialogue_panel.cancel_button
    assert (
        scroll.viewport().rect().contains(target.mapTo(scroll.viewport(), target.rect().center()))
    )


def test_mail_controls_scroll_with_long_partial_notice_without_window_growth(
    window: DesktopWindow, application: QApplication
) -> None:
    window.show()
    window.tabs.setCurrentWidget(window.mail_panel)
    application.processEvents()
    initial_height = window.height()
    assert initial_height == 720
    panel = window.mail_panel
    assert panel.catalog_availability.isHidden()
    panel.catalog_availability.setText("Synthetic partial metadata notice. " * 30)
    panel.catalog_availability.show()
    application.processEvents()
    assert window.height() == initial_height
    scroll = panel.scroll_area
    assert scroll.verticalScrollBar().maximum() > 0
    for widget in (panel.catalog_availability, panel.read_button):
        scroll.ensureWidgetVisible(widget)
        application.processEvents()
        assert (
            scroll.viewport()
            .rect()
            .contains(widget.mapTo(scroll.viewport(), widget.rect().center()))
        )
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    application.processEvents()
    assert (
        scroll.viewport()
        .rect()
        .contains(panel.preview.mapTo(scroll.viewport(), panel.preview.rect().center()))
    )
    assert window.process.state() == QProcess.ProcessState.NotRunning
    assert panel.controller.account is None
    assert not panel.controller.busy


def wait_calendar_card(window: DesktopWindow) -> None:
    deadline = monotonic() + 5
    while window.calendar_panel.controller.busy and monotonic() < deadline:
        QTest.qWait(10)
    assert not window.calendar_panel.controller.busy
    assert window.calendar_panel.controller.dialogue_answer is not None
    assert window.calendar_dialogue_panel.answer.toPlainText()


def test_window_calendar_action_uses_shared_helper_without_core_history_or_tts(
    window: DesktopWindow,
    calendar_source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del calendar_source
    calls: list[tuple[object, ...]] = []
    speech: list[tuple[object, ...]] = []
    monkeypatch.setattr(window, "request", lambda *args: calls.append(args))
    monkeypatch.setattr(
        window.voice_panel, "committed_reply", lambda *args, **kwargs: speech.append(args)
    )
    window.transcript.setPlainText("EXISTING CANONICAL HISTORY")
    window.editor.setPlainText("PRIVATE NORMAL DRAFT")
    controller = window.calendar_panel.controller
    assert window.calendar_dialogue_panel.controller is controller
    assert window.process.state() == QProcess.ProcessState.NotRunning
    start_calendar_card(window)
    wait_calendar_card(window)
    card = window.calendar_dialogue_panel.answer.toPlainText()
    assert "SYNTHETIC <img src=x> EVENT" in card
    assert "SYNTHETIC ACCOUNT / SYNTHETIC CALENDAR" in card
    assert "Локальный снимок" in card
    assert "срок до" in card
    assert window.transcript.toPlainText() == "EXISTING CANONICAL HISTORY"
    assert window.editor.toPlainText() == "PRIVATE NORMAL DRAFT"
    assert not calls
    assert not speech
    assert not list(tmp_path.rglob("*.db"))
    assert "SYNTHETIC <img" not in window.diagnostics.toPlainText()
    assert "SYNTHETIC <img" not in json.dumps(
        [event.model_dump(mode="json") for event in controller.dialogue_audit_events]
    )


@pytest.mark.parametrize(
    "action", ["draft", "asr_draft", "new_chat", "select_chat", "restart", "hide_action"]
)
def test_window_source_card_is_cleared_by_dialogue_lifecycle(
    window: DesktopWindow,
    calendar_source: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    del calendar_source
    monkeypatch.setattr(window, "request", lambda *args: None)
    monkeypatch.setattr(window, "start", lambda: None)
    start_calendar_card(window)
    wait_calendar_card(window)
    prior_chat = window.calendar_panel.controller.dialogue_chat_id
    if action == "draft":
        window.editor.setPlainText("EDITED NORMAL DRAFT")
    elif action == "asr_draft":
        window.voice_panel.apply_draft("EDITABLE ASR DRAFT")
    elif action == "new_chat":
        window._new_conversation()
    elif action == "select_chat":
        window.session_ids = ["other-chat"]
        window.closed_sessions = {"other-chat"}
        window.select_session(0)
    elif action == "restart":
        window.restart()
    else:
        window.calendar_action.click()
    assert window.calendar_panel.controller.dialogue_answer is None
    assert window.calendar_dialogue_panel.answer.toPlainText() == ""
    assert not window.calendar_dialogue_panel.consent.isChecked()
    if action in {"new_chat", "select_chat", "restart"}:
        assert window.calendar_panel.controller.dialogue_chat_id != prior_chat
    if action == "asr_draft":
        assert window.editor.toPlainText() == "EDITABLE ASR DRAFT"
    assert not window.pending


def test_editing_normal_draft_cancels_source_read_and_late_callback_cannot_overwrite_it(
    window: DesktopWindow,
    calendar_source: Path,
) -> None:
    controller = window.calendar_panel.controller
    controller._helper_command = lambda: (sys.executable, str(calendar_source), "late")
    start_calendar_card(window)
    job = controller._job
    assert job is not None
    revision = window._calendar_editor_revision
    window.editor.setPlainText("NEW DRAFT MUST SURVIVE")
    assert window._calendar_editor_revision > revision
    assert not controller.busy
    controller._started(job)
    controller._finished(job, 0, QProcess.ExitStatus.NormalExit)
    assert window.editor.toPlainText() == "NEW DRAFT MUST SURVIVE"
    assert window.calendar_dialogue_panel.answer.toPlainText() == ""


def test_normal_send_after_calendar_card_has_only_normal_text_on_core_wire(
    window: DesktopWindow,
    calendar_source: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del calendar_source
    window.editor.setPlainText("ONLY NORMAL USER TEXT")
    start_calendar_card(window)
    wait_calendar_card(window)
    payloads: list[bytes] = []
    monkeypatch.setattr(window.process, "state", lambda: QProcess.ProcessState.Running)
    monkeypatch.setattr(window.process, "write", lambda data: payloads.append(data))
    window.activated = True
    window.send()
    assert len(payloads) == 1
    request = json.loads(payloads[0])
    assert request["command"] == "send"
    assert request["data"]["text"] == "ONLY NORMAL USER TEXT"
    assert set(request["data"]) == {"text", "request_id"}
    assert "SYNTHETIC" not in payloads[0].decode()
    assert window.calendar_panel.controller.dialogue_answer is None
    assert window.calendar_dialogue_panel.answer.toPlainText() == ""
    # Restore fake worker state before fixture cleanup; no worker was started.
    monkeypatch.undo()


QUIT_SOURCE_ORDER = ("places", "public", "calendar", "mail", "telegram")


def _quit_dispatch_surface() -> tuple[Any, dict[str, int], list[tuple[str, ...]]]:
    counts: dict[str, int] = dict.fromkeys(
        (
            "voice",
            "mobile_settings",
            "mobile_bridge",
            "assistant",
            "reminders",
            *QUIT_SOURCE_ORDER,
            "stop",
        ),
        0,
    )
    completed: list[str] = []
    quit_at: list[tuple[str, ...]] = []
    surface = SimpleNamespace(
        quitting=False,
        _quit_started=False,
        _quit_issued=False,
        _shutdown_requests_issued=False,
        _places_shutdown_complete=False,
        notification_adapter=None,
        process=SimpleNamespace(state=lambda: QProcess.ProcessState.NotRunning),
    )
    surface._try_finish_quit = lambda: DesktopWindow._try_finish_quit(cast(DesktopWindow, surface))
    surface.stop = lambda: counts.__setitem__("stop", counts["stop"] + 1)
    surface.voice_panel = SimpleNamespace(
        cancel=lambda: counts.__setitem__("voice", counts["voice"] + 1)
    )
    surface.assistant = SimpleNamespace(
        clear=lambda: counts.__setitem__("assistant", counts["assistant"] + 1)
    )
    surface.mobile_settings = SimpleNamespace(
        shutdown=lambda: counts.__setitem__("mobile_settings", counts["mobile_settings"] + 1)
    )
    surface.mobile_bridge = SimpleNamespace(
        shutdown=lambda: counts.__setitem__("mobile_bridge", counts["mobile_bridge"] + 1)
    )
    surface.reminder_panel = SimpleNamespace(
        controller=SimpleNamespace(
            shutdown=lambda: counts.__setitem__("reminders", counts["reminders"] + 1)
        )
    )
    for name in QUIT_SOURCE_ORDER:
        panel = SimpleNamespace(shutdown_complete=name == "telegram")

        def shutdown(name: str = name, panel: Any = panel) -> None:
            assert counts["mobile_settings"] == 1
            assert counts["mobile_bridge"] == 1
            assert counts["assistant"] == 1
            assert counts["reminders"] == 1
            counts[name] += 1
            completed.append(name)
            panel.shutdown_complete = True
            if name == "places":
                surface._places_shutdown_complete = True
            surface._try_finish_quit()

        panel.shutdown = shutdown
        setattr(surface, name + "_panel", panel)
        if name == "public":
            surface.public_sources = panel
    surface.completed = completed
    return surface, counts, quit_at


def test_quit_dispatches_all_sources_before_synchronous_completion_can_quit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface, counts, quit_at = _quit_dispatch_surface()
    monkeypatch.setattr(QApplication, "quit", lambda: quit_at.append(tuple(surface.completed)))
    DesktopWindow.quit_app(surface)
    assert quit_at == [QUIT_SOURCE_ORDER]
    assert counts == dict.fromkeys(
        ("voice", "mobile_settings", "mobile_bridge", "assistant", "reminders", *QUIT_SOURCE_ORDER),
        1,
    ) | {"stop": 0}


def test_quit_dispatch_reentry_and_repeated_request_are_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface, counts, quit_at = _quit_dispatch_surface()
    # Existing window fixtures set this before close to bypass tray concealment.
    surface.quitting = True
    monkeypatch.setattr(QApplication, "quit", lambda: quit_at.append(tuple(surface.completed)))
    original = surface.calendar_panel.shutdown
    reentered = False

    def shutdown() -> None:
        nonlocal reentered
        if not reentered:
            reentered = True
            DesktopWindow.quit_app(surface)
        original()

    surface.calendar_panel.shutdown = shutdown
    DesktopWindow.quit_app(surface)
    DesktopWindow.quit_app(surface)
    assert quit_at == [QUIT_SOURCE_ORDER]
    assert counts == dict.fromkeys(
        ("voice", "mobile_settings", "mobile_bridge", "assistant", "reminders", *QUIT_SOURCE_ORDER),
        1,
    ) | {"stop": 0}


def test_quit_barrier_preserves_pending_source_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface, counts, quit_at = _quit_dispatch_surface()
    monkeypatch.setattr(QApplication, "quit", lambda: quit_at.append(tuple(surface.completed)))
    surface.telegram_panel.shutdown_complete = False

    def shutdown() -> None:
        counts["telegram"] += 1
        surface.completed.append("telegram")

    surface.telegram_panel.shutdown = shutdown
    DesktopWindow.quit_app(surface)
    assert quit_at == []
    surface.telegram_panel.shutdown_complete = True
    surface._try_finish_quit()
    assert quit_at == [QUIT_SOURCE_ORDER]


def test_quit_barrier_preserves_running_core_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface, counts, quit_at = _quit_dispatch_surface()
    monkeypatch.setattr(QApplication, "quit", lambda: quit_at.append(tuple(surface.completed)))
    surface.process.state = lambda: QProcess.ProcessState.Running
    DesktopWindow.quit_app(surface)
    assert quit_at == []
    assert counts["stop"] == 1
    surface.process.state = lambda: QProcess.ProcessState.NotRunning
    surface._try_finish_quit()
    assert quit_at == [QUIT_SOURCE_ORDER]


def test_quit_dispatches_native_notifications_and_waits_for_its_terminal_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface, counts, quit_at = _quit_dispatch_surface()
    counts["notifications"] = 0
    surface.notification_adapter = SimpleNamespace(
        shutdown_complete=False,
        shutdown=lambda: counts.__setitem__("notifications", counts["notifications"] + 1),
    )
    monkeypatch.setattr(QApplication, "quit", lambda: quit_at.append(tuple(surface.completed)))
    DesktopWindow.quit_app(surface)
    assert counts["notifications"] == 1
    assert quit_at == []
    surface.notification_adapter.shutdown_complete = True
    surface._try_finish_quit()
    surface._try_finish_quit()
    assert quit_at == [QUIT_SOURCE_ORDER]
    assert counts["notifications"] == 1


def _quit_close_surface() -> tuple[Any, dict[str, int], list[tuple[str, ...]]]:
    surface, counts, quit_at = _quit_dispatch_surface()
    surface.source_answer = SimpleNamespace(
        clear=lambda: counts.__setitem__(
            "source_answer_clear", counts.get("source_answer_clear", 0) + 1
        )
    )
    surface.quit_app = lambda: DesktopWindow.quit_app(cast(DesktopWindow, surface))
    surface.hide = lambda: counts.__setitem__("hide", counts.get("hide", 0) + 1)
    surface.mail_panel.cancel = lambda: counts.__setitem__("mail_cancel", 1)
    surface.telegram_panel.conceal = lambda: counts.__setitem__("telegram_conceal", 1)
    return surface, counts, quit_at


def test_native_quit_latches_before_reentrant_close_and_is_issued_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface, counts, quit_at = _quit_close_surface()
    observed: list[tuple[bool, bool]] = []
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: False)

    def native_quit() -> None:
        quit_at.append(tuple(surface.completed))
        event = QCloseEvent()
        event.ignore()
        DesktopWindow.closeEvent(cast(DesktopWindow, surface), event)
        observed.append((surface._quit_issued, event.isAccepted()))
        if len(quit_at) == 1:
            surface._try_finish_quit()

    monkeypatch.setattr(QApplication, "quit", native_quit)
    DesktopWindow.quit_app(cast(DesktopWindow, surface))
    surface._try_finish_quit()
    DesktopWindow.quit_app(cast(DesktopWindow, surface))
    assert observed == [(True, True)]
    assert quit_at == [QUIT_SOURCE_ORDER]
    assert counts["telegram"] == 1


def test_first_close_accepts_when_shutdown_finishes_synchronously(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface, counts, quit_at = _quit_close_surface()
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: False)
    monkeypatch.setattr(QApplication, "quit", lambda: quit_at.append(tuple(surface.completed)))
    event = QCloseEvent()
    event.ignore()
    DesktopWindow.closeEvent(cast(DesktopWindow, surface), event)
    assert event.isAccepted()
    assert quit_at == [QUIT_SOURCE_ORDER]
    assert counts["source_answer_clear"] == 1


@pytest.mark.parametrize("pending", ["core", *QUIT_SOURCE_ORDER, "notifications", "dispatch"])
def test_close_is_rejected_until_every_shutdown_boundary_completes(
    pending: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    surface, _counts, quit_at = _quit_close_surface()
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: False)
    monkeypatch.setattr(QApplication, "quit", lambda: quit_at.append(tuple(surface.completed)))
    surface.quitting = True
    surface._quit_started = True
    surface._shutdown_requests_issued = pending != "dispatch"
    for name in QUIT_SOURCE_ORDER:
        getattr(surface, name + "_panel").shutdown_complete = name != pending
    surface._places_shutdown_complete = pending != "places"
    surface.notification_adapter = SimpleNamespace(shutdown_complete=pending != "notifications")
    if pending == "core":
        surface.process.state = lambda: QProcess.ProcessState.Running
    surface._try_finish_quit()
    event = QCloseEvent()
    DesktopWindow.closeEvent(cast(DesktopWindow, surface), event)
    assert not event.isAccepted()
    assert not surface._quit_issued
    assert quit_at == []

    surface._shutdown_requests_issued = True
    surface.process.state = lambda: QProcess.ProcessState.NotRunning
    for name in QUIT_SOURCE_ORDER:
        getattr(surface, name + "_panel").shutdown_complete = True
    surface._places_shutdown_complete = True
    surface.notification_adapter.shutdown_complete = True
    surface._try_finish_quit()
    event = QCloseEvent()
    event.ignore()
    DesktopWindow.closeEvent(cast(DesktopWindow, surface), event)
    surface._try_finish_quit()
    assert event.isAccepted()
    assert len(quit_at) == 1


def test_close_to_tray_before_quit_keeps_existing_concealment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface, counts, quit_at = _quit_close_surface()
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: True)
    monkeypatch.setattr(QApplication, "quit", lambda: quit_at.append(tuple(surface.completed)))
    event = QCloseEvent()
    DesktopWindow.closeEvent(cast(DesktopWindow, surface), event)
    assert not event.isAccepted()
    assert counts["hide"] == 1
    assert counts["mail_cancel"] == 1
    assert counts["telegram_conceal"] == 1
    assert counts["source_answer_clear"] == 1
    assert not surface._quit_started
    assert not surface._quit_issued
    assert quit_at == []


@pytest.mark.parametrize("voice_setting", ["voice_id", "session_key"])
def test_voice_settings_save_preserves_older_selected_chat_and_its_draft(
    window: DesktopWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    voice_setting: str,
) -> None:
    monkeypatch.setattr(settings, "preferences_path", lambda: tmp_path / "preferences.json")
    monkeypatch.setattr(ui, "_bundle_path", lambda: None)
    monkeypatch.setattr(window, "restart", lambda: None)
    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(window, "request", lambda command, data=None: calls.append((command, data)))
    window.activated = True
    window.session_id = "older-A"
    window.editor.setPlainText("DRAFT OWNED BY older-A")

    def accept(dialog: QDialog) -> QDialog.DialogCode:
        name = "elevenlabs_voice_id" if voice_setting == "voice_id" else "elevenlabs_api_key"
        field = dialog.findChild(QLineEdit, name)
        assert field is not None
        field.setText(
            "SyntheticOtherVoice12345"
            if voice_setting == "voice_id"
            else "SYNTHETIC-SESSION-KEY-0123456789"
        )
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", accept)
    window.edit_profile_preferences()
    assert window.editor.toPlainText() == "DRAFT OWNED BY older-A"
    window.synchronize_session = True
    window._show_history(
        {
            "sessions": [
                {"session_id": "older-A", "status": "open"},
                {"session_id": "newer-B", "status": "open"},
            ]
        }
    )
    resume_request = calls[-1]
    assert resume_request == ("resume_session", {"session_id": "older-A"})
    window.pending["voice-settings-resume"] = "resume_session"
    window._receive(
        {"id": "voice-settings-resume", "event": "result", "data": {"session_id": "older-A"}}
    )
    window.send()
    assert window.session_id == "older-A"
    assert calls[-1][0] == "send"
    assert calls[-1][1]["text"] == "DRAFT OWNED BY older-A"
