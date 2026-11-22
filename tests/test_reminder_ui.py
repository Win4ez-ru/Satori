"""Reminder UI/controller with synthetic clocks and delivery ports; no OS notifications."""

import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate, QTime
from PySide6.QtWidgets import QApplication

from satori.application.assistant.reminder_contracts import (
    ReminderDeliveryReceipt,
    ReminderDeliveryRequest,
)
from satori.desktop.reminder_controller import ReminderController
from satori.desktop.reminder_ui import ReminderPanel

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = NOW
        self.mono = 1.0

    def __call__(self) -> datetime:
        return self.now


class Port:
    def __init__(self) -> None:
        self.requests: list[ReminderDeliveryRequest] = []
        self.callbacks: list[Callable[[ReminderDeliveryReceipt], None]] = []

    def submit(
        self, request: ReminderDeliveryRequest, complete: Callable[[ReminderDeliveryReceipt], None]
    ) -> None:
        self.requests.append(request)
        self.callbacks.append(complete)

    def reply(self, index: int = 0) -> None:
        request = self.requests[index]
        self.callbacks[index](
            ReminderDeliveryReceipt(
                attempt_id=request.attempt_id,
                reminder_id=request.reminder_id,
                operation=request.operation,
                notification_identifier=request.notification_identifier,
                payload_digest=request.payload_digest,
                status="cancelled" if request.operation == "cancel" else "scheduled",
                observed_at=NOW,
            )
        )


@pytest.fixture
def app() -> Iterator[QApplication]:
    current = QApplication.instance() or QApplication([])
    assert isinstance(current, QApplication)
    yield current
    current.processEvents()


def test_construction_and_missing_restore_do_not_create_store(
    app: QApplication, tmp_path: Path
) -> None:
    controller = ReminderController(tmp_path)
    panel = ReminderPanel(tmp_path, controller=controller)
    assert not list(tmp_path.iterdir())
    assert not controller.restore_existing()
    assert not list(tmp_path.iterdir())
    assert panel.list.count() == 0
    controller.shutdown()
    panel.deleteLater()


def test_create_due_restart_and_cancel_real_ui(app: QApplication, tmp_path: Path) -> None:
    clock = Clock()
    controller = ReminderController(tmp_path, clock=clock)
    due: list[object] = []
    controller.due.connect(due.append)
    panel = ReminderPanel(tmp_path, controller=controller)
    panel.title.setText("SYNTHETIC PRIVATE TASK")
    panel.day.setDate(QDate(2026, 10, 31))
    panel.time.setTime(QTime(16, 0))
    panel.timezone.setText("Europe/Moscow")
    panel.create_button.click()
    assert panel.list.count() == 1
    assert controller.records[0].delivery_state == "not_requested"
    assert not controller.notification_available
    controller.shutdown()
    restored = ReminderController(tmp_path, clock=clock)
    assert restored.restore_existing()
    assert len(restored.records) == 1
    clock.now += timedelta(hours=2)
    observed: list[object] = []
    restored.due.connect(observed.append)
    restored.tick()
    restored.tick()
    assert len(observed) == 1
    restored.cancel(restored.records[0].reminder_id)
    assert restored.records[0].state == "cancelled"
    restored.shutdown()
    panel.deleteLater()


def test_only_native_callback_can_confirm_schedule_and_timeout_never_replays(
    app: QApplication, tmp_path: Path
) -> None:
    clock, port = Clock(), Port()
    controller = ReminderController(
        tmp_path, clock=clock, monotonic_clock=lambda: clock.mono, delivery=port
    )
    receipt = controller.create("SYNTHETIC", NOW + timedelta(hours=1), "Europe/Moscow", uuid4())
    assert receipt is not None
    assert str(controller.records[0].delivery_state) == "submitting"
    assert port.requests[0].title == "Напоминание Сатори"
    port.reply()
    app.processEvents()
    assert str(controller.records[0].delivery_state) == "scheduled"
    controller.cancel(receipt.record.reminder_id)
    assert len(port.requests) == 2
    clock.mono += 16
    controller.tick()
    assert controller.records[0].delivery_state == "unknown"
    port.reply(1)
    app.processEvents()
    assert controller.records[0].delivery_state == "unknown"
    assert len(port.requests) == 2
    controller.shutdown()


def test_profile_switch_fences_late_callback(app: QApplication, tmp_path: Path) -> None:
    port = Port()
    controller = ReminderController(tmp_path / "one", clock=Clock(), delivery=port)
    assert controller.create("ONE", NOW + timedelta(hours=1), "Europe/Moscow") is not None
    controller.set_profile(tmp_path / "two")
    port.reply()
    app.processEvents()
    assert controller.records == ()
    assert not controller.enabled
    controller.shutdown()
