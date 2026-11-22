"""Native process protocol using synthetic helper applications, never UserNotifications."""

import json
import os
import plistlib
import sys
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from uuid import uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from satori.application.assistant.reminder_contracts import (
    ReminderDeliveryReceipt,
    ReminderDeliveryRequest,
)
from satori.desktop.reminder_delivery import NativeReminderDeliveryPort


@pytest.fixture
def app() -> Iterator[QApplication]:
    current = QApplication.instance() or QApplication([])
    assert isinstance(current, QApplication)
    yield current
    current.processEvents()


def helper(tmp_path: Path, mode: str) -> Path:
    target = tmp_path / "Synthetic.app"
    contents = target / "Contents"
    binary = contents / "MacOS" / "SatoriReminderNotifications"
    binary.parent.mkdir(parents=True)
    (contents / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "ai.satori.desktop.reminder-notifications",
                "CFBundleExecutable": "SatoriReminderNotifications",
            }
        )
    )
    program = f"""#!{sys.executable}
import json, sys, time
from pathlib import Path
from datetime import datetime, UTC
request=json.loads(sys.stdin.readline())
mode={json.dumps(mode)}
if mode=="hold":
 Path(__file__).with_suffix(".started").touch()
 time.sleep(30)
if mode=="exit": sys.exit(3)
if request["operation"] in {{"status","authorize"}}:
 print(json.dumps({{"operation":request["operation"],"status":"not_determined","observed_at":datetime.now(UTC).isoformat()}}))
else:
 keys=("attempt_id","reminder_id","operation","notification_identifier","payload_digest")
 response={{key:request[key] for key in keys}}
 response.update(status="scheduled",observed_at=datetime.now(UTC).isoformat(),platform="native_os")
 if mode=="stale": response["attempt_id"]="00000000-0000-0000-0000-000000000000"
 if mode=="overflow": print("x"*20000)
 elif mode=="duplicate": print('{{"status":"scheduled","status":"cancelled"}}')
 else: print(json.dumps(response))
"""
    binary.write_text(program)
    binary.chmod(0o700)
    return target


def pump(app: QApplication, done: Sequence[object]) -> None:
    deadline = monotonic() + 5
    while not done and monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    assert done


def close_adapter(app: QApplication, adapter: NativeReminderDeliveryPort) -> None:
    finished: list[object] = []
    adapter.shutdown_finished.connect(lambda: finished.append(True))
    adapter.shutdown()
    if not adapter.shutdown_complete:
        pump(app, finished)
    assert adapter.shutdown_complete
    app.processEvents()


def request() -> ReminderDeliveryRequest:
    reminder_id = uuid4()
    return ReminderDeliveryRequest(
        owner_id=uuid4(),
        generation=uuid4(),
        attempt_id=uuid4(),
        reminder_id=reminder_id,
        operation="schedule",
        notification_identifier=f"satori.reminder.{reminder_id}",
        due_at=datetime.now(UTC) + timedelta(hours=1),
        payload_digest="a" * 64,
    )


def test_no_helper_or_authorization_on_construction(app: QApplication, tmp_path: Path) -> None:
    adapter = NativeReminderDeliveryPort(tmp_path / "Missing.app")
    assert adapter.status == "unknown"
    assert not list(tmp_path.iterdir())
    states: list[object] = []
    adapter.authorization_changed.connect(states.append)
    adapter.refresh_authorization()
    assert states == ["unavailable"]
    close_adapter(app, adapter)


def test_synthetic_native_normal_exit_and_correlated_receipt(
    app: QApplication, tmp_path: Path
) -> None:
    adapter = NativeReminderDeliveryPort(helper(tmp_path, "normal"))
    states: list[object] = []
    adapter.authorization_changed.connect(states.append)
    adapter.refresh_authorization()
    pump(app, states)
    assert states == ["not_determined"]
    receipts: list[ReminderDeliveryReceipt] = []
    proposal = request()
    adapter.submit(proposal, receipts.append)
    pump(app, receipts)
    assert receipts[0].status == "scheduled"
    assert receipts[0].attempt_id == proposal.attempt_id
    close_adapter(app, adapter)


@pytest.mark.parametrize("mode", ["exit", "stale", "overflow", "duplicate"])
def test_invalid_process_result_is_unknown_and_never_retried(
    app: QApplication, tmp_path: Path, mode: str
) -> None:
    adapter = NativeReminderDeliveryPort(helper(tmp_path, mode))
    receipts: list[ReminderDeliveryReceipt] = []
    proposal = request()
    adapter.submit(proposal, receipts.append)
    pump(app, receipts)
    assert len(receipts) == 1
    assert receipts[0].status == "unknown"
    assert receipts[0].platform == "local_watchdog"
    assert receipts[0].attempt_id == proposal.attempt_id
    close_adapter(app, adapter)


def test_shutdown_kills_and_retires_active_process_before_completion(
    app: QApplication, tmp_path: Path
) -> None:
    target = helper(tmp_path, "hold")
    adapter = NativeReminderDeliveryPort(target)
    receipts: list[ReminderDeliveryReceipt] = []
    adapter.submit(request(), receipts.append)
    marker = target / "Contents" / "MacOS" / "SatoriReminderNotifications.started"
    deadline = monotonic() + 5
    while not marker.exists() and monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    assert marker.exists()
    finished: list[object] = []
    adapter.shutdown_finished.connect(lambda: finished.append(True))
    adapter.shutdown()
    pump(app, finished)
    assert adapter.shutdown_complete
    assert len(finished) == 1
    assert len(receipts) == 1
    assert receipts[0].status == "unknown"
