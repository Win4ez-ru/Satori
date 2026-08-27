"""Stage 14 sole-owner atomicity, replay, checkpoint, restore, and export tests."""

# ruff: noqa: RUF001  # Russian acceptance evidence intentionally uses Cyrillic.

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event, func, select, update

from satori.application.personality.ports import (
    PersonalityEvolutionWrite,
    PersonalityRestoreWrite,
)
from satori.application.personality.use_cases import (
    ApplyPersonalityReflection,
    ApprovePersonalityCheckpoint,
    GetPersonalityEvolution,
    PersonalityCheckpointApprovalProposal,
    RestorePersonalityCheckpoint,
    _checkpoint_snapshot,
)
from satori.core.personality import (
    PersonalityChangeProposal,
    PersonalityCitation,
    PersonalityCitationRole,
    PersonalityDirection,
    PersonalityRestoreProposal,
    PersonalityTraitKey,
)
from satori.core.reflection import (
    ReflectionLineageKind,
    ReflectionPurpose,
    ReflectionSourceKind,
    ReflectionTargetOwner,
)
from satori.domain.personality_evolution import (
    PERSONALITY_EVOLUTION_POLICY_VERSION,
    PersonalityChangeEvaluation,
    PersonalityCheckpointKind,
    PersonalityDecisionKind,
    PersonalityManager,
    personality_drift_metrics,
)
from satori.domain.reflection import (
    REFLECTION_POLICY_VERSION_V3,
    REFLECTION_SCHEMA_VERSION_V3,
    ReflectionOutcome,
    ReflectionOutcomeDecision,
    ReflectionSourceRecord,
    reflection_outcome_id,
    source_set_hash,
)
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.models.conversation import (
    ConversationInteractionRow,
    ConversationMessageRow,
    ConversationSessionRow,
)
from satori.infrastructure.persistence.models.initial_self import (
    AuditEventRow,
    PersonalityStateRow,
    PersonalityTraitRow,
)
from satori.infrastructure.persistence.models.personality import (
    PersonalityCheckpointApprovalRow,
    PersonalityCheckpointRow,
    PersonalityEvidenceRow,
    PersonalityRestoreEventRow,
    PersonalityRevisionRow,
)
from satori.infrastructure.persistence.models.positions import (
    PositionEvidenceRow,
    SatoriPositionRow,
)
from satori.infrastructure.persistence.models.reflection import (
    ReflectionOutcomeRow,
    ReflectionProposalRow,
    ReflectionRunRow,
    ReflectionSourceRow,
)
from satori.infrastructure.persistence.personality_uow import SQLAlchemyPersonalityUnitOfWork
from tests.fakes import FrozenClock
from tests.test_stage4_conversation_memory import ACTIVATION_TIME, activate

NOW = ACTIVATION_TIME + timedelta(days=125)
SOURCE_TEXTS = (
    "При выборе хранилища фактов были проверены три независимых набора данных.",
    "Долгий эксперимент с датчиками завершился после повторной калибровки.",
    "Разбор интерфейса показал пользу коротких названий и явных границ.",
    "В отчёте о нагрузке сравнивались медиана, хвост распределения и ошибки.",
    "Прототип поиска стал точнее после отдельной проверки шумных примеров.",
    "Наблюдение за садом подтвердило разный темп роста растений в тени.",
    "Перед публикацией исследования команда воспроизвела расчёты на новой выборке.",
    "Маршрут экспедиции изменили после сверки карт и прогноза погоды.",
)


@dataclass(slots=True)
class PrefixIds:
    prefix: str
    ordinal: int = 0

    def new(self) -> str:
        self.ordinal += 1
        return f"{self.prefix}-{self.ordinal}"


@dataclass(frozen=True, slots=True)
class PersonalityRunFixture:
    identity_id: str
    run_id: str
    proposal_id: str
    proposal: PersonalityChangeProposal
    source_ids: tuple[str, ...]


def _uow(database: Database) -> SQLAlchemyPersonalityUnitOfWork:
    return SQLAlchemyPersonalityUnitOfWork(database.session_factory)


def _apply(
    database: Database,
    fixture: PersonalityRunFixture,
    *,
    prefix: str = "personality-owner",
    now: datetime = NOW,
) -> ApplyPersonalityReflection:
    return ApplyPersonalityReflection(
        unit_of_work_factory=lambda: _uow(database),
        manager=PersonalityManager(),
        clock=FrozenClock(now),
        id_generator=PrefixIds(prefix),
    )


def _seed_personality_run(
    database: Database,
    *,
    confidence: float = 0.9,
    identity_id: str | None = None,
    prefix: str = "personality",
    trait_key: PersonalityTraitKey = "curiosity",
    expected_personality_version: int = 1,
) -> PersonalityRunFixture:
    if identity_id is None:
        snapshot = activate(database)
        identity_id = snapshot.identity.identity_id
    run_id = f"reflection-run-{prefix}-owner"
    proposal_id = f"reflection-proposal-{prefix}-owner"
    records: list[ReflectionSourceRecord] = []
    source_ids: list[str] = []
    with database.session_factory() as session:
        positions = []
        for lineage_index in range(4):
            observed_at = ACTIVATION_TIME + timedelta(days=1 + lineage_index * 32)
            positions.append(
                SatoriPositionRow(
                    position_id=f"{prefix}-position-{lineage_index}",
                    position_key=hashlib.sha256(
                        f"{prefix}-position-{lineage_index}".encode()
                    ).hexdigest(),
                    identity_id=identity_id,
                    schema_version=1,
                    aggregate_version=1,
                    policy_version=1,
                    formation_version=1,
                    normalization_version=1,
                    proposition=f"Проверяемое наблюдение номер {lineage_index}.",
                    normalized_proposition=f"проверяемое наблюдение номер {lineage_index}",
                    kind="belief",
                    stance="support",
                    confidence=0.7,
                    status="active",
                    value_key=None,
                    competing_with_position_id=None,
                    superseded_by_position_id=None,
                    created_at=observed_at,
                    updated_at=observed_at,
                )
            )
        session.add_all(positions)
        session.flush()

        for index, text in enumerate(SOURCE_TEXTS):
            observed_at = ACTIVATION_TIME + timedelta(days=1 + index * 16)
            session_id = f"{prefix}-session-{index}"
            interaction_id = f"{prefix}-interaction-{index}"
            message_id = f"{prefix}-message-{index}"
            edge_id = f"{prefix}-position-evidence-{index}"
            source_id = f"{prefix}-source-{index}"
            lineage_id = f"{prefix}-position-{index // 2}"
            session.add(
                ConversationSessionRow(
                    session_id=session_id,
                    identity_id=identity_id,
                    counterparty_id="local-default",
                    schema_version=1,
                    kind="implicit",
                    status="closed",
                    started_at=observed_at,
                    ended_at=observed_at,
                )
            )
            session.flush()
            session.add(
                ConversationInteractionRow(
                    interaction_id=interaction_id,
                    session_id=session_id,
                    client_request_id=f"{prefix}-request-{index}",
                    trace_id=f"{prefix}-trace-{index}",
                    schema_version=1,
                    status="completed",
                    started_at=observed_at,
                    completed_at=observed_at,
                    provider="fixture-provider",
                    model="fixture-model",
                    finish_status="stop",
                    input_tokens=None,
                    output_tokens=None,
                    context_schema_version=16,
                    context_manifest_schema_version=16,
                    policy_id="fixture-policy",
                    policy_schema_version=1,
                    retrieval_status=None,
                    retrieved_memory_ids=None,
                    semantic_retrieval_status=None,
                    retrieved_semantic_claim_ids=None,
                    emotion_appraisal_status=None,
                    emotion_context_schema_version=None,
                    emotion_state_version=None,
                    mood_state_version=None,
                    emotion_state_as_of=None,
                    relationship_context_schema_version=None,
                    relationship_state_version=None,
                    failure_kind=None,
                    relationship_processing_required=False,
                    model_processing_required=False,
                    position_processing_required=False,
                    model_context_status=None,
                    user_model_context_schema_version=None,
                    user_model_context_claim_ids=None,
                    world_model_context_schema_version=None,
                    world_model_context_claim_ids=None,
                    position_context_status=None,
                    position_context_schema_version=None,
                    position_context_ids=None,
                    inclination_context_status=None,
                    inclination_context_schema_version=None,
                    inclination_context_ids=None,
                    inclination_curiosity_influence=None,
                    personality_aggregate_version=1,
                    personality_expression_schema_version=2,
                    personality_expression_cues=[],
                )
            )
            session.flush()
            session.add_all(
                (
                    ConversationMessageRow(
                        message_id=message_id,
                        session_id=session_id,
                        interaction_id=interaction_id,
                        schema_version=1,
                        role="user",
                        content=text,
                        created_at=observed_at,
                        sequence=1,
                    ),
                    ConversationMessageRow(
                        message_id=f"{prefix}-assistant-{index}",
                        session_id=session_id,
                        interaction_id=interaction_id,
                        schema_version=1,
                        role="assistant",
                        content="Канонический ответ провайдера не является evidence.",
                        created_at=observed_at,
                        sequence=2,
                    ),
                )
            )
            session.flush()
            session.add(
                PositionEvidenceRow(
                    evidence_id=edge_id,
                    position_id=lineage_id,
                    source_message_id=message_id,
                    source_interaction_id=interaction_id,
                    source_counterparty_id="local-default",
                    quote=text,
                    normalized_signature=f"{prefix} signature {index}",
                    role="observation",
                    observed_at=observed_at,
                )
            )
            record = ReflectionSourceRecord(
                source_id=source_id,
                run_id=run_id,
                ordinal=index,
                kind=ReflectionSourceKind.POSITION_EVIDENCE,
                evidence_edge_id=edge_id,
                evidence_edge_version=1,
                root_interaction_id=interaction_id,
                root_message_id=message_id,
                root_counterparty_id="local-default",
                observed_at=observed_at,
                content_hash=hashlib.sha256(text.encode()).hexdigest(),
                upstream_lineage_kind=ReflectionLineageKind.POSITION,
                upstream_lineage_id=lineage_id,
            )
            records.append(record)
            source_ids.append(source_id)
        session.flush()
        fixed = tuple(records)
        run_hash = source_set_hash(
            fixed,
            schema_version=REFLECTION_SCHEMA_VERSION_V3,
            purpose=ReflectionPurpose.PERSONALITY_EVOLUTION,
        )
        session.add(
            ReflectionRunRow(
                run_id=run_id,
                run_key=hashlib.sha256(f"{prefix}-run-key".encode()).hexdigest(),
                identity_id=identity_id,
                schema_version=REFLECTION_SCHEMA_VERSION_V3,
                policy_version=REFLECTION_POLICY_VERSION_V3,
                purpose=ReflectionPurpose.PERSONALITY_EVOLUTION.value,
                trigger_kind="automatic",
                source_set_hash=run_hash,
                status="applying",
                aggregate_version=3,
                attempt_count=1,
                created_at=records[0].observed_at,
                updated_at=NOW,
                completed_at=None,
            )
        )
        session.flush()
        session.add_all(
            ReflectionSourceRow(
                source_id=item.source_id,
                run_id=item.run_id,
                ordinal=item.ordinal,
                kind=item.kind.value,
                evidence_edge_id=item.evidence_edge_id,
                evidence_edge_version=item.evidence_edge_version,
                root_interaction_id=item.root_interaction_id,
                root_message_id=item.root_message_id,
                root_counterparty_id=item.root_counterparty_id,
                observed_at=item.observed_at,
                content_hash=item.content_hash,
                upstream_lineage_kind=ReflectionLineageKind.POSITION.value,
                upstream_lineage_id=item.upstream_lineage_id,
                affective_transition_id=None,
                affective_state_version=None,
                affective_signal_hash=None,
            )
            for item in records
        )
        proposal = PersonalityChangeProposal(
            trait_key=trait_key,
            direction=PersonalityDirection.INCREASE,
            confidence=confidence,
            citations=tuple(
                PersonalityCitation(
                    source_id=source_id,
                    role=PersonalityCitationRole.SUPPORT,
                )
                for source_id in source_ids
            ),
            expected_personality_version=expected_personality_version,
        )
        session.add(
            ReflectionProposalRow(
                proposal_id=proposal_id,
                run_id=run_id,
                ordinal=0,
                target_owner=ReflectionTargetOwner.PERSONALITY.value,
                payload={
                    "target_owner": ReflectionTargetOwner.PERSONALITY.value,
                    **proposal.model_dump(mode="json"),
                },
                evidence_source_ids=list(source_ids),
                created_at=NOW,
            )
        )
        session.commit()
    return PersonalityRunFixture(identity_id, run_id, proposal_id, proposal, tuple(source_ids))


def _counts(database: Database) -> dict[str, int]:
    with database.session_factory() as session:
        return {
            "outcomes": session.scalar(select(func.count()).select_from(ReflectionOutcomeRow)) or 0,
            "revisions": session.scalar(select(func.count()).select_from(PersonalityRevisionRow))
            or 0,
            "evidence": session.scalar(select(func.count()).select_from(PersonalityEvidenceRow))
            or 0,
            "checkpoints": session.scalar(
                select(func.count()).select_from(PersonalityCheckpointRow)
            )
            or 0,
            "approvals": session.scalar(
                select(func.count()).select_from(PersonalityCheckpointApprovalRow)
            )
            or 0,
            "restores": session.scalar(select(func.count()).select_from(PersonalityRestoreEventRow))
            or 0,
            "personality_audits": session.scalar(
                select(func.count())
                .select_from(AuditEventRow)
                .where(AuditEventRow.aggregate_type == "personality")
            )
            or 0,
        }


def _prepare_evolution_write(
    database: Database,
    fixture: PersonalityRunFixture,
    owner: ApplyPersonalityReflection,
) -> tuple[PersonalityEvolutionWrite, PersonalityChangeEvaluation]:
    with _uow(database) as unit:
        current = unit.personality.get_current(fixture.identity_id)
        approved = unit.personality.get_approved_checkpoint(fixture.identity_id)
        resolved = unit.personality.resolve_reflection_sources(
            identity_id=fixture.identity_id,
            reflection_run_id=fixture.run_id,
            reflection_proposal_id=fixture.proposal_id,
            proposal=fixture.proposal,
        )
        assert current is not None
        assert approved is not None
        evaluation = PersonalityManager().evaluate_change(
            fixture.proposal,
            identity_id=fixture.identity_id,
            personality=current,
            approved_checkpoint=approved.snapshot,
            fixed_sources=tuple(item.source for item in resolved),
            prior_evolution=unit.personality.list_evolution_records(fixture.identity_id),
            used_root_message_ids=unit.personality.list_used_root_message_ids(fixture.identity_id),
            now=NOW,
        )
        write = owner._evolution_write(
            identity_id=fixture.identity_id,
            reflection_run_id=fixture.run_id,
            reflection_proposal_id=fixture.proposal_id,
            current=current,
            evaluation=evaluation,
            resolved=resolved,
            proposal=fixture.proposal,
            current_checkpoint=unit.personality.get_checkpoint_for_version(
                fixture.identity_id, current.aggregate_version
            ),
        )
    return write, evaluation


def _prepare_restore_write(
    database: Database,
    fixture: PersonalityRunFixture,
    *,
    now: datetime,
) -> tuple[PersonalityRestoreProposal, PersonalityRestoreWrite]:
    with _uow(database) as unit:
        current = unit.personality.get_current(fixture.identity_id)
        activation = unit.personality.get_activation_checkpoint(fixture.identity_id)
        approved = unit.personality.get_approved_checkpoint(fixture.identity_id)
        assert current is not None
        assert activation is not None
        assert approved is not None
        proposal = PersonalityRestoreProposal(
            checkpoint_id=activation.snapshot.checkpoint_id,
            checkpoint_hash=activation.snapshot.checkpoint_hash,
            expected_personality_version=current.aggregate_version,
            reason="Локальное восстановление для проверки атомарности.",
        )
        evaluation = PersonalityManager().evaluate_restore(
            proposal,
            identity_id=fixture.identity_id,
            personality=current,
            checkpoint=activation.snapshot,
        )
        assert evaluation.plan is not None
        history = unit.personality.list_evolution_records(fixture.identity_id)
        metrics = personality_drift_metrics(
            evaluation.plan.personality,
            approved_checkpoint=approved.snapshot,
            history=history,
            target_trait="analytical_thinking",
            now=now,
        )
        write = PersonalityRestoreWrite(
            before_personality=current,
            evaluation=evaluation,
            source_checkpoint=activation.snapshot,
            approved_checkpoint=approved.snapshot,
            resulting_checkpoint=_checkpoint_snapshot(
                fixture.identity_id,
                evaluation.plan.personality,
                checkpoint_kind=PersonalityCheckpointKind.RESTORE,
            ),
            prior_evolution=history,
            activation_distance=metrics.activation,
            approved_checkpoint_distance=metrics.approved_checkpoint,
            rolling_total_path=metrics.rolling_global_path,
            lifetime_total_path=metrics.lifetime_global_path,
            revision_id="prepared-restore-revision",
            restore_id="prepared-restore-event",
        )
    return proposal, write


def test_accept_replay_restart_approve_restore_compare_and_safe_export(
    migrated_database: Database,
) -> None:
    fixture = _seed_personality_run(migrated_database)
    owner = _apply(migrated_database, fixture)

    result = owner.apply(
        fixture.identity_id,
        reflection_run_id=fixture.run_id,
        reflection_proposal_id=fixture.proposal_id,
        proposal=fixture.proposal,
        trace_id="trace-personality-accept",
    )

    assert result.recorded is True
    assert result.outcome.decision is ReflectionOutcomeDecision.ACCEPTED
    assert result.personality.aggregate_version == 2
    assert result.personality.trait("curiosity").value == pytest.approx(0.925)
    assert result.personality.trait("curiosity").baseline_value == 0.92
    assert _counts(migrated_database) == {
        "outcomes": 1,
        "revisions": 1,
        "evidence": 8,
        "checkpoints": 2,
        "approvals": 0,
        "restores": 0,
        "personality_audits": 1,
    }

    replay = _apply(migrated_database, fixture, prefix="personality-replay").apply(
        fixture.identity_id,
        reflection_run_id=fixture.run_id,
        reflection_proposal_id=fixture.proposal_id,
        proposal=fixture.proposal,
        trace_id="trace-personality-replay",
    )
    assert replay.recorded is False
    assert replay.outcome == result.outcome
    assert _counts(migrated_database)["revisions"] == 1

    inspection = GetPersonalityEvolution(
        unit_of_work_factory=lambda: _uow(migrated_database),
        clock=FrozenClock(NOW),
    )
    state = inspection.inspect(fixture.identity_id)
    assert state is not None
    assert state.personality.aggregate_version == 2
    assert state.budgets.lifetime_global_path == 0.005
    assert state.budgets.rolling_global_path == 0.005
    assert len(state.evidence) == 8
    evolution_checkpoint = next(
        item for item in state.checkpoints if item.snapshot.checkpoint_kind.value == "evolution"
    )
    comparison = inspection.compare(
        fixture.identity_id, state.activation_checkpoint.snapshot.checkpoint_id
    )
    assert comparison is not None
    assert comparison.distance_linf == 0.005
    assert comparison.trait_diffs == (
        replace(
            comparison.trait_diffs[0],
            trait_key="curiosity",
            before_value=0.92,
            after_value=0.925,
        ),
    )

    approval = ApprovePersonalityCheckpoint(
        unit_of_work_factory=lambda: _uow(migrated_database),
        clock=FrozenClock(NOW + timedelta(days=1)),
        id_generator=PrefixIds("personality-approval"),
    ).execute(
        fixture.identity_id,
        PersonalityCheckpointApprovalProposal(
            checkpoint_id=evolution_checkpoint.snapshot.checkpoint_id,
            checkpoint_hash=evolution_checkpoint.snapshot.checkpoint_hash,
            expected_personality_version=2,
            reason="Проверено локально по anchor corpus.",
        ),
        trace_id="trace-personality-approval",
    )
    assert approval.checkpoint_id == evolution_checkpoint.snapshot.checkpoint_id

    restorer = RestorePersonalityCheckpoint(
        unit_of_work_factory=lambda: _uow(migrated_database),
        manager=PersonalityManager(),
        clock=FrozenClock(NOW + timedelta(days=2)),
        id_generator=PrefixIds("personality-restore"),
    )
    restore_proposal = PersonalityRestoreProposal(
        checkpoint_id=state.activation_checkpoint.snapshot.checkpoint_id,
        checkpoint_hash=state.activation_checkpoint.snapshot.checkpoint_hash,
        expected_personality_version=2,
        reason="Возврат к проверенной activation baseline.",
    )
    restore = restorer.execute(
        fixture.identity_id,
        restore_proposal,
        trace_id="trace-personality-restore",
    )
    assert restore.restored is True
    assert restore.personality.aggregate_version == 3
    assert restore.personality.trait("curiosity").value == 0.92
    assert restore.personality.trait("curiosity").baseline_value == 0.92
    restore_replay = restorer.execute(
        fixture.identity_id,
        restore_proposal,
        trace_id="trace-personality-restore-replay",
    )
    assert restore_replay.restored is False
    assert restore_replay.evaluation.reason_code == "personality_target_version_conflict"

    restored = inspection.inspect(fixture.identity_id)
    assert restored is not None
    assert restored.budgets.activation_distance_l1 == 0.0
    assert restored.budgets.lifetime_global_path == 0.005
    assert len(restored.revisions) == 2
    assert len(restored.restores) == 1
    payload_text = inspection.export_json(fixture.identity_id)
    assert payload_text is not None
    payload = json.loads(payload_text)
    assert payload["personality"]["aggregate_version"] == 3
    assert payload["budgets"]["lifetime_global_path"] == 0.005
    assert "quote" not in payload_text
    assert "Канонический ответ провайдера" not in payload_text
    assert all(text not in payload_text for text in SOURCE_TEXTS)
    assert "Проверено локально" not in payload_text
    assert "Возврат к проверенной" not in payload_text
    assert _counts(migrated_database) == {
        "outcomes": 1,
        "revisions": 2,
        "evidence": 8,
        "checkpoints": 3,
        "approvals": 1,
        "restores": 1,
        "personality_audits": 3,
    }


def test_rejection_and_replay_store_outcome_and_audit_only(
    migrated_database: Database,
) -> None:
    fixture = _seed_personality_run(migrated_database, confidence=0.79)
    owner = _apply(migrated_database, fixture)
    result = owner.apply(
        fixture.identity_id,
        reflection_run_id=fixture.run_id,
        reflection_proposal_id=fixture.proposal_id,
        proposal=fixture.proposal,
        trace_id="trace-personality-reject",
    )

    assert result.outcome.decision is ReflectionOutcomeDecision.REJECTED
    assert result.outcome.reason_code == "provider_confidence_too_low"
    assert result.personality.aggregate_version == 1
    assert _counts(migrated_database) == {
        "outcomes": 1,
        "revisions": 0,
        "evidence": 0,
        "checkpoints": 1,
        "approvals": 0,
        "restores": 0,
        "personality_audits": 1,
    }
    replay = _apply(migrated_database, fixture, prefix="personality-reject-replay").apply(
        fixture.identity_id,
        reflection_run_id=fixture.run_id,
        reflection_proposal_id=fixture.proposal_id,
        proposal=fixture.proposal,
        trace_id="trace-personality-reject-replay",
    )
    assert replay.recorded is False
    assert _counts(migrated_database)["personality_audits"] == 1


def test_distinct_roots_may_reuse_prior_normalized_signatures(
    migrated_database: Database,
) -> None:
    first = _seed_personality_run(migrated_database)
    first_result = _apply(migrated_database, first).apply(
        first.identity_id,
        reflection_run_id=first.run_id,
        reflection_proposal_id=first.proposal_id,
        proposal=first.proposal,
        trace_id="trace-personality-first-signature-set",
    )
    assert first_result.outcome.decision is ReflectionOutcomeDecision.ACCEPTED

    second = _seed_personality_run(
        migrated_database,
        identity_id=first.identity_id,
        prefix="personality-second",
        trait_key="warmth",
        expected_personality_version=2,
    )
    second_result = _apply(
        migrated_database,
        second,
        prefix="personality-second-owner",
        now=NOW + timedelta(days=31),
    ).apply(
        second.identity_id,
        reflection_run_id=second.run_id,
        reflection_proposal_id=second.proposal_id,
        proposal=second.proposal,
        trace_id="trace-personality-second-signature-set",
    )

    assert second_result.outcome.decision is ReflectionOutcomeDecision.ACCEPTED
    assert second_result.personality.aggregate_version == 3
    assert second_result.personality.trait("warmth").value == pytest.approx(0.735)
    with migrated_database.session_factory() as session:
        signatures = tuple(
            session.execute(
                select(
                    PersonalityEvidenceRow.normalized_signature,
                    func.count(PersonalityEvidenceRow.evidence_id),
                )
                .group_by(PersonalityEvidenceRow.normalized_signature)
                .order_by(PersonalityEvidenceRow.normalized_signature)
            ).all()
        )
    assert len(signatures) == 8
    assert all(count == 2 for _, count in signatures)


@pytest.mark.parametrize(
    ("tamper_field", "tamper_value"),
    [
        ("citation_role", PersonalityCitationRole.COUNTEREVIDENCE),
        ("normalized_signature", "f" * 64),
    ],
)
def test_owner_rejects_application_role_and_signature_tampering(
    migrated_database: Database,
    tamper_field: str,
    tamper_value: object,
) -> None:
    fixture = _seed_personality_run(migrated_database)
    owner = _apply(migrated_database, fixture)
    with _uow(migrated_database) as unit:
        current = unit.personality.get_current(fixture.identity_id)
        approved = unit.personality.get_approved_checkpoint(fixture.identity_id)
        resolved = unit.personality.resolve_reflection_sources(
            identity_id=fixture.identity_id,
            reflection_run_id=fixture.run_id,
            reflection_proposal_id=fixture.proposal_id,
            proposal=fixture.proposal,
        )
        assert current is not None
        assert approved is not None
        evaluation = PersonalityManager().evaluate_change(
            fixture.proposal,
            identity_id=fixture.identity_id,
            personality=current,
            approved_checkpoint=approved.snapshot,
            fixed_sources=tuple(item.source for item in resolved),
            prior_evolution=(),
            used_root_message_ids=frozenset(),
            now=NOW,
        )
        assert evaluation.kind is PersonalityDecisionKind.APPLIED
        write = owner._evolution_write(
            identity_id=fixture.identity_id,
            reflection_run_id=fixture.run_id,
            reflection_proposal_id=fixture.proposal_id,
            current=current,
            evaluation=evaluation,
            resolved=resolved,
            proposal=fixture.proposal,
            current_checkpoint=unit.personality.get_checkpoint_for_version(
                fixture.identity_id, current.aggregate_version
            ),
        )
        if tamper_field == "citation_role":
            first = replace(
                write.evidence[0],
                citation_role=PersonalityCitationRole.COUNTEREVIDENCE,
            )
        else:
            first = replace(write.evidence[0], normalized_signature=str(tamper_value))
        tampered = replace(write, evidence=(first, *write.evidence[1:]))
        outcome = ReflectionOutcome(
            outcome_id=reflection_outcome_id(
                proposal_id=fixture.proposal_id,
                target_policy_version=PERSONALITY_EVOLUTION_POLICY_VERSION,
            ),
            proposal_id=fixture.proposal_id,
            target_policy_version=PERSONALITY_EVOLUTION_POLICY_VERSION,
            decision=ReflectionOutcomeDecision.ACCEPTED,
            reason_code=evaluation.reason_code,
            target_aggregate_type="personality",
            target_aggregate_id=fixture.identity_id,
            decided_at=NOW,
        )
        with pytest.raises(ValueError, match="role or normalized signature"):
            unit.personality.record_reflection_decision(
                outcome,
                tampered,
                identity_id=fixture.identity_id,
                trace_id="trace-personality-tamper",
                audit_event_id="personality-tamper-audit",
            )
    assert _counts(migrated_database)["outcomes"] == 0
    assert _counts(migrated_database)["revisions"] == 0


def test_owner_recomputes_fixed_source_hash_before_evaluation(
    migrated_database: Database,
) -> None:
    fixture = _seed_personality_run(migrated_database)
    with migrated_database.session_factory() as session:
        source = session.get(ReflectionSourceRow, fixture.source_ids[0])
        assert source is not None
        source.content_hash = "f" * 64
        session.commit()
    with pytest.raises(ValueError, match="source-set hash"):
        _apply(migrated_database, fixture).apply(
            fixture.identity_id,
            reflection_run_id=fixture.run_id,
            reflection_proposal_id=fixture.proposal_id,
            proposal=fixture.proposal,
            trace_id="trace-personality-source-hash-tamper",
        )
    assert _counts(migrated_database)["outcomes"] == 0


def test_cross_run_evolution_write_is_rejected_before_any_owner_write(
    migrated_database: Database,
) -> None:
    fixture = _seed_personality_run(migrated_database)
    foreign = _seed_personality_run(
        migrated_database,
        identity_id=fixture.identity_id,
        prefix="personality-foreign",
    )
    write, evaluation = _prepare_evolution_write(
        migrated_database, fixture, _apply(migrated_database, fixture)
    )
    tampered = replace(write, reflection_run_id=foreign.run_id)
    outcome = ReflectionOutcome(
        outcome_id=reflection_outcome_id(
            proposal_id=fixture.proposal_id,
            target_policy_version=PERSONALITY_EVOLUTION_POLICY_VERSION,
        ),
        proposal_id=fixture.proposal_id,
        target_policy_version=PERSONALITY_EVOLUTION_POLICY_VERSION,
        decision=ReflectionOutcomeDecision.ACCEPTED,
        reason_code=evaluation.reason_code,
        target_aggregate_type="personality",
        target_aggregate_id=fixture.identity_id,
        decided_at=NOW,
    )
    with _uow(migrated_database) as unit, pytest.raises(ValueError, match="proposal lineage"):
        unit.personality.record_reflection_decision(
            outcome,
            tampered,
            identity_id=fixture.identity_id,
            trace_id="trace-personality-cross-run",
            audit_event_id="personality-cross-run-audit",
        )
    assert _counts(migrated_database)["outcomes"] == 0
    assert _counts(migrated_database)["revisions"] == 0


def test_stale_optimistic_write_rolls_back_outcome_and_owner_rows(
    migrated_database: Database,
) -> None:
    fixture = _seed_personality_run(migrated_database)
    owner = _apply(migrated_database, fixture)
    with _uow(migrated_database) as unit:
        current = unit.personality.get_current(fixture.identity_id)
        approved = unit.personality.get_approved_checkpoint(fixture.identity_id)
        resolved = unit.personality.resolve_reflection_sources(
            identity_id=fixture.identity_id,
            reflection_run_id=fixture.run_id,
            reflection_proposal_id=fixture.proposal_id,
            proposal=fixture.proposal,
        )
        assert current is not None
        assert approved is not None
        evaluation = PersonalityManager().evaluate_change(
            fixture.proposal,
            identity_id=fixture.identity_id,
            personality=current,
            approved_checkpoint=approved.snapshot,
            fixed_sources=tuple(item.source for item in resolved),
            prior_evolution=(),
            used_root_message_ids=frozenset(),
            now=NOW,
        )
        write = owner._evolution_write(
            identity_id=fixture.identity_id,
            reflection_run_id=fixture.run_id,
            reflection_proposal_id=fixture.proposal_id,
            current=current,
            evaluation=evaluation,
            resolved=resolved,
            proposal=fixture.proposal,
            current_checkpoint=unit.personality.get_checkpoint_for_version(
                fixture.identity_id, current.aggregate_version
            ),
        )
    with migrated_database.session_factory() as session:
        session.execute(
            update(PersonalityStateRow)
            .where(PersonalityStateRow.identity_id == fixture.identity_id)
            .values(aggregate_version=2)
        )
        session.commit()
    outcome = ReflectionOutcome(
        outcome_id=reflection_outcome_id(
            proposal_id=fixture.proposal_id,
            target_policy_version=PERSONALITY_EVOLUTION_POLICY_VERSION,
        ),
        proposal_id=fixture.proposal_id,
        target_policy_version=PERSONALITY_EVOLUTION_POLICY_VERSION,
        decision=ReflectionOutcomeDecision.ACCEPTED,
        reason_code=evaluation.reason_code,
        target_aggregate_type="personality",
        target_aggregate_id=fixture.identity_id,
        decided_at=NOW,
    )

    def commit_stale_write() -> None:
        with _uow(migrated_database) as unit:
            unit.personality.record_reflection_decision(
                outcome,
                write,
                identity_id=fixture.identity_id,
                trace_id="trace-personality-conflict",
                audit_event_id="personality-conflict-audit",
            )
            unit.commit()

    with pytest.raises(RuntimeError, match="concurrently modified"):
        commit_stale_write()
    assert _counts(migrated_database)["outcomes"] == 0
    assert _counts(migrated_database)["revisions"] == 0
    with migrated_database.session_factory() as session:
        state = session.get(PersonalityStateRow, fixture.identity_id)
        assert state is not None
        assert state.aggregate_version == 2
        curiosity = session.get(PersonalityTraitRow, (fixture.identity_id, "curiosity"))
        assert curiosity is not None
        assert curiosity.value == 0.92


@pytest.mark.parametrize(
    "tamper_kind",
    [
        "approved_checkpoint",
        "activation_distance",
        "approved_distance",
        "path_metrics",
    ],
)
def test_restore_revalidates_approved_origin_and_drift_ledger(
    migrated_database: Database,
    tamper_kind: str,
) -> None:
    fixture = _seed_personality_run(migrated_database)
    _apply(migrated_database, fixture).apply(
        fixture.identity_id,
        reflection_run_id=fixture.run_id,
        reflection_proposal_id=fixture.proposal_id,
        proposal=fixture.proposal,
        trace_id="trace-personality-before-restore-tamper",
    )
    inspection = GetPersonalityEvolution(
        unit_of_work_factory=lambda: _uow(migrated_database),
        clock=FrozenClock(NOW + timedelta(days=1)),
    ).inspect(fixture.identity_id)
    assert inspection is not None
    evolution_checkpoint = next(
        item
        for item in inspection.checkpoints
        if item.snapshot.checkpoint_kind is PersonalityCheckpointKind.EVOLUTION
    )
    ApprovePersonalityCheckpoint(
        unit_of_work_factory=lambda: _uow(migrated_database),
        clock=FrozenClock(NOW + timedelta(days=1)),
        id_generator=PrefixIds("restore-tamper-approval"),
    ).execute(
        fixture.identity_id,
        PersonalityCheckpointApprovalProposal(
            checkpoint_id=evolution_checkpoint.snapshot.checkpoint_id,
            checkpoint_hash=evolution_checkpoint.snapshot.checkpoint_hash,
            expected_personality_version=2,
            reason="Reviewed checkpoint for restore tamper test.",
        ),
        trace_id="trace-restore-tamper-approval",
    )
    _proposal, write = _prepare_restore_write(
        migrated_database,
        fixture,
        now=NOW + timedelta(days=2),
    )
    if tamper_kind == "approved_checkpoint":
        tampered = replace(write, approved_checkpoint=inspection.activation_checkpoint.snapshot)
    elif tamper_kind == "activation_distance":
        tampered = replace(
            write,
            activation_distance=replace(
                write.activation_distance,
                l1=write.activation_distance.l1 + 0.005,
            ),
        )
    elif tamper_kind == "approved_distance":
        tampered = replace(
            write,
            approved_checkpoint_distance=replace(
                write.approved_checkpoint_distance,
                linf=write.approved_checkpoint_distance.linf + 0.005,
            ),
        )
    else:
        tampered = replace(write, lifetime_total_path=write.lifetime_total_path + 0.005)

    def commit_tampered_restore() -> None:
        with _uow(migrated_database) as unit:
            unit.personality.record_restore(
                tampered,
                reason="Controlled tamper.",
                restored_at=NOW + timedelta(days=2),
                trace_id="trace-restore-tamper",
                audit_event_id="restore-tamper-audit",
            )
            unit.commit()

    with pytest.raises((RuntimeError, ValueError), match=r"inputs changed|metrics were altered"):
        commit_tampered_restore()
    counts = _counts(migrated_database)
    assert counts["revisions"] == 1
    assert counts["restores"] == 0
    assert counts["checkpoints"] == 2


def test_stale_restore_rolls_back_revision_checkpoint_event_and_audit(
    migrated_database: Database,
) -> None:
    fixture = _seed_personality_run(migrated_database)
    _apply(migrated_database, fixture).apply(
        fixture.identity_id,
        reflection_run_id=fixture.run_id,
        reflection_proposal_id=fixture.proposal_id,
        proposal=fixture.proposal,
        trace_id="trace-personality-before-stale-restore",
    )
    _proposal, write = _prepare_restore_write(
        migrated_database,
        fixture,
        now=NOW + timedelta(days=2),
    )
    with migrated_database.session_factory() as session:
        session.execute(
            update(PersonalityStateRow)
            .where(PersonalityStateRow.identity_id == fixture.identity_id)
            .values(aggregate_version=3)
        )
        session.commit()

    def commit_stale_restore() -> None:
        with _uow(migrated_database) as unit:
            unit.personality.record_restore(
                write,
                reason="Controlled stale restore.",
                restored_at=NOW + timedelta(days=2),
                trace_id="trace-stale-restore",
                audit_event_id="stale-restore-audit",
            )
            unit.commit()

    with pytest.raises(RuntimeError, match="concurrently modified"):
        commit_stale_restore()
    counts = _counts(migrated_database)
    assert counts["revisions"] == 1
    assert counts["restores"] == 0
    assert counts["checkpoints"] == 2


@pytest.mark.parametrize(
    "statement_marker",
    [
        "INSERT INTO personality_checkpoints",
        "UPDATE satori_personality_states",
        "INSERT INTO personality_revisions",
        "INSERT INTO personality_restore_events",
        "INSERT INTO audit_events",
    ],
)
def test_every_restore_write_point_failure_rolls_back_the_whole_restore(
    migrated_database: Database,
    statement_marker: str,
) -> None:
    fixture = _seed_personality_run(migrated_database)
    _apply(migrated_database, fixture).apply(
        fixture.identity_id,
        reflection_run_id=fixture.run_id,
        reflection_proposal_id=fixture.proposal_id,
        proposal=fixture.proposal,
        trace_id="trace-personality-before-restore-failure",
    )
    inspection = GetPersonalityEvolution(
        unit_of_work_factory=lambda: _uow(migrated_database),
        clock=FrozenClock(NOW + timedelta(days=2)),
    ).inspect(fixture.identity_id)
    assert inspection is not None
    proposal = PersonalityRestoreProposal(
        checkpoint_id=inspection.activation_checkpoint.snapshot.checkpoint_id,
        checkpoint_hash=inspection.activation_checkpoint.snapshot.checkpoint_hash,
        expected_personality_version=2,
        reason="Controlled restore write failure.",
    )

    def fail_write(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        if statement_marker.lower() in statement.lower():
            raise RuntimeError(f"controlled restore failure: {statement_marker}")

    event.listen(migrated_database.engine, "before_cursor_execute", fail_write)
    try:
        with pytest.raises(RuntimeError, match="controlled restore failure"):
            RestorePersonalityCheckpoint(
                unit_of_work_factory=lambda: _uow(migrated_database),
                manager=PersonalityManager(),
                clock=FrozenClock(NOW + timedelta(days=2)),
                id_generator=PrefixIds("restore-write-failure"),
            ).execute(
                fixture.identity_id,
                proposal,
                trace_id="trace-restore-write-failure",
            )
    finally:
        event.remove(migrated_database.engine, "before_cursor_execute", fail_write)
    counts = _counts(migrated_database)
    assert counts["outcomes"] == 1
    assert counts["revisions"] == 1
    assert counts["evidence"] == 8
    assert counts["checkpoints"] == 2
    assert counts["restores"] == 0
    assert counts["personality_audits"] == 1
    with migrated_database.session_factory() as session:
        state = session.get(PersonalityStateRow, fixture.identity_id)
        trait = session.get(PersonalityTraitRow, (fixture.identity_id, "curiosity"))
        assert state is not None
        assert state.aggregate_version == 2
        assert trait is not None
        assert trait.value == pytest.approx(0.925)


@pytest.mark.parametrize(
    "statement_marker",
    [
        "INSERT INTO reflection_outcomes",
        "INSERT INTO personality_checkpoints",
        "UPDATE satori_personality_states",
        "INSERT INTO personality_revisions",
        "INSERT INTO personality_evidence",
        "INSERT INTO audit_events",
    ],
)
def test_every_owner_write_point_failure_rolls_back_the_whole_decision(
    migrated_database: Database,
    statement_marker: str,
) -> None:
    fixture = _seed_personality_run(migrated_database)

    def fail_write(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        if statement_marker.lower() in statement.lower():
            raise RuntimeError(f"controlled write failure: {statement_marker}")

    event.listen(migrated_database.engine, "before_cursor_execute", fail_write)
    try:
        with pytest.raises(RuntimeError, match="controlled write failure"):
            _apply(migrated_database, fixture).apply(
                fixture.identity_id,
                reflection_run_id=fixture.run_id,
                reflection_proposal_id=fixture.proposal_id,
                proposal=fixture.proposal,
                trace_id="trace-personality-write-failure",
            )
    finally:
        event.remove(migrated_database.engine, "before_cursor_execute", fail_write)
    assert _counts(migrated_database) == {
        "outcomes": 0,
        "revisions": 0,
        "evidence": 0,
        "checkpoints": 1,
        "approvals": 0,
        "restores": 0,
        "personality_audits": 0,
    }
    with migrated_database.session_factory() as session:
        state = session.get(PersonalityStateRow, fixture.identity_id)
        trait = session.get(PersonalityTraitRow, (fixture.identity_id, "curiosity"))
        assert state is not None
        assert state.aggregate_version == 1
        assert trait is not None
        assert trait.value == 0.92
