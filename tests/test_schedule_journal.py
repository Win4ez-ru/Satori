"""Pending-import journal durability/identity checks; no Calendar or Qt calls."""

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from satori.application.assistant.contracts import CalendarSourceRef, LocalPrincipal
from satori.application.assistant.schedule import SchedulePlanCoordinator, parse_schedule_json
from satori.application.assistant.schedule_contracts import ScheduleError, SchedulePlan
from satori.desktop.schedule_journal import ScheduleJournal

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)
TARGET = CalendarSourceRef(account_id="synthetic-account", calendar_id="synthetic-work")


def plan(*, different_target: bool = False, different_rows: bool = False) -> SchedulePlan:
    principal = LocalPrincipal(os_uid=os.getuid(), profile_id=uuid4(), session_id=uuid4())
    schedule = parse_schedule_json(
        json.dumps(
            {
                "schema_version": 1,
                "time_zone": "Europe/Moscow",
                "shifts": [
                    {
                        "title": "Synthetic title never journaled"
                        + (" changed" if different_rows else ""),
                        "start": "2026-10-26T09:00:00+03:00",
                        "end": "2026-10-26T18:00:00+03:00",
                    }
                ],
            }
        )
    )
    target = (
        CalendarSourceRef(account_id="synthetic-account", calendar_id="other")
        if different_target
        else TARGET
    )
    return SchedulePlanCoordinator(principal, clock=lambda: NOW).prepare(
        schedule, target, 1, writable=True
    )


def test_constructor_and_pending_never_create_directory_or_file(tmp_path: Path) -> None:
    directory = tmp_path / "not-created"
    journal = ScheduleJournal(directory)
    assert journal.pending() is None
    assert not directory.exists()


def test_begin_is_private_atomic_metadata_only_and_survives_new_instance(tmp_path: Path) -> None:
    journal = ScheduleJournal(tmp_path)
    current = plan()
    journal.begin(current)
    path = tmp_path / "calendar-import-pending.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    payload = path.read_text()
    assert all(
        value not in payload
        for value in ("Synthetic title", "2026-10", "09:00", "18:00", "ocr", "photo")
    )
    assert set(json.loads(payload)) == {
        "schema_version",
        "plan_fingerprint",
        "target",
        "row_tokens",
        "principal",
        "state",
    }
    record = ScheduleJournal(tmp_path).pending()
    assert record is not None
    assert record.target == current.target
    assert record.row_tokens == tuple(row.owned_token for row in current.rows)
    assert record.plan_fingerprint == current.plan_fingerprint
    assert record.state == "pending"
    assert list(tmp_path.iterdir()) == [path]


def test_begin_never_replays_or_overwrites_even_identical_plan(tmp_path: Path) -> None:
    journal = ScheduleJournal(tmp_path)
    current = plan()
    journal.begin(current)
    before = (tmp_path / "calendar-import-pending.json").read_bytes()
    with pytest.raises(ScheduleError, match="schedule_journal_pending"):
        journal.begin(current)
    assert (tmp_path / "calendar-import-pending.json").read_bytes() == before


@pytest.mark.parametrize("changes", [{"different_target": True}, {"different_rows": True}])
def test_clear_rejects_unrelated_target_or_tokens(tmp_path: Path, changes: dict[str, bool]) -> None:
    journal = ScheduleJournal(tmp_path)
    journal.begin(plan())
    with pytest.raises(ScheduleError, match="schedule_journal_mismatch"):
        journal.clear(plan(**changes))
    assert journal.pending() is not None


def test_terminal_reconcile_in_new_session_can_clear_matching_target_and_tokens(
    tmp_path: Path,
) -> None:
    journal = ScheduleJournal(tmp_path)
    first, second = plan(), plan()
    assert first.plan_fingerprint != second.plan_fingerprint
    journal.begin(first)
    journal.clear(second)
    assert journal.pending() is None
    assert list(tmp_path.iterdir()) == []


def test_symlink_record_is_never_followed_or_replaced(tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.write_text("preserve me")
    link = tmp_path / "calendar-import-pending.json"
    link.symlink_to(victim)
    journal = ScheduleJournal(tmp_path)
    for action in (journal.pending, lambda: journal.begin(plan()), lambda: journal.clear(plan())):
        with pytest.raises(ScheduleError):
            action()
        assert link.is_symlink()
        assert victim.read_text() == "preserve me"


def test_symlink_directory_does_not_create_journal_in_target(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    journal = ScheduleJournal(link)
    with pytest.raises(ScheduleError):
        journal.begin(plan())
    assert not list(target.iterdir())


def test_existing_malformed_record_is_fail_closed_and_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "calendar-import-pending.json"
    path.write_bytes(b"{not-json}")
    path.chmod(0o600)
    journal = ScheduleJournal(tmp_path)
    for action in (journal.pending, lambda: journal.begin(plan()), lambda: journal.clear(plan())):
        with pytest.raises(ScheduleError, match="schedule_journal_invalid"):
            action()
    assert path.read_bytes() == b"{not-json}"


@pytest.mark.parametrize("mode", [0o644, 0o666])
def test_broad_permissions_fail_closed(tmp_path: Path, mode: int) -> None:
    journal = ScheduleJournal(tmp_path)
    journal.begin(plan())
    (tmp_path / "calendar-import-pending.json").chmod(mode)
    with pytest.raises(ScheduleError, match="schedule_journal_invalid"):
        journal.pending()


@pytest.mark.parametrize("bad", ["schema_version", "row_tokens", "state", "extra", "duplicate"])
def test_invalid_metadata_schema_is_not_admitted(tmp_path: Path, bad: str) -> None:
    journal = ScheduleJournal(tmp_path)
    journal.begin(plan())
    path = tmp_path / "calendar-import-pending.json"
    value: dict[str, Any] = json.loads(path.read_bytes())
    if bad == "schema_version":
        value["schema_version"] = True
    elif bad == "row_tokens":
        value["row_tokens"] = ["0" * 64, "0" * 64]
    elif bad == "state":
        value["state"] = "committed"
    elif bad == "extra":
        value["title"] = "must not be stored"
    raw = json.dumps(value)
    if bad == "duplicate":
        raw = raw[:-1] + ',"schema_version":1}'
    path.write_text(raw)
    with pytest.raises(ScheduleError, match="schedule_journal_invalid"):
        journal.pending()


def test_directory_and_fifo_records_rejected_without_reading(tmp_path: Path) -> None:
    record = tmp_path / "calendar-import-pending.json"
    record.mkdir()
    with pytest.raises(ScheduleError):
        ScheduleJournal(tmp_path).pending()
    record.rmdir()
    os.mkfifo(record, 0o600)
    with pytest.raises(ScheduleError):
        ScheduleJournal(tmp_path).pending()


def test_clear_io_failure_preserves_unresolved_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = ScheduleJournal(tmp_path)
    current = plan()
    journal.begin(current)
    path = tmp_path / "calendar-import-pending.json"
    before = path.read_bytes()

    def fail_sync(descriptor: int) -> None:
        raise OSError("synthetic_io_failure")

    monkeypatch.setattr(os, "fsync", fail_sync)
    with pytest.raises(ScheduleError, match="schedule_journal_unavailable"):
        journal.clear(current)
    assert path.read_bytes() == before


def test_begin_write_failure_never_creates_partial_canonical_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "write", lambda descriptor, data: 0)
    with pytest.raises(ScheduleError, match="schedule_journal_unavailable"):
        ScheduleJournal(tmp_path).begin(plan())
    assert list(tmp_path.iterdir()) == []


def test_restore_unknown_never_issues_fresh_create_permit() -> None:
    current = plan()
    coordinator = SchedulePlanCoordinator(current.principal, clock=lambda: NOW)
    schedule = parse_schedule_json(
        '{"schema_version":1,"time_zone":"UTC","shifts":[{"title":"Fixture","start":"2026-10-26T09:00:00Z","end":"2026-10-26T17:00:00Z"}]}'
    )
    frozen = coordinator.prepare(schedule, TARGET, 1, writable=True)
    coordinator.restore_unknown(frozen)
    assert str(coordinator.state) == "unknown"
    with pytest.raises(ScheduleError):
        coordinator.consume(frozen, principal=current.principal, generation=1, target=TARGET)
    with pytest.raises(ScheduleError):
        coordinator.prepare(schedule, TARGET, 1, writable=True)
