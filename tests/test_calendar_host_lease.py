"""Public metadata-only lease files; no accounts, native helper, model or permissions."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from satori.application.assistant.calendar_actions_contracts import CalendarActionScope
from satori.application.assistant.calendar_host_lease import CalendarHostLease
from satori.application.assistant.calendar_planning_contracts import CalendarPlanningError
from satori.application.assistant.contracts import (
    CalendarDescriptor,
    CalendarGrant,
    CalendarSourceRef,
    LocalPrincipal,
)
from satori.infrastructure.assistant.calendar_lease import (
    CalendarHostLeaseOwner,
    CalendarHostLeaseReader,
)

NOW = datetime(2026, 11, 3, 9, tzinfo=UTC)
PID = 12345


class LeaseClock:
    def __init__(self) -> None:
        self.now, self.tick = NOW, 1000.0

    def __call__(self) -> datetime:
        return self.now

    def monotonic(self) -> float:
        return self.tick


def public_lease() -> CalendarHostLease:
    principal = LocalPrincipal(os_uid=os.getuid(), profile_id=uuid4(), session_id=uuid4())
    resource = CalendarSourceRef(account_id="public-account", calendar_id="public-work")
    grant = CalendarGrant(
        principal=principal,
        grant_id=uuid4(),
        generation=1,
        resources=(resource,),
        issued_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    return CalendarHostLease(
        full_access=True,
        lease_id=uuid4(),
        request_id=uuid4(),
        scope=CalendarActionScope(
            identity_id=uuid4(),
            profile_id=principal.profile_id,
            session_id=uuid4(),
            generation=uuid4(),
            provider="macos_eventkit",
            account_id=resource.account_id,
            calendar_id=resource.calendar_id,
            timezone="Europe/Moscow",
        ),
        read_grant=grant,
        controller_generation=1,
        calendars=(
            CalendarDescriptor(
                source=resource,
                title="PUBLIC WORK",
                account_title="PUBLIC ACCOUNT",
                writable=True,
                allows_modification=True,
                supports_events=True,
                source_kind="caldav",
            ),
        ),
        catalog_revision=uuid4(),
        catalog_observed_at=NOW,
        issued_at=NOW,
        expires_at=NOW + timedelta(seconds=90),
        monotonic_deadline=1090.0,
        ui_pid=PID,
    )


def lease_ports(
    tmp_path: Path,
) -> tuple[CalendarHostLeaseOwner, CalendarHostLeaseReader, Path, LeaseClock]:
    tmp_path = tmp_path.resolve()
    tmp_path.chmod(0o700)
    clock, lease = LeaseClock(), public_lease()
    owner = CalendarHostLeaseOwner(
        tmp_path, clock=clock, monotonic=clock.monotonic, ui_pid=lambda: PID
    )
    path = owner.issue(lease)
    reader = CalendarHostLeaseReader(
        tmp_path, lease, clock=clock, monotonic=clock.monotonic, parent_pid=lambda: PID
    )
    return owner, reader, path, clock


def test_original_file_revocation_is_immediately_observed(tmp_path: Path) -> None:
    owner, reader, path, _ = lease_ports(tmp_path)
    reader.require()
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert b"PUBLIC WORK" in path.read_bytes()
    assert b'"events":' not in path.read_bytes()
    assert b"user_text" not in path.read_bytes()
    owner.revoke()
    assert not path.exists()
    with pytest.raises(CalendarPlanningError, match="calendar_host_lease_unavailable"):
        reader.require()


@pytest.mark.parametrize(
    "change", ["bytes", "mode", "hardlink", "replacement", "symlink", "directory_symlink"]
)
def test_worker_refuses_changed_material_or_filesystem_identity(
    tmp_path: Path, change: str
) -> None:
    _, reader, path, _ = lease_ports(tmp_path)
    reader.require()
    raw = path.read_bytes()
    if change == "bytes":
        path.write_bytes(raw + b"\n")
    elif change == "mode":
        path.chmod(0o644)
    elif change == "hardlink":
        os.link(path, tmp_path / "public-hardlink")
    elif change == "replacement":
        replacement = path.with_suffix(".new")
        replacement.write_bytes(raw)
        replacement.chmod(0o600)
        replacement.replace(path)
    elif change == "symlink":
        replacement = tmp_path / "public-copy"
        replacement.write_bytes(raw)
        replacement.chmod(0o600)
        path.unlink()
        path.symlink_to(replacement)
    else:
        destination = tmp_path / "moved-public-leases"
        path.parent.rename(destination)
        path.parent.symlink_to(destination, target_is_directory=True)
    with pytest.raises(CalendarPlanningError, match="calendar_host_lease_unavailable"):
        reader.require()


@pytest.mark.parametrize(
    "change", ["wall_expiry", "monotonic_expiry", "wall_rollback", "parent_dead"]
)
def test_both_deadlines_clock_order_and_ui_parent_are_live_fences(
    tmp_path: Path, change: str
) -> None:
    _, reader, _, clock = lease_ports(tmp_path)
    clock.now += timedelta(seconds=10)
    clock.tick += 10
    reader.require()
    if change == "wall_expiry":
        clock.now = NOW + timedelta(seconds=90)
    elif change == "monotonic_expiry":
        clock.tick = 1090
    elif change == "wall_rollback":
        clock.now = NOW + timedelta(seconds=5)
    else:
        reader = CalendarHostLeaseReader(
            tmp_path,
            reader.lease,
            clock=clock,
            monotonic=clock.monotonic,
            parent_pid=lambda: PID + 1,
        )
    with pytest.raises(CalendarPlanningError, match="calendar_host_lease_unavailable"):
        reader.require()


@pytest.mark.parametrize(
    ("field", "value"),
    [("full_access", 1), ("expires_at", NOW + timedelta(minutes=3)), ("request_id", UUID(int=0))],
)
def test_lease_dto_revalidates_copied_values(field: str, value: object) -> None:
    lease = public_lease()
    with pytest.raises(ValidationError):
        CalendarHostLease.model_validate(lease.model_copy(update={field: value}))
