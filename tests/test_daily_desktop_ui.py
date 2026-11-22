"""Synthetic chat-first end-to-end Qt routing/lifecycle; never a live provider proof."""

# ruff: noqa: RUF001 - intentional Russian user-authored commands

import json
import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QProcess
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
)

from satori.application.assistant.contracts import (
    CalendarCatalog,
    CalendarDescriptor,
    CalendarOccurrence,
    CalendarReadRequest,
    CalendarReadResult,
    CalendarSourceRef,
)
from satori.application.assistant.dialogue_contracts import PendingCalendarRead
from satori.desktop import ui
from satori.desktop.assistant_controller import TaskCard
from satori.desktop.calendar_controller import Operation
from satori.desktop.mobile_settings_ui import MobileProfileChangeError
from satori.desktop.reminder_controller import ReminderController
from satori.desktop.settings import DesktopPreferences
from satori.desktop.ui import DesktopApplication, DesktopWindow
from satori.infrastructure.providers.ollama_http import OllamaHttpClient

WindowFactory = Callable[[Path], DesktopWindow]
SOURCE = CalendarSourceRef(account_id="SYNTHETIC ACCOUNT", calendar_id="SYNTHETIC WORK")
EXTERNAL_TEXT = '<img src="https://example.invalid/tracker"> SYNTHETIC PRIVATE SHIFT'


@pytest.fixture
def app() -> QApplication:
    current = QApplication.instance() or DesktopApplication([])
    assert isinstance(current, QApplication)
    return current


@pytest.fixture
def factory(app: QApplication, monkeypatch: pytest.MonkeyPatch) -> Iterator[WindowFactory]:
    windows: list[DesktopWindow] = []

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("unexpected_real_process_or_model_in_synthetic_ui")

    monkeypatch.setattr(QProcess, "start", forbidden)
    monkeypatch.setattr(OllamaHttpClient, "post_json", forbidden)
    monkeypatch.setattr(QSystemTrayIcon, "show", lambda self: None)
    monkeypatch.delenv("SATORI_DESKTOP_BUNDLE", raising=False)

    def create(profile: Path) -> DesktopWindow:
        window = DesktopWindow(
            DesktopPreferences(
                data_directory=profile,
                repository_directory=profile / "no-checkout",
                voice_model_directory=profile / "no-stt",
                speech_model_directory=profile / "no-tts",
            )
        )
        window.poll.stop()
        windows.append(window)
        return window

    yield create
    for window in windows:
        window.poll.stop()
        window.mobile_settings.shutdown()
        window.mobile_bridge.shutdown()
        window.reminder_panel.controller.shutdown()
        if window.notification_adapter is not None:
            window.notification_adapter.shutdown()
        window.places_panel.shutdown()
        window.public_sources.shutdown()
        window.calendar_panel.shutdown()
        window.mail_panel.shutdown()
        window.telegram_panel.shutdown()
        window.tray.hide()
        window._quit_issued = True
        window.quitting = True
        window.close()
        window.deleteLater()
    app.processEvents()


def worker_transport(
    window: DesktopWindow, monkeypatch: pytest.MonkeyPatch
) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    monkeypatch.setattr(window.process, "state", lambda: QProcess.ProcessState.Running)

    def write(payload: bytes) -> int:
        frames.append(json.loads(payload))
        return len(payload)

    monkeypatch.setattr(window.process, "write", write)
    window.activated = True
    window.session_id = "SYNTHETIC CONVERSATION"
    return frames


def calendar_source(
    window: DesktopWindow, monkeypatch: pytest.MonkeyPatch
) -> list[PendingCalendarRead]:
    """Keep real grant/session owners, replace only their native process boundary."""
    controller = window.calendar_panel.controller
    pending_reads: list[PendingCalendarRead] = []

    def read(
        operation: Operation,
        request: CalendarReadRequest | None = None,
        *,
        dialogue: PendingCalendarRead | None = None,
        schedule_body: dict[str, object] | None = None,
    ) -> None:
        assert operation == "read_events"
        assert dialogue is not None
        assert request == dialogue.request
        assert schedule_body is None
        assert window.editor.toPlainText() == ""
        pending_reads.append(dialogue)
        controller._notify()

    monkeypatch.setattr(controller, "_start", read)
    controller._coordinator.accept_catalog(
        CalendarCatalog(
            request_id=uuid4(),
            observed_at=datetime.now(UTC),
            calendars=(CalendarDescriptor(source=SOURCE, title="Work", account_title="SYNTHETIC"),),
        )
    )
    assert controller.use_calendars((SOURCE,))
    return pending_reads


def finish_calendar(window: DesktopWindow, pending: PendingCalendarRead) -> None:
    controller = window.calendar_panel.controller
    owner = controller._dialogue
    assert owner is not None
    answer = owner.accept(
        pending,
        CalendarReadResult(
            request_id=pending.request.request_id,
            observed_at=datetime.now(UTC),
            occurrences=(
                CalendarOccurrence(
                    source=SOURCE,
                    event_id="SYNTHETIC EVENT",
                    occurrence_id="SYNTHETIC OCCURRENCE",
                    title=EXTERNAL_TEXT,
                    starts_at=pending.permit.proposal.window.starts_at + timedelta(hours=10),
                    ends_at=pending.permit.proposal.window.starts_at + timedelta(hours=11),
                    timezone="Europe/Moscow",
                ),
            ),
        ),
    )
    controller._dialogue_status = answer.receipt.status
    controller.dialogue_changed.emit()
    controller._notify()


def card(window: DesktopWindow) -> None:
    window.assistant.card = TaskCard(uuid4(), "SYNTHETIC CARD", EXTERNAL_TEXT, "ok", "TEST")
    window.assistant.changed.emit()


def seed_reminder(profile: Path, title: str) -> None:
    controller = ReminderController(profile)
    try:
        assert controller.create(title, datetime.now(UTC) + timedelta(days=1), "Europe/Moscow")
    finally:
        controller.shutdown()


def test_ordinary_text_still_uses_only_canonical_send(
    factory: WindowFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = factory(tmp_path / "profile")
    frames = worker_transport(window, monkeypatch)
    draft = "Что думаешь о напоминаниях и памяти? <b>literal</b>"
    window.editor.setPlainText(draft)
    window.send()
    assert [frame["command"] for frame in frames] == ["send"]
    assert frames[0]["data"]["text"] == draft
    assert frames[0]["data"]["request_id"]
    assert window.editor.toPlainText() == draft
    assert window.editor.isReadOnly()
    assert window.assistant.card is None
    assert window.transcript.toPlainText() == ""


def test_actual_calendar_chip_clears_draft_before_read_and_never_sends_source_to_core(
    factory: WindowFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = factory(tmp_path / "profile")
    frames = worker_transport(window, monkeypatch)
    reads = calendar_source(window, monkeypatch)
    window.show()
    QApplication.processEvents()
    assert window.isActiveWindow()
    chip = next(
        button for button in window.findChildren(QPushButton) if button.text() == "Планы на сегодня"
    )
    chip.click()
    assert window.editor.toPlainText()
    window.send()
    assert frames == []
    assert len(reads) == 1
    assert window.editor.toPlainText() == ""
    assert window.calendar_panel.controller._dialogue is not None
    assert window.calendar_panel.controller._dialogue.pending == reads[0]
    finish_calendar(window, reads[0])
    assert window.assistant.card is not None
    assert EXTERNAL_TEXT in window.task_card.body.toPlainText()
    assert EXTERNAL_TEXT not in window.transcript.toPlainText()
    assert EXTERNAL_TEXT not in window.editor.toPlainText()
    assert all(EXTERNAL_TEXT not in event for event in window.events)
    assert frames == []
    assert window.send_scopes == {}
    assert window.send_client_requests == {}
    window.editor.setPlainText("Новый несохранённый черновик")
    assert EXTERNAL_TEXT not in window.task_card.body.toPlainText()
    assert window.editor.toPlainText() == "Новый несохранённый черновик"


def test_quick_chips_preserve_unsent_draft_and_do_not_execute_on_click(
    factory: WindowFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = factory(tmp_path / "profile")
    frames = worker_transport(window, monkeypatch)
    draft = "SYNTHETIC UNSENT DRAFT"
    window.editor.setPlainText(draft)
    buttons = [
        button
        for button in window.findChildren(QPushButton)
        if button.objectName() == "QuickAction"
    ]
    assert len(buttons) >= 3
    for button in buttons:
        button.click()
        assert window.editor.toPlainText() == draft
        assert frames == []
        assert not window.reminder_panel.controller.enabled
        assert window.calendar_panel.controller.grant is None


@pytest.mark.parametrize("operation", ["new", "select", "deactivate"])
def test_conversation_changes_and_deactivation_clear_transient_task_fields(
    factory: WindowFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    window = factory(tmp_path / "profile")
    worker_transport(window, monkeypatch)
    card(window)
    assert EXTERNAL_TEXT in window.task_card.body.toPlainText()
    if operation == "new":
        window._new_conversation()
    elif operation == "select":
        window.session_ids = ["OTHER SYNTHETIC SESSION"]
        window.select_session(0)
    else:
        QApplication.sendEvent(window, QEvent(QEvent.Type.WindowDeactivate))
    assert window.assistant.card is None
    assert window.task_card.body.toPlainText() == ""
    assert window.task_card.title.text() == ""
    assert window.task_card.source.text() == ""


def test_saved_reminders_restore_on_window_creation_without_core_or_notification_dispatch(
    factory: WindowFactory, tmp_path: Path
) -> None:
    profile = tmp_path / "profile"
    seed_reminder(profile, "SYNTHETIC SAVED REMINDER")
    window = factory(profile)
    controller = window.reminder_panel.controller
    assert controller.enabled
    assert len(controller.records) == 1
    assert controller.records[0].title == "SYNTHETIC SAVED REMINDER"
    assert controller.records[0].delivery_state == "not_requested"
    assert window.reminder_panel.list.count() == 1
    assert window.process.state() == QProcess.ProcessState.NotRunning
    assert window.calendar_panel.controller.grant is None
    assert window.mail_panel.controller.account is None
    assert window.telegram_panel.controller.process is None
    assert not (profile / "satori.db").exists()


def test_profile_dialog_switch_clears_task_and_restores_only_destination_reminders(
    factory: WindowFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original, destination = tmp_path / "original", tmp_path / "destination"
    seed_reminder(original, "SYNTHETIC ORIGINAL REMINDER")
    seed_reminder(destination, "SYNTHETIC DESTINATION REMINDER")
    window = factory(original)
    card(window)
    window.editor.setPlainText("SYNTHETIC OLD DRAFT")
    window.transcript.setPlainText("SYNTHETIC OLD HISTORY")
    window.session_id = "OLD SESSION"
    monkeypatch.setattr(ui, "save_preferences", lambda preferences: None)
    monkeypatch.setattr(window, "restart", lambda: None)

    def accept(dialog: QDialog) -> int:
        directory = next(
            field for field in dialog.findChildren(QLineEdit) if field.text() == str(original)
        )
        directory.setText(str(destination))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", accept)
    window.edit_profile_preferences()
    assert window.preferences.data_directory == destination
    assert window.assistant.card is None
    assert window.task_card.body.toPlainText() == ""
    assert window.transcript.toPlainText() == ""
    assert window.editor.toPlainText() == ""
    assert window.session_id is None
    records = window.reminder_panel.controller.records
    assert [record.title for record in records] == ["SYNTHETIC DESTINATION REMINDER"]
    assert records[0].delivery_state == "not_requested"
    assert window.calendar_panel.controller.grant is None
    assert window.mail_panel.controller.account is None
    assert window.telegram_panel.controller.process is None


def test_reminder_task_is_saved_beside_history_without_a_canonical_user_message(
    factory: WindowFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / "profile"
    window = factory(profile)
    frames = worker_transport(window, monkeypatch)
    window.transcript.setPlainText("SYNTHETIC PREVIOUS CONVERSATION")
    window.editor.setPlainText("Напомни через 15 минут SYNTHETIC TASK")
    window.send()
    assert frames == []
    assert window.editor.toPlainText() == ""
    assert window.assistant.card is not None
    assert window.assistant.card.status == "saved"
    assert window.transcript.toPlainText() == "SYNTHETIC PREVIOUS CONVERSATION"
    assert [record.title for record in window.reminder_panel.controller.records] == [
        "SYNTHETIC TASK"
    ]
    assert not (profile / "satori.db").exists()


def test_failed_mobile_profile_revoke_aborts_profile_preferences_and_source_switch(
    factory: WindowFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original, destination = tmp_path / "original", tmp_path / "destination"
    seed_reminder(original, "SYNTHETIC ORIGINAL REMINDER")
    window = factory(original)
    principal = window.calendar_panel.controller.principal
    previous = window.preferences
    saves: list[object] = []
    restarts: list[bool] = []
    warnings: list[str] = []
    monkeypatch.setattr(ui, "save_preferences", saves.append)
    monkeypatch.setattr(window, "restart", lambda: restarts.append(True))
    monkeypatch.setattr(
        QMessageBox, "warning", lambda parent, title, message: warnings.append(message)
    )

    def cannot_revoke(data_directory: Path) -> None:
        assert data_directory == destination
        raise MobileProfileChangeError

    monkeypatch.setattr(window.mobile_settings, "set_profile", cannot_revoke)

    def accept(dialog: QDialog) -> int:
        directory = next(
            field for field in dialog.findChildren(QLineEdit) if field.text() == str(original)
        )
        directory.setText(str(destination))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", accept)
    window.edit_profile_preferences()
    assert window.preferences == previous
    assert window.calendar_panel.controller.principal == principal
    assert saves == []
    assert restarts == []
    assert warnings == ["MobileProfileChangeError"]
    assert [record.title for record in window.reminder_panel.controller.records] == [
        "SYNTHETIC ORIGINAL REMINDER"
    ]


def test_current_due_reminder_is_separate_from_core_and_old_profile_due_is_rejected(
    factory: WindowFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / "profile"
    window = factory(profile)
    frames = worker_transport(window, monkeypatch)
    controller = window.reminder_panel.controller
    now = datetime.now(UTC)
    clock = [now]
    controller._clock = lambda: clock[0]
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    notices: list[tuple[str, str]] = []
    monkeypatch.setattr(
        window.tray, "showMessage", lambda title, body: notices.append((title, body))
    )

    def no_automatic_voice(*args: object, **kwargs: object) -> None:
        raise AssertionError("task_card_must_not_be_automatic_voice_input")

    monkeypatch.setattr(window.voice_panel, "committed_reply", no_automatic_voice)
    receipt = controller.create(
        "SYNTHETIC PRIVATE DUE TASK", now + timedelta(minutes=1), "Europe/Moscow"
    )
    assert receipt is not None
    window.transcript.setPlainText("SYNTHETIC CANONICAL HISTORY")
    clock[0] += timedelta(minutes=2)
    controller.tick()
    due_record = controller.records[0]
    assert due_record.state == "due"
    assert window.assistant.card is not None
    assert window.assistant.card.status == "due"
    assert window.assistant.card.body == due_record.title
    assert window.transcript.toPlainText() == "SYNTHETIC CANONICAL HISTORY"
    assert frames == []
    assert len(notices) == 1
    assert due_record.title not in notices[0][1]
    assert all(due_record.title not in event for event in window.events)
    assert not (profile / "satori.db").exists()
    controller.set_profile(tmp_path / "destination")
    window.assistant.clear()
    window._reminder_due(due_record)
    window._reminder_due(object())
    assert window.assistant.card is None
    assert len(notices) == 1
    assert frames == []


@pytest.mark.parametrize("width", [900, 1000])
def test_long_task_card_body_and_footer_do_not_overlap_at_720px(
    app: QApplication, factory: WindowFactory, tmp_path: Path, width: int
) -> None:
    window = factory(tmp_path / "profile")
    window.resize(width, 720)
    window.show()
    app.processEvents()
    window.transcript.setPlainText("SYNTHETIC LONG CONVERSATION\n" * 80)
    window.assistant.card = TaskCard(
        uuid4(),
        "SYNTHETIC LONG RESULT",
        "SYNTHETIC BODY LINE\n" * 60,
        "partial",
        "TEST",
        "calendar",
    )
    window.assistant.changed.emit()
    app.processEvents()
    view = window.task_card
    assert window.height() == 720
    assert view.body.height() >= view.body.minimumHeight()
    assert view.title.geometry().bottom() < view.body.geometry().top()
    assert view.body.geometry().bottom() < view.action.geometry().top()
    assert view.body.geometry().bottom() < view.source.geometry().top()
    assert view.action.geometry().bottom() < view.height()
    assert window.rect().contains(window.send_button.mapTo(window, QPoint(0, 0)))
    assert window.rect().contains(
        window.send_button.mapTo(window, window.send_button.rect().bottomRight())
    )


def test_mobile_address_fields_keep_their_editable_height_at_720px(
    app: QApplication, factory: WindowFactory, tmp_path: Path
) -> None:
    window = factory(tmp_path / "profile")
    window.resize(1000, 720)
    window.show()
    window._open_settings_section("mobile")
    app.processEvents()
    assert window.height() == 720
    panel = window.mobile_settings
    for field in (panel.bind_host, panel.advertised_host, panel.port):
        assert field.height() >= field.minimumSizeHint().height()
        assert window.rect().contains(field.mapTo(window, QPoint(0, 0)))
        assert window.rect().contains(field.mapTo(window, field.rect().bottomRight()))
