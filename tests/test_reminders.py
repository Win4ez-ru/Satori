"""Private reminder persistence, idempotency and ambiguous notification outcomes."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from satori.application.assistant.reminder_contracts import (
    ReminderCreate,
    ReminderDeliveryReceipt,
    ReminderError,
)
from satori.application.assistant.reminders import ReminderManager
from satori.infrastructure.assistant.reminder_store import SQLiteReminderStore

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def _proposal() -> ReminderCreate:
    return ReminderCreate(
        request_id=uuid4(),
        title="SYNTHETIC TASK",
        due_at=NOW + timedelta(hours=1),
        timezone="Europe/Moscow",
    )


def test_restart_due_once_and_cancel_persists(tmp_path: Path) -> None:
    clock = Clock()
    path = tmp_path / "private" / "reminders.sqlite3"
    manager = ReminderManager(SQLiteReminderStore(path), clock=clock)
    receipt = manager.create(_proposal())
    assert receipt.status == "created"
    assert receipt.record.delivery_state == "not_requested"
    manager.close()
    clock.now += timedelta(hours=2)
    restored = ReminderManager(SQLiteReminderStore(path), clock=clock)
    assert len(restored.records) == 1
    assert len(restored.tick()) == 1
    assert restored.tick() == ()
    cancel_id = uuid4()
    cancelled = restored.cancel(receipt.record.reminder_id, cancel_id)
    assert cancelled.record.state == "cancelled"
    assert restored.cancel(receipt.record.reminder_id, cancel_id).status == "unchanged"
    restored.close()
    reopened = ReminderManager(SQLiteReminderStore(path), clock=clock)
    assert reopened.records[0].state == "cancelled"
    assert reopened.tick() == ()
    reopened.close()


def test_request_payload_binding_and_duplicate_clicks(tmp_path: Path) -> None:
    clock = Clock()
    manager = ReminderManager(
        SQLiteReminderStore(tmp_path / "private" / "reminders.sqlite3"), clock=clock
    )
    proposal = _proposal()
    first = manager.create(proposal)
    assert manager.create(proposal).record.reminder_id == first.record.reminder_id
    alias = proposal.model_copy(update={"request_id": uuid4()})
    assert manager.create(alias).status == "existing"
    assert len(manager.records) == 1
    with pytest.raises(ReminderError, match="reminder_request_conflict"):
        manager.create(proposal.model_copy(update={"title": "DIFFERENT"}))
    clock.now += timedelta(hours=2)
    assert manager.create(alias).status == "existing"
    manager.close()


def test_past_and_unbounded_future_are_rejected_without_writes(tmp_path: Path) -> None:
    manager = ReminderManager(
        SQLiteReminderStore(tmp_path / "private" / "reminders.sqlite3"), clock=Clock()
    )
    for due in [NOW, NOW - timedelta(seconds=1), NOW + timedelta(days=2000)]:
        with pytest.raises(ReminderError, match="reminder_time_not_future"):
            manager.create(_proposal().model_copy(update={"due_at": due}))
    assert manager.records == ()
    manager.close()


def test_private_store_rejects_symlinks_hardlinks_and_foreign_file(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    other = tmp_path / "other"
    other.write_text("UNRELATED")
    path = private / "reminders.sqlite3"
    path.symlink_to(other)
    with pytest.raises(ReminderError):
        SQLiteReminderStore(path)
    assert other.read_text() == "UNRELATED"
    path.unlink()
    os.link(other, path)
    with pytest.raises(ReminderError):
        SQLiteReminderStore(path)
    assert other.read_text() == "UNRELATED"
    path.unlink()
    path.write_text("UNRELATED")
    path.chmod(0o600)
    with pytest.raises(ReminderError):
        SQLiteReminderStore(path)
    assert path.read_text() == "UNRELATED"


def test_single_owner_lock_and_file_replacement(tmp_path: Path) -> None:
    path = tmp_path / "private" / "reminders.sqlite3"
    store = SQLiteReminderStore(path)
    with pytest.raises(ReminderError, match="reminder_store_in_use"):
        SQLiteReminderStore(path)
    path.rename(path.with_suffix(".original"))
    path.write_text("REPLACED")
    path.chmod(0o600)
    with pytest.raises(ReminderError, match="reminder_store_replaced"):
        store.list()
    store.close()
    assert path.read_text() == "REPLACED"


def test_restart_inflight_is_unknown_and_does_not_replay(tmp_path: Path) -> None:
    clock = Clock()
    path = tmp_path / "private" / "reminders.sqlite3"
    manager = ReminderManager(SQLiteReminderStore(path), clock=clock)
    record = manager.create(_proposal()).record
    request = manager.begin_delivery(record.reminder_id, "schedule")
    assert request.title == "Напоминание Сатори"
    manager.close()
    restored = ReminderManager(SQLiteReminderStore(path), clock=clock)
    assert restored.records[0].delivery_state == "unknown"
    with pytest.raises(ReminderError, match="reminder_delivery_reconcile_required"):
        restored.begin_delivery(record.reminder_id, "schedule")
    reconcile = restored.begin_delivery(record.reminder_id, "reconcile")
    receipt = ReminderDeliveryReceipt(
        attempt_id=reconcile.attempt_id,
        reminder_id=record.reminder_id,
        operation="reconcile",
        notification_identifier=reconcile.notification_identifier,
        payload_digest=reconcile.payload_digest,
        status="scheduled",
        observed_at=clock.now,
    )
    assert restored.accept_delivery(reconcile, receipt).delivery_state == "scheduled"
    with pytest.raises(ReminderError, match="reminder_delivery_stale"):
        restored.accept_delivery(request, receipt)
    restored.close()


def test_cancel_supersedes_pending_schedule_and_late_receipt(tmp_path: Path) -> None:
    clock = Clock()
    manager = ReminderManager(
        SQLiteReminderStore(tmp_path / "private" / "reminders.sqlite3"), clock=clock
    )
    record = manager.create(_proposal()).record
    schedule = manager.begin_delivery(record.reminder_id, "schedule")
    manager.cancel(record.reminder_id, uuid4())
    cancel = manager.begin_delivery(record.reminder_id, "cancel")
    old = ReminderDeliveryReceipt(
        attempt_id=schedule.attempt_id,
        reminder_id=record.reminder_id,
        operation="schedule",
        notification_identifier=schedule.notification_identifier,
        payload_digest=schedule.payload_digest,
        status="scheduled",
        observed_at=clock.now,
    )
    with pytest.raises(ReminderError, match="reminder_delivery_stale"):
        manager.accept_delivery(schedule, old)
    current = ReminderDeliveryReceipt(
        attempt_id=cancel.attempt_id,
        reminder_id=record.reminder_id,
        operation="cancel",
        notification_identifier=cancel.notification_identifier,
        payload_digest=cancel.payload_digest,
        status="cancelled",
        observed_at=clock.now,
    )
    assert manager.accept_delivery(cancel, current).delivery_state == "cancelled"
    manager.close()


def test_clock_rollback_is_not_a_due_delivery(tmp_path: Path) -> None:
    clock = Clock()
    manager = ReminderManager(
        SQLiteReminderStore(tmp_path / "private" / "reminders.sqlite3"), clock=clock
    )
    manager.create(_proposal())
    clock.now -= timedelta(seconds=1)
    with pytest.raises(ReminderError, match="reminder_clock_invalid"):
        manager.tick()
    manager.close()


def test_local_watchdog_cannot_claim_os_schedule() -> None:
    with pytest.raises(ValueError, match="reminder_native_receipt_required"):
        ReminderDeliveryReceipt(
            attempt_id=uuid4(),
            reminder_id=uuid4(),
            operation="schedule",
            notification_identifier="synthetic",
            payload_digest="a" * 64,
            status="scheduled",
            observed_at=NOW,
            platform="local_watchdog",
        )


def test_absent_readback_allows_only_explicit_schedule_and_cancel_is_idempotent(
    tmp_path: Path,
) -> None:
    manager = ReminderManager(
        SQLiteReminderStore(tmp_path / "private" / "reminders.sqlite3"), clock=Clock()
    )
    record = manager.create(_proposal()).record
    check = manager.begin_delivery(record.reminder_id, "reconcile")
    result = ReminderDeliveryReceipt(
        attempt_id=check.attempt_id,
        reminder_id=record.reminder_id,
        operation="reconcile",
        notification_identifier=check.notification_identifier,
        payload_digest=check.payload_digest,
        status="absent",
        observed_at=NOW,
    )
    assert manager.accept_delivery(check, result).delivery_state == "absent"
    assert manager.tick() == ()
    assert manager.records[0].delivery_state == "absent"
    assert manager.begin_delivery(record.reminder_id, "schedule").operation == "schedule"
    manager.close()
