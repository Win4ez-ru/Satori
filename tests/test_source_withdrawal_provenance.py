"""Deterministic owner retirement carries historical DAGs without admitting learning.

These controlled receipts are installed before exclusion. Real independent-floor
and actual provider-input coverage are exercised by the knowledge integration suite.
"""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.engine import Connection

from satori.domain.erasure_knowledge import KnowledgeBodyAction, KnowledgeBodyKind
from satori.domain.source_policy import PayloadRef, SourceEligibilitySnapshot, SourcePolicyError
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.knowledge_material import KNOWLEDGE_OWNER_SPECS
from satori.infrastructure.persistence.models.source_policy import (
    OwnerSourceWithdrawalRow,
    SourceInputRow,
    SourcePayloadRow,
)
from satori.infrastructure.persistence.repositories.models import SQLAlchemyCurrentModelsRepository
from satori.infrastructure.persistence.repositories.positions import SQLAlchemyPositionsRepository
from satori.infrastructure.persistence.repositories.semantic import (
    SQLAlchemySemanticMemoryRepository,
)
from satori.infrastructure.persistence.source_policy import (
    SessionSourceAdmission,
    copy_payload,
    materialize_mirror,
    record_owner_withdrawal,
)
from tests.test_erasure_knowledge_owner import _knowledge_fixture
from tests.test_recovery_whole_state import stored_rows
from tests.test_source_policy_memory import _receipt, _snapshot
from tests.test_stage4_conversation_memory import INTERACTION_TIME

NOW = INTERACTION_TIME + timedelta(days=13)


def _seed(
    database: Database, *, proof: str = "complete"
) -> tuple[KnowledgeBodyAction, SourceEligibilitySnapshot]:
    scope, action = _knowledge_fixture(database)
    initial = _snapshot(scope.identity_id)
    with database.session_factory() as session:
        session.info["source_snapshot"] = initial
        for target in action.proposal.targets:
            if proof == "missing":
                continue
            _receipt(
                session,
                initial,
                PayloadRef(target.kind.value, target.aggregate_id, target.aggregate_version),
                roots=(action.proposal.root.message_id,),
                unknown=proof == "unknown",
            )
        session.flush()
        if proof == "overflow":
            for row in session.scalars(select(SourcePayloadRow)):
                row.proof_status = "overflow"
                row.legacy = False
            session.flush()
        materialize_mirror(session, initial)
        session.commit()
    excluded = replace(
        initial, generation=1, excluded_message_ids=frozenset((action.proposal.root.message_id,))
    )
    with database.session_factory() as session:
        session.info["source_snapshot"] = excluded
        materialize_mirror(session, excluded)
        session.commit()
    return action, excluded


def _withdraw(database: Database, snapshot: SourceEligibilitySnapshot) -> None:
    with database.session_factory() as session:
        session.info["source_snapshot"] = snapshot
        for repository in (
            SQLAlchemyCurrentModelsRepository(session),
            SQLAlchemyPositionsRepository(session),
            SQLAlchemySemanticMemoryRepository(session),
        ):
            repository.withdraw_source_support(
                excluded_message_ids=snapshot.excluded_message_ids,
                request_id="controlled-withdrawal",
                policy_generation=snapshot.generation,
                now=NOW,
            )
        session.commit()


@pytest.mark.parametrize("proof", ["complete", "unknown", "overflow", "missing"])
def test_all_versioned_owners_carry_prior_proof_without_promoting_or_admitting_it(
    migrated_database: Database, proof: str
) -> None:
    database = migrated_database
    action, snapshot = _seed(database, proof=proof)
    path = Path(str(database.engine.url.database))
    before = stored_rows(path)
    _withdraw(database, snapshot)
    after = stored_rows(path)
    untouched = {
        "user_model_claim_evidence",
        "user_model_claim_revisions",
        "world_model_claim_evidence",
        "world_model_claim_revisions",
        "semantic_claim_evidence",
        "semantic_claim_revisions",
        "satori_position_evidence",
        "satori_position_revisions",
        "satori_inclinations",
        "satori_inclination_evidence",
        "satori_inclination_revisions",
        "source_inputs",
    }
    for table in untouched:
        assert after[table] == before[table], table
    expected_proof = "unknown" if proof == "missing" else proof
    with database.session_factory() as session:
        session.info["source_snapshot"] = snapshot
        for target in action.proposal.targets:
            spec = KNOWLEDGE_OWNER_SPECS[target.kind]
            ref = PayloadRef(target.kind.value, target.aggregate_id, target.aggregate_version + 1)
            carried = session.get(SourcePayloadRow, (ref.kind, ref.payload_id, ref.version))
            if target.kind is KnowledgeBodyKind.INCLINATION:
                assert carried is None
                continue
            assert carried is not None
            assert carried.proof_status == expected_proof
            assert carried.legacy is (proof == "unknown")
            assert carried.identity_id == snapshot.identity_id
            assert carried.policy_generation == snapshot.generation
            assert carried.root_message_ids == []
            assert carried.payload_refs == [
                {
                    "kind": target.kind.value,
                    "payload_id": target.aggregate_id,
                    "version": target.aggregate_version,
                }
            ]
            assert carried.eligible is False
            parent = (
                session.connection()
                .exec_driver_sql(
                    f'SELECT aggregate_version,status FROM "{spec.table}" WHERE "{spec.key}"=?',
                    (target.aggregate_id,),
                )
                .one()
            )
            assert tuple(parent) == (target.aggregate_version + 1, "retracted")
            with pytest.raises(SourcePolicyError, match=r"^source_payload_unavailable$"):
                SessionSourceAdmission(session).prepare(root_message_ids=(), payload_refs=(ref,))
        assert session.scalar(select(func.count()).select_from(SourceInputRow)) == 0
        assert session.scalar(select(func.count()).select_from(OwnerSourceWithdrawalRow)) == 5
        # Reconciliation continues to follow the excluded prior node. A COMPLETE
        # historical receipt is not a current grant and never resurrects input.
        materialize_mirror(session, snapshot)
        session.commit()
        assert not any(row.eligible for row in session.scalars(select(SourcePayloadRow)))


def test_payload_carry_failure_rolls_back_owner_states_receipts_and_audit(
    migrated_database: Database,
) -> None:
    database = migrated_database
    _action, snapshot = _seed(database)
    path = Path(str(database.engine.url.database))
    before = stored_rows(path)

    def fail(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _many: bool,
    ) -> None:
        if statement.startswith("INSERT INTO source_payload_receipts"):
            raise RuntimeError("synthetic_withdrawal_carry_fault")

    event.listen(database.engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="synthetic_withdrawal_carry_fault"):
            _withdraw(database, snapshot)
    finally:
        event.remove(database.engine, "before_cursor_execute", fail)
    assert stored_rows(path) == before


@pytest.mark.parametrize("damage", ["wrong_identity", "conflicting_next_receipt"])
def test_carry_denies_foreign_or_conflicting_ancestry_without_overwriting_it(
    migrated_database: Database, damage: str
) -> None:
    database = migrated_database
    action, snapshot = _seed(database)
    target = next(
        item for item in action.proposal.targets if item.kind is KnowledgeBodyKind.USER_MODEL_CLAIM
    )
    with database.session_factory() as session:
        if damage == "wrong_identity":
            row = session.get_one(
                SourcePayloadRow, (target.kind.value, target.aggregate_id, target.aggregate_version)
            )
            row.identity_id = "foreign-synthetic-identity"
        else:
            _receipt(
                session,
                snapshot,
                PayloadRef(target.kind.value, target.aggregate_id, target.aggregate_version + 1),
                roots=(action.proposal.root.message_id,),
            )
        session.commit()
    path = Path(str(database.engine.url.database))
    before = stored_rows(path)
    reason = (
        "source_payload_attribution_unproven"
        if damage == "wrong_identity"
        else "source_payload_receipt_conflict"
    )
    with pytest.raises(SourcePolicyError, match=f"^{reason}$"):
        _withdraw(database, snapshot)
    assert stored_rows(path) == before


def test_replaying_old_withdrawal_never_guesses_a_missing_historical_receipt(
    migrated_database: Database,
) -> None:
    database = migrated_database
    action, snapshot = _seed(database)
    _withdraw(database, snapshot)
    target = next(
        item for item in action.proposal.targets if item.kind is KnowledgeBodyKind.USER_MODEL_CLAIM
    )
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "DELETE FROM source_payload_receipts WHERE kind=? AND payload_id=? "
            "AND payload_version=?",
            (target.kind.value, target.aggregate_id, target.aggregate_version + 1),
        )
    path = Path(str(database.engine.url.database))
    before = stored_rows(path)
    with database.session_factory() as session:
        session.info["source_snapshot"] = snapshot
        record_owner_withdrawal(
            session,
            owner=target.kind.value,
            aggregate_id=target.aggregate_id,
            request_id="controlled-withdrawal",
            policy_generation=snapshot.generation,
            before_status="current",
            after_status="retracted",
            before_version=target.aggregate_version,
            after_version=target.aggregate_version + 1,
            root_message_ids=(action.proposal.root.message_id,),
            now=NOW,
        )
        session.commit()
    assert stored_rows(path) == before


def test_owner_receipt_cannot_bless_a_callback_without_actual_scoped_retirement(
    migrated_database: Database,
) -> None:
    database = migrated_database
    action, snapshot = _seed(database)
    target = next(
        item for item in action.proposal.targets if item.kind is KnowledgeBodyKind.USER_MODEL_CLAIM
    )
    path = Path(str(database.engine.url.database))
    before = stored_rows(path)
    with database.session_factory() as session:
        session.info["source_snapshot"] = snapshot
        with pytest.raises(SourcePolicyError, match=r"^source_withdrawal_transition_unproven$"):
            record_owner_withdrawal(
                session,
                owner=target.kind.value,
                aggregate_id=target.aggregate_id,
                request_id="unproven-callback",
                policy_generation=snapshot.generation,
                before_status="current",
                after_status="retracted",
                before_version=target.aggregate_version,
                after_version=target.aggregate_version + 1,
                root_message_ids=(action.proposal.root.message_id,),
                now=NOW,
            )
    assert stored_rows(path) == before


def test_semantic_double_carry_preserves_one_exact_receipt(migrated_database: Database) -> None:
    database = migrated_database
    action, snapshot = _seed(database)
    _withdraw(database, snapshot)
    target = next(
        item for item in action.proposal.targets if item.kind is KnowledgeBodyKind.SEMANTIC_CLAIM
    )
    path = Path(str(database.engine.url.database))
    before = stored_rows(path)
    with database.session_factory() as session:
        session.info["source_snapshot"] = snapshot
        copy_payload(
            session,
            target.kind.value,
            target.aggregate_id,
            target.aggregate_version,
            target.aggregate_version + 1,
            allow_withheld=True,
        )
        session.commit()
    assert stored_rows(path) == before
