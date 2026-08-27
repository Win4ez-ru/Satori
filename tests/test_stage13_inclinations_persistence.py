"""Stage 13 inclination persistence, Reflection V2 attachment, and replay tests."""

from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from satori.core.inclinations import InclinationKind, InclinationStateReference
from satori.domain.inclinations import (
    INCLINATION_NORMALIZATION_VERSION,
    INCLINATION_POLICY_VERSION,
    INCLINATION_SCHEMA_VERSION,
    InclinationDecisionKind,
    InclinationEvaluation,
    InclinationEvidence,
    InclinationEvidenceRole,
    InclinationRevision,
    InclinationRevisionKind,
    SatoriInclination,
    inclination_key,
)
from satori.domain.reflection import (
    REFLECTION_POLICY_VERSION_V1,
    REFLECTION_POLICY_VERSION_V2,
    REFLECTION_SCHEMA_VERSION_V1,
    REFLECTION_SCHEMA_VERSION_V2,
    ReflectionOutcome,
    ReflectionOutcomeDecision,
    ReflectionRun,
    ReflectionRunStatus,
    ReflectionSourceRecord,
    ReflectionTriggerKind,
    reflection_run_key,
    source_set_hash,
)
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.models.affect import AffectiveTransitionRow
from satori.infrastructure.persistence.models.conversation import (
    ConversationInteractionRow,
    ConversationMessageRow,
    ConversationSessionRow,
)
from satori.infrastructure.persistence.models.initial_self import AuditEventRow
from satori.infrastructure.persistence.models.memory import EpisodicMemoryRow, MemoryEvidenceRow
from satori.infrastructure.persistence.models.positions import (
    InclinationEvidenceRow,
    InclinationRevisionRow,
    PositionEvidenceRow,
    SatoriInclinationRow,
    SatoriPositionRow,
)
from satori.infrastructure.persistence.models.reflection import (
    ReflectionOutcomeRow,
    ReflectionProposalRow,
    ReflectionSourceRow,
)
from satori.infrastructure.persistence.positions_uow import SQLAlchemyPositionsUnitOfWork
from satori.infrastructure.persistence.reflection_uow import SQLAlchemyReflectionUnitOfWork
from tests.test_stage4_conversation_memory import INTERACTION_TIME, activate
from tests.test_stage11_positions_integration import create_interaction

APPRAISAL_PAYLOAD = {
    "pleasantness": 0.55,
    "activation": 0.35,
    "novelty": 0.75,
    "salience": 0.85,
    "uncertainty": 0.10,
    "curiosity_signal": 0.70,
    "interest_signal": 0.80,
    "humor_signal": 0.05,
    "concern_signal": 0.10,
    "frustration_signal": 0.02,
    "confidence_signal": 0.75,
}
APPLIED_DELTA = {
    "valence": 0.08,
    "arousal": 0.04,
    "tension": -0.01,
    "curiosity": 0.09,
    "interest": 0.10,
    "amusement": 0.0,
    "concern": 0.01,
    "frustration": 0.0,
    "situational_confidence": 0.03,
}


def _canonical_position_source(
    database: Database,
    *,
    identity_id: str,
    transition_source_role: str = "user",
) -> tuple[str, str, str, str]:
    interaction_id = create_interaction(
        database,
        counterparty_id="alice",
        content="Архитектура этого решения раскрывается через ясные границы модулей.",
        prefix="inclination-persistence",
        day=1,
    )
    observed_at = INTERACTION_TIME + timedelta(days=1)
    with database.session_factory() as session:
        interaction = session.get(ConversationInteractionRow, interaction_id)
        assert interaction is not None
        conversation_session = session.get(ConversationSessionRow, interaction.session_id)
        assert conversation_session is not None
        messages = tuple(
            session.execute(
                select(ConversationMessageRow)
                .where(ConversationMessageRow.interaction_id == interaction_id)
                .order_by(ConversationMessageRow.sequence)
            ).scalars()
        )
        user_message = next(item for item in messages if item.role == "user")
        transition_message = next(item for item in messages if item.role == transition_source_role)
        quote = "Архитектура этого решения"
        session.add(
            SatoriPositionRow(
                position_id="inclination-source-position",
                position_key="1" * 64,
                identity_id=identity_id,
                schema_version=1,
                aggregate_version=1,
                policy_version=1,
                formation_version=1,
                normalization_version=1,
                proposition="Ясные архитектурные границы полезны.",
                normalized_proposition="ясные архитектурные границы полезны",
                kind="belief",
                stance="support",
                confidence=0.6,
                status="active",
                value_key=None,
                competing_with_position_id=None,
                superseded_by_position_id=None,
                created_at=observed_at,
                updated_at=observed_at,
            )
        )
        session.flush()
        session.add(
            PositionEvidenceRow(
                evidence_id="inclination-position-evidence",
                position_id="inclination-source-position",
                source_message_id=user_message.message_id,
                source_interaction_id=interaction_id,
                source_counterparty_id="alice",
                quote=quote,
                normalized_signature="inclination architecture evidence",
                role="observation",
                observed_at=observed_at,
            )
        )
        session.add(
            AffectiveTransitionRow(
                transition_id="inclination-affective-transition",
                identity_id=identity_id,
                interaction_id=interaction_id,
                source_message_id=transition_message.message_id,
                trace_id="trace-inclination-affect",
                appraisal_schema_version=1,
                emotion_policy_version=1,
                mood_policy_version=1,
                base_state_version=1,
                resulting_state_version=2,
                base_mood_version=1,
                resulting_mood_version=2,
                appraised_at=observed_at,
                committed_at=observed_at,
                appraisal_confidence=0.8,
                appraisal_payload=dict(APPRAISAL_PAYLOAD),
                source_refs=[interaction_id],
                reason_codes=["fixture_owner_approved"],
                applied_delta=dict(APPLIED_DELTA),
                mood_delta={"valence": 0.01, "energy": 0.01, "tension": 0.0},
                state_before={},
                state_after={},
                provider="fixture-affect",
                model="fixture",
                appraisal_method="fixture.affect.v1",
            )
        )
        session.commit()
        return (
            interaction_id,
            user_message.message_id,
            conversation_session.session_id,
            quote,
        )


def _memory_source_citing_prior_interaction(
    database: Database,
    *,
    identity_id: str,
) -> tuple[str, str, str]:
    root_interaction_id = create_interaction(
        database,
        counterparty_id="alice",
        content="Модульная архитектура помогла проверить границы.",
        prefix="inclination-memory-root",
        day=1,
    )
    with database.session_factory() as session:
        root = session.get(ConversationInteractionRow, root_interaction_id)
        assert root is not None
        root_session = session.get(ConversationSessionRow, root.session_id)
        assert root_session is not None
        assert root_session.identity_id == identity_id
        root_message = session.execute(
            select(ConversationMessageRow).where(
                ConversationMessageRow.interaction_id == root_interaction_id,
                ConversationMessageRow.role == "user",
            )
        ).scalar_one()
        formation_at = root.started_at + timedelta(hours=1)
        formation_interaction_id = "inclination-memory-formation-interaction"
        session.add(
            ConversationInteractionRow(
                interaction_id=formation_interaction_id,
                session_id=root.session_id,
                client_request_id="request-inclination-memory-formation",
                trace_id="trace-inclination-memory-formation",
                schema_version=1,
                status="completed",
                started_at=formation_at,
                completed_at=formation_at,
                provider="fixture-conversation",
                model="fixture",
                finish_status="stop",
                context_schema_version=15,
                context_manifest_schema_version=15,
                policy_id="fixture.behavior",
                policy_schema_version=1,
                relationship_processing_required=False,
                model_processing_required=False,
                position_processing_required=False,
            )
        )
        session.flush()
        session.add_all(
            (
                ConversationMessageRow(
                    message_id="inclination-memory-formation-user",
                    session_id=root.session_id,
                    interaction_id=formation_interaction_id,
                    schema_version=1,
                    role="user",
                    content="Учитывай предыдущее наблюдение.",
                    created_at=formation_at,
                    sequence=1,
                ),
                ConversationMessageRow(
                    message_id="inclination-memory-formation-assistant",
                    session_id=root.session_id,
                    interaction_id=formation_interaction_id,
                    schema_version=1,
                    role="assistant",
                    content="Связала.",
                    created_at=formation_at,
                    sequence=2,
                ),
            )
        )
        session.add(
            EpisodicMemoryRow(
                memory_id="inclination-prior-root-memory",
                schema_version=1,
                source_interaction_id=formation_interaction_id,
                occurred_at=root.started_at,
                summary="Проверка модульных границ.",
                importance=0.8,
                confidence=0.8,
                created_at=formation_at,
                formation_method="fixture.episode.v1",
                formation_version=1,
                lifecycle_status="active",
            )
        )
        session.flush()
        quote = "Модульная архитектура"
        session.add(
            MemoryEvidenceRow(
                evidence_id="inclination-prior-root-memory-evidence",
                memory_id="inclination-prior-root-memory",
                source_message_id=root_message.message_id,
                provenance_kind="explicit_user_statement",
                quote=quote,
                observed_at=root_message.created_at,
            )
        )
        session.commit()
        return root_interaction_id, root_message.message_id, quote


def _create_reflection_source(
    database: Database,
    *,
    identity_id: str,
    schema_version: int = REFLECTION_SCHEMA_VERSION_V2,
) -> tuple[ReflectionRun, ReflectionSourceRecord, str]:
    with SQLAlchemyReflectionUnitOfWork(database.session_factory) as unit:
        candidates = unit.reflection.list_eligible_sources(identity_id=identity_id, limit=12)
    assert len(candidates) == 1
    candidate = candidates[0]
    if schema_version == REFLECTION_SCHEMA_VERSION_V2:
        assert candidate.affective is not None
        transition_id = candidate.affective.transition_id
        state_version = candidate.affective.resulting_state_version
        signal_hash = candidate.affective.signal_hash
        policy_version = REFLECTION_POLICY_VERSION_V2
    else:
        transition_id = None
        state_version = None
        signal_hash = None
        policy_version = REFLECTION_POLICY_VERSION_V1
    provisional = ReflectionSourceRecord(
        source_id="inclination-reflection-source",
        run_id="pending",
        ordinal=0,
        kind=candidate.kind,
        evidence_edge_id=candidate.evidence_edge_id,
        evidence_edge_version=candidate.evidence_edge_version,
        root_interaction_id=candidate.root_interaction_id,
        root_message_id=candidate.root_message_id,
        root_counterparty_id=candidate.root_counterparty_id,
        observed_at=candidate.observed_at,
        content_hash=candidate.content_hash,
        affective_transition_id=transition_id,
        affective_state_version=state_version,
        affective_signal_hash=signal_hash,
    )
    digest = source_set_hash((provisional,), schema_version=schema_version)
    key = reflection_run_key(
        identity_id=identity_id,
        source_hash=digest,
        schema_version=schema_version,
        policy_version=policy_version,
    )
    run_id = f"inclination-reflection-run-v{schema_version}"
    source = replace(provisional, run_id=run_id)
    created_at = INTERACTION_TIME + timedelta(days=2)
    run = ReflectionRun(
        run_id=run_id,
        run_key=key,
        identity_id=identity_id,
        schema_version=schema_version,
        policy_version=policy_version,
        trigger_kind=ReflectionTriggerKind.EXPLICIT_LOCAL,
        source_set_hash=digest,
        status=ReflectionRunStatus.PENDING_GENERATION,
        aggregate_version=1,
        attempt_count=0,
        created_at=created_at,
        updated_at=created_at,
    )
    with SQLAlchemyReflectionUnitOfWork(database.session_factory) as unit:
        assert unit.reflection.create_run(run, (source,)) is True
        unit.commit()
    proposal_id = f"inclination-reflection-proposal-v{schema_version}"
    with database.session_factory() as session:
        session.add(
            ReflectionProposalRow(
                proposal_id=proposal_id,
                run_id=run_id,
                ordinal=0,
                target_owner=(
                    "satori_inclinations"
                    if schema_version == REFLECTION_SCHEMA_VERSION_V2
                    else "satori_positions"
                ),
                payload={},
                evidence_source_ids=[source.source_id],
                created_at=created_at,
            )
        )
        session.commit()
    return run, source, proposal_id


def _applied_inclination(
    *,
    identity_id: str,
    source: ReflectionSourceRecord,
    source_session_id: str,
    outcome_id: str,
) -> tuple[SatoriInclination, InclinationEvaluation]:
    assert source.affective_transition_id is not None
    assert source.affective_state_version is not None
    assert source.affective_signal_hash is not None
    now = INTERACTION_TIME + timedelta(days=8)
    inclination_id = "inclination-architecture"
    evidence = InclinationEvidence(
        evidence_id="inclination-evidence-1",
        inclination_id=inclination_id,
        reflection_source_id=source.source_id,
        affective_transition_id=source.affective_transition_id,
        affective_state_version=source.affective_state_version,
        affective_signal_hash=source.affective_signal_hash,
        source_message_id=source.root_message_id,
        source_interaction_id=source.root_interaction_id,
        source_session_id=source_session_id,
        source_counterparty_id=source.root_counterparty_id,
        content_hash=source.content_hash,
        content_signature="2" * 64,
        role=InclinationEvidenceRole.TOPIC,
        signal=0.4,
        observed_at=source.observed_at,
        accepted_at=now,
    )
    revision = InclinationRevision(
        revision_id="inclination-revision-1",
        inclination_id=inclination_id,
        inclination_version=1,
        reflection_outcome_id=outcome_id,
        kind=InclinationRevisionKind.CREATED,
        prior_score=None,
        new_score=0.12,
        applied_delta=0.12,
        prior_confidence=None,
        new_confidence=0.7,
        prior_stability=None,
        new_stability=0.2,
        state_as_of=now,
        reason_code="eligible_inclination_created",
        occurred_at=now,
    )
    inclination = SatoriInclination(
        inclination_id=inclination_id,
        inclination_key=inclination_key(InclinationKind.INTEREST, "архитектура", None),
        identity_id=identity_id,
        schema_version=INCLINATION_SCHEMA_VERSION,
        aggregate_version=1,
        policy_version=INCLINATION_POLICY_VERSION,
        normalization_version=INCLINATION_NORMALIZATION_VERSION,
        kind=InclinationKind.INTEREST,
        topic="Архитектура",
        normalized_topic="архитектура",
        alternative_topic=None,
        normalized_alternative_topic=None,
        score=0.12,
        confidence=0.7,
        stability=0.2,
        state_as_of=now,
        last_accepted_at=now,
        created_at=now,
        updated_at=now,
        evidence=(evidence,),
        revisions=(revision,),
    )
    return inclination, InclinationEvaluation(
        kind=InclinationDecisionKind.APPLIED,
        reason_code="eligible_inclination_created",
        inclination=inclination,
        new_evidence=(evidence,),
        revision=revision,
    )


def test_atomic_inclination_round_trip_compact_read_replay_and_stale_rollback(
    migrated_database: Database,
) -> None:
    snapshot = activate(migrated_database)
    _, _, source_session_id, _ = _canonical_position_source(
        migrated_database, identity_id=snapshot.identity.identity_id
    )
    _, source, proposal_id = _create_reflection_source(
        migrated_database, identity_id=snapshot.identity.identity_id
    )
    outcome_id = "inclination-reflection-outcome-1"
    inclination, evaluation = _applied_inclination(
        identity_id=snapshot.identity.identity_id,
        source=source,
        source_session_id=source_session_id,
        outcome_id=outcome_id,
    )
    outcome = ReflectionOutcome(
        outcome_id=outcome_id,
        proposal_id=proposal_id,
        target_policy_version=INCLINATION_POLICY_VERSION,
        decision=ReflectionOutcomeDecision.ACCEPTED,
        reason_code=evaluation.reason_code,
        target_aggregate_type="satori_inclinations",
        target_aggregate_id=inclination.inclination_id,
        decided_at=inclination.updated_at,
    )
    with SQLAlchemyPositionsUnitOfWork(migrated_database.session_factory) as unit:
        assert unit.positions.record_inclination_reflection_decision(
            outcome,
            evaluation,
            identity_id=snapshot.identity.identity_id,
            trace_id="trace-inclination-accepted",
            audit_event_id="audit-inclination-accepted",
        )
        unit.commit()
    with SQLAlchemyPositionsUnitOfWork(migrated_database.session_factory) as replay:
        assert not replay.positions.record_inclination_reflection_decision(
            outcome,
            evaluation,
            identity_id=snapshot.identity.identity_id,
            trace_id="trace-inclination-replay",
            audit_event_id="audit-inclination-replay",
        )
        replay.commit()

    with SQLAlchemyPositionsUnitOfWork(migrated_database.session_factory) as restarted:
        assert restarted.positions.get_inclination(inclination.inclination_id) == inclination
        assert restarted.positions.list_inclinations(identity_id=snapshot.identity.identity_id) == (
            inclination,
        )
        assert restarted.positions.list_inclination_references(
            identity_id=snapshot.identity.identity_id
        ) == (
            InclinationStateReference(
                inclination_id=inclination.inclination_id,
                aggregate_version=1,
                kind=InclinationKind.INTEREST,
                topic="Архитектура",
                alternative_topic=None,
                score=0.12,
                confidence=0.7,
                stability=0.2,
                state_as_of=inclination.state_as_of,
            ),
        )

    stale_proposal_id = "inclination-reflection-proposal-stale"
    with migrated_database.session_factory() as session:
        session.add(
            ReflectionProposalRow(
                proposal_id=stale_proposal_id,
                run_id=source.run_id,
                ordinal=1,
                target_owner="satori_inclinations",
                payload={},
                evidence_source_ids=[source.source_id],
                created_at=inclination.updated_at,
            )
        )
        session.commit()
    stale_outcome_id = "inclination-reflection-outcome-stale"
    stale_revision = replace(
        inclination.revisions[0],
        revision_id="inclination-revision-stale",
        inclination_version=3,
        reflection_outcome_id=stale_outcome_id,
        kind=InclinationRevisionKind.STRENGTHENED,
        prior_score=inclination.score,
        new_score=0.14,
        applied_delta=0.02,
        prior_confidence=inclination.confidence,
        prior_stability=inclination.stability,
        reason_code="inclination_strengthened",
    )
    stale_inclination = replace(
        inclination,
        aggregate_version=3,
        score=0.14,
        revisions=(*inclination.revisions, stale_revision),
    )
    stale_evaluation = InclinationEvaluation(
        kind=InclinationDecisionKind.APPLIED,
        reason_code="inclination_strengthened",
        inclination=stale_inclination,
        revision=stale_revision,
    )
    stale_outcome = replace(
        outcome,
        outcome_id=stale_outcome_id,
        proposal_id=stale_proposal_id,
        reason_code="inclination_strengthened",
    )
    with (
        pytest.raises(RuntimeError, match="concurrently modified"),
        SQLAlchemyPositionsUnitOfWork(migrated_database.session_factory) as unit,
    ):
        unit.positions.record_inclination_reflection_decision(
            stale_outcome,
            stale_evaluation,
            identity_id=snapshot.identity.identity_id,
            trace_id="trace-inclination-stale",
            audit_event_id="audit-inclination-stale",
        )

    with migrated_database.session_factory() as session:
        assert session.get(ReflectionOutcomeRow, stale_outcome_id) is None
        assert session.get(AuditEventRow, "audit-inclination-stale") is None
        persisted = session.get(SatoriInclinationRow, inclination.inclination_id)
        assert persisted is not None
        assert persisted.aggregate_version == 1
        assert session.scalar(select(func.count()).select_from(InclinationEvidenceRow)) == 1
        assert session.scalar(select(func.count()).select_from(InclinationRevisionRow)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEventRow)
                .where(AuditEventRow.event_type == "reflection.inclination_accepted")
            )
            == 1
        )
        audit = session.get(AuditEventRow, "audit-inclination-accepted")
        assert audit is not None
        assert audit.details["revision_id"] == "inclination-revision-1"
        assert audit.details["evidence_ids"] == ["inclination-evidence-1"]
        assert audit.details["prior_score"] is None
        assert audit.details["new_score"] == 0.12
        assert audit.details["applied_delta"] == 0.12
        assert audit.details["new_confidence"] == 0.7
        assert audit.details["new_stability"] == 0.2


@pytest.mark.parametrize("tamper", ["reason", "policy", "target_type", "revision_version"])
def test_inclination_atomic_write_rejects_inconsistent_owner_contract(
    migrated_database: Database,
    tamper: str,
) -> None:
    snapshot = activate(migrated_database)
    _, _, source_session_id, _ = _canonical_position_source(
        migrated_database, identity_id=snapshot.identity.identity_id
    )
    _, source, proposal_id = _create_reflection_source(
        migrated_database, identity_id=snapshot.identity.identity_id
    )
    outcome_id = f"inclination-inconsistent-outcome-{tamper}"
    inclination, evaluation = _applied_inclination(
        identity_id=snapshot.identity.identity_id,
        source=source,
        source_session_id=source_session_id,
        outcome_id=outcome_id,
    )
    outcome = ReflectionOutcome(
        outcome_id=outcome_id,
        proposal_id=proposal_id,
        target_policy_version=INCLINATION_POLICY_VERSION,
        decision=ReflectionOutcomeDecision.ACCEPTED,
        reason_code=evaluation.reason_code,
        target_aggregate_type="satori_inclinations",
        target_aggregate_id=inclination.inclination_id,
        decided_at=inclination.updated_at,
    )
    if tamper == "reason":
        outcome = replace(outcome, reason_code="different_reason")
    elif tamper == "policy":
        outcome = replace(outcome, target_policy_version=2)
    elif tamper == "target_type":
        outcome = replace(outcome, target_aggregate_type="satori_positions")
    else:
        assert evaluation.revision is not None
        evaluation = replace(
            evaluation,
            revision=replace(evaluation.revision, inclination_version=2),
        )

    with (
        SQLAlchemyPositionsUnitOfWork(migrated_database.session_factory) as unit,
        pytest.raises(ValueError, match=r"inclination|reflection outcome"),
    ):
        unit.positions.record_inclination_reflection_decision(
            outcome,
            evaluation,
            identity_id=snapshot.identity.identity_id,
            trace_id="trace-inclination-inconsistent",
            audit_event_id="audit-inclination-inconsistent",
        )

    with migrated_database.session_factory() as session:
        assert session.get(ReflectionOutcomeRow, outcome_id) is None
        assert session.scalar(select(func.count()).select_from(SatoriInclinationRow)) == 0


def test_rejected_inclination_decision_persists_only_outcome_and_audit(
    migrated_database: Database,
) -> None:
    snapshot = activate(migrated_database)
    _canonical_position_source(migrated_database, identity_id=snapshot.identity.identity_id)
    _, _, proposal_id = _create_reflection_source(
        migrated_database, identity_id=snapshot.identity.identity_id
    )
    decided_at = INTERACTION_TIME + timedelta(days=8)
    outcome = ReflectionOutcome(
        outcome_id="inclination-rejected-outcome",
        proposal_id=proposal_id,
        target_policy_version=INCLINATION_POLICY_VERSION,
        decision=ReflectionOutcomeDecision.REJECTED,
        reason_code="insufficient_inclination_evidence_diversity",
        target_aggregate_type=None,
        target_aggregate_id=None,
        decided_at=decided_at,
    )
    evaluation = InclinationEvaluation(
        kind=InclinationDecisionKind.REJECTED,
        reason_code="insufficient_inclination_evidence_diversity",
    )
    with SQLAlchemyPositionsUnitOfWork(migrated_database.session_factory) as unit:
        assert unit.positions.record_inclination_reflection_decision(
            outcome,
            evaluation,
            identity_id=snapshot.identity.identity_id,
            trace_id="trace-inclination-rejected",
            audit_event_id="audit-inclination-rejected",
        )
        unit.commit()

    with migrated_database.session_factory() as session:
        assert session.get(ReflectionOutcomeRow, outcome.outcome_id) is not None
        assert session.get(AuditEventRow, "audit-inclination-rejected") is not None
        assert session.scalar(select(func.count()).select_from(SatoriInclinationRow)) == 0
        assert session.scalar(select(func.count()).select_from(InclinationEvidenceRow)) == 0
        assert session.scalar(select(func.count()).select_from(InclinationRevisionRow)) == 0

    malformed = replace(
        outcome,
        outcome_id="inclination-rejected-targeted-outcome",
        target_aggregate_type="satori_inclinations",
        target_aggregate_id="fabricated-target",
    )
    with (
        SQLAlchemyPositionsUnitOfWork(migrated_database.session_factory) as unit,
        pytest.raises(ValueError, match="cannot target"),
    ):
        unit.positions.record_inclination_reflection_decision(
            malformed,
            evaluation,
            identity_id=snapshot.identity.identity_id,
            trace_id="trace-inclination-rejected-target",
            audit_event_id="audit-inclination-rejected-target",
        )


def test_reflection_v1_source_remains_readable_without_late_affect_attachment(
    migrated_database: Database,
) -> None:
    snapshot = activate(migrated_database)
    _, _, source_session_id, quote = _canonical_position_source(
        migrated_database, identity_id=snapshot.identity.identity_id
    )
    run, source, _ = _create_reflection_source(
        migrated_database,
        identity_id=snapshot.identity.identity_id,
        schema_version=REFLECTION_SCHEMA_VERSION_V1,
    )

    with SQLAlchemyReflectionUnitOfWork(migrated_database.session_factory) as unit:
        assert unit.reflection.list_sources(run.run_id) == (source,)
        loaded = unit.reflection.load_generation_sources(run.run_id)
    assert len(loaded) == 1
    assert loaded[0].quote == quote
    assert loaded[0].root_session_id == source_session_id
    assert loaded[0].affective is None


def test_reflection_memory_source_can_cite_prior_interaction_in_same_session(
    migrated_database: Database,
) -> None:
    snapshot = activate(migrated_database)
    root_interaction_id, root_message_id, quote = _memory_source_citing_prior_interaction(
        migrated_database,
        identity_id=snapshot.identity.identity_id,
    )
    run, source, _ = _create_reflection_source(
        migrated_database,
        identity_id=snapshot.identity.identity_id,
        schema_version=REFLECTION_SCHEMA_VERSION_V1,
    )
    assert source.kind.value == "episodic_memory_evidence"
    assert source.root_interaction_id == root_interaction_id
    assert source.root_message_id == root_message_id

    with SQLAlchemyReflectionUnitOfWork(migrated_database.session_factory) as unit:
        loaded = unit.reflection.load_generation_sources(run.run_id)
    assert len(loaded) == 1
    assert loaded[0].quote == quote
    assert loaded[0].root_interaction_id == root_interaction_id


@pytest.mark.parametrize("tamper", ["state_version", "signal_hash", "transition_payload"])
def test_reflection_v2_load_rejects_affect_attachment_tampering(
    migrated_database: Database,
    tamper: str,
) -> None:
    snapshot = activate(migrated_database)
    _canonical_position_source(migrated_database, identity_id=snapshot.identity.identity_id)
    run, source, _ = _create_reflection_source(
        migrated_database, identity_id=snapshot.identity.identity_id
    )
    with migrated_database.session_factory() as session:
        if tamper == "state_version":
            row = session.get(ReflectionSourceRow, source.source_id)
            assert row is not None
            row.affective_state_version = 3
        elif tamper == "signal_hash":
            row = session.get(ReflectionSourceRow, source.source_id)
            assert row is not None
            row.affective_signal_hash = "f" * 64
        else:
            transition = session.get(AffectiveTransitionRow, source.affective_transition_id)
            assert transition is not None
            payload = dict(transition.appraisal_payload)
            payload["novelty"] = 0.25
            transition.appraisal_payload = payload
        session.commit()

    with (
        SQLAlchemyReflectionUnitOfWork(migrated_database.session_factory) as unit,
        pytest.raises(ValueError, match="attachment"),
    ):
        unit.reflection.load_generation_sources(run.run_id)


def test_reflection_source_attachment_is_all_or_none_and_outer_join_is_exact(
    migrated_database: Database,
) -> None:
    snapshot = activate(migrated_database)
    _canonical_position_source(
        migrated_database,
        identity_id=snapshot.identity.identity_id,
        transition_source_role="assistant",
    )
    with SQLAlchemyReflectionUnitOfWork(migrated_database.session_factory) as unit:
        candidates = unit.reflection.list_eligible_sources(
            identity_id=snapshot.identity.identity_id, limit=12
        )
    assert len(candidates) == 1
    assert candidates[0].affective is None

    run, source, _ = _create_reflection_source(
        migrated_database,
        identity_id=snapshot.identity.identity_id,
        schema_version=REFLECTION_SCHEMA_VERSION_V1,
    )
    with migrated_database.session_factory() as session:
        row = session.get(ReflectionSourceRow, source.source_id)
        assert row is not None
        row.affective_transition_id = "inclination-affective-transition"
        with pytest.raises(IntegrityError):
            session.commit()
    with SQLAlchemyReflectionUnitOfWork(migrated_database.session_factory) as unit:
        loaded = unit.reflection.load_generation_sources(run.run_id)
    assert loaded[0].affective is None
