"Existing knowledge owners remove bodies while preserving numerical and attribution rows."

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from satori.domain.erasure_dialogue import HistoricalEvidenceTable
from satori.domain.erasure_knowledge import (
    KnowledgeBodyAction,
    KnowledgeBodyKind,
    KnowledgeBodyProposal,
    KnowledgeBodyTarget,
    KnowledgeWitnessOrigin,
    KnowledgeWitnessTransition,
)
from satori.domain.inclinations import INCLINATION_POLICY_VERSION
from satori.domain.memory_scope import MemoryScope
from satori.domain.reflection import ReflectionOutcome, ReflectionOutcomeDecision
from satori.domain.source_policy import CanonicalRootRef, PayloadRef, SourcePolicyError
from satori.infrastructure.persistence.canonical_provenance import canonical_row_digest
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure_body_codec import encode_body_action
from satori.infrastructure.persistence.knowledge_material import (
    KNOWLEDGE_OWNER_SPECS,
    knowledge_material_digest,
)
from satori.infrastructure.persistence.models.erasure_bodies import ErasureBodyApplicationRow
from satori.infrastructure.persistence.repositories.models import SQLAlchemyCurrentModelsRepository
from satori.infrastructure.persistence.repositories.positions import SQLAlchemyPositionsRepository
from satori.infrastructure.persistence.repositories.semantic import (
    SQLAlchemySemanticMemoryRepository,
)
from satori.infrastructure.persistence.source_policy import require_current_payloads
from tests.test_memory_scope_integration import _probe, _turn
from tests.test_recovery_whole_state import stored_rows
from tests.test_source_policy_owners import _form_models
from tests.test_stage4_conversation_memory import INTERACTION_TIME
from tests.test_stage13_inclinations_persistence import (
    _applied_inclination,
    _canonical_position_source,
    _create_reflection_source,
)


def _knowledge_fixture(database: Database) -> tuple[MemoryScope, KnowledgeBodyAction]:
    identity = _form_models(database)[0]
    _, root_id, session_id, _ = _canonical_position_source(database, identity_id=identity)
    _, source, proposal_id = _create_reflection_source(database, identity_id=identity)
    inclination, evaluation = _applied_inclination(
        identity_id=identity,
        source=source,
        source_session_id=session_id,
        outcome_id="knowledge-outcome",
    )
    outcome = ReflectionOutcome(
        "knowledge-outcome",
        proposal_id,
        INCLINATION_POLICY_VERSION,
        ReflectionOutcomeDecision.ACCEPTED,
        evaluation.reason_code,
        "satori_inclinations",
        inclination.inclination_id,
        inclination.updated_at,
    )
    with database.session_factory() as session:
        SQLAlchemyPositionsRepository(session).record_inclination_reflection_decision(
            outcome,
            evaluation,
            identity_id=identity,
            trace_id="knowledge-fixture",
            audit_event_id="knowledge-fixture",
        )
        session.commit()
    probe = _probe(database, counterparty="alice", prefix="knowledge-semantic", day=10)
    _turn(probe, "Меня зовут KNOWLEDGE_SYNTHETIC.", "knowledge-semantic")
    with database.engine.connect() as connection:
        targets = []
        for kind, spec in KNOWLEDGE_OWNER_SPECS.items():
            parents = tuple(
                dict(row)
                for row in connection.exec_driver_sql(f"SELECT * FROM {spec.table}").mappings()
            )
            assert len(parents) == 1
            parent = parents[0]
            evidence = tuple(
                dict(row)
                for row in connection.exec_driver_sql(
                    f"SELECT * FROM {spec.evidence_table}"
                ).mappings()
            )
            revisions = tuple(
                dict(row)
                for row in connection.exec_driver_sql(
                    f"SELECT * FROM {spec.revision_table}"
                ).mappings()
            )
            transitions: tuple[KnowledgeWitnessTransition, ...] = ()
            if kind is KnowledgeBodyKind.POSITION:
                transitions = tuple(
                    KnowledgeWitnessTransition(
                        HistoricalEvidenceTable.POSITION,
                        str(edge[spec.evidence_key]),
                        CanonicalRootRef(
                            "knowledge-lineage",
                            identity,
                            "alice",
                            session_id,
                            str(edge["source_interaction_id"]),
                            str(edge["source_message_id"]),
                        ),
                        canonical_row_digest(edge),
                        canonical_row_digest(dict(edge, quote=None, normalized_signature=None)),
                        KnowledgeWitnessOrigin.LIVE,
                    )
                    for edge in evidence
                )
            targets.append(
                KnowledgeBodyTarget(
                    kind,
                    str(parent[spec.key]),
                    int(str(parent["aggregate_version"])),
                    knowledge_material_digest(kind, parent, evidence, revisions, terminal=False),
                    knowledge_material_digest(kind, parent, evidence, revisions, terminal=True),
                    transitions,
                )
            )
        root_row = connection.exec_driver_sql(
            "SELECT interaction_id FROM conversation_messages WHERE message_id=?", (root_id,)
        ).one()
    root = CanonicalRootRef(
        "knowledge-lineage", identity, "alice", session_id, str(root_row[0]), root_id
    )
    proposal = KnowledgeBodyProposal(
        root,
        "knowledge-intent",
        "knowledge-request",
        1,
        1,
        0,
        "a" * 64,
        "b" * 64,
        "c" * 64,
        tuple(sorted(targets, key=lambda item: (item.kind.value, item.aggregate_id))),
    )
    action = KnowledgeBodyAction(
        proposal,
        "knowledge-action",
        "knowledge-floor",
        "knowledge-installation",
        1,
        INTERACTION_TIME + timedelta(days=11),
        "knowledge-trace",
    )
    return MemoryScope(identity, "alice"), action


def _receipt(action: KnowledgeBodyAction) -> ErasureBodyApplicationRow:
    return ErasureBodyApplicationRow(
        action_id=action.action_id,
        request_id=action.proposal.request_id,
        identity_id=action.proposal.root.identity_id,
        root_message_id=action.proposal.root.message_id,
        floor_id=action.floor_id,
        lineage_id=action.proposal.root.lineage_id,
        body_revision=action.result_body_revision,
        action=encode_body_action(action),
        applied_at=action.decided_at,
    )


def _apply(database: Database, action: KnowledgeBodyAction) -> None:
    with database.session_factory() as session:
        session.add(_receipt(action))
        session.flush()
        for repository, kinds in (
            (SQLAlchemySemanticMemoryRepository(session), {KnowledgeBodyKind.SEMANTIC_CLAIM}),
            (
                SQLAlchemyCurrentModelsRepository(session),
                {KnowledgeBodyKind.USER_MODEL_CLAIM, KnowledgeBodyKind.WORLD_MODEL_CLAIM},
            ),
            (
                SQLAlchemyPositionsRepository(session),
                {KnowledgeBodyKind.POSITION, KnowledgeBodyKind.INCLINATION},
            ),
        ):
            repository.erase_knowledge_bodies(
                tuple(target for target in action.proposal.targets if target.kind in kinds),
                action_id=action.action_id,
            )
        session.commit()


def test_all_knowledge_bodies_are_sql_null_and_normal_reads_hold_reserved_keys(
    migrated_database: Database,
) -> None:
    scope, action = _knowledge_fixture(migrated_database)
    path = Path(str(migrated_database.engine.url.database))
    before = stored_rows(path)
    _apply(migrated_database, action)
    after = stored_rows(path)
    changed = {spec.table for spec in KNOWLEDGE_OWNER_SPECS.values()} | {
        "satori_position_evidence",
        "erasure_body_applications",
    }
    for table in before.keys() - changed:
        assert after[table] == before[table]
    with migrated_database.engine.connect() as connection:
        for target in action.proposal.targets:
            spec = KNOWLEDGE_OWNER_SPECS[target.kind]
            row = dict(
                connection.exec_driver_sql(
                    f"SELECT * FROM {spec.table} WHERE {spec.key}=?", (target.aggregate_id,)
                )
                .mappings()
                .one()
            )
            assert all(row[field] is None for field in spec.content_columns)
            assert row["aggregate_version"] == target.aggregate_version
            assert row["body_state"] == "erased"
            assert row["body_action_id"] == action.action_id
            previous = dict(zip(row, before[spec.table][0], strict=True))
            for column in row.keys() - set(spec.content_columns) - {"body_state", "body_action_id"}:
                assert row[column] == previous[column]
        assert connection.exec_driver_sql(
            "SELECT quote,normalized_signature FROM satori_position_evidence"
        ).one() == (None, None)
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    with migrated_database.session_factory() as session:
        semantic = SQLAlchemySemanticMemoryRepository(session)
        models = SQLAlchemyCurrentModelsRepository(session)
        positions = SQLAlchemyPositionsRepository(session)
        assert semantic.list_claims(scope=scope) == ()
        assert (
            models.list_user_claims(
                identity_id=scope.identity_id, counterparty_id=scope.counterparty_id
            )
            == ()
        )
        assert (
            models.list_world_claims(
                identity_id=scope.identity_id, counterparty_id=scope.counterparty_id
            )
            == ()
        )
        assert positions.list_positions(identity_id=scope.identity_id) == ()
        assert positions.list_inclinations(identity_id=scope.identity_id) == ()
        assert positions.list_inclination_references(identity_id=scope.identity_id) == ()
        assert len(positions.list_held_inclination_keys(identity_id=scope.identity_id)) == 1
        for target in action.proposal.targets:
            repo = (
                semantic
                if target.kind is KnowledgeBodyKind.SEMANTIC_CLAIM
                else models
                if target.kind
                in {KnowledgeBodyKind.USER_MODEL_CLAIM, KnowledgeBodyKind.WORLD_MODEL_CLAIM}
                else positions
            )
            header = repo.get_erased_knowledge_header(target.kind, target.aggregate_id)
            assert header is not None
            assert header.action_id == action.action_id
            assert header.evidence_ids
            assert not hasattr(header, "value")
            assert not hasattr(header, "proposition")
            with pytest.raises(SourcePolicyError, match="knowledge_body_unavailable"):
                require_current_payloads(
                    session,
                    (PayloadRef(target.kind.value, target.aggregate_id, target.aggregate_version),),
                )
        assert (
            semantic.get_claim(
                next(
                    t.aggregate_id
                    for t in action.proposal.targets
                    if t.kind is KnowledgeBodyKind.SEMANTIC_CLAIM
                ),
                scope=scope,
            )
            is None
        )
        assert (
            models.get_user_claim(
                next(
                    t.aggregate_id
                    for t in action.proposal.targets
                    if t.kind is KnowledgeBodyKind.USER_MODEL_CLAIM
                )
            )
            is None
        )
        assert (
            models.get_world_claim(
                next(
                    t.aggregate_id
                    for t in action.proposal.targets
                    if t.kind is KnowledgeBodyKind.WORLD_MODEL_CLAIM
                )
            )
            is None
        )
        assert (
            positions.get_position(
                next(
                    t.aggregate_id
                    for t in action.proposal.targets
                    if t.kind is KnowledgeBodyKind.POSITION
                )
            )
            is None
        )
        assert (
            positions.get_inclination(
                next(
                    t.aggregate_id
                    for t in action.proposal.targets
                    if t.kind is KnowledgeBodyKind.INCLINATION
                )
            )
            is None
        )


@pytest.mark.parametrize("damage", ["body", "terminal", "evidence", "version", "scope"])
def test_owner_preflight_rejects_stale_or_forged_targets_before_any_mutation(
    migrated_database: Database, damage: str
) -> None:
    _, action = _knowledge_fixture(migrated_database)
    target = next(
        item for item in action.proposal.targets if item.kind is KnowledgeBodyKind.POSITION
    )
    if damage == "body":
        target = replace(target, body_digest="d" * 64)
    elif damage == "terminal":
        target = replace(target, terminal_digest="d" * 64)
    elif damage == "evidence":
        target = replace(target, witness_transitions=())
    elif damage == "version":
        target = replace(target, aggregate_version=target.aggregate_version + 1)
    else:
        action = replace(
            action,
            proposal=replace(
                action.proposal,
                root=replace(action.proposal.root, counterparty_id="bob"),
                targets=(replace(target, witness_transitions=()),),
            ),
        )
    action = (
        replace(action, proposal=replace(action.proposal, targets=(target,)))
        if damage != "scope"
        else action
    )
    before = stored_rows(Path(str(migrated_database.engine.url.database)))

    def apply_invalid() -> None:
        with migrated_database.session_factory() as session:
            session.add(_receipt(action))
            session.flush()
            SQLAlchemyPositionsRepository(session).erase_knowledge_bodies(
                action.proposal.targets, action_id=action.action_id
            )
            session.commit()

    with pytest.raises(SourcePolicyError):
        apply_invalid()
    assert stored_rows(Path(str(migrated_database.engine.url.database))) == before


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE semantic_claims SET confidence=0.1",
        "DELETE FROM semantic_claims",
        "UPDATE user_model_claims SET aggregate_version=aggregate_version+1",
        "UPDATE world_model_claims SET subject_label='resurrection'",
        "UPDATE satori_positions SET status='active'",
        "UPDATE satori_inclinations SET score=0.2",
        (
            "UPDATE satori_position_evidence SET quote='resurrection',normalized_signatu"
            "re='resurrection'"
        ),
        "DELETE FROM satori_position_evidence",
        "DELETE FROM satori_inclination_evidence",
        "DELETE FROM satori_inclination_revisions",
        "DELETE FROM user_model_claim_evidence",
        "DELETE FROM world_model_claim_revisions",
        "DELETE FROM semantic_claim_evidence",
        "UPDATE semantic_claim_revisions SET new_confidence=0.1",
        (
            "INSERT INTO satori_position_evidence SELECT evidence_id||'new',position_id,"
            "source_message_id,source_interaction_id,source_counterparty_id,'new','new',"
            "role,observed_at FROM satori_position_evidence"
        ),
    ],
)
def test_terminal_sql_guards_protect_bodies_evidence_and_numeric_trajectory(
    migrated_database: Database, statement: str
) -> None:
    _, action = _knowledge_fixture(migrated_database)
    _apply(migrated_database, action)
    before = stored_rows(Path(str(migrated_database.engine.url.database)))
    with (
        migrated_database.engine.begin() as connection,
        pytest.raises(IntegrityError, match="knowledge_body_terminal"),
    ):
        connection.exec_driver_sql(statement)
    assert stored_rows(Path(str(migrated_database.engine.url.database))) == before


def test_prepared_actual_input_denied_after_body_null_at_same_owner_versions(
    migrated_database: Database,
) -> None:
    from satori.infrastructure.persistence.source_policy import (
        SessionSourceAdmission,
        materialize_mirror,
        require_source_inputs,
    )
    from tests.test_source_policy_memory import _receipt as dependency_receipt
    from tests.test_source_policy_memory import _snapshot

    scope, action = _knowledge_fixture(migrated_database)
    snapshot = _snapshot(scope.identity_id)
    refs = tuple(
        PayloadRef(target.kind.value, target.aggregate_id, target.aggregate_version)
        for target in action.proposal.targets
    )
    with migrated_database.session_factory() as session:
        session.info["source_snapshot"] = snapshot
        for ref in refs:
            dependency_receipt(session, snapshot, ref)
        session.flush()
        materialize_mirror(session, snapshot)
        input_id = SessionSourceAdmission(session).prepare(root_message_ids=(), payload_refs=refs)
        require_source_inputs(session, input_id)
        session.commit()
    _apply(migrated_database, action)
    with migrated_database.session_factory() as session:
        session.info["source_snapshot"] = snapshot
        with pytest.raises(SourcePolicyError, match="knowledge_body_unavailable"):
            require_source_inputs(session, input_id)


def test_permanent_inclination_identity_cannot_be_recreated_or_decay_after_terminal(
    migrated_database: Database,
) -> None:
    _, action = _knowledge_fixture(migrated_database)
    target = next(
        item for item in action.proposal.targets if item.kind is KnowledgeBodyKind.INCLINATION
    )
    with migrated_database.session_factory() as session:
        prior = SQLAlchemyPositionsRepository(session).get_inclination(target.aggregate_id)
        assert prior is not None
    _apply(migrated_database, action)
    before = stored_rows(Path(str(migrated_database.engine.url.database)))
    with migrated_database.session_factory() as session:
        repository = SQLAlchemyPositionsRepository(session)
        with pytest.raises(SourcePolicyError, match="knowledge_body_unavailable"):
            repository._apply_inclination(
                replace(prior, aggregate_version=prior.aggregate_version + 1)
            )
    assert stored_rows(Path(str(migrated_database.engine.url.database))) == before


@pytest.mark.parametrize(
    ("value_kind", "stored", "normalized"), [("number", "42", "42"), ("boolean", "true", "true")]
)
def test_numeric_and_boolean_claim_values_are_content_and_become_sql_null(
    migrated_database: Database, value_kind: str, stored: str, normalized: str
) -> None:
    _, action = _knowledge_fixture(migrated_database)
    target = next(
        item for item in action.proposal.targets if item.kind is KnowledgeBodyKind.USER_MODEL_CLAIM
    )
    spec = KNOWLEDGE_OWNER_SPECS[target.kind]
    with migrated_database.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE user_model_claims SET value_kind=?,value=?,normalized_value=? WHERE claim_id=?",
            (value_kind, stored, normalized, target.aggregate_id),
        )
        parent = dict(
            connection.exec_driver_sql(
                "SELECT * FROM user_model_claims WHERE claim_id=?", (target.aggregate_id,)
            )
            .mappings()
            .one()
        )
        evidence = tuple(
            dict(row)
            for row in connection.exec_driver_sql(
                "SELECT * FROM user_model_claim_evidence WHERE claim_id=?", (target.aggregate_id,)
            ).mappings()
        )
        revisions = tuple(
            dict(row)
            for row in connection.exec_driver_sql(
                "SELECT * FROM user_model_claim_revisions WHERE claim_id=?", (target.aggregate_id,)
            ).mappings()
        )
    with migrated_database.session_factory() as session:
        parsed = SQLAlchemyCurrentModelsRepository(session).get_user_claim(target.aggregate_id)
        assert parsed is not None
        assert parsed.value == (42 if value_kind == "number" else True)
    updated = replace(
        target,
        body_digest=knowledge_material_digest(
            target.kind, parent, evidence, revisions, terminal=False
        ),
        terminal_digest=knowledge_material_digest(
            target.kind, parent, evidence, revisions, terminal=True
        ),
    )
    action = replace(
        action,
        proposal=replace(
            action.proposal,
            targets=tuple(updated if item == target else item for item in action.proposal.targets),
        ),
    )
    _apply(migrated_database, action)
    with migrated_database.engine.connect() as connection:
        row = connection.exec_driver_sql(
            f"SELECT value,typeof(value),normalized_value FROM {spec.table} WHERE claim_id=?",
            (target.aggregate_id,),
        ).one()
        assert tuple(row) == (None, "null", None)
