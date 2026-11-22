"""Private full-head inventories share one bounded read snapshot and prove all targets."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy.engine import Connection

import satori.infrastructure.persistence.erasure as erasure_adapter
from satori.domain.source_policy import CanonicalRootRef, SourcePolicyError
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure import SQLiteErasureInventory
from satori.infrastructure.persistence.erasure_inventory import Store
from satori.infrastructure.source_policy.installation import canonical_root
from tests.erasure_support import create_case, provider_counts
from tests.test_recovery_whole_state import stored_rows
from tests.test_source_exclusion_production import _exclude


def test_two_root_head_uses_one_physical_read_transaction_and_one_store_census(
    migrated_database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = create_case(migrated_database, tmp_path / "batch-floor")
    _exclude(case.database, case.runtime, case.derived_message_id)
    roots = tuple(
        canonical_root(case.database, case.runtime, message_id, counterparty_id="alice")
        for message_id in (case.message_id, case.derived_message_id)
    )
    adapter = SQLiteErasureInventory(case.database)
    before = stored_rows(case.path)
    original_read = erasure_adapter._read_transaction
    original_load = erasure_adapter._load_store
    transactions: list[Connection] = []
    loads: list[Connection] = []

    @contextmanager
    def read(database: Database) -> Iterator[Connection]:
        with original_read(database) as connection:
            transactions.append(connection)
            yield connection

    def load(
        connection: Connection,
        schema_digest: str,
    ) -> tuple[Store, int, str, dict[str, tuple[str, ...]], str]:
        raw = cast(sqlite3.Connection, connection.connection.driver_connection)
        assert raw.in_transaction
        assert connection.exec_driver_sql("PRAGMA query_only").scalar_one() == 1
        loads.append(connection)
        return original_load(connection, schema_digest)

    with monkeypatch.context() as context:
        context.setattr(erasure_adapter, "_read_transaction", read)
        context.setattr(erasure_adapter, "_load_store", load)
        with case.runtime.exclusive() as snapshot:
            inventories = adapter.inspect_many(roots, snapshot=snapshot)
    assert len(transactions) == len(loads) == 1
    assert loads[0] is transactions[0]
    assert tuple(item.root for item in inventories) == roots
    assert inventories[0].store_digest == inventories[1].store_digest
    assert inventories[0].retention_digest == inventories[1].retention_digest
    with case.runtime.exclusive() as snapshot:
        independent = tuple(
            adapter.inspect(
                root.message_id, counterparty_id=root.counterparty_id, snapshot=snapshot
            )
            for root in roots
        )
    assert inventories == independent
    assert stored_rows(case.path) == before
    assert provider_counts(case.services) == case.initial_provider_calls


@pytest.mark.parametrize("forged_field", ["counterparty", "session", "interaction", "lineage"])
def test_all_private_batch_roots_are_proven_before_any_content_census(
    migrated_database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    forged_field: str,
) -> None:
    case = create_case(migrated_database, tmp_path / "batch-scope-floor")
    _exclude(case.database, case.runtime, case.derived_message_id)
    first = canonical_root(case.database, case.runtime, case.message_id, counterparty_id="alice")
    second = canonical_root(
        case.database, case.runtime, case.derived_message_id, counterparty_id="alice"
    )
    if forged_field == "counterparty":
        forged = replace(second, counterparty_id="bob")
    elif forged_field == "session":
        forged = replace(second, session_id="foreign-session")
    elif forged_field == "interaction":
        forged = replace(second, interaction_id="foreign-interaction")
    else:
        forged = replace(second, lineage_id="foreign-lineage")
    roots: tuple[CanonicalRootRef, ...] = (first, forged)
    before = stored_rows(case.path)

    def forbidden_load(
        connection: Connection,
        schema_digest: str,
    ) -> tuple[Store, int, str, dict[str, tuple[str, ...]], str]:
        raise AssertionError("every canonical binding must precede the content census")

    monkeypatch.setattr(erasure_adapter, "_load_store", forbidden_load)
    adapter = SQLiteErasureInventory(case.database)
    with case.runtime.exclusive() as snapshot, pytest.raises(SourcePolicyError):
        adapter.inspect_many(roots, snapshot=snapshot)
    assert stored_rows(case.path) == before
    assert provider_counts(case.services) == case.initial_provider_calls
