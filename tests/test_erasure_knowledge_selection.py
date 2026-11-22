"""Full-store selection refuses unknown evidence and preserves an independent person."""

from pathlib import Path

import pytest
from sqlalchemy.engine import Connection

from satori.application.erasure.body_contracts import BodyErasureCommand
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.database import Database
from tests.erasure_support import ErasureCase, create_case, provider_counts, stored_state
from tests.test_erasure_knowledge_integration import knowledge_command, prepare_knowledge
from tests.test_source_exclusion_production import _services, _turn


@pytest.fixture
def case(migrated_database: Database, tmp_path: Path) -> ErasureCase:
    return create_case(migrated_database, tmp_path / "knowledge-selection-floor")


def test_independent_counterparty_bodies_and_evidence_survive_selection(case: ErasureCase) -> None:
    foreign, _, _ = _services(
        case.database, case.directory, "knowledge-independent", 3, counterparty="bob"
    )
    # The shared synthetic semantic proposer emits the literal value "код";
    # retain that evidence while keeping Bob's input independent of Alice.
    turn = _turn(foreign, "Независимый код другого собеседника.", "knowledge-independent")
    with case.database.engine.connect() as connection:
        foreign_ids = tuple(
            str(row[0])
            for row in connection.exec_driver_sql(
                "SELECT claim_id FROM semantic_claim_evidence WHERE root_interaction_id=?",
                (turn.interaction_id,),
            )
        )
        assert foreign_ids
        before = tuple(
            tuple(
                connection.exec_driver_sql("SELECT * FROM semantic_claims WHERE claim_id=?", (key,))
            )
            for key in foreign_ids
        )
        evidence_before = tuple(
            tuple(
                connection.exec_driver_sql(
                    "SELECT * FROM semantic_claim_evidence WHERE claim_id=?", (key,)
                )
            )
            for key in foreign_ids
        )
    manager, request = prepare_knowledge(case)
    calls = provider_counts((*case.services, foreign))
    preview = manager.preview(request)
    assert preview.readiness == "ready", preview.blockers
    assert preview.semantic_claims == len(case.claim_ids)
    assert manager.commit(knowledge_command(manager, request)).main_projection_current
    with case.database.engine.connect() as connection:
        after = tuple(
            tuple(
                connection.exec_driver_sql("SELECT * FROM semantic_claims WHERE claim_id=?", (key,))
            )
            for key in foreign_ids
        )
        evidence_after = tuple(
            tuple(
                connection.exec_driver_sql(
                    "SELECT * FROM semantic_claim_evidence WHERE claim_id=?", (key,)
                )
            )
            for key in foreign_ids
        )
    assert after == before
    assert evidence_after == evidence_before
    assert provider_counts((*case.services, foreign)) == calls


def test_unknown_selected_claim_receipt_blocks_without_mutating_any_store(
    case: ErasureCase,
) -> None:
    manager, request = prepare_knowledge(case)
    with case.database.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE source_payload_receipts SET proof_status='unknown' "
            "WHERE kind='semantic_claim' AND payload_id=?",
            (case.claim_ids[0],),
        )
    before = stored_state(case)
    calls = provider_counts(case.services)
    preview = manager.preview(request)
    assert preview.readiness == "blocked"
    assert "knowledge_body_provenance_unproven" in preview.blockers
    command = BodyErasureCommand(
        **request.model_dump(),
        request_id="unknown-knowledge-request",
        expected_generation=preview.generation,
        expected_erasure_revision=preview.erasure_revision,
        expected_body_revision=preview.body_revision,
        approval_digest=preview.approval_digest,
    )
    with pytest.raises(SourcePolicyError, match="body_erasure_plan_blocked"):
        manager.commit(command)
    assert stored_state(case) == before
    assert provider_counts(case.services) == calls


def _insert_copy(connection: Connection, table: str, row: dict[str, object]) -> None:
    # Reused accepted synthetic evidence. These clones exercise the deterministic
    # planner's target census; they are not new model/provider quality evidence.
    columns = tuple(row)
    quoted = ",".join(f'"{column}"' for column in columns)
    marks = ",".join("?" for _ in columns)
    connection.exec_driver_sql(
        f'INSERT INTO "{table}" ({quoted}) VALUES ({marks})', tuple(row[c] for c in columns)
    )


def test_more_than_256_grounded_aggregates_requires_a_new_bounded_policy(case: ErasureCase) -> None:
    manager, request = prepare_knowledge(case)
    with case.database.engine.begin() as connection:
        original = dict(
            connection.exec_driver_sql(
                "SELECT * FROM semantic_claims WHERE claim_id=?", (case.claim_ids[0],)
            )
            .mappings()
            .one()
        )
        evidence = tuple(
            dict(row)
            for row in connection.exec_driver_sql(
                "SELECT * FROM semantic_claim_evidence WHERE claim_id=?", (case.claim_ids[0],)
            ).mappings()
        )
        receipt = dict(
            connection.exec_driver_sql(
                "SELECT * FROM source_payload_receipts "
                "WHERE kind='semantic_claim' AND payload_id=? "
                "ORDER BY payload_version DESC LIMIT 1",
                (case.claim_ids[0],),
            )
            .mappings()
            .one()
        )
        for index in range(255):
            key = f"bounded-synthetic-claim-{index:03}"
            parent = dict(original, claim_id=key, status="retracted", superseded_by_claim_id=None)
            _insert_copy(connection, "semantic_claims", parent)
            for edge in evidence:
                cloned = dict(
                    edge,
                    claim_id=key,
                    semantic_evidence_id=f"bounded-edge-{index:03}-{edge['semantic_evidence_id']}",
                )
                _insert_copy(connection, "semantic_claim_evidence", cloned)
            cloned_receipt = dict(receipt, payload_id=key)
            _insert_copy(connection, "source_payload_receipts", cloned_receipt)
    before = stored_state(case)
    calls = provider_counts(case.services)
    preview = manager.preview(request)
    assert preview.semantic_claims == 257
    assert preview.readiness == "blocked"
    assert "knowledge_target_capacity_exceeded" in preview.blockers
    command = BodyErasureCommand(
        **request.model_dump(),
        request_id="oversized-knowledge-request",
        expected_generation=preview.generation,
        expected_erasure_revision=preview.erasure_revision,
        expected_body_revision=preview.body_revision,
        approval_digest=preview.approval_digest,
    )
    with pytest.raises(SourcePolicyError, match="body_erasure_plan_blocked"):
        manager.commit(command)
    assert stored_state(case) == before
    assert provider_counts(case.services) == calls
