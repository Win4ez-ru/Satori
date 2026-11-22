"""Real SQLite knowledge-head damage, stale review and owner-independent repair."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Connection

from satori.application.erasure.knowledge_use_cases import KnowledgeBodyManager
from satori.application.erasure.retention_contracts import RetentionStatusRequest
from satori.application.erasure.retention_use_cases import RetentionPlanner
from satori.domain.erasure_knowledge import KnowledgeBodyAction
from satori.domain.retention import RetentionFacet
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.erasure_knowledge import SQLiteKnowledgeBodyStore
from satori.infrastructure.persistence.retention_context import RetentionContext
from satori.infrastructure.persistence.retention_inventory import SQLiteRetentionInventory
from satori.infrastructure.persistence.source_policy import verify_mirror
from satori.infrastructure.source_policy.installation import bind_installation, repair_installation
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime
from tests.erasure_knowledge_support import add_knowledge
from tests.erasure_support import ErasureCase, create_case, provider_counts, stored_state
from tests.test_erasure_intent_integration import _manager as intent_manager
from tests.test_erasure_intent_integration import _request as intent_request
from tests.test_erasure_knowledge_integration import knowledge_command, prepare_knowledge
from tests.test_recovery_whole_state import stored_rows
from tests.test_retention_context import census
from tests.test_source_exclusion_production import _exclude


@pytest.fixture
def case(migrated_database: Database, tmp_path: Path) -> ErasureCase:
    return create_case(migrated_database, tmp_path / "knowledge-corruption-floor")


def _backup(case: ErasureCase, destination: Path) -> None:
    with (
        closing(sqlite3.connect(case.path)) as source,
        closing(sqlite3.connect(destination)) as target,
    ):
        source.backup(target)


def _body_rows(path: Path) -> tuple[tuple[object, ...], ...]:
    with closing(sqlite3.connect(path)) as connection:
        return tuple(connection.execute("SELECT * FROM semantic_claims ORDER BY claim_id"))


def _body_revision(path: Path) -> int:
    with closing(sqlite3.connect(path)) as connection:
        row = connection.execute("SELECT body_revision FROM erasure_body_bindings").fetchone()
        assert row is not None
        return int(row[0])


def test_copied_valid_knowledge_receipt_and_ack_do_not_prove_available_bodies(
    case: ErasureCase, tmp_path: Path
) -> None:
    manager, request = prepare_knowledge(case)
    backup = tmp_path / "available-main-with-copied-ack.db"
    _backup(case, backup)
    command = knowledge_command(manager, request)
    assert manager.commit(command).main_projection_current

    # All JSON, action IDs and head bindings are genuine. Only the private main
    # projection is dishonest: its selected semantic bodies were never removed.
    with (
        closing(sqlite3.connect(case.path)) as source,
        closing(sqlite3.connect(backup)) as target,
    ):
        receipt = source.execute(
            "SELECT * FROM erasure_body_applications WHERE body_revision=3"
        ).fetchone()
        assert receipt is not None
        target.execute("INSERT INTO erasure_body_applications VALUES (?,?,?,?,?,?,?,?,?)", receipt)
        target.execute("UPDATE erasure_body_bindings SET body_revision=3")
        target.commit()
        assert target.execute(
            "SELECT count(*) FROM semantic_claims WHERE value IS NULL"
        ).fetchone() == (0,)

    restored = create_database(f"sqlite+pysqlite:///{backup}")
    baseline = stored_rows(backup)
    trusted_baseline = stored_state(case)
    calls = provider_counts(case.services)
    try:
        with case.runtime.exclusive() as snapshot:
            assert snapshot.body_revision == 3
            with (
                restored.session_factory() as session,
                pytest.raises(SourcePolicyError, match=r"^body_reconciliation_required$"),
            ):
                verify_mirror(session, snapshot)
            assert not SQLiteKnowledgeBodyStore(restored).current(
                case.runtime.body_actions(), snapshot=snapshot
            )
        fresh = KnowledgeBodyManager(case.runtime, SQLiteKnowledgeBodyStore(restored))
        status = fresh.status(request, request_id=command.request_id)
        assert status.phase == "body_application_pending"
        assert not status.main_projection_current
        assert not status.selected_bodies_removed
        assert status.physical_erasure is False
        with pytest.raises(SourcePolicyError, match=r"^body_reconciliation_required$"):
            bind_installation(restored, case.directory)
        assert stored_rows(backup) == baseline
        assert stored_state(case) == trusted_baseline
        assert provider_counts(case.services) == calls
    finally:
        restored.dispose()


@pytest.mark.parametrize("entry", ["current", "bind"])
def test_rich_copied_ack_denies_before_reading_protected_owner_or_witness_rows(
    migrated_database: Database, tmp_path: Path, entry: str
) -> None:
    case = create_case(migrated_database, tmp_path / "rich-forged-head-floor", excluded=False)
    fixture = add_knowledge(case)
    _exclude(case.database, case.runtime, case.message_id)
    manager, request = prepare_knowledge(case)
    backup = tmp_path / "rich-available-main-with-copied-ack.db"
    _backup(case, backup)
    command = knowledge_command(manager, request)
    assert manager.commit(command).main_projection_current
    with (
        closing(sqlite3.connect(case.path)) as source,
        closing(sqlite3.connect(backup)) as target,
    ):
        receipt = source.execute(
            "SELECT * FROM erasure_body_applications WHERE body_revision=3"
        ).fetchone()
        assert receipt is not None
        target.execute("INSERT INTO erasure_body_applications VALUES (?,?,?,?,?,?,?,?,?)", receipt)
        target.execute("UPDATE erasure_body_bindings SET body_revision=3")
        target.commit()
        # Both original dialogue-witnessed and independent live-origin quotes
        # remain available in the candidate. A later failure cannot undo a read.
        assert target.execute(
            "SELECT count(*) FROM satori_position_evidence "
            "WHERE position_id=? AND quote IS NOT NULL AND normalized_signature IS NOT NULL",
            (fixture.position_id,),
        ).fetchone() == (2,)

    restored = create_database(f"sqlite+pysqlite:///{backup}")
    candidate_before = stored_rows(backup)
    trusted_before = stored_state(case)
    calls = provider_counts(case.services)
    queries: list[str] = []

    def observe(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _many: bool,
    ) -> None:
        queries.append(" ".join(statement.lower().replace('"', "").split()))

    event.listen(restored.engine, "before_cursor_execute", observe)
    try:
        if entry == "current":
            with case.runtime.exclusive() as snapshot:
                assert not SQLiteKnowledgeBodyStore(restored).current(
                    case.runtime.body_actions(), snapshot=snapshot
                )
        else:
            with pytest.raises(SourcePolicyError, match=r"^body_reconciliation_required$"):
                bind_installation(restored, case.directory)
        protected_tables = (
            "semantic_claims",
            "user_model_claims",
            "world_model_claims",
            "satori_positions",
            "satori_inclinations",
            "satori_position_evidence",
        )
        assert not any(
            query.startswith(f"select * from {table} ") or query == f"select * from {table}"
            for query in queries
            for table in protected_tables
        )
        # The test must exercise the real terminal shape gate, rather than an
        # unrelated stale policy watermark or missing receipt shortcut.
        assert any(
            "body_state!='available'" in query and "from satori_positions" in query
            for query in queries
        )
        assert stored_rows(backup) == candidate_before
        assert stored_state(case) == trusted_before
        assert provider_counts(case.services) == calls
    finally:
        event.remove(restored.engine, "before_cursor_execute", observe)
        restored.dispose()


def test_source_and_intent_repair_cannot_acknowledge_pending_knowledge_head(
    case: ErasureCase, tmp_path: Path
) -> None:
    manager, request = prepare_knowledge(case)
    backup = tmp_path / "old-body-head.db"
    _backup(case, backup)
    command = knowledge_command(manager, request)
    assert manager.commit(command).main_projection_current
    restored = create_database(f"sqlite+pysqlite:///{backup}")
    bodies = _body_rows(backup)
    calls = provider_counts(case.services)
    floor = stored_rows(case.runtime.ledger.database_path)
    floor_bytes = case.runtime.ledger.database_path.read_bytes()
    binding_bytes = case.runtime.ledger.binding_path.read_bytes()
    lock_bytes = case.runtime.ledger.lock_path.read_bytes()
    try:
        with case.runtime.exclusive() as snapshot:
            receipt = case.runtime.receipt(f"exclude-{case.message_id}")
            assert receipt is not None
            repair_installation(restored, case.runtime, snapshot, receipt)
        assert _body_revision(backup) == 2
        assert _body_rows(backup) == bodies
        with pytest.raises(SourcePolicyError, match=r"^body_reconciliation_required$"):
            bind_installation(restored, case.directory)

        # The trusted complete floor permits historical-only operator assessment
        # of this already-terminal root. Preparation may append its own metadata;
        # it cannot materialize the missing body action or grant current readiness.
        before_intent = stored_rows(backup)
        repaired = intent_manager(case, database=restored).resume(
            intent_request(case), request_id=request.intent_request_id
        )
        assert repaired.phase == "intent_committed_preparation_pending"
        assert repaired.failure_reason == "body_reconciliation_required"
        assert not repaired.main_projection_current
        assert not repaired.preparation_current
        assert repaired.physical_erasure is False
        assert repaired.execution_authorized is False
        after_intent = stored_rows(backup)
        assert set(after_intent) == set(before_intent)
        assert {
            table for table in before_intent if before_intent[table] != after_intent[table]
        } == {"erasure_preparations"}
        # Existing version-one preparation rows are retained exactly; only the
        # next complete 15-owner assessment is appended.
        assert set(before_intent["erasure_preparations"]) <= set(
            after_intent["erasure_preparations"]
        )
        assert (
            len(after_intent["erasure_preparations"])
            == len(before_intent["erasure_preparations"]) + 15
        )
        assert (
            after_intent["erasure_body_applications"] == before_intent["erasure_body_applications"]
        )
        assert after_intent["erasure_body_bindings"] == before_intent["erasure_body_bindings"]
        assert _body_revision(backup) == 2
        assert _body_rows(backup) == bodies
        with case.runtime.exclusive() as snapshot:
            assert snapshot.body_revision == 3
            physical, keys, schema, digest = census(restored)
            operator = RetentionContext.for_owner_assessment(
                physical,
                snapshot=snapshot,
                primary_keys=keys,
                schema_digest=schema,
                store_digest=digest,
                approved_actions=case.runtime.body_actions(),
            )
            assert operator.historical.admits_root(case.message_id)
            assert not any(
                RetentionFacet.TERMINAL_BODY in operator.classify(table, row).facets
                for table, rows in physical.items()
                for row in rows
            )
        with pytest.raises(SourcePolicyError, match=r"^body_reconciliation_required$"):
            bind_installation(restored, case.directory)
        standalone = SourcePolicyRuntime(case.directory, case.scope.identity_id)
        assert standalone.verify_mirror is None
        global_retention = RetentionPlanner(standalone, SQLiteRetentionInventory(restored))
        with pytest.raises(SourcePolicyError, match=r"^body_reconciliation_required$"):
            global_retention.status(RetentionStatusRequest(identity_id=case.scope.identity_id))
        assert stored_rows(backup) == after_intent
        assert stored_rows(case.runtime.ledger.database_path) == floor
        assert case.runtime.ledger.database_path.read_bytes() == floor_bytes
        assert case.runtime.ledger.binding_path.read_bytes() == binding_bytes
        assert case.runtime.ledger.lock_path.read_bytes() == lock_bytes
        assert provider_counts(case.services) == calls

        resumed = KnowledgeBodyManager(case.runtime, SQLiteKnowledgeBodyStore(restored)).resume(
            request, request_id=command.request_id
        )
        assert resumed.main_projection_current
        assert _body_revision(backup) == 3
        bind_installation(restored, case.directory)
        assert global_retention.status(
            RetentionStatusRequest(identity_id=case.scope.identity_id)
        ).whole_head_current
        assert stored_rows(case.runtime.ledger.database_path) == floor
        assert case.runtime.ledger.database_path.read_bytes() == floor_bytes
        assert case.runtime.ledger.binding_path.read_bytes() == binding_bytes
        assert case.runtime.ledger.lock_path.read_bytes() == lock_bytes
        assert provider_counts(case.services) == calls
    finally:
        restored.dispose()


def test_stale_real_retained_metadata_review_cannot_write_main_or_floor(
    case: ErasureCase,
) -> None:
    manager, request = prepare_knowledge(case)
    command = knowledge_command(manager, request)
    # The content, source evidence and numerical trajectory remain valid. The
    # retained physical timestamp still belongs to the exact approved preimage.
    with case.database.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE semantic_claims SET updated_at=? WHERE claim_id=?",
            ("2026-10-06 23:59:00.000000", case.claim_ids[0]),
        )
    current_preview = manager.preview(request)
    assert current_preview.readiness == "ready"
    assert current_preview.approval_digest != command.approval_digest
    before = stored_state(case)
    calls = provider_counts(case.services)
    with pytest.raises(SourcePolicyError, match=r"^body_erasure_review_stale$"):
        manager.commit(command)
    assert stored_state(case) == before
    assert provider_counts(case.services) == calls
    with case.runtime.exclusive() as snapshot:
        assert snapshot.body_revision == 2
        assert case.runtime.body_action(command.request_id) is None


@pytest.mark.parametrize("column", ["value", "normalized_value"])
def test_unapproved_extra_sql_null_cannot_bypass_available_knowledge_shape(
    case: ErasureCase, column: str
) -> None:
    prepare_knowledge(case)
    before = stored_state(case)
    calls = provider_counts(case.services)
    with (
        closing(sqlite3.connect(case.path)) as connection,
        pytest.raises(sqlite3.IntegrityError, match="body_consistent"),
    ):
        # The closed parameter set is intentional; there is no operator SQL port.
        connection.execute(
            f"UPDATE semantic_claims SET {column}=NULL WHERE claim_id=?", (case.claim_ids[0],)
        )
    assert stored_state(case) == before
    assert provider_counts(case.services) == calls


def test_terminal_normalized_copy_cannot_be_reintroduced_by_sql(case: ErasureCase) -> None:
    manager, request = prepare_knowledge(case)
    assert manager.commit(knowledge_command(manager, request)).main_projection_current
    before = stored_state(case)
    calls = provider_counts(case.services)
    with (
        closing(sqlite3.connect(case.path)) as connection,
        pytest.raises(sqlite3.IntegrityError, match="knowledge_body_terminal"),
    ):
        connection.execute(
            "UPDATE semantic_claims SET normalized_value=? WHERE claim_id=?",
            ("Synthetic normalized body copy", case.claim_ids[0]),
        )
    assert stored_state(case) == before
    assert provider_counts(case.services) == calls


@pytest.mark.parametrize("damage", ["missing_guard", "unregistered_body_column"])
def test_schema_pin_denies_terminal_store_with_removed_guard_or_hidden_copy(
    case: ErasureCase, tmp_path: Path, damage: str
) -> None:
    manager, request = prepare_knowledge(case)
    command = knowledge_command(manager, request)
    assert manager.commit(command).main_projection_current
    backup = tmp_path / "damaged-schema.db"
    _backup(case, backup)
    with closing(sqlite3.connect(backup)) as connection:
        if damage == "missing_guard":
            connection.execute("DROP TRIGGER semantic_claims_body_update")
        else:
            connection.execute("ALTER TABLE semantic_claims ADD COLUMN hidden_normalized_copy TEXT")
            # Deliberate malicious same-user schema damage is a fail-closed
            # admission test, not an authorized owner body-writing path.
        connection.commit()
    restored = create_database(f"sqlite+pysqlite:///{backup}")
    baseline = stored_rows(backup)
    trusted_baseline = stored_state(case)
    calls = provider_counts(case.services)
    try:
        with case.runtime.exclusive() as snapshot:
            assert not SQLiteKnowledgeBodyStore(restored).current(
                case.runtime.body_actions(), snapshot=snapshot
            )
        with pytest.raises(SourcePolicyError, match=r"^erasure_schema_unsupported$"):
            bind_installation(restored, case.directory)
        fresh = KnowledgeBodyManager(case.runtime, SQLiteKnowledgeBodyStore(restored))
        with pytest.raises(SourcePolicyError, match=r"^erasure_schema_unsupported$"):
            fresh.preview(request)
        assert stored_rows(backup) == baseline
        assert stored_state(case) == trusted_baseline
        assert provider_counts(case.services) == calls
    finally:
        restored.dispose()


def test_pending_changed_preimage_requires_new_review_and_preserves_old_actions(
    case: ErasureCase,
) -> None:
    manager, request = prepare_knowledge(case)
    command = knowledge_command(manager, request, "knowledge-pending")
    before = stored_rows(case.path)
    calls = provider_counts(case.services)
    writes = 0

    def fail(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _many: bool,
    ) -> None:
        nonlocal writes
        if statement.startswith("UPDATE semantic_claims SET"):
            writes += 1
            raise RuntimeError("synthetic_knowledge_preimage_interruption")

    event.listen(case.database.engine, "before_cursor_execute", fail)
    try:
        pending = manager.commit(command)
    finally:
        event.remove(case.database.engine, "before_cursor_execute", fail)
    assert writes == 1
    assert pending.body_revision == 3
    assert not pending.main_projection_current
    assert stored_rows(case.path) == before
    with case.runtime.exclusive():
        old_actions = case.runtime.body_actions()
        assert isinstance(old_actions[-1], KnowledgeBodyAction)
    with case.database.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE semantic_claims SET updated_at=? WHERE claim_id=?",
            ("2026-10-06 23:59:00.000000", case.claim_ids[0]),
        )
    changed = stored_state(case)
    refused = manager.resume(request, request_id=command.request_id)
    assert refused.phase == "body_application_pending"
    assert refused.failure_reason == "body_target_changed_review_required"
    assert not refused.main_projection_current
    assert stored_state(case) == changed
    with pytest.raises(SourcePolicyError, match=r"^body_reconciliation_required$"):
        bind_installation(case.database, case.directory)

    reviewed = knowledge_command(manager, request, "knowledge-reapproved")
    assert reviewed.expected_body_revision == 3
    assert reviewed.approval_digest != command.approval_digest
    repaired = manager.commit(reviewed)
    assert repaired.body_revision == 4
    assert repaired.main_projection_current
    assert repaired.selected_bodies_removed
    assert repaired.physical_erasure is False
    with case.runtime.shared() as snapshot:
        assert snapshot.body_revision == 4
        assert case.runtime.body_actions()[:3] == old_actions
    after = stored_rows(case.path)
    for table, rows in before.items():
        if table not in {"semantic_claims", "erasure_body_applications", "erasure_body_bindings"}:
            assert after[table] == rows, table
    assert provider_counts(case.services) == calls
