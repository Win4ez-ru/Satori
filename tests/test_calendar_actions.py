"""Create-only approval/dispatch/restart contracts using public synthetic events."""

import asyncio
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from satori.application.assistant.calendar_actions import CalendarActionOwner
from satori.application.assistant.calendar_actions_contracts import (
    CalendarActionCatalog,
    CalendarActionError,
    CalendarActionRecord,
    CalendarActionScope,
    CalendarCreateInput,
)
from satori.infrastructure.assistant.calendar_actions_store import SQLiteCalendarActionStore
from tests.calendar_actions_fixture import FixtureCalendarActionProvider, FixtureCalendarEvent

NOW = datetime(2026, 11, 3, 9, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def guard() -> None:
    pass


def scope() -> CalendarActionScope:
    return CalendarActionScope(
        identity_id=uuid4(),
        profile_id=uuid4(),
        session_id=uuid4(),
        generation=uuid4(),
        provider="synthetic_fixture",
        account_id="public-account",
        calendar_id="public-work",
        timezone="Europe/Moscow",
    )


def catalog(bound: CalendarActionScope, clock: Clock) -> CalendarActionCatalog:
    return CalendarActionCatalog(
        scope=bound,
        revision=uuid4(),
        observed_at=clock.now,
        full_access=True,
        writable=True,
        supports_events=True,
        calendar_title="SYNTHETIC WORK",
        account_title="SYNTHETIC ACCOUNT",
    )


def command() -> CalendarCreateInput:
    return CalendarCreateInput(
        request_id=uuid4(),
        context_digest="a" * 64,
        title="PUBLIC SYNTHETIC MEETING",
        starts_at=NOW + timedelta(days=1),
        ends_at=NOW + timedelta(days=1, hours=1),
        timezone="Europe/Moscow",
    )


def setup(
    tmp_path: Path,
) -> tuple[
    Clock,
    CalendarActionScope,
    CalendarActionCatalog,
    CalendarActionOwner,
    FixtureCalendarActionProvider,
    Path,
]:
    clock, bound = Clock(), scope()
    observed = catalog(bound, clock)
    provider = FixtureCalendarActionProvider(clock)
    path = tmp_path / "private-actions" / "calendar.sqlite3"
    owner = CalendarActionOwner(SQLiteCalendarActionStore(path), provider, clock=clock)
    return clock, bound, observed, owner, provider, path


def approved(
    owner: CalendarActionOwner,
    bound: CalendarActionScope,
    observed: CalendarActionCatalog,
    value: CalendarCreateInput | None = None,
) -> CalendarActionRecord:
    proposal = owner.propose(value or command(), scope=bound, catalog=observed)
    return owner.approve(
        proposal.proposal.command.request_id,
        proposal.proposal.review_digest,
        scope=bound,
        catalog=observed,
    )


def dispatch(
    owner: CalendarActionOwner,
    record: CalendarActionRecord,
    bound: CalendarActionScope,
    observed: CalendarActionCatalog,
) -> CalendarActionRecord:
    assert record.approval is not None
    return asyncio.run(
        owner.dispatch(
            record.proposal.command.request_id,
            record.approval.approval_id,
            scope=bound,
            catalog=observed,
            precondition_guard=guard,
        )
    )


def test_review_is_effect_free_and_claim_precedes_single_effect(tmp_path: Path) -> None:
    _, bound, observed, owner, provider, _ = setup(tmp_path)
    record = approved(owner, bound, observed)
    assert provider.create_calls == provider.effects == 0

    def ready() -> None:
        claimed = owner.store.get(record.proposal.command.request_id)
        assert claimed is not None
        assert claimed.state == "dispatch_claimed"
        assert claimed.attempt is not None

    provider.on_ready = ready
    created = dispatch(owner, record, bound, observed)
    assert created.state == "created"
    assert created.receipt is not None
    assert created.receipt.fresh_store_verified
    assert not created.receipt.remote_sync_verified
    assert dispatch(owner, record, bound, observed).state == "created"
    assert provider.create_calls == provider.effects == 1
    owner.close()


def test_request_payload_and_review_digest_cannot_be_changed(tmp_path: Path) -> None:
    _, bound, observed, owner, _, _ = setup(tmp_path)
    original = command()
    proposal = owner.propose(original, scope=bound, catalog=observed)
    assert owner.propose(original, scope=bound, catalog=observed) == proposal
    for changed in (
        original.model_copy(update={"title": "OTHER"}),
        original.model_copy(update={"context_digest": "b" * 64}),
    ):
        with pytest.raises(CalendarActionError, match="request_conflict"):
            owner.propose(changed, scope=bound, catalog=observed)
    with pytest.raises(CalendarActionError, match="review_required"):
        owner.approve(original.request_id, "0" * 64, scope=bound, catalog=observed)
    owner.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"title": "unsafe\nline"},
        {"title": " unsafe"},
        {"title": "\u202eunsafe"},
        {"timezone": "unknown/zone"},
        {"starts_at": NOW.replace(tzinfo=None)},
        {"starts_at": NOW + timedelta(microseconds=1)},
        {"starts_at": NOW + timedelta(days=2)},
        {"ends_at": NOW + timedelta(days=10)},
        {"request_id": UUID(int=0)},
        {"context_digest": "not-a-digest"},
        {"attendees": ["not-allowed@example.invalid"]},
        {"recurrence": "daily"},
    ],
)
def test_inputs_are_exact_bounded_and_create_only(changes: dict[str, object]) -> None:
    value = command().model_dump() | changes
    with pytest.raises(ValidationError):
        CalendarCreateInput.model_validate(value)


def test_no_approval_or_default_real_write_gate_reaches_provider(tmp_path: Path) -> None:
    _, bound, observed, owner, provider, _ = setup(tmp_path)
    proposal = owner.propose(command(), scope=bound, catalog=observed)
    with pytest.raises(CalendarActionError, match="review_required"):
        asyncio.run(
            owner.dispatch(
                proposal.proposal.command.request_id,
                uuid4(),
                scope=bound,
                catalog=observed,
                precondition_guard=guard,
            )
        )
    record = owner.approve(
        proposal.proposal.command.request_id,
        proposal.proposal.review_digest,
        scope=bound,
        catalog=observed,
    )
    provider.external_effects = True
    with pytest.raises(CalendarActionError, match="real_writes_disabled"):
        dispatch(owner, record, bound, observed)
    assert provider.create_calls == provider.effects == 0
    assert owner.store.get(record.proposal.command.request_id) == record
    owner.close()


@pytest.mark.parametrize(
    "field",
    [
        "identity_id",
        "profile_id",
        "session_id",
        "generation",
        "calendar_id",
        "account_id",
        "timezone",
    ],
)
def test_scope_changes_deny_before_claim(tmp_path: Path, field: str) -> None:
    _, bound, observed, owner, provider, _ = setup(tmp_path)
    record = approved(owner, bound, observed)
    changed = bound.model_copy(
        update={
            field: uuid4()
            if (field.endswith("_id") and field not in {"calendar_id", "account_id"})
            or field == "generation"
            else ("UTC" if field == "timezone" else "different")
        }
    )
    with pytest.raises(CalendarActionError):
        dispatch(owner, record, changed, observed)
    assert provider.create_calls == provider.effects == 0
    owner.close()


def test_stale_catalog_and_changed_catalog_revision_deny(tmp_path: Path) -> None:
    clock, bound, observed, owner, provider, _ = setup(tmp_path)
    record = approved(owner, bound, observed)
    changed = observed.model_copy(update={"revision": uuid4()})
    with pytest.raises(CalendarActionError):
        dispatch(owner, record, bound, changed)
    clock.now += timedelta(minutes=6)
    with pytest.raises(CalendarActionError):
        dispatch(owner, record, bound, observed)
    assert provider.effects == 0
    owner.close()


def test_expired_approval_and_revoke_do_not_renew_effect_authority(tmp_path: Path) -> None:
    clock, bound, observed, owner, provider, _ = setup(tmp_path)
    record = approved(owner, bound, observed)
    clock.now += timedelta(minutes=2)
    with pytest.raises(CalendarActionError, match="approval_expired"):
        dispatch(owner, record, bound, observed)
    owner.revoke()
    clock.now = NOW + timedelta(minutes=2, seconds=1)
    with pytest.raises(CalendarActionError):
        dispatch(owner, record, bound, observed)
    assert provider.effects == 0
    owner.close()


def test_guard_revocation_before_claim_has_no_effect(tmp_path: Path) -> None:
    _, bound, observed, owner, provider, _ = setup(tmp_path)
    record = approved(owner, bound, observed)
    assert record.approval is not None

    def revoked() -> None:
        raise ValueError("private runtime data must not escape")

    with pytest.raises(CalendarActionError, match="revoked"):
        asyncio.run(
            owner.dispatch(
                record.proposal.command.request_id,
                record.approval.approval_id,
                scope=bound,
                catalog=observed,
                precondition_guard=revoked,
            )
        )
    assert provider.create_calls == provider.effects == 0
    assert owner.store.get(record.proposal.command.request_id) == record
    with pytest.raises(CalendarActionError, match="revoked"):
        dispatch(owner, record, bound, observed)
    owner.close()


def test_revoke_between_claim_and_before_effect_rejects_without_write(tmp_path: Path) -> None:
    _, bound, observed, owner, provider, _ = setup(tmp_path)
    record = approved(owner, bound, observed)
    provider.on_ready = owner.revoke
    result = dispatch(owner, record, bound, observed)
    assert result.state == "rejected"
    assert provider.create_calls == 1
    assert provider.effects == 0
    owner.close()


def test_expiry_while_provider_waits_does_not_write(tmp_path: Path) -> None:
    clock, bound, observed, owner, provider, _ = setup(tmp_path)
    record = approved(owner, bound, observed)
    provider.on_ready = lambda: setattr(clock, "now", NOW + timedelta(seconds=31))
    result = dispatch(owner, record, bound, observed)
    assert result.state == "rejected"
    assert provider.effects == 0
    owner.close()


def test_unknown_after_effect_restarts_and_reconciles_without_replay(tmp_path: Path) -> None:
    clock, bound, observed, owner, provider, path = setup(tmp_path)
    record = approved(owner, bound, observed)
    provider.fail_after_create = True
    result = dispatch(owner, record, bound, observed)
    assert result.state == "unknown"
    assert provider.effects == 1
    assert dispatch(owner, record, bound, observed).state == "unknown"
    assert provider.create_calls == 1
    owner.close()
    restored = CalendarActionOwner(SQLiteCalendarActionStore(path), provider, clock=clock)
    current_scope = bound.model_copy(update={"session_id": uuid4(), "generation": uuid4()})
    current_catalog = catalog(current_scope, clock).model_copy(update={"writable": False})
    reconciled = asyncio.run(
        restored.reconcile(
            record.proposal.command.request_id,
            scope=current_scope,
            catalog=current_catalog,
            precondition_guard=guard,
        )
    )
    assert reconciled.state == "reconciled_present"
    assert provider.lookup_calls == 1
    assert provider.create_calls == provider.effects == 1
    restored.close()


def test_late_receipt_after_revoke_is_unknown_not_success(tmp_path: Path) -> None:
    _, bound, observed, owner, provider, _ = setup(tmp_path)
    record = approved(owner, bound, observed)
    provider.after_effect = owner.revoke
    result = dispatch(owner, record, bound, observed)
    assert result.state == "unknown"
    assert result.receipt is None
    assert provider.effects == 1
    owner.close()


def test_crash_after_durable_claim_recovers_unknown_without_dispatch(tmp_path: Path) -> None:
    clock, bound, observed, owner, provider, path = setup(tmp_path)
    record = approved(owner, bound, observed)

    def crash() -> None:
        raise SystemExit(17)

    provider.on_ready = crash
    with pytest.raises(SystemExit):
        dispatch(owner, record, bound, observed)
    claimed = owner.store.get(record.proposal.command.request_id)
    assert claimed is not None
    assert claimed.state == "dispatch_claimed"
    owner.close()
    restored = CalendarActionOwner(SQLiteCalendarActionStore(path), provider, clock=clock)
    assert restored.get(record.proposal.command.request_id, scope=bound).state == "unknown"  # type: ignore[union-attr]
    assert dispatch(restored, record, bound, observed).state == "unknown"
    assert provider.create_calls == 1
    assert provider.effects == 0
    restored.close()


def test_absent_reconciliation_never_rearms_create(tmp_path: Path) -> None:
    _, bound, observed, owner, provider, _ = setup(tmp_path)
    record = approved(owner, bound, observed)
    provider.fail_after_create = True
    assert dispatch(owner, record, bound, observed).state == "unknown"
    provider.events.clear()
    found = asyncio.run(
        owner.reconcile(
            record.proposal.command.request_id,
            scope=bound,
            catalog=observed,
            precondition_guard=guard,
        )
    )
    assert found.state == "reconciled_absent"
    assert dispatch(owner, record, bound, observed).state == "reconciled_absent"
    assert provider.create_calls == 1
    owner.close()


def test_cross_profile_journal_read_and_backward_clock_deny(tmp_path: Path) -> None:
    clock, bound, observed, owner, _, _ = setup(tmp_path)
    record = approved(owner, bound, observed)
    with pytest.raises(CalendarActionError, match="scope_mismatch"):
        owner.get(
            record.proposal.command.request_id,
            scope=bound.model_copy(update={"profile_id": uuid4()}),
        )
    clock.now -= timedelta(seconds=1)
    with pytest.raises(CalendarActionError, match="clock_invalid"):
        owner.get(record.proposal.command.request_id, scope=bound)
    owner.close()


def test_store_single_owner_and_replacement_fence(tmp_path: Path) -> None:
    _, bound, observed, owner, _, path = setup(tmp_path)
    record = approved(owner, bound, observed)
    with pytest.raises(CalendarActionError, match="store_in_use"):
        SQLiteCalendarActionStore(path)
    path.rename(path.with_suffix(".preserved"))
    path.write_bytes(b"UNRELATED")
    path.chmod(0o600)
    with pytest.raises(CalendarActionError, match="store_replaced"):
        owner.store.get(record.proposal.command.request_id)
    owner.close()
    assert path.read_bytes() == b"UNRELATED"


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_store_rejects_nonowned_paths_without_changing_other_file(
    tmp_path: Path, kind: str
) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    other = tmp_path / "unrelated"
    other.write_bytes(b"UNRELATED")
    path = private / "actions.sqlite3"
    if kind == "symlink":
        path.symlink_to(other)
    else:
        os.link(other, path)
    with pytest.raises(CalendarActionError):
        SQLiteCalendarActionStore(path)
    assert other.read_bytes() == b"UNRELATED"


def test_permission_and_capability_changes_never_issue_an_approval(tmp_path: Path) -> None:
    _, bound, observed, owner, provider, _ = setup(tmp_path)
    for changes in (
        {"full_access": False},
        {"writable": False},
        {"subscribed": True},
        {"supports_events": False},
    ):
        with pytest.raises(CalendarActionError):
            owner.propose(command(), scope=bound, catalog=observed.model_copy(update=changes))
    assert provider.effects == provider.create_calls == 0
    owner.close()


def test_fresh_overlap_after_preflight_is_rejected(tmp_path: Path) -> None:
    _, bound, observed, owner, provider, _ = setup(tmp_path)
    record = approved(owner, bound, observed)
    provider.on_ready = lambda: provider.events.update(
        {
            "foreign": FixtureCalendarEvent(
                "foreign-public-event", bound, record.proposal.command, "foreign-token"
            )
        }
    )
    result = dispatch(owner, record, bound, observed)
    assert result.state == "rejected"
    assert provider.effects == 0
    owner.close()


def test_cancelled_existing_event_does_not_block_fixture_slot(tmp_path: Path) -> None:
    _, bound, observed, owner, provider, _ = setup(tmp_path)
    record = approved(owner, bound, observed)
    provider.events["cancelled"] = FixtureCalendarEvent(
        "cancelled-public-event", bound, record.proposal.command, "cancelled-token", cancelled=True
    )
    assert dispatch(owner, record, bound, observed).state == "created"
    assert provider.effects == 1
    owner.close()


def test_cancelled_await_keeps_unknown_claim_and_never_resends(tmp_path: Path) -> None:
    _, bound, observed, owner, provider, _ = setup(tmp_path)
    record = approved(owner, bound, observed)
    assert record.approval is not None
    approval_id = record.approval.approval_id

    async def scenario() -> None:
        ready = asyncio.Event()
        provider.pause_before_effect = asyncio.Event()
        provider.on_ready = ready.set
        task = asyncio.create_task(
            owner.dispatch(
                record.proposal.command.request_id,
                approval_id,
                scope=bound,
                catalog=observed,
                precondition_guard=guard,
            )
        )
        await ready.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    current = owner.get(record.proposal.command.request_id, scope=bound)
    assert current is not None
    assert current.state == "unknown"
    assert dispatch(owner, record, bound, observed).state == "unknown"
    assert provider.create_calls == 1
    assert provider.effects == 0
    owner.close()


def test_journal_boolean_version_and_duplicate_json_keys_reject(tmp_path: Path) -> None:
    _, bound, observed, owner, _, path = setup(tmp_path)
    record = approved(owner, bound, observed)
    with pytest.raises(ValidationError):
        CalendarActionRecord.model_validate(record.model_dump() | {"schema_version": True})
    with sqlite3.connect(path) as connection:
        bad = record.model_dump_json()[:-1] + ',"revision":2}'
        connection.execute("UPDATE actions SET data=?", (bad,))
    with pytest.raises(CalendarActionError, match="store_invalid"):
        owner.store.get(record.proposal.command.request_id)
    owner.close()


def test_journal_lock_replacement_and_stale_cas_deny(tmp_path: Path) -> None:
    _, bound, observed, owner, _, path = setup(tmp_path)
    record = approved(owner, bound, observed)
    with pytest.raises(CalendarActionError, match="concurrent_update"):
        owner.store.replace(record, expected_revision=record.revision - 1)
    assert owner.store.get(record.proposal.command.request_id) == record
    lock = Path(str(path) + ".lock")
    lock.rename(Path(str(lock) + ".preserved"))
    lock.write_bytes(b"")
    lock.chmod(0o600)
    with pytest.raises(CalendarActionError, match="store_replaced"):
        owner.store.get(record.proposal.command.request_id)
    owner.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"attempt_id": UUID(int=2)},
        {"idempotency_token": "b" * 64},
        {"payload_digest": "c" * 64},
        {"provider": "caldav"},
        {"observed_at": NOW - timedelta(seconds=1)},
        {"observed_at": NOW + timedelta(seconds=6)},
        {"fresh_store_verified": False},
    ],
)
def test_unbound_or_unverified_positive_receipts_remain_unknown(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    _, bound, observed, owner, provider, _ = setup(tmp_path)
    record = approved(owner, bound, observed)
    provider.receipt_transform = lambda receipt: receipt.model_copy(update=changes)
    assert dispatch(owner, record, bound, observed).state == "unknown"
    assert dispatch(owner, record, bound, observed).state == "unknown"
    assert provider.effects == provider.create_calls == 1
    owner.close()


def test_verified_but_late_effect_receipt_is_unknown(tmp_path: Path) -> None:
    clock, bound, observed, owner, provider, _ = setup(tmp_path)
    record = approved(owner, bound, observed)
    provider.after_effect = lambda: setattr(clock, "now", NOW + timedelta(seconds=31))
    assert dispatch(owner, record, bound, observed).state == "unknown"
    assert provider.effects == 1
    owner.close()


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
def test_unsafe_sqlite_sidecars_reject_before_open(tmp_path: Path, suffix: str) -> None:
    _, bound, observed, owner, _, path = setup(tmp_path)
    approved(owner, bound, observed)
    owner.close()
    other = tmp_path / "unrelated-sidecar"
    other.write_bytes(b"UNRELATED")
    Path(str(path) + suffix).symlink_to(other)
    with pytest.raises(CalendarActionError, match="store_invalid"):
        SQLiteCalendarActionStore(path)
    assert other.read_bytes() == b"UNRELATED"
