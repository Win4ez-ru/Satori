"""Observe committed action identity after uncertainty without replay or a read-side write."""

import hashlib
import os
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from satori.application.assistant.reminder_contracts import ReminderCreate, ReminderError
from satori.application.assistant.reminders import ReminderManager
from satori.desktop.reminder_controller import ReminderController
from satori.infrastructure.assistant.reminder_store import SQLiteReminderStore

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


@pytest.fixture
def app() -> Iterator[QApplication]:
    current = QApplication.instance() or QApplication([])
    assert isinstance(current, QApplication)
    yield current
    current.processEvents()


def proposal() -> ReminderCreate:
    return ReminderCreate(
        request_id=uuid4(),
        title="SYNTHETIC",
        due_at=NOW + timedelta(hours=1),
        timezone="Europe/Moscow",
    )


def state(path: Path) -> tuple[str, int, int]:
    with sqlite3.connect(path) as connection:
        requests = connection.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        audit = connection.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    return hashlib.sha256(path.read_bytes()).hexdigest(), requests, audit


@pytest.mark.parametrize("cancel", [False, True])
def test_committed_but_unknown_action_can_be_observed_after_restart(
    tmp_path: Path, cancel: bool
) -> None:
    path = tmp_path / "private/reminders.sqlite3"
    store = SQLiteReminderStore(path)
    owner = ReminderManager(store, clock=lambda: NOW)
    value = proposal()
    existing = owner.create(value).record if cancel else None
    request_id = uuid4() if cancel else value.request_id
    original_check = store._check
    fired = False

    def fail_once_after_commit() -> None:
        nonlocal fired
        original_check()
        if (
            not fired
            and not store._db.in_transaction
            and store._db.execute(
                "SELECT 1 FROM requests WHERE id=?", (str(request_id),)
            ).fetchone()
        ):
            fired = True
            raise ReminderError("reminder_store_replaced")

    def action() -> None:
        if cancel:
            assert existing is not None
            owner.cancel(existing.reminder_id, request_id)
        else:
            owner.create(value)

    with (
        patch.object(store, "_check", side_effect=fail_once_after_commit),
        pytest.raises(ReminderError, match="reminder_store_outcome_unknown"),
    ):
        action()
    assert fired
    owner.close()
    before = state(path)
    reopened = ReminderManager(
        SQLiteReminderStore(path, read_only=True), clock=lambda: NOW, recover_inflight=False
    )
    receipt = reopened.reconcile_action(request_id)
    assert receipt is not None
    assert receipt.request_id == request_id
    assert receipt.operation == ("cancel" if cancel else "create")
    assert receipt.status == ("unchanged" if cancel else "existing")
    assert receipt.record.state == ("cancelled" if cancel else "scheduled")
    assert reopened.reconcile_action(uuid4()) is None
    reopened.close()
    assert state(path) == before


def test_creation_alias_and_cancel_request_keep_their_distinct_binding(tmp_path: Path) -> None:
    store = SQLiteReminderStore(tmp_path / "private/reminders.sqlite3")
    owner = ReminderManager(store, clock=lambda: NOW)
    value = proposal()
    record = owner.create(value).record
    alias = value.model_copy(update={"request_id": uuid4()})
    owner.create(alias)
    cancel_id = uuid4()
    owner.cancel(record.reminder_id, cancel_id)
    for request_id in (value.request_id, alias.request_id, cancel_id):
        result = owner.reconcile_action(request_id)
        assert result is not None
        assert result.request_id == request_id
        assert result.record.reminder_id == record.reminder_id
        assert result.record.state == "cancelled"
        assert result.operation == ("cancel" if request_id == cancel_id else "create")
    owner.close()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("digest", "0" * 64),
        ("operation", "delete"),
        pytest.param("reminder_id", str(uuid4()), id="foreign-reminder"),
    ],
)
def test_malformed_request_binding_is_never_confirmed(
    tmp_path: Path, column: str, value: str
) -> None:
    path = tmp_path / "private/reminders.sqlite3"
    store = SQLiteReminderStore(path)
    owner = ReminderManager(store, clock=lambda: NOW)
    item = proposal()
    owner.create(item)
    # Closed test-authored column allowlist, not caller-controlled SQL.
    assert column in {"digest", "operation", "reminder_id"}
    store._db.execute("PRAGMA foreign_keys=OFF")
    store._db.execute(f"UPDATE requests SET {column}=? WHERE id=?", (value, str(item.request_id)))
    before = state(path)
    with pytest.raises(ReminderError, match="reminder_store_request_invalid"):
        owner.reconcile_action(item.request_id)
    assert state(path) == before
    owner.close()


def test_owner_change_and_cancel_record_contradiction_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "private/reminders.sqlite3"
    store = SQLiteReminderStore(path)
    owner = ReminderManager(store, clock=lambda: NOW)
    item = proposal()
    record = owner.create(item).record
    false_cancel = uuid4()
    digest = hashlib.sha256(f"cancel:{record.reminder_id}".encode()).hexdigest()
    store._db.execute(
        "INSERT INTO requests VALUES (?,?,?,?)",
        (
            str(false_cancel),
            digest,
            str(record.reminder_id),
            "cancel",
        ),
    )
    with pytest.raises(ReminderError, match="reminder_store_request_invalid"):
        owner.reconcile_action(false_cancel)
    store._db.execute("UPDATE meta SET value=? WHERE name='owner_id'", (str(uuid4()),))
    with pytest.raises(ReminderError, match="reminder_store_owner_invalid"):
        owner.reconcile_action(item.request_id)
    owner.close()


def test_reconciliation_is_a_point_lookup_not_a_catalog_scan(tmp_path: Path) -> None:
    store = SQLiteReminderStore(tmp_path / "private/reminders.sqlite3")
    owner = ReminderManager(store, clock=lambda: NOW)
    item = proposal()
    owner.create(item)
    store._db.execute("INSERT INTO reminders VALUES (?,?)", (str(uuid4()), "UNRELATED_INVALID"))
    with patch.object(store, "list", side_effect=AssertionError("catalog must not be read")):
        result = owner.reconcile_action(item.request_id)
        assert result is not None
        assert result.request_id == item.request_id
        assert owner.reconcile_action(uuid4()) is None
    owner.close()


@pytest.mark.parametrize("value", [UUID(int=0), "not-a-uuid"])
def test_invalid_request_id_is_rejected(tmp_path: Path, value: object) -> None:
    owner = ReminderManager(
        SQLiteReminderStore(tmp_path / "private/reminders.sqlite3"), clock=lambda: NOW
    )
    with pytest.raises(ReminderError, match="reminder_input_invalid"):
        owner.reconcile_action(cast(UUID, value))
    owner.close()


def test_read_only_open_never_creates_directories_db_or_missing_lock(tmp_path: Path) -> None:
    path = tmp_path / "not-created/reminders.sqlite3"
    with pytest.raises(ReminderError):
        SQLiteReminderStore(path, read_only=True)
    assert not list(tmp_path.iterdir())
    store = SQLiteReminderStore(path)
    store.close()
    lock = path.with_name(path.name + ".lock")
    lock.unlink()
    before = path.read_bytes()
    with pytest.raises(ReminderError):
        SQLiteReminderStore(path, read_only=True)
    assert not lock.exists()
    assert path.read_bytes() == before


def test_disabled_controller_reads_only_existing_action_without_due_or_recovery(
    app: QApplication, tmp_path: Path
) -> None:
    path = tmp_path / "assistant/reminders.sqlite3"
    store = SQLiteReminderStore(path)
    owner = ReminderManager(store, clock=lambda: NOW)
    item = proposal()
    created = owner.create(item)
    owner.begin_delivery(created.record.reminder_id, "schedule")
    owner.close()
    before = state(path)
    controller = ReminderController(tmp_path, clock=lambda: NOW + timedelta(hours=2))
    due: list[object] = []
    controller.due.connect(due.append)
    result = controller.reconcile_action(item.request_id)
    assert result is not None
    assert result.record.state == "scheduled"
    assert result.record.delivery_state == "submitting"
    assert not controller.enabled
    assert controller.records == ()
    assert due == []
    assert state(path) == before
    controller.shutdown()


def test_controller_unknown_and_other_profile_never_create_or_replay(
    app: QApplication, tmp_path: Path
) -> None:
    empty = tmp_path / "empty"
    controller = ReminderController(empty, clock=lambda: NOW)
    assert controller.reconcile_action(uuid4()) is None
    assert not empty.exists()
    assert controller.create("SYNTHETIC", NOW + timedelta(hours=1), "Europe/Moscow") is not None
    creation = controller.last_receipt
    assert creation is not None
    cancel_id = uuid4()
    controller.cancel(creation.record.reminder_id, cancel_id)
    before = state(controller.store_path)
    result = controller.reconcile_action(cancel_id)
    assert result is not None
    assert result.operation == "cancel"
    assert state(controller.store_path) == before
    controller.set_profile(tmp_path / "other")
    assert controller.reconcile_action(cancel_id) is None
    assert not (tmp_path / "other").exists()
    controller.shutdown()


def test_active_controller_refreshes_reconciled_unknown_creation_without_replay(
    app: QApplication, tmp_path: Path
) -> None:
    controller = ReminderController(tmp_path, clock=lambda: NOW)
    assert controller.enable()
    manager = controller._manager
    assert manager is not None
    store = manager.store
    assert isinstance(store, SQLiteReminderStore)
    item = proposal()
    original_check = store._check
    fired = False

    def fail_once_after_commit() -> None:
        nonlocal fired
        original_check()
        if (
            not fired
            and not store._db.in_transaction
            and store._db.execute(
                "SELECT 1 FROM requests WHERE id=?", (str(item.request_id),)
            ).fetchone()
        ):
            fired = True
            raise ReminderError("reminder_store_replaced")

    with patch.object(store, "_check", side_effect=fail_once_after_commit):
        assert controller.create(item.title, item.due_at, item.timezone, item.request_id) is None
    assert controller.diagnostic == "reminder_store_outcome_unknown"
    assert list(controller.records) == []
    before = state(controller.store_path)
    result = controller.reconcile_action(item.request_id)
    assert result is not None
    assert controller.records == (result.record,)
    assert controller.diagnostic is None
    assert state(controller.store_path) == before
    controller.shutdown()
