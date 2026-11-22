"""Reflection removes payload copies without changing retained routing or budgets."""

import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from satori.application.reflection.use_cases import ApplyReflectionProposals, GetReflections
from satori.core.personality import (
    PersonalityChangeProposal,
    PersonalityCitation,
    PersonalityCitationRole,
    PersonalityDirection,
)
from satori.core.reflection import ReflectionLineageKind, ReflectionPurpose
from satori.domain.erasure_reflection import (
    ReflectionBodyAction,
    ReflectionBodyProposal,
    ReflectionBodyTarget,
)
from satori.domain.positions import PositionManager
from satori.domain.reflection import ReflectionRunStatus, source_set_hash
from satori.domain.source_policy import CanonicalRootRef, SourcePolicyError
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure_body_codec import encode_body_action
from satori.infrastructure.persistence.models.erasure_bodies import ErasureBodyApplicationRow
from satori.infrastructure.persistence.models.reflection import (
    ReflectionAttemptRow,
    ReflectionOutcomeRow,
    ReflectionProposalRow,
    ReflectionRunRow,
    ReflectionSourceRow,
)
from satori.infrastructure.persistence.positions_uow import SQLAlchemyPositionsUnitOfWork
from satori.infrastructure.persistence.reflection_material import read_reflection_material
from satori.infrastructure.persistence.reflection_uow import SQLAlchemyReflectionUnitOfWork
from satori.infrastructure.persistence.repositories.personality import (
    SQLAlchemyPersonalityRepository,
)
from satori.infrastructure.persistence.repositories.reflection import SQLAlchemyReflectionRepository
from tests.fakes import FrozenClock, SequenceIdGenerator
from tests.test_recovery_whole_state import stored_rows
from tests.test_stage4_conversation_memory import INTERACTION_TIME, activate
from tests.test_stage13_inclinations_persistence import (
    _canonical_position_source,
    _create_reflection_source,
)


def _reflection_fixture(
    database: Database, *, schema_version: int = 2, status: str = "exhausted", history: bool = False
) -> ReflectionBodyAction:
    identity = activate(database).identity.identity_id
    interaction_id, message_id, session_id, _ = _canonical_position_source(
        database, identity_id=identity
    )
    run, source, proposal_id = _create_reflection_source(
        database, identity_id=identity, schema_version=1 if schema_version == 1 else 2
    )
    with database.session_factory() as session:
        parent = session.get(ReflectionRunRow, run.run_id)
        proposal = session.get(ReflectionProposalRow, proposal_id)
        assert parent is not None
        assert proposal is not None
        parent.status = status
        parent.aggregate_version = 2
        parent.completed_at = INTERACTION_TIME
        proposal.payload = {"private_observation": "SYNTHETIC_REFLECTION_BODY", "delta": 0.01}
        if schema_version == 3:
            source = replace(
                source,
                affective_transition_id=None,
                affective_state_version=None,
                affective_signal_hash=None,
                upstream_lineage_kind=ReflectionLineageKind.POSITION,
                upstream_lineage_id="inclination-source-position",
            )
            parent.schema_version = parent.policy_version = 3
            parent.purpose = ReflectionPurpose.PERSONALITY_EVOLUTION.value
            parent.source_set_hash = source_set_hash(
                (source,), schema_version=3, purpose=ReflectionPurpose.PERSONALITY_EVOLUTION
            )
            edge = session.get(ReflectionSourceRow, source.source_id)
            assert edge is not None
            edge.affective_transition_id = None
            edge.affective_state_version = None
            edge.affective_signal_hash = None
            edge.upstream_lineage_kind = "position"
            edge.upstream_lineage_id = source.upstream_lineage_id
            proposal.target_owner = "personality"
        session.add(
            ReflectionProposalRow(
                proposal_id="reflection-disabled-values",
                run_id=run.run_id,
                ordinal=1,
                target_owner="values",
                payload={"private_observation": "SYNTHETIC_VALUES_BODY"},
                evidence_source_ids=[source.source_id],
                created_at=INTERACTION_TIME,
            )
        )
        if history:
            parent.attempt_count = 1
            session.add(
                ReflectionAttemptRow(
                    attempt_id="retained-attempt",
                    run_id=run.run_id,
                    ordinal=1,
                    status="succeeded",
                    reason_code="proposals_ready",
                    provider="fake",
                    model="fixture",
                    formation_method="fixture.reflection",
                    started_at=INTERACTION_TIME,
                    finished_at=INTERACTION_TIME,
                    metrics={"tokens": 10},
                )
            )
            session.add(
                ReflectionOutcomeRow(
                    outcome_id="retained-outcome",
                    proposal_id=proposal_id,
                    target_policy_version=1,
                    decision="rejected",
                    reason_code="owner_disabled",
                    target_aggregate_type=None,
                    target_aggregate_id=None,
                    decided_at=INTERACTION_TIME,
                )
            )
        session.commit()
    with database.session_factory() as session:
        ids = SQLAlchemyReflectionRepository(session).list_proposal_ids(run.run_id)
        material = read_reflection_material(session, ids, require_terminal=False)
        targets = tuple(
            sorted(
                (
                    ReflectionBodyTarget(
                        item,
                        run.run_id,
                        2,
                        1,
                        material.digest(item),
                        material.digest(item, terminal=True),
                        material.metadata_reference_count(item),
                    )
                    for item in ids
                ),
                key=lambda item: item.proposal_id,
            )
        )
    root = CanonicalRootRef(
        identity_id=identity,
        lineage_id="reflection-lineage",
        counterparty_id="alice",
        session_id=session_id,
        interaction_id=interaction_id,
        message_id=message_id,
    )
    body_proposal = ReflectionBodyProposal(
        root,
        "reflection-intent",
        "reflection-request",
        1,
        1,
        0,
        "a" * 64,
        "b" * 64,
        "c" * 64,
        targets,
    )
    return ReflectionBodyAction(
        body_proposal,
        "reflection-action",
        "reflection-floor",
        "reflection-installation",
        1,
        INTERACTION_TIME,
        "reflection-trace",
    )


def _apply(database: Database, action: ReflectionBodyAction) -> None:
    with database.session_factory() as session:
        session.add(
            ErasureBodyApplicationRow(
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
        )
        session.flush()
        SQLAlchemyReflectionRepository(session).erase_payload_bodies(
            action.proposal.targets, action_id=action.action_id
        )
        session.commit()


@pytest.mark.parametrize("schema_version", [1, 2, 3])
def test_all_payloads_become_sql_null_with_exact_retained_empty_outcome_history(
    migrated_database: Database, schema_version: int
) -> None:
    action = _reflection_fixture(migrated_database, schema_version=schema_version)
    path = Path(str(migrated_database.engine.url.database))
    before = stored_rows(path)
    _apply(migrated_database, action)
    after = stored_rows(path)
    for table in before.keys() - {"reflection_proposals", "erasure_body_applications"}:
        assert after[table] == before[table]
    with migrated_database.engine.connect() as connection:
        assert all(
            tuple(row) == (None, "null", "erased", action.action_id)
            for row in connection.exec_driver_sql(
                "SELECT payload,typeof(payload),body_state,body_action_id FROM reflection_proposals"
            )
        )
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []

    def factory() -> SQLAlchemyReflectionUnitOfWork:
        return SQLAlchemyReflectionUnitOfWork(migrated_database.session_factory)

    inspection = GetReflections(factory).inspect(
        action.proposal.targets[0].run_id, identity_id=action.proposal.root.identity_id
    )
    assert inspection is not None
    assert inspection.proposals == ()
    assert len(inspection.unavailable_proposals) == 2
    assert inspection.outcomes == ()
    assert all(not hasattr(item, "payload") for item in inspection.unavailable_proposals)


@pytest.mark.parametrize("status", ["completed", "exhausted"])
def test_terminal_apply_replay_reads_no_proposal_body_or_source_quote(
    migrated_database: Database, status: str
) -> None:
    action = _reflection_fixture(migrated_database, status=status)
    _apply(migrated_database, action)
    statements: list[str] = []

    def observe(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _many: bool,
    ) -> None:
        statements.append(statement)

    event.listen(migrated_database.engine, "before_cursor_execute", observe)
    try:
        use_case = ApplyReflectionProposals(
            lambda: SQLAlchemyReflectionUnitOfWork(migrated_database.session_factory),
            lambda: SQLAlchemyPositionsUnitOfWork(migrated_database.session_factory),
            PositionManager(),
            FrozenClock(INTERACTION_TIME),
            SequenceIdGenerator(),
        )
        if status == "completed":
            assert (
                use_case.execute(action.proposal.targets[0].run_id, trace_id="replay").status
                is ReflectionRunStatus.COMPLETED
            )
        else:
            with pytest.raises(ValueError, match="no proposals ready"):
                use_case.execute(action.proposal.targets[0].run_id, trace_id="replay")
    finally:
        event.remove(migrated_database.engine, "before_cursor_execute", observe)
    assert not any("reflection_proposals" in statement for statement in statements)
    assert not any("reflection_sources" in statement for statement in statements)
    assert not any("conversation_messages" in statement for statement in statements)


@pytest.mark.parametrize("damage", ["body", "terminal", "run_version", "references", "scope"])
def test_all_target_preimages_are_checked_before_any_body_write(
    migrated_database: Database, damage: str
) -> None:
    action = _reflection_fixture(migrated_database)
    target = action.proposal.targets[-1]
    if damage == "body":
        target = replace(target, body_digest="f" * 64)
    elif damage == "terminal":
        target = replace(target, terminal_digest="f" * 64)
    elif damage == "run_version":
        target = replace(target, run_version=target.run_version + 1)
    elif damage == "references":
        target = replace(target, metadata_reference_count=target.metadata_reference_count + 1)
    else:
        action = replace(
            action,
            proposal=replace(
                action.proposal, root=replace(action.proposal.root, counterparty_id="bob")
            ),
        )
    action = replace(
        action, proposal=replace(action.proposal, targets=(*action.proposal.targets[:-1], target))
    )
    before = stored_rows(Path(str(migrated_database.engine.url.database)))
    with pytest.raises(
        SourcePolicyError, match=r"body_owner_state_changed|body_action_target_mismatch"
    ):
        _apply(migrated_database, action)
    assert stored_rows(Path(str(migrated_database.engine.url.database))) == before


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE reflection_proposals SET payload='{}'",
        "DELETE FROM reflection_proposals",
        "UPDATE reflection_proposals SET evidence_source_ids='[]'",
        "UPDATE reflection_runs SET attempt_count=0",
        "DELETE FROM reflection_runs",
        "INSERT OR REPLACE INTO reflection_runs SELECT * FROM reflection_runs",
        "UPDATE reflection_sources SET content_hash='forged'",
        "DELETE FROM reflection_sources",
        "UPDATE reflection_attempts SET metrics='{}'",
        "DELETE FROM reflection_attempts",
        "UPDATE reflection_outcomes SET reason_code='forged'",
        "DELETE FROM reflection_outcomes",
        "INSERT INTO reflection_proposals SELECT proposal_id||'new',run_id,2,target_owner,'{}',"
        "evidence_source_ids,created_at,'available',NULL FROM reflection_proposals LIMIT 1",
    ],
)
def test_terminal_sql_guards_preserve_bodies_and_whole_retained_parent_closure(
    migrated_database: Database, statement: str
) -> None:
    action = _reflection_fixture(migrated_database, history=True)
    _apply(migrated_database, action)
    before = stored_rows(Path(str(migrated_database.engine.url.database)))
    with (
        migrated_database.engine.begin() as connection,
        pytest.raises(IntegrityError, match="reflection_body_terminal"),
    ):
        connection.exec_driver_sql(statement)
    assert stored_rows(Path(str(migrated_database.engine.url.database))) == before


def test_personality_source_resolution_never_maps_an_erased_payload(
    migrated_database: Database,
) -> None:
    action = _reflection_fixture(migrated_database, schema_version=3)
    _apply(migrated_database, action)
    proposal_id = next(
        target.proposal_id
        for target in action.proposal.targets
        if target.proposal_id != "reflection-disabled-values"
    )
    candidate = PersonalityChangeProposal(
        trait_key="curiosity",
        direction=PersonalityDirection.INCREASE,
        confidence=0.9,
        citations=tuple(
            PersonalityCitation(
                source_id=f"private-source-{index}", role=PersonalityCitationRole.SUPPORT
            )
            for index in range(8)
        ),
        expected_personality_version=1,
    )
    statements: list[str] = []

    def observe(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _many: bool,
    ) -> None:
        statements.append(statement)

    event.listen(migrated_database.engine, "before_cursor_execute", observe)
    try:
        with (
            migrated_database.session_factory() as session,
            pytest.raises(ValueError, match="exact V3 personality run"),
        ):
            SQLAlchemyPersonalityRepository(session).resolve_reflection_sources(
                identity_id=action.proposal.root.identity_id,
                reflection_run_id=action.proposal.targets[0].run_id,
                reflection_proposal_id=proposal_id,
                proposal=candidate,
            )
    finally:
        event.remove(migrated_database.engine, "before_cursor_execute", observe)
    payload_reads = [
        statement
        for statement in statements
        if statement.startswith("SELECT") and "reflection_proposals.payload" in statement
    ]
    assert payload_reads
    assert all(
        "body_state =" in statement
        and "body_action_id IS NULL" in statement
        and "payload IS NOT NULL" in statement
        for statement in payload_reads
    )


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT OR REPLACE INTO reflection_proposals SELECT proposal_id,'fresh-run',1,target_owner,"
        "'{}',evidence_source_ids,created_at,'available',NULL FROM reflection_proposals "
        "WHERE proposal_id!='fresh-proposal' LIMIT 1",
        "INSERT OR REPLACE INTO reflection_sources(source_id,run_id,ordinal,kind,evidence_edge_id,"
        "evidence_edge_version,root_interaction_id,root_message_id,root_counterparty_id,observed_at,"
        "content_hash,upstream_lineage_kind,upstream_lineage_id,affective_transition_id,"
        "affective_state_version,affective_signal_hash) SELECT source_id,'fresh-run',ordinal,kind,"
        "evidence_edge_id,evidence_edge_version,root_interaction_id,root_message_id,"
        "root_counterparty_id,observed_at,content_hash,upstream_lineage_kind,upstream_lineage_id,"
        "affective_transition_id,affective_state_version,affective_signal_hash "
        "FROM reflection_sources",
        "INSERT OR REPLACE INTO reflection_attempts SELECT attempt_id,'fresh-run',ordinal,status,"
        "reason_code,provider,model,formation_method,started_at,finished_at,metrics "
        "FROM reflection_attempts",
        "INSERT OR REPLACE INTO reflection_outcomes SELECT outcome_id,'fresh-proposal',"
        "target_policy_version,decision,reason_code,target_aggregate_type,target_aggregate_id,"
        "decided_at FROM reflection_outcomes",
        "INSERT OR REPLACE INTO reflection_runs(run_id,run_key,identity_id,schema_version,"
        "policy_version,purpose,trigger_kind,source_set_hash,status,aggregate_version,attempt_count,"
        "created_at,updated_at,completed_at) SELECT 'replace-run',run_key,identity_id,"
        "schema_version,policy_version,purpose,trigger_kind,source_set_hash,status,aggregate_version,"
        "attempt_count,created_at,updated_at,completed_at FROM reflection_runs "
        "WHERE run_id!='fresh-run'",
        "INSERT OR REPLACE INTO reflection_proposals SELECT proposal_id||'new',run_id,ordinal,"
        "target_owner,'{}',evidence_source_ids,created_at,'available',NULL "
        "FROM reflection_proposals WHERE run_id!='fresh-run' LIMIT 1",
        "INSERT OR REPLACE INTO reflection_outcomes SELECT outcome_id||'new',proposal_id,"
        "target_policy_version,decision,reason_code,target_aggregate_type,target_aggregate_id,"
        "decided_at FROM reflection_outcomes",
    ],
)
def test_cross_parent_replace_cannot_destroy_frozen_membership_with_sqlite_defaults(
    migrated_database: Database, statement: str
) -> None:
    action = _reflection_fixture(migrated_database, history=True)
    _apply(migrated_database, action)
    path = Path(str(migrated_database.engine.url.database))
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA recursive_triggers=OFF")
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (0,)
        assert connection.execute("PRAGMA recursive_triggers").fetchone() == (0,)
        connection.execute(
            "INSERT INTO reflection_runs(run_id,run_key,identity_id,schema_version,policy_version,"
            "purpose,trigger_kind,source_set_hash,status,aggregate_version,attempt_count,created_at,"
            "updated_at,completed_at) SELECT 'fresh-run',?,identity_id,schema_version,"
            "policy_version,purpose,trigger_kind,source_set_hash,status,aggregate_version,"
            "attempt_count,created_at,updated_at,completed_at FROM reflection_runs LIMIT 1",
            ("f" * 64,),
        )
        connection.execute(
            "INSERT INTO reflection_proposals SELECT 'fresh-proposal','fresh-run',0,target_owner,"
            "'{}',evidence_source_ids,created_at,'available',NULL FROM reflection_proposals LIMIT 1"
        )
        connection.commit()
        before = stored_rows(path)
        with pytest.raises(sqlite3.IntegrityError, match="reflection_body_terminal"):
            connection.execute(statement)
        connection.rollback()
        assert stored_rows(path) == before
