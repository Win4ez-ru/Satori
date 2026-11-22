"""Owner acknowledgement is append-only metadata, not content deletion authority.

These adapter fixtures use controlled snapshot revisions to test the main projection.
Trusted floor commit/replay is covered independently by its real-ledger tests.
"""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete, event, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

import satori.infrastructure.persistence.erasure_preparation as preparation_adapter
from satori.domain.erasure import ErasureDisposition, ErasureInventory, ErasureRoute
from satori.domain.erasure_intent import ERASURE_RETENTION_POLICY_VERSION, ErasureIntent
from satori.domain.erasure_preparation import (
    ErasureBodyReadiness,
    ErasureOwnerPreparation,
    ErasurePreparationOwner,
    prepare_owner_contracts,
)
from satori.domain.source_policy import SourceEligibilitySnapshot, SourcePolicyError
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.erasure import SQLiteErasureInventory
from satori.infrastructure.persistence.erasure_preparation import SQLiteErasurePreparationStore
from satori.infrastructure.persistence.models import (
    ErasurePolicyBindingRow,
    ErasurePreparationRow,
    SourceOwnerRepairRow,
)
from satori.infrastructure.persistence.source_policy import materialize_mirror, verify_mirror
from tests.erasure_support import ErasureCase, create_case, provider_counts, stored_state
from tests.test_recovery_whole_state import stored_rows
from tests.test_source_exclusion_production import ROOT_TEXT, SECRET

NOW = datetime(2026, 10, 4, tzinfo=UTC)
BOOKKEEPING = frozenset({"erasure_policy_bindings", "erasure_preparations"})


@pytest.fixture
def case(migrated_database: Database, tmp_path: Path) -> ErasureCase:
    return create_case(migrated_database, tmp_path / "erasure-policy")


def _preparation(
    case: ErasureCase,
    *,
    request_id: str = "controlled-erasure-request",
    revision: int = 1,
    snapshot: SourceEligibilitySnapshot | None = None,
) -> tuple[SourceEligibilitySnapshot, ErasureOwnerPreparation]:
    with case.runtime.shared():
        source = snapshot or replace(case.runtime.snapshot(), erasure_revision=revision)
        inventory = SQLiteErasureInventory(case.database).inspect(
            case.message_id, counterparty_id="alice", snapshot=source
        )
    intent = ErasureIntent(
        root=inventory.root,
        request_id=request_id,
        decision_id=f"controlled-decision-{revision}",
        floor_id=source.floor_id,
        installation_id=source.installation_id,
        expected_source_generation=source.generation,
        expected_erasure_revision=revision - 1,
        result_erasure_revision=revision,
        reviewed_preview_digest="a" * 64,
        approval_digest="b" * 64,
        schema_revision=inventory.schema_revision,
        schema_digest=inventory.schema_digest,
        store_digest=inventory.store_digest,
        routing_digest=inventory.routing_digest,
        assessment_status="blocked" if inventory.blockers else "complete",
        retention_policy=ERASURE_RETENTION_POLICY_VERSION,
        decided_at=NOW,
        trace_id="controlled-projection-test",
    )
    return source, prepare_owner_contracts(
        intent, inventory, source_generation=source.generation, prepared_at=NOW
    )


def _body_rows(case: ErasureCase) -> dict[str, list[tuple[object, ...]]]:
    return {name: rows for name, rows in stored_rows(case.path).items() if name not in BOOKKEEPING}


def _assert_admitted(case: ErasureCase, snapshot: SourceEligibilitySnapshot) -> None:
    with case.database.session_factory() as session:
        verify_mirror(session, snapshot)


def test_ack_metadata_retains_all_content_numeric_rows_floor_and_provider_counts(
    case: ErasureCase,
) -> None:
    snapshot, preparation = _preparation(case)
    store = SQLiteErasurePreparationStore(case.database)
    before = _body_rows(case)
    floor = stored_state(case)[1:]
    store.admit(snapshot)
    with pytest.raises(SourcePolicyError, match="erasure_reconciliation_required"):
        _assert_admitted(case, snapshot)
    store.persist((preparation,), snapshot=snapshot)
    _assert_admitted(case, snapshot)
    assert (
        store.inspect(
            preparation.intent.request_id, root=preparation.intent.root, snapshot=snapshot
        )
        == preparation
    )
    assert _body_rows(case) == before
    assert stored_state(case)[1:] == floor
    assert provider_counts(case.services) == case.initial_provider_calls
    assert not preparation.intent.physical_erasure
    assert not preparation.intent.execution_authorized
    with case.database.session_factory() as session:
        rows = tuple(session.scalars(select(ErasurePreparationRow)))
        assert len(rows) == len(ErasurePreparationOwner)
        assert all(row.preparation_revision == 1 for row in rows)
        assert all(
            set(row.counts) == {"content_rows", "numeric_rows", "metadata_rows", "uncertain_rows"}
            for row in rows
        )
        assert all(SECRET not in str(row.counts) for row in rows)
        assert all(ROOT_TEXT not in str(row.reason_codes) for row in rows)
    counts = {item.owner: item for item in preparation.assessments}
    assert counts[ErasurePreparationOwner.INTERACTION_LOG].content_rows >= 2
    assert counts[ErasurePreparationOwner.MEMORY_MANAGER].content_rows > 0
    assert counts[ErasurePreparationOwner.RETRIEVAL_INDEX].content_rows > 0
    assert counts[ErasurePreparationOwner.AFFECT].numeric_rows > 0
    assert counts[ErasurePreparationOwner.AFFECT].body_readiness is ErasureBodyReadiness.RETAIN_ONLY
    assert (
        counts[ErasurePreparationOwner.AUDIT].body_readiness
        is ErasureBodyReadiness.BLOCKED_PROVENANCE
    )


def test_equal_preparation_replay_is_noop_while_fresh_assessment_appends(case: ErasureCase) -> None:
    snapshot, preparation = _preparation(case)
    store = SQLiteErasurePreparationStore(case.database)
    store.persist((preparation,), snapshot=snapshot)
    original = stored_state(case)
    retried = replace(
        preparation,
        prepared_at=NOW + timedelta(minutes=1),
        store_digest="c" * 64,
        routing_digest="d" * 64,
    )
    assert retried.preparation_digest == preparation.preparation_digest
    store.persist((retried,), snapshot=snapshot)
    assert stored_state(case) == original
    refreshed = replace(retried, retention_digest="e" * 64)
    store.persist((refreshed,), snapshot=snapshot)
    with case.database.session_factory() as session:
        rows = tuple(session.scalars(select(ErasurePreparationRow)))
        assert len(rows) == 2 * len(ErasurePreparationOwner)
        assert {row.preparation_revision for row in rows} == {1, 2}
        assert {row.preparation_digest for row in rows if row.preparation_revision == 1} == {
            preparation.preparation_digest
        }
    assert (
        store.inspect(
            preparation.intent.request_id, root=preparation.intent.root, snapshot=snapshot
        )
        == refreshed
    )
    _assert_admitted(case, snapshot)


def test_restart_keeps_ack_and_original_intent_but_no_physical_permission(
    case: ErasureCase,
) -> None:
    snapshot, preparation = _preparation(case)
    SQLiteErasurePreparationStore(case.database).persist((preparation,), snapshot=snapshot)
    restarted = create_database(str(case.database.engine.url))
    try:
        store = SQLiteErasurePreparationStore(restarted)
        result = store.inspect(
            preparation.intent.request_id, root=preparation.intent.root, snapshot=snapshot
        )
        assert result == preparation
        assert result is not None
        assert result.intent.execution_authorized is False
        with restarted.session_factory() as session:
            verify_mirror(session, snapshot)
    finally:
        restarted.dispose()


def test_all_trusted_intent_revisions_required_before_head_ack(case: ErasureCase) -> None:
    snapshot, first = _preparation(case)
    store = SQLiteErasurePreparationStore(case.database)
    store.persist((first,), snapshot=snapshot)
    second_snapshot, second = _preparation(case, revision=2, request_id="second-request")
    before = stored_state(case)
    before_body = _body_rows(case)
    with pytest.raises(SourcePolicyError, match="erasure_preparation_intent_coverage_incomplete"):
        store.persist((second,), snapshot=second_snapshot)
    assert stored_state(case) == before
    with pytest.raises(SourcePolicyError, match="erasure_reconciliation_required"):
        _assert_admitted(case, second_snapshot)
    store.persist((first, second), snapshot=second_snapshot)
    _assert_admitted(case, second_snapshot)
    assert _body_rows(case) == before_body


def test_source_only_materialization_cannot_clear_erasure_pending(case: ErasureCase) -> None:
    snapshot, _ = _preparation(case)
    with case.database.session_factory() as session:
        materialize_mirror(session, snapshot)
        session.commit()
    with case.database.session_factory() as session:
        assert session.get(ErasurePolicyBindingRow, snapshot.identity_id) is None
    with pytest.raises(SourcePolicyError, match="erasure_reconciliation_required"):
        _assert_admitted(case, snapshot)


@pytest.mark.parametrize(
    "damage",
    [
        "missing_binding",
        "wrong_floor",
        "wrong_lineage",
        "missing_group",
        "partial_owner_group",
        "bad_counts",
        "bad_digest",
    ],
)
def test_current_binding_cannot_admit_incomplete_or_corrupt_projection(
    case: ErasureCase, damage: str
) -> None:
    snapshot, preparation = _preparation(case)
    store = SQLiteErasurePreparationStore(case.database)
    store.persist((preparation,), snapshot=snapshot)
    with case.database.session_factory() as session:
        if damage == "missing_binding":
            session.execute(delete(ErasurePolicyBindingRow))
        elif damage == "wrong_floor":
            session.execute(update(ErasurePolicyBindingRow).values(floor_id="foreign-floor"))
        elif damage == "wrong_lineage":
            session.execute(update(ErasurePolicyBindingRow).values(lineage_id="foreign-lineage"))
        elif damage == "missing_group":
            session.execute(delete(ErasurePreparationRow))
        elif damage == "partial_owner_group":
            session.execute(
                delete(ErasurePreparationRow).where(ErasurePreparationRow.owner == "positions")
            )
        elif damage == "bad_counts":
            session.execute(
                update(ErasurePreparationRow)
                .where(ErasurePreparationRow.owner == "positions")
                .values(counts={"content_rows": 0})
            )
        else:
            session.execute(update(ErasurePreparationRow).values(preparation_digest="f" * 64))
        session.commit()
    before = stored_state(case)
    with pytest.raises(SourcePolicyError, match="erasure_reconciliation_required"):
        _assert_admitted(case, snapshot)
    assert stored_state(case) == before


def test_historical_tamper_cannot_be_ignored_by_read_or_refresh(case: ErasureCase) -> None:
    snapshot, preparation = _preparation(case)
    store = SQLiteErasurePreparationStore(case.database)
    store.persist((preparation,), snapshot=snapshot)
    fresh = replace(preparation, retention_digest="e" * 64)
    store.persist((fresh,), snapshot=snapshot)
    with case.database.session_factory() as session:
        session.execute(
            delete(ErasurePreparationRow).where(
                ErasurePreparationRow.preparation_revision == 1,
                ErasurePreparationRow.owner == "audit",
            )
        )
        session.commit()
    before = stored_state(case)
    with pytest.raises(SourcePolicyError, match="erasure_preparation_projection_invalid"):
        store.inspect(
            preparation.intent.request_id, root=preparation.intent.root, snapshot=snapshot
        )
    with pytest.raises(SourcePolicyError, match="erasure_preparation_commit_failed"):
        store.persist((fresh,), snapshot=snapshot)
    assert stored_state(case) == before


def test_scope_proof_precedes_receipt_lookup(
    case: ErasureCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot, preparation = _preparation(case)
    store = SQLiteErasurePreparationStore(case.database)
    store.persist((preparation,), snapshot=snapshot)

    def forbidden_lookup(*args: object, **kwargs: object) -> None:
        pytest.fail("scope proof must happen before any receipt lookup")

    monkeypatch.setattr(preparation_adapter, "_latest", forbidden_lookup)
    foreign = replace(preparation.intent.root, counterparty_id="bob")
    before = stored_state(case)
    with pytest.raises(SourcePolicyError, match="canonical_source_unavailable_in_scope"):
        store.inspect(preparation.intent.request_id, root=foreign, snapshot=snapshot)
    assert stored_state(case) == before


def test_unexcluded_root_and_lagged_source_owner_never_prepare(case: ErasureCase) -> None:
    snapshot, preparation = _preparation(case)
    store = SQLiteErasurePreparationStore(case.database)
    without_root = replace(snapshot, excluded_message_ids=frozenset())
    with pytest.raises(SourcePolicyError, match="erasure_requires_source_exclusion"):
        store.inspect("absent-request", root=preparation.intent.root, snapshot=without_root)
    with case.database.session_factory() as session:
        session.execute(
            update(SourceOwnerRepairRow)
            .where(SourceOwnerRepairRow.owner == "positions")
            .values(policy_generation=0)
        )
        session.commit()
    before = stored_state(case)
    with pytest.raises(SourcePolicyError, match="source_owner_reconciliation_required"):
        store.admit(snapshot)
    with pytest.raises(SourcePolicyError, match="source_owner_reconciliation_required"):
        store.persist((preparation,), snapshot=snapshot)
    assert stored_state(case) == before


def test_changed_intent_envelope_same_request_is_rejected(case: ErasureCase) -> None:
    snapshot, preparation = _preparation(case)
    store = SQLiteErasurePreparationStore(case.database)
    store.persist((preparation,), snapshot=snapshot)
    conflicting = replace(preparation, intent=replace(preparation.intent, approval_digest="f" * 64))
    before = stored_state(case)
    with pytest.raises(SourcePolicyError, match="erasure_request_conflict"):
        store.persist((conflicting,), snapshot=snapshot)
    assert stored_state(case) == before


@pytest.mark.parametrize("boundary", ["preparation_insert", "binding_insert", "commit"])
def test_preparation_and_ack_atomicity_on_sql_fault(
    case: ErasureCase, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    snapshot, preparation = _preparation(case)
    store = SQLiteErasurePreparationStore(case.database)
    before = stored_state(case)

    def fail_statement(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        target = (
            "INSERT INTO erasure_preparations"
            if boundary == "preparation_insert"
            else "INSERT INTO erasure_policy_bindings"
        )
        if statement.startswith(target):
            raise SQLAlchemyError("synthetic_projection_fault")

    def fail_commit(session: Session) -> None:
        raise SQLAlchemyError("synthetic_commit_fault")

    if boundary == "commit":
        monkeypatch.setattr(Session, "commit", fail_commit)
    else:
        event.listen(case.database.engine, "before_cursor_execute", fail_statement)
    try:
        with pytest.raises(SourcePolicyError, match="erasure_preparation_commit_failed"):
            store.persist((preparation,), snapshot=snapshot)
    finally:
        if boundary != "commit":
            event.remove(case.database.engine, "before_cursor_execute", fail_statement)
    assert stored_state(case) == before


def test_fixed_owner_contracts_keep_numeric_knowledge_values_as_content(case: ErasureCase) -> None:
    snapshot, actual = _preparation(case)
    routes = [
        ErasureRoute(owner.value, f"metadata_{owner.value}", ErasureDisposition.RETAIN_METADATA, 1)
        for owner in ErasurePreparationOwner
    ]
    routes.extend(
        (
            ErasureRoute(
                "current_models",
                "user_model_claims",
                ErasureDisposition.REDACT_CONTENT,
                3,
                ("value", "normalized_value"),
            ),
            ErasureRoute(
                "current_models",
                "world_model_claims",
                ErasureDisposition.REDACT_CONTENT,
                3,
                ("subject_label", "value"),
            ),
            ErasureRoute(
                "semantic_memory",
                "semantic_claims",
                ErasureDisposition.REDACT_CONTENT,
                3,
                ("value",),
            ),
            ErasureRoute(
                "retrieval_index",
                "episodic_memory_embeddings",
                ErasureDisposition.REMOVE_INDEX,
                1,
                ("vector",),
            ),
            ErasureRoute(
                "positions",
                "satori_position_evidence",
                ErasureDisposition.REDACT_CONTENT,
                1,
                ("quote", "normalized_signature"),
            ),
            ErasureRoute("affect", "affective_states", ErasureDisposition.RETAIN_NUMERIC, 1),
            ErasureRoute(
                "source_policy", "erasure_preparations", ErasureDisposition.RETAIN_METADATA, 150
            ),
            ErasureRoute(
                "source_policy", "erasure_policy_bindings", ErasureDisposition.RETAIN_METADATA, 1
            ),
        )
    )
    inventory = ErasureInventory(
        actual.intent.root,
        actual.schema_revision,
        "a" * 64,
        "b" * 64,
        "c" * 64,
        tuple(routes),
        168,
        0,
        0,
        retention_digest="d" * 64,
    )
    prepared = prepare_owner_contracts(
        actual.intent, inventory, source_generation=snapshot.generation, prepared_at=NOW
    )
    owners = {item.owner: item for item in prepared.assessments}
    assert owners[ErasurePreparationOwner.CURRENT_MODELS].content_rows == 6
    assert owners[ErasurePreparationOwner.CURRENT_MODELS].numeric_rows == 0
    assert (
        owners[ErasurePreparationOwner.SEMANTIC_MEMORY].body_readiness
        is ErasureBodyReadiness.BLOCKED_EXECUTOR
    )
    assert (
        owners[ErasurePreparationOwner.RETRIEVAL_INDEX].body_readiness
        is ErasureBodyReadiness.BLOCKED_EXECUTOR
    )
    assert owners[ErasurePreparationOwner.POSITIONS].content_rows == 1
    assert owners[ErasurePreparationOwner.AFFECT].body_readiness is ErasureBodyReadiness.RETAIN_ONLY
    assert owners[ErasurePreparationOwner.SOURCE_POLICY].metadata_rows == 1
    assert prepared.assessment_status == "complete"
    assert prepared.intent.execution_authorized is False
    with pytest.raises(FrozenInstanceError):
        prepared.retention_digest = "e" * 64  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        prepared.assessments[0].content_rows = 0  # type: ignore[misc]
    with pytest.raises(ValueError, match="erasure_preparation_owner_coverage_incomplete"):
        replace(prepared, assessments=prepared.assessments[:-1])
    with pytest.raises(ValueError, match="erasure_preparation_reason_status_mismatch"):
        replace(
            owners[ErasurePreparationOwner.CURRENT_MODELS],
            reason_codes=("owner_retained_metadata_only",),
        )
