"""0017 preserves bodies, pins the admission boundary, and refuses lossy rollback."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import select

from satori.domain.memory_scope import MemoryScope
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.erasure_preparation import SQLiteErasurePreparationStore
from satori.infrastructure.persistence.migrations import downgrade_database, upgrade_database
from satori.infrastructure.persistence.models import ErasurePreparationRow, SourcePayloadRow
from satori.infrastructure.source_policy.installation import (
    _legacy_root_proofs,
    enroll_installation,
)
from tests.erasure_support import ErasureCase, create_case, stored_state
from tests.test_erasure_preparation import _preparation
from tests.test_memory_scope_migration import _episode
from tests.test_recovery_whole_state import stored_rows
from tests.test_source_exclusion_production import _exclude
from tests.test_stage4_conversation_memory import activate

LEGACY = "0016_source_policy"
CURRENT = "0017_erasure_intent"


def _legacy_case(database: Database, directory: Path, *, excluded: bool = True) -> ErasureCase:
    """Seed actual predecessor columns; no current conversation writer runs here."""
    identity_id = activate(database).identity.identity_id
    path = Path(str(database.engine.url.database))
    with closing(sqlite3.connect(path)) as connection:
        for suffix in ("legacy-origin", "legacy-second"):
            _episode(
                connection,
                identity_id=identity_id,
                suffix=suffix,
                counterparty_id="alice",
                verified=True,
            )
        connection.commit()
    runtime = enroll_installation(database, directory, reviewed=True, new_installation=True)
    if excluded:
        _exclude(database, runtime, "message-legacy-origin")
    return ErasureCase(
        database=database,
        directory=directory,
        runtime=runtime,
        scope=MemoryScope(identity_id, "alice"),
        message_id="message-legacy-origin",
        interaction_id="interaction-legacy-origin",
        derived_message_id="message-legacy-second",
        derived_interaction_id="interaction-legacy-second",
        episode_ids=("memory-legacy-origin", "memory-legacy-second"),
        claim_ids=(),
        services=(),
        initial_provider_calls=(),
    )


def test_upgrade_preserves_all_legacy_rows_and_empty_downgrade_roundtrips(
    sqlite_url: str, project_root: Path, tmp_path: Path
) -> None:
    config = project_root / "alembic.ini"
    upgrade_database(sqlite_url, config_path=config, revision=LEGACY)
    database = create_database(sqlite_url)
    try:
        case = _legacy_case(database, tmp_path / "legacy-policy")
        before = stored_rows(case.path)
        floor = stored_state(case)[1:]
        with (
            case.runtime.shared() as snapshot,
            pytest.raises(SourcePolicyError, match="erasure_preparation_schema_required"),
        ):
            SQLiteErasurePreparationStore(database).admit(snapshot)
        assert stored_rows(case.path) == before
        assert stored_state(case)[1:] == floor
        upgrade_database(sqlite_url, config_path=config, revision=CURRENT)
        after = stored_rows(case.path)
        for table, rows in before.items():
            if table == "alembic_version":
                assert after[table] == [(CURRENT,)]
            else:
                assert after[table] == rows
        assert after["erasure_policy_bindings"] == []
        assert after["erasure_preparations"] == []
        with closing(sqlite3.connect(case.path)) as connection:
            columns = tuple(
                row[1] for row in connection.execute("PRAGMA table_info(erasure_preparations)")
            )
            assert columns == tuple(ErasurePreparationRow.__table__.columns.keys())
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        with case.runtime.shared() as snapshot:
            SQLiteErasurePreparationStore(database).admit(snapshot)
        downgrade_database(sqlite_url, config_path=config, revision=LEGACY)
        assert stored_rows(case.path) == before
        assert stored_state(case)[1:] == floor
    finally:
        database.dispose()


def test_downgrade_cannot_discard_owner_preparation_or_ack(
    sqlite_url: str, project_root: Path, tmp_path: Path
) -> None:
    upgrade_database(sqlite_url, config_path=project_root / "alembic.ini", revision=CURRENT)
    database = create_database(sqlite_url)
    try:
        case = _legacy_case(database, tmp_path / "retained-policy")
        snapshot, preparation = _preparation(case)
        SQLiteErasurePreparationStore(database).persist((preparation,), snapshot=snapshot)
        before = stored_state(case)
        with pytest.raises(
            RuntimeError, match="erasure_intent_downgrade_would_lose_preparation_history"
        ):
            downgrade_database(
                sqlite_url,
                config_path=project_root / "alembic.ini",
                revision=LEGACY,
            )
        assert stored_state(case) == before
    finally:
        database.dispose()


@pytest.mark.parametrize("revision", [LEGACY, CURRENT])
def test_legacy_enrollment_proves_nonempty_literal_quotes_without_body_columns(
    sqlite_url: str, project_root: Path, tmp_path: Path, revision: str
) -> None:
    upgrade_database(sqlite_url, config_path=project_root / "alembic.ini", revision=revision)
    database = create_database(sqlite_url)
    try:
        case = _legacy_case(database, tmp_path / "quote-policy", excluded=False)
        before = stored_rows(case.path)
        with database.session_factory() as session:
            receipt = session.scalar(
                select(SourcePayloadRow).where(
                    SourcePayloadRow.kind == "episode",
                    SourcePayloadRow.payload_id == "memory-legacy-origin",
                )
            )
            assert receipt is not None
            assert receipt.proof_status == "unknown"
            assert receipt.legacy
            assert receipt.eligible
            assert receipt.root_message_ids == [case.message_id]
        with case.runtime.shared() as snapshot, database.session_factory() as session:
            roots, invalid = _legacy_root_proofs(session, snapshot)
        assert roots[("episode", "memory-legacy-origin")] == {case.message_id}
        assert ("episode", "memory-legacy-origin") not in invalid
        assert stored_rows(case.path) == before
        _exclude(database, case.runtime, case.message_id)
        after = stored_rows(case.path)
        assert after["episodic_memories"] == before["episodic_memories"]
        assert after["memory_evidence"] == before["memory_evidence"]
        with database.session_factory() as session:
            assert (
                session.scalar(
                    select(SourcePayloadRow.eligible).where(
                        SourcePayloadRow.kind == "episode",
                        SourcePayloadRow.payload_id == "memory-legacy-origin",
                    )
                )
                is False
            )
    finally:
        database.dispose()


@pytest.mark.parametrize(
    "damage",
    [
        "ALTER TABLE episodic_memories ADD COLUMN body_state TEXT DEFAULT 'available'",
        "UPDATE alembic_version SET version_num='0018_erasure_bodies'",
    ],
)
def test_missing_or_partial_current_body_schema_cannot_inherit_legacy_admission(
    sqlite_url: str, project_root: Path, tmp_path: Path, damage: str
) -> None:
    upgrade_database(sqlite_url, config_path=project_root / "alembic.ini", revision=CURRENT)
    database = create_database(sqlite_url)
    try:
        case = _legacy_case(database, tmp_path / "partial-policy", excluded=False)
        with closing(sqlite3.connect(case.path)) as connection:
            connection.execute(damage)
            connection.commit()
        before = stored_state(case)
        with (
            case.runtime.exclusive() as snapshot,
            database.session_factory() as session,
            pytest.raises(SourcePolicyError, match="source_enrollment_body_schema_unproven"),
        ):
            _legacy_root_proofs(session, snapshot)
        assert stored_state(case) == before
    finally:
        database.dispose()


@pytest.mark.parametrize("column", ["quote", "summary"])
def test_current_null_body_cannot_prove_legacy_episode(
    migrated_database: Database, tmp_path: Path, column: str
) -> None:
    case = create_case(migrated_database, tmp_path / "null-policy", excluded=False)
    with closing(sqlite3.connect(case.path)) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        if column == "quote":
            connection.execute(
                "UPDATE memory_evidence SET quote=NULL WHERE memory_id=?", (case.episode_ids[0],)
            )
        else:
            connection.execute(
                "UPDATE episodic_memories SET summary=NULL WHERE memory_id=?",
                (case.episode_ids[0],),
            )
        connection.commit()
    before = stored_state(case)
    with (
        pytest.raises(SourcePolicyError, match="body_reconciliation_required"),
        case.runtime.shared(),
    ):
        pass
    # Operator-only proof cannot turn corrupt/terminal storage into legacy live content.
    with case.runtime.exclusive() as snapshot, migrated_database.session_factory() as session:
        _, invalid = _legacy_root_proofs(session, snapshot)
    assert ("episode", case.episode_ids[0]) in invalid
    assert stored_state(case) == before


@pytest.mark.parametrize(
    "corruption",
    [
        "UPDATE erasure_preparations SET owner='generic_writer'",
        "UPDATE erasure_preparations SET preparation_revision=0",
        "UPDATE erasure_preparations SET contract_version=2",
        "UPDATE erasure_preparations SET result_erasure_revision=expected_erasure_revision",
        "UPDATE erasure_preparations SET source_generation=expected_source_generation-1",
        "UPDATE erasure_preparations SET body_readiness='execution_authorized'",
        "UPDATE erasure_preparations SET assessment_status='erased'",
        "UPDATE erasure_policy_bindings SET erasure_revision=0",
    ],
)
def test_migration_constraints_reject_invented_executor_or_invalid_revisions(
    migrated_database: Database, tmp_path: Path, corruption: str
) -> None:
    case = create_case(migrated_database, tmp_path / "constraint-policy")
    snapshot, preparation = _preparation(case)
    SQLiteErasurePreparationStore(migrated_database).persist((preparation,), snapshot=snapshot)
    before = stored_state(case)
    with (
        closing(sqlite3.connect(case.path)) as connection,
        pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"),
    ):
        connection.execute(corruption)
    assert stored_state(case) == before
