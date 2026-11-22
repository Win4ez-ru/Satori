"""Synthetic accounting acceptance; no provider, profile, billing or content access."""

import json
import sqlite3
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from pydantic import ValidationError

from satori.server import usage
from satori.server.usage import (
    MAX_MICRO_RUB,
    AttemptSpec,
    AttemptState,
    Model,
    Provider,
    Role,
    UsageError,
    UsageLedger,
)

NOW = datetime(2026, 10, 31, 23, 59, tzinfo=UTC)


def spec(amount: int = 100) -> AttemptSpec:
    return AttemptSpec(
        attempt_id=uuid4(),
        provider=Provider.CLOUD_RU,
        model=Model.GIGACHAT_3_PRO,
        role=Role.CONVERSATION,
        reservation_micro_rub=amount,
    )


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[UsageLedger]:
    owner = UsageLedger(tmp_path / "usage", clock=lambda: NOW)
    owner.configure_period("2026-10", 1000)
    yield owner
    owner.close()


def test_durable_reservation_dispatch_settlement_and_restart(tmp_path: Path) -> None:
    directory = tmp_path / "usage"
    owner = UsageLedger(directory, clock=lambda: NOW)
    owner.configure_period("2026-10", 1000)
    item = spec(500)
    assert owner.reserve(item).liability_micro_rub == 500
    assert owner.begin_dispatch(item.attempt_id).dispatch_permitted
    owner.close()

    restarted = UsageLedger(directory, clock=lambda: NOW)
    try:
        assert restarted.reconcile(item.attempt_id).state == AttemptState.DISPATCHED
        assert not restarted.begin_dispatch(item.attempt_id).dispatch_permitted
        assert restarted.snapshot("2026-10").unsettled_attempts == 1
        result = restarted.settle_report(item.attempt_id, 120, input_tokens=50, output_tokens=10)
        assert result.liability_micro_rub == 120
        assert result.input_tokens == 50
        snapshot = restarted.snapshot("2026-10")
        assert snapshot.liability_micro_rub == snapshot.settled_reported_micro_rub == 120
        assert snapshot.provider_invoice_verified is False
    finally:
        restarted.close()


def test_same_attempt_reserve_is_idempotent_and_dispatch_permit_is_one_use(
    ledger: UsageLedger,
) -> None:
    item = spec(900)
    first = ledger.reserve(item)
    assert ledger.reserve(item) == first
    assert ledger.snapshot("2026-10").liability_micro_rub == 900
    assert ledger.begin_dispatch(item.attempt_id).dispatch_permitted
    assert not ledger.begin_dispatch(item.attempt_id).dispatch_permitted
    assert ledger.reserve(item).state == AttemptState.DISPATCHED
    assert not ledger.begin_dispatch(item.attempt_id).dispatch_permitted
    with pytest.raises(UsageError, match="conflict"):
        ledger.reserve(item.model_copy(update={"reservation_micro_rub": 800}))


def test_timeout_retains_liability_and_cannot_cancel_or_replay(ledger: UsageLedger) -> None:
    item = spec(1000)
    ledger.reserve(item)
    ledger.begin_dispatch(item.attempt_id)
    assert ledger.mark_unknown(item.attempt_id).liability_micro_rub == 1000
    assert ledger.mark_unknown(item.attempt_id).state == AttemptState.UNKNOWN
    assert ledger.reconcile(item.attempt_id).state == AttemptState.UNKNOWN
    assert ledger.snapshot("2026-10").liability_micro_rub == 1000
    assert not ledger.begin_dispatch(item.attempt_id).dispatch_permitted
    with pytest.raises(UsageError, match="invalid_transition"):
        ledger.cancel_before_dispatch(item.attempt_id)
    with pytest.raises(UsageError, match="cap_exceeded"):
        ledger.reserve(spec(1))


def test_missing_usage_is_not_zero_and_retry_is_separate_reservation(ledger: UsageLedger) -> None:
    original = spec(600)
    ledger.reserve(original)
    ledger.begin_dispatch(original.attempt_id)
    ledger.mark_unknown(original.attempt_id)
    with pytest.raises(UsageError, match="cap_exceeded"):
        ledger.reserve(spec(600))
    assert ledger.reconcile(original.attempt_id).reported_micro_rub is None
    retry = spec(400)
    ledger.reserve(retry)
    assert ledger.begin_dispatch(retry.attempt_id).dispatch_permitted
    assert ledger.snapshot("2026-10").liability_micro_rub == 1000


def test_only_pre_dispatch_cancellation_releases_reservation(ledger: UsageLedger) -> None:
    item = spec(900)
    ledger.reserve(item)
    assert ledger.cancel_before_dispatch(item.attempt_id).liability_micro_rub == 0
    assert ledger.cancel_before_dispatch(item.attempt_id).state == AttemptState.CANCELLED
    assert ledger.reserve(item).state == AttemptState.CANCELLED
    assert not ledger.begin_dispatch(item.attempt_id).dispatch_permitted
    assert ledger.snapshot("2026-10").liability_micro_rub == 0


def test_actual_report_above_reservation_is_retained_and_blocks_over_cap(
    ledger: UsageLedger,
) -> None:
    item = spec(500)
    ledger.reserve(item)
    ledger.begin_dispatch(item.attempt_id)
    ledger.settle_report(item.attempt_id, 1200)
    snapshot = ledger.snapshot("2026-10")
    assert snapshot.over_cap
    assert snapshot.liability_micro_rub == 1200
    with pytest.raises(UsageError, match="cap_exceeded"):
        ledger.reserve(spec(1))
    assert ledger.settle_report(item.attempt_id, 1200).reported_micro_rub == 1200
    with pytest.raises(UsageError, match="conflict"):
        ledger.settle_report(item.attempt_id, 1199)


def test_concurrent_reservations_cannot_overspend(ledger: UsageLedger) -> None:
    def reserve_one(_: int) -> str:
        try:
            ledger.reserve(spec(300))
        except UsageError as error:
            return error.code
        return "admitted"

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve_one, range(20)))
    assert set(results) == {"admitted", "cap_exceeded"}
    assert results.count("admitted") == 3
    assert ledger.snapshot("2026-10").liability_micro_rub == 900


def test_overrun_stops_already_reserved_but_not_dispatched_work(ledger: UsageLedger) -> None:
    first, second = spec(500), spec(500)
    ledger.reserve(first)
    ledger.reserve(second)
    ledger.begin_dispatch(first.attempt_id)
    ledger.settle_report(first.attempt_id, 600)
    with pytest.raises(UsageError, match="cap_exceeded"):
        ledger.begin_dispatch(second.attempt_id)
    assert ledger.reconcile(second.attempt_id).state == AttemptState.RESERVED


def test_dispatch_receipt_is_unique_under_concurrency(ledger: UsageLedger) -> None:
    item = spec()
    ledger.reserve(item)
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(lambda _: ledger.begin_dispatch(item.attempt_id), range(20)))
    assert sum(receipt.dispatch_permitted for receipt in receipts) == 1


def test_period_requires_explicit_budget_and_reservation_cannot_cross_utc_month(
    tmp_path: Path,
) -> None:
    clock = [NOW]
    owner = UsageLedger(tmp_path / "usage", clock=lambda: clock[0])
    try:
        with pytest.raises(UsageError, match="budget_required"):
            owner.reserve(spec())
        owner.configure_period("2026-10", 1000)
        item = spec(1000)
        owner.reserve(item)
        clock[0] = datetime(2026, 11, 1, tzinfo=UTC)
        with pytest.raises(UsageError, match="invalid_transition"):
            owner.begin_dispatch(item.attempt_id)
        with pytest.raises(UsageError, match="budget_required"):
            owner.reserve(spec())
        owner.configure_period("2026-11", 200)
        owner.reserve(spec(200))
        assert owner.snapshot("2026-10").liability_micro_rub == 1000
        assert owner.snapshot("2026-11").liability_micro_rub == 200
    finally:
        owner.close()


def test_budget_configuration_is_immutable_and_zero_is_valid(ledger: UsageLedger) -> None:
    ledger.configure_period("2026-10", 1000)
    with pytest.raises(UsageError, match="conflict"):
        ledger.configure_period("2026-10", 1001)
    ledger.configure_period("2026-11", 0)
    assert ledger.snapshot("2026-11").cap_micro_rub == 0


@pytest.mark.parametrize("value", [True, 0.1, -1, MAX_MICRO_RUB + 1])
def test_money_rejects_float_bool_negative_and_overflow(ledger: UsageLedger, value: int) -> None:
    with pytest.raises(UsageError, match="invalid_metadata"):
        ledger.configure_period("2026-11", value)
    with pytest.raises(ValidationError):
        spec(value)


@pytest.mark.parametrize(
    "period", ["2026-00", "2026-13", "26-11", "2026-1", "\uff12\uff10\uff12\uff16-11"]
)
def test_invalid_period_rejected(ledger: UsageLedger, period: str) -> None:
    with pytest.raises(UsageError, match="invalid_metadata"):
        ledger.configure_period(period, 1)


def test_arbitrary_content_fields_and_free_text_identifiers_are_rejected() -> None:
    values = spec().model_dump(mode="json")
    with pytest.raises(ValidationError):
        AttemptSpec.model_validate_json(json.dumps(values | {"prompt": "private-content"}))
    for name in ("provider", "model", "role", "attempt_id"):
        with pytest.raises(ValidationError):
            AttemptSpec.model_validate_json(json.dumps(values | {name: "private-content"}))


def test_model_copy_cannot_bypass_reservation_bounds(ledger: UsageLedger) -> None:
    item = spec().model_copy(update={"reservation_micro_rub": -1})
    with pytest.raises(UsageError, match="invalid_metadata"):
        ledger.reserve(item)
    assert ledger.snapshot("2026-10").liability_micro_rub == 0


def test_single_namespace_owner_and_private_file_permissions(tmp_path: Path) -> None:
    directory = tmp_path / "usage"
    owner = UsageLedger(directory)
    try:
        assert (directory.stat().st_mode & 0o777) == 0o700
        assert ((directory / "usage.db").stat().st_mode & 0o777) == 0o600
        with pytest.raises(UsageError, match="unavailable"):
            UsageLedger(directory)
    finally:
        owner.close()
    replacement = UsageLedger(directory)
    replacement.close()


@pytest.mark.parametrize("mutation", ["schema", "bad_json", "negative_budget", "trigger"])
def test_tampered_existing_store_fails_closed(tmp_path: Path, mutation: str) -> None:
    directory = tmp_path / "usage"
    owner = UsageLedger(directory, clock=lambda: NOW)
    owner.configure_period("2026-10", 1000)
    item = spec()
    owner.reserve(item)
    owner.close()
    with sqlite3.connect(directory / "usage.db") as connection:
        if mutation == "schema":
            connection.execute("CREATE TABLE unexpected (value TEXT)")
        elif mutation == "bad_json":
            connection.execute("UPDATE attempts SET document='{}'")
        elif mutation == "negative_budget":
            connection.execute("UPDATE periods SET cap=-1")
        else:
            connection.execute(
                "CREATE TRIGGER unexpected AFTER INSERT ON attempts BEGIN DELETE FROM attempts; END"
            )
    with pytest.raises(UsageError, match="unavailable"):
        UsageLedger(directory)


def test_file_replacement_or_permissions_change_refuses_live_owner(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "usage"
    owner = UsageLedger(directory, clock=lambda: NOW)
    owner.configure_period("2026-10", 1000)
    try:
        path = directory / "usage.db"
        preserved = directory / "old.db"
        path.rename(preserved)
        path.write_bytes(preserved.read_bytes())
        path.chmod(0o600)
        with pytest.raises(UsageError, match="unavailable"):
            owner.reserve(spec())
    finally:
        owner.close()


def test_settlement_requires_prior_dispatch_and_reconcile_has_no_effect(
    ledger: UsageLedger,
) -> None:
    item = spec()
    ledger.reserve(item)
    with pytest.raises(UsageError, match="invalid_transition"):
        ledger.settle_report(item.attempt_id, 0)
    assert ledger.reconcile(item.attempt_id).state == AttemptState.RESERVED
    with pytest.raises(UsageError, match="not_found"):
        ledger.reconcile(uuid4())


def test_closed_ledger_does_not_implicitly_reopen(ledger: UsageLedger) -> None:
    ledger.close()
    with pytest.raises(UsageError, match="unavailable"):
        ledger.reserve(spec())


def test_openai_metadata_is_supported_without_account_or_key_fields(ledger: UsageLedger) -> None:
    item = spec().model_copy(update={"provider": Provider.OPENAI, "model": Model.GPT_6_LUNA})
    assert ledger.reserve(item).spec.provider == Provider.OPENAI
    embedded = spec().model_copy(
        update={
            "provider": Provider.OPENAI,
            "model": Model.TEXT_EMBEDDING_3_SMALL,
            "role": Role.EMBEDDING,
        }
    )
    assert ledger.reserve(embedded).spec.role == Role.EMBEDDING


def test_commit_postcondition_failure_never_returns_a_new_dispatch_permit(
    ledger: UsageLedger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = spec()
    ledger.reserve(item)
    original = ledger._check_identity
    calls = 0

    def fail_after_commit() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise UsageError("unavailable")
        original()

    monkeypatch.setattr(ledger, "_check_identity", fail_after_commit)
    with pytest.raises(UsageError, match="outcome_unknown"):
        ledger.begin_dispatch(item.attempt_id)
    monkeypatch.setattr(ledger, "_check_identity", original)
    assert ledger.reconcile(item.attempt_id).state == AttemptState.DISPATCHED
    assert not ledger.begin_dispatch(item.attempt_id).dispatch_permitted


def test_capacity_limits_do_not_discard_old_attempts_or_budgets(
    ledger: UsageLedger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(usage, "MAX_ATTEMPTS", 2)
    monkeypatch.setattr(usage, "MAX_PERIODS", 2)
    first, second = spec(), spec()
    ledger.reserve(first)
    ledger.reserve(second)
    with pytest.raises(UsageError, match="capacity_reached"):
        ledger.reserve(spec())
    assert ledger.reconcile(first.attempt_id).state == AttemptState.RESERVED
    ledger.configure_period("2026-11", 100)
    with pytest.raises(UsageError, match="capacity_reached"):
        ledger.configure_period("2026-12", 100)


def test_naive_accounting_clock_is_rejected_without_admission(tmp_path: Path) -> None:
    owner = UsageLedger(tmp_path / "usage", clock=lambda: datetime(2026, 10, 31))
    try:
        owner.configure_period("2026-10", 1000)
        with pytest.raises(UsageError, match="invalid_metadata"):
            owner.reserve(spec())
        assert owner.snapshot("2026-10").liability_micro_rub == 0
    finally:
        owner.close()


@pytest.mark.parametrize("bad_period", [None, True, 202610, [], {}])
def test_non_string_period_has_closed_failure(ledger: UsageLedger, bad_period: object) -> None:
    with pytest.raises(UsageError, match=r"^invalid_metadata$"):
        ledger.configure_period(cast(str, bad_period), 1000)


def test_more_than_old_lifetime_limit_survives_restart_without_releasing_unknowns(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "usage"
    owner = UsageLedger(directory, clock=lambda: NOW)
    owner.configure_period("2026-10", 1_000_000)
    owner.close()
    item = spec(1)
    documents = [
        usage.AttemptRecord(
            spec=item.model_copy(update={"attempt_id": uuid4()}),
            period="2026-10",
            created_at=NOW,
            state=AttemptState.UNKNOWN,
        )
        for _ in range(5000)
    ]
    # Only fixture seeding uses direct SQL while no ledger owner is alive.
    with sqlite3.connect(directory / "usage.db") as connection:
        connection.executemany(
            "INSERT INTO attempts VALUES (?, ?)",
            [(str(record.spec.attempt_id), record.model_dump_json()) for record in documents],
        )
    assert (directory / "usage.db").stat().st_size > 1024 * 1024
    owner = UsageLedger(directory, clock=lambda: NOW)
    try:
        assert usage.MAX_ATTEMPTS == 100_000
        snapshot = owner.snapshot("2026-10")
        assert snapshot.liability_micro_rub == 5000
        assert snapshot.unsettled_attempts == 5000
        assert not owner.begin_dispatch(documents[0].spec.attempt_id).dispatch_permitted
        owner.reserve(spec(10))
        assert owner.snapshot("2026-10").liability_micro_rub == 5010
    finally:
        owner.close()
