"""Stage 14 personality owner orchestration, inspection, approval, and restore."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from satori.application.personality.ports import (
    PersonalityCheckpointApprovalRecord,
    PersonalityCheckpointRecord,
    PersonalityEvidenceRecord,
    PersonalityEvidenceWrite,
    PersonalityEvolutionWrite,
    PersonalityRestoreEventRecord,
    PersonalityRestoreWrite,
    PersonalityRevisionRecord,
    PersonalityTraitDiff,
    PersonalityUnitOfWork,
    ResolvedPersonalitySource,
)
from satori.core.clock import Clock
from satori.core.ids import IdGenerator
from satori.core.personality import (
    CANONICAL_TRAIT_KEYS,
    PersonalityChangeProposal,
    PersonalityRestoreProposal,
    PersonalityStateReference,
    PersonalityTraitKey,
)
from satori.domain.errors import CorruptSatoriState
from satori.domain.personality import Personality, PersonalityTrait
from satori.domain.personality_evolution import (
    ACTIVATION_L1_CAP,
    ACTIVATION_LINF_CAP,
    CHECKPOINT_L1_CAP,
    CHECKPOINT_LINF_CAP,
    LIFETIME_GLOBAL_PATH_CAP,
    LIFETIME_TRAIT_PATH_CAP,
    PERSONALITY_CHECKPOINT_HASH_SCHEMA_VERSION,
    PERSONALITY_EVOLUTION_POLICY_VERSION,
    PERSONALITY_EVOLUTION_SCHEMA_VERSION,
    ROLLING_GLOBAL_PATH_CAP,
    ROLLING_TRAIT_PATH_CAP,
    ROLLING_WINDOW,
    PersonalityChangeEvaluation,
    PersonalityCheckpointKind,
    PersonalityCheckpointSnapshot,
    PersonalityDecisionKind,
    PersonalityEvolutionRecord,
    PersonalityManager,
    PersonalityRestoreEvaluation,
    checkpoint_hash,
    personality_content_signature,
    trait_distance,
)
from satori.domain.reflection import (
    ReflectionOutcome,
    ReflectionOutcomeDecision,
    reflection_outcome_id,
)

PersonalityUnitOfWorkFactory = Callable[[], PersonalityUnitOfWork]


class PersonalityCheckpointApprovalProposal(BaseModel):
    """Explicit local approval; provider/reflection paths cannot construct it implicitly."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
    )

    checkpoint_id: str = Field(min_length=1, max_length=128)
    checkpoint_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_personality_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)


@dataclass(frozen=True, slots=True)
class PersonalityReflectionResult:
    outcome: ReflectionOutcome
    evaluation: PersonalityChangeEvaluation | None
    personality: Personality
    recorded: bool


@dataclass(frozen=True, slots=True)
class PersonalityRestoreResult:
    evaluation: PersonalityRestoreEvaluation
    personality: Personality
    restored: bool


@dataclass(frozen=True, slots=True)
class PersonalityTraitBudget:
    trait_key: PersonalityTraitKey
    rolling_path: float
    rolling_remaining: float
    lifetime_path: float
    lifetime_remaining: float


@dataclass(frozen=True, slots=True)
class PersonalityBudgetInspection:
    activation_distance_linf: float
    activation_distance_l1: float
    activation_linf_remaining: float
    activation_l1_remaining: float
    approved_checkpoint_distance_linf: float
    approved_checkpoint_distance_l1: float
    approved_checkpoint_linf_remaining: float
    approved_checkpoint_l1_remaining: float
    rolling_global_path: float
    rolling_global_remaining: float
    lifetime_global_path: float
    lifetime_global_remaining: float
    traits: tuple[PersonalityTraitBudget, ...]


@dataclass(frozen=True, slots=True)
class PersonalityInspection:
    identity_id: str
    personality: Personality
    activation_checkpoint: PersonalityCheckpointRecord
    approved_checkpoint: PersonalityCheckpointRecord
    checkpoints: tuple[PersonalityCheckpointRecord, ...]
    revisions: tuple[PersonalityRevisionRecord, ...]
    evidence: tuple[PersonalityEvidenceRecord, ...]
    approvals: tuple[PersonalityCheckpointApprovalRecord, ...]
    restores: tuple[PersonalityRestoreEventRecord, ...]
    budgets: PersonalityBudgetInspection


@dataclass(frozen=True, slots=True)
class PersonalityCheckpointComparison:
    identity_id: str
    current_aggregate_version: int
    checkpoint_id: str
    checkpoint_aggregate_version: int
    checkpoint_hash: str
    trait_diffs: tuple[PersonalityTraitDiff, ...]
    distance_linf: float
    distance_l1: float


@dataclass(slots=True)
class ApplyPersonalityReflection:
    """Sole application entry point for one persisted Reflection V3 proposal."""

    unit_of_work_factory: PersonalityUnitOfWorkFactory
    manager: PersonalityManager
    clock: Clock
    id_generator: IdGenerator

    def execute(
        self,
        identity_id: str,
        *,
        reflection_run_id: str,
        reflection_proposal_id: str,
        proposal: PersonalityChangeProposal,
        trace_id: str,
    ) -> None:
        """Implement the narrow Reflection router protocol."""

        self.apply(
            identity_id,
            reflection_run_id=reflection_run_id,
            reflection_proposal_id=reflection_proposal_id,
            proposal=proposal,
            trace_id=trace_id,
        )

    def apply(
        self,
        identity_id: str,
        *,
        reflection_run_id: str,
        reflection_proposal_id: str,
        proposal: PersonalityChangeProposal,
        trace_id: str,
    ) -> PersonalityReflectionResult:
        now = self.clock.now()
        outcome_id = reflection_outcome_id(
            proposal_id=reflection_proposal_id,
            target_policy_version=PERSONALITY_EVOLUTION_POLICY_VERSION,
        )
        with self.unit_of_work_factory() as unit:
            prior = unit.personality.get_reflection_outcome(
                reflection_proposal_id, PERSONALITY_EVOLUTION_POLICY_VERSION
            )
            current = unit.personality.get_current(identity_id)
            if current is None:
                raise ValueError("personality identity is not active")
            if prior is not None:
                return PersonalityReflectionResult(prior, None, current, False)
            resolved = unit.personality.resolve_reflection_sources(
                identity_id=identity_id,
                reflection_run_id=reflection_run_id,
                reflection_proposal_id=reflection_proposal_id,
                proposal=proposal,
            )
            approved = unit.personality.get_approved_checkpoint(identity_id)
            if approved is None:
                raise CorruptSatoriState("personality approved checkpoint is missing")
            history = unit.personality.list_evolution_records(identity_id)
            used_roots = unit.personality.list_used_root_message_ids(identity_id)
            evaluation = self.manager.evaluate_change(
                proposal,
                identity_id=identity_id,
                personality=current,
                approved_checkpoint=approved.snapshot,
                fixed_sources=tuple(item.source for item in resolved),
                prior_evolution=history,
                used_root_message_ids=used_roots,
                now=now,
            )
            accepted = evaluation.kind is PersonalityDecisionKind.APPLIED
            outcome = ReflectionOutcome(
                outcome_id=outcome_id,
                proposal_id=reflection_proposal_id,
                target_policy_version=PERSONALITY_EVOLUTION_POLICY_VERSION,
                decision=(
                    ReflectionOutcomeDecision.ACCEPTED
                    if accepted
                    else ReflectionOutcomeDecision.REJECTED
                ),
                reason_code=evaluation.reason_code,
                target_aggregate_type="personality" if accepted else None,
                target_aggregate_id=identity_id if accepted else None,
                decided_at=now,
            )
            evolution = (
                self._evolution_write(
                    identity_id=identity_id,
                    reflection_run_id=reflection_run_id,
                    reflection_proposal_id=reflection_proposal_id,
                    current=current,
                    evaluation=evaluation,
                    resolved=resolved,
                    proposal=proposal,
                    current_checkpoint=unit.personality.get_checkpoint_for_version(
                        identity_id, current.aggregate_version
                    ),
                )
                if accepted
                else None
            )
            recorded = unit.personality.record_reflection_decision(
                outcome,
                evolution,
                identity_id=identity_id,
                trace_id=trace_id,
                audit_event_id=self.id_generator.new(),
            )
            if recorded:
                unit.commit()
                personality = (
                    evaluation.plan.personality if evaluation.plan is not None else current
                )
                return PersonalityReflectionResult(outcome, evaluation, personality, True)
            replay = unit.personality.get_reflection_outcome(
                reflection_proposal_id, PERSONALITY_EVOLUTION_POLICY_VERSION
            )
            latest = unit.personality.get_current(identity_id)
            if replay is None or latest is None:
                raise RuntimeError("personality reflection replay disappeared")
            return PersonalityReflectionResult(replay, None, latest, False)

    def _evolution_write(
        self,
        *,
        identity_id: str,
        reflection_run_id: str,
        reflection_proposal_id: str,
        current: Personality,
        evaluation: PersonalityChangeEvaluation,
        resolved: tuple[ResolvedPersonalitySource, ...],
        proposal: PersonalityChangeProposal,
        current_checkpoint: PersonalityCheckpointRecord | None,
    ) -> PersonalityEvolutionWrite:
        plan = evaluation.plan
        if plan is None:
            raise ValueError("applied personality evaluation has no mutation plan")
        source_checkpoint = (
            current_checkpoint.snapshot
            if current_checkpoint is not None
            else _checkpoint_snapshot(
                identity_id,
                current,
                checkpoint_kind=PersonalityCheckpointKind.MANUAL,
            )
        )
        resulting_checkpoint = _checkpoint_snapshot(
            identity_id,
            plan.personality,
            checkpoint_kind=PersonalityCheckpointKind.EVOLUTION,
        )
        resolved_by_id = {item.source.source_id: item for item in resolved}
        roles = {item.source_id: item.role for item in proposal.citations}
        evidence = tuple(
            PersonalityEvidenceWrite(
                evidence_id=self.id_generator.new(),
                source=resolved_by_id[item.source_id],
                normalized_signature=personality_content_signature(item.quote),
                citation_role=roles[item.source_id],
            )
            for item in plan.accepted_sources
        )
        return PersonalityEvolutionWrite(
            before_personality=current,
            evaluation=evaluation,
            source_checkpoint=source_checkpoint,
            resulting_checkpoint=resulting_checkpoint,
            revision_id=self.id_generator.new(),
            evidence=evidence,
            reflection_run_id=reflection_run_id,
            reflection_proposal_id=reflection_proposal_id,
        )


@dataclass(frozen=True, slots=True)
class GetPersonalityEvolution:
    unit_of_work_factory: PersonalityUnitOfWorkFactory
    clock: Clock

    def get_state_reference(self, identity_id: str, /) -> PersonalityStateReference | None:
        with self.unit_of_work_factory() as unit:
            personality = unit.personality.get_current(identity_id)
        return (
            None
            if personality is None
            else PersonalityStateReference(
                identity_id=identity_id,
                aggregate_version=personality.aggregate_version,
            )
        )

    def list_used_root_message_ids(self, identity_id: str, /) -> frozenset[str]:
        with self.unit_of_work_factory() as unit:
            return unit.personality.list_used_root_message_ids(identity_id)

    def inspect(self, identity_id: str) -> PersonalityInspection | None:
        now = self.clock.now()
        with self.unit_of_work_factory() as unit:
            personality = unit.personality.get_current(identity_id)
            if personality is None:
                return None
            activation = unit.personality.get_activation_checkpoint(identity_id)
            approved = unit.personality.get_approved_checkpoint(identity_id)
            if activation is None or approved is None:
                raise CorruptSatoriState("personality checkpoint lineage is incomplete")
            history = unit.personality.list_evolution_records(identity_id)
            return PersonalityInspection(
                identity_id=identity_id,
                personality=personality,
                activation_checkpoint=activation,
                approved_checkpoint=approved,
                checkpoints=unit.personality.list_checkpoints(identity_id),
                revisions=unit.personality.list_revisions(identity_id),
                evidence=unit.personality.list_evidence(identity_id),
                approvals=unit.personality.list_checkpoint_approvals(identity_id),
                restores=unit.personality.list_restore_events(identity_id),
                budgets=_budget_inspection(
                    personality,
                    activation=activation.snapshot,
                    approved=approved.snapshot,
                    history=history,
                    now=now,
                ),
            )

    def compare(
        self, identity_id: str, checkpoint_id: str
    ) -> PersonalityCheckpointComparison | None:
        with self.unit_of_work_factory() as unit:
            personality = unit.personality.get_current(identity_id)
            checkpoint = unit.personality.get_checkpoint(checkpoint_id)
        if (
            personality is None
            or checkpoint is None
            or checkpoint.snapshot.identity_id != identity_id
        ):
            return None
        checkpoint_personality = _checkpoint_personality(checkpoint.snapshot)
        distance = trait_distance(personality, checkpoint_personality)
        current_values = {item.key: item.value for item in personality.traits}
        checkpoint_values = {item.key: item.value for item in checkpoint.snapshot.traits}
        diffs = tuple(
            PersonalityTraitDiff(key, checkpoint_values[key], current_values[key])
            for key in CANONICAL_TRAIT_KEYS
            if checkpoint_values[key] != current_values[key]
        )
        return PersonalityCheckpointComparison(
            identity_id=identity_id,
            current_aggregate_version=personality.aggregate_version,
            checkpoint_id=checkpoint.snapshot.checkpoint_id,
            checkpoint_aggregate_version=checkpoint.snapshot.source_aggregate_version,
            checkpoint_hash=checkpoint.snapshot.checkpoint_hash,
            trait_diffs=diffs,
            distance_linf=distance.linf,
            distance_l1=distance.l1,
        )

    def export_json(self, identity_id: str) -> str | None:
        inspection = self.inspect(identity_id)
        if inspection is None:
            return None
        payload = {
            "schema_version": PERSONALITY_EVOLUTION_SCHEMA_VERSION,
            "policy_version": PERSONALITY_EVOLUTION_POLICY_VERSION,
            "identity_id": identity_id,
            "personality": _personality_payload(inspection.personality),
            "activation_checkpoint_id": inspection.activation_checkpoint.snapshot.checkpoint_id,
            "approved_checkpoint_id": inspection.approved_checkpoint.snapshot.checkpoint_id,
            "checkpoints": [
                {
                    "checkpoint_id": item.snapshot.checkpoint_id,
                    "checkpoint_kind": item.snapshot.checkpoint_kind.value,
                    "source_aggregate_version": item.snapshot.source_aggregate_version,
                    "personality_schema_version": item.snapshot.personality_schema_version,
                    "hash_schema_version": item.snapshot.hash_schema_version,
                    "checkpoint_hash": item.snapshot.checkpoint_hash,
                    "created_at": item.created_at.isoformat(),
                    "traits": _traits_payload(item.snapshot.traits),
                }
                for item in inspection.checkpoints
            ],
            "revisions": [_revision_payload(item) for item in inspection.revisions],
            "evidence": [_evidence_payload(item) for item in inspection.evidence],
            "approvals": [
                {
                    "approval_id": item.approval_id,
                    "checkpoint_id": item.checkpoint_id,
                    "checkpoint_hash": item.checkpoint_hash,
                    "expected_aggregate_version": item.expected_aggregate_version,
                    "approved_at": item.approved_at.isoformat(),
                }
                for item in inspection.approvals
            ],
            "restores": [
                {
                    "restore_id": item.restore_id,
                    "revision_id": item.revision_id,
                    "source_checkpoint_id": item.source_checkpoint_id,
                    "source_checkpoint_hash": item.source_checkpoint_hash,
                    "resulting_checkpoint_id": item.resulting_checkpoint_id,
                    "before_aggregate_version": item.before_aggregate_version,
                    "after_aggregate_version": item.after_aggregate_version,
                    "trait_diffs": [_trait_diff_payload(diff) for diff in item.trait_diffs],
                    "restored_at": item.restored_at.isoformat(),
                }
                for item in inspection.restores
            ],
            "budgets": _budgets_payload(inspection.budgets),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class ApprovePersonalityCheckpoint:
    unit_of_work_factory: PersonalityUnitOfWorkFactory
    clock: Clock
    id_generator: IdGenerator

    def execute(
        self,
        identity_id: str,
        proposal: PersonalityCheckpointApprovalProposal,
        *,
        trace_id: str,
    ) -> PersonalityCheckpointApprovalRecord:
        with self.unit_of_work_factory() as unit:
            existing = next(
                (
                    item
                    for item in unit.personality.list_checkpoint_approvals(identity_id)
                    if item.checkpoint_id == proposal.checkpoint_id
                ),
                None,
            )
            if existing is not None:
                if existing.checkpoint_hash != proposal.checkpoint_hash:
                    raise CorruptSatoriState("checkpoint approval hash changed")
                return existing
            current = unit.personality.get_current(identity_id)
            checkpoint = unit.personality.get_checkpoint(proposal.checkpoint_id)
            if (
                current is None
                or checkpoint is None
                or checkpoint.snapshot.identity_id != identity_id
            ):
                raise ValueError("personality checkpoint does not exist")
            if (
                current.aggregate_version != proposal.expected_personality_version
                or checkpoint.snapshot.source_aggregate_version != current.aggregate_version
            ):
                raise ValueError("personality checkpoint approval target is stale")
            if checkpoint.snapshot.checkpoint_hash != proposal.checkpoint_hash:
                raise ValueError("personality checkpoint approval hash mismatch")
            if checkpoint.snapshot.checkpoint_kind is PersonalityCheckpointKind.ACTIVATION:
                raise ValueError("activation checkpoint is approved by definition")
            approval = PersonalityCheckpointApprovalRecord(
                approval_id=self.id_generator.new(),
                identity_id=identity_id,
                checkpoint_id=proposal.checkpoint_id,
                checkpoint_hash=proposal.checkpoint_hash,
                expected_aggregate_version=current.aggregate_version,
                reason=proposal.reason,
                approved_at=self.clock.now(),
            )
            recorded = unit.personality.record_checkpoint_approval(
                approval,
                trace_id=trace_id,
                audit_event_id=self.id_generator.new(),
            )
            if recorded:
                unit.commit()
                return approval
            replay = next(
                (
                    item
                    for item in unit.personality.list_checkpoint_approvals(identity_id)
                    if item.checkpoint_id == proposal.checkpoint_id
                ),
                None,
            )
            if replay is None:
                raise RuntimeError("personality checkpoint approval replay disappeared")
            return replay


@dataclass(frozen=True, slots=True)
class RestorePersonalityCheckpoint:
    unit_of_work_factory: PersonalityUnitOfWorkFactory
    manager: PersonalityManager
    clock: Clock
    id_generator: IdGenerator

    def execute(
        self,
        identity_id: str,
        proposal: PersonalityRestoreProposal,
        *,
        trace_id: str,
    ) -> PersonalityRestoreResult:
        now = self.clock.now()
        with self.unit_of_work_factory() as unit:
            current = unit.personality.get_current(identity_id)
            checkpoint = unit.personality.get_checkpoint(proposal.checkpoint_id)
            approved = unit.personality.get_approved_checkpoint(identity_id)
            if current is None:
                raise ValueError("personality identity is not active")
            if checkpoint is None:
                return PersonalityRestoreResult(
                    PersonalityRestoreEvaluation(
                        kind=PersonalityDecisionKind.REJECTED,
                        reason_code="personality_checkpoint_not_found",
                    ),
                    current,
                    False,
                )
            if approved is None:
                raise CorruptSatoriState("personality approved checkpoint is missing")
            activation = unit.personality.get_activation_checkpoint(identity_id)
            if activation is None:
                raise CorruptSatoriState("personality activation checkpoint is missing")
            evaluation = self.manager.evaluate_restore(
                proposal,
                identity_id=identity_id,
                personality=current,
                checkpoint=checkpoint.snapshot,
            )
            if evaluation.kind is not PersonalityDecisionKind.APPLIED:
                return PersonalityRestoreResult(evaluation, current, False)
            assert evaluation.plan is not None
            resulting = _checkpoint_snapshot(
                identity_id,
                evaluation.plan.personality,
                checkpoint_kind=PersonalityCheckpointKind.RESTORE,
            )
            prior_evolution = unit.personality.list_evolution_records(identity_id)
            recent_evolution = tuple(
                item for item in prior_evolution if item.occurred_at >= now - ROLLING_WINDOW
            )
            write = PersonalityRestoreWrite(
                before_personality=current,
                evaluation=evaluation,
                source_checkpoint=checkpoint.snapshot,
                approved_checkpoint=approved.snapshot,
                resulting_checkpoint=resulting,
                prior_evolution=prior_evolution,
                activation_distance=trait_distance(
                    evaluation.plan.personality,
                    _checkpoint_personality(activation.snapshot),
                ),
                approved_checkpoint_distance=trait_distance(
                    evaluation.plan.personality,
                    _checkpoint_personality(approved.snapshot),
                ),
                rolling_total_path=round(
                    sum(abs(item.applied_delta) for item in recent_evolution), 6
                ),
                lifetime_total_path=round(
                    sum(abs(item.applied_delta) for item in prior_evolution), 6
                ),
                revision_id=self.id_generator.new(),
                restore_id=self.id_generator.new(),
            )
            unit.personality.record_restore(
                write,
                reason=proposal.reason,
                restored_at=now,
                trace_id=trace_id,
                audit_event_id=self.id_generator.new(),
            )
            unit.commit()
            return PersonalityRestoreResult(evaluation, evaluation.plan.personality, True)


def _checkpoint_snapshot(
    identity_id: str,
    personality: Personality,
    *,
    checkpoint_kind: PersonalityCheckpointKind,
) -> PersonalityCheckpointSnapshot:
    digest = checkpoint_hash(
        identity_id=identity_id,
        checkpoint_kind=checkpoint_kind,
        personality=personality,
    )
    return PersonalityCheckpointSnapshot(
        checkpoint_id=f"personality-checkpoint-{digest}",
        checkpoint_kind=checkpoint_kind,
        identity_id=identity_id,
        source_aggregate_version=personality.aggregate_version,
        personality_schema_version=personality.schema_version,
        hash_schema_version=PERSONALITY_CHECKPOINT_HASH_SCHEMA_VERSION,
        checkpoint_hash=digest,
        traits=personality.traits,
    )


def _checkpoint_personality(checkpoint: PersonalityCheckpointSnapshot) -> Personality:
    return Personality(
        schema_version=checkpoint.personality_schema_version,
        aggregate_version=checkpoint.source_aggregate_version,
        traits=checkpoint.traits,
    )


def _budget_inspection(
    personality: Personality,
    *,
    activation: PersonalityCheckpointSnapshot,
    approved: PersonalityCheckpointSnapshot,
    history: tuple[PersonalityEvolutionRecord, ...],
    now: datetime,
) -> PersonalityBudgetInspection:
    activation_distance = trait_distance(personality, _checkpoint_personality(activation))
    approved_distance = trait_distance(personality, _checkpoint_personality(approved))
    recent = tuple(item for item in history if item.occurred_at >= now - ROLLING_WINDOW)
    rolling_global = round(sum(abs(item.applied_delta) for item in recent), 6)
    lifetime_global = round(sum(abs(item.applied_delta) for item in history), 6)
    trait_budgets = tuple(
        PersonalityTraitBudget(
            trait_key=key,
            rolling_path=(
                rolling := round(
                    sum(abs(item.applied_delta) for item in recent if item.trait_key == key), 6
                )
            ),
            rolling_remaining=round(max(0.0, ROLLING_TRAIT_PATH_CAP - rolling), 6),
            lifetime_path=(
                lifetime := round(
                    sum(abs(item.applied_delta) for item in history if item.trait_key == key),
                    6,
                )
            ),
            lifetime_remaining=round(max(0.0, LIFETIME_TRAIT_PATH_CAP - lifetime), 6),
        )
        for key in CANONICAL_TRAIT_KEYS
    )
    return PersonalityBudgetInspection(
        activation_distance_linf=activation_distance.linf,
        activation_distance_l1=activation_distance.l1,
        activation_linf_remaining=round(
            max(0.0, ACTIVATION_LINF_CAP - activation_distance.linf), 6
        ),
        activation_l1_remaining=round(max(0.0, ACTIVATION_L1_CAP - activation_distance.l1), 6),
        approved_checkpoint_distance_linf=approved_distance.linf,
        approved_checkpoint_distance_l1=approved_distance.l1,
        approved_checkpoint_linf_remaining=round(
            max(0.0, CHECKPOINT_LINF_CAP - approved_distance.linf), 6
        ),
        approved_checkpoint_l1_remaining=round(
            max(0.0, CHECKPOINT_L1_CAP - approved_distance.l1), 6
        ),
        rolling_global_path=rolling_global,
        rolling_global_remaining=round(max(0.0, ROLLING_GLOBAL_PATH_CAP - rolling_global), 6),
        lifetime_global_path=lifetime_global,
        lifetime_global_remaining=round(max(0.0, LIFETIME_GLOBAL_PATH_CAP - lifetime_global), 6),
        traits=trait_budgets,
    )


def _personality_payload(personality: Personality) -> dict[str, object]:
    return {
        "schema_version": personality.schema_version,
        "aggregate_version": personality.aggregate_version,
        "traits": _traits_payload(personality.traits),
    }


def _traits_payload(traits: tuple[PersonalityTrait, ...]) -> list[dict[str, object]]:
    return [
        {
            "trait_key": item.key,
            "value": item.value,
            "baseline_value": item.baseline_value,
        }
        for item in traits
    ]


def _trait_diff_payload(diff: PersonalityTraitDiff) -> dict[str, object]:
    return {
        "trait_key": diff.trait_key,
        "before_value": diff.before_value,
        "after_value": diff.after_value,
    }


def _revision_payload(item: PersonalityRevisionRecord) -> dict[str, object]:
    return {
        "revision_id": item.revision_id,
        "revision_kind": item.revision_kind,
        "before_aggregate_version": item.before_aggregate_version,
        "after_aggregate_version": item.after_aggregate_version,
        "trait_key": item.trait_key,
        "direction": item.direction,
        "before_value": item.before_value,
        "after_value": item.after_value,
        "applied_delta": item.applied_delta,
        "decision_confidence": item.decision_confidence,
        "policy_version": item.policy_version,
        "reason_code": item.reason_code,
        "source_checkpoint_id": item.source_checkpoint_id,
        "resulting_checkpoint_id": item.resulting_checkpoint_id,
        "reflection_outcome_id": item.reflection_outcome_id,
        "trait_diffs": [_trait_diff_payload(diff) for diff in item.trait_diffs],
        "activation_distance_linf": item.activation_distance_linf,
        "activation_distance_l1": item.activation_distance_l1,
        "approved_checkpoint_distance_linf": item.approved_checkpoint_distance_linf,
        "approved_checkpoint_distance_l1": item.approved_checkpoint_distance_l1,
        "rolling_trait_path": item.rolling_trait_path,
        "rolling_total_path": item.rolling_total_path,
        "lifetime_trait_path": item.lifetime_trait_path,
        "lifetime_total_path": item.lifetime_total_path,
        "occurred_at": item.occurred_at.isoformat(),
    }


def _evidence_payload(item: PersonalityEvidenceRecord) -> dict[str, object]:
    return {
        "evidence_id": item.evidence_id,
        "revision_id": item.revision_id,
        "trait_key": item.trait_key,
        "direction": item.direction,
        "reflection_run_id": item.reflection_run_id,
        "reflection_proposal_id": item.reflection_proposal_id,
        "reflection_source_id": item.reflection_source_id,
        "evidence_edge_id": item.evidence_edge_id,
        "evidence_edge_version": item.evidence_edge_version,
        "root_interaction_id": item.root_interaction_id,
        "root_message_id": item.root_message_id,
        "root_session_id": item.root_session_id,
        "root_counterparty_id": item.root_counterparty_id,
        "upstream_lineage_kind": item.upstream_lineage_kind.value,
        "upstream_lineage_id": item.upstream_lineage_id,
        "content_hash": item.content_hash,
        "normalized_signature": item.normalized_signature,
        "citation_role": item.citation_role.value,
        "observed_at": item.observed_at.isoformat(),
        "accepted_at": item.accepted_at.isoformat(),
    }


def _budgets_payload(item: PersonalityBudgetInspection) -> dict[str, object]:
    return {
        "activation_distance_linf": item.activation_distance_linf,
        "activation_distance_l1": item.activation_distance_l1,
        "activation_linf_remaining": item.activation_linf_remaining,
        "activation_l1_remaining": item.activation_l1_remaining,
        "approved_checkpoint_distance_linf": item.approved_checkpoint_distance_linf,
        "approved_checkpoint_distance_l1": item.approved_checkpoint_distance_l1,
        "approved_checkpoint_linf_remaining": item.approved_checkpoint_linf_remaining,
        "approved_checkpoint_l1_remaining": item.approved_checkpoint_l1_remaining,
        "rolling_global_path": item.rolling_global_path,
        "rolling_global_remaining": item.rolling_global_remaining,
        "lifetime_global_path": item.lifetime_global_path,
        "lifetime_global_remaining": item.lifetime_global_remaining,
        "traits": [
            {
                "trait_key": trait.trait_key,
                "rolling_path": trait.rolling_path,
                "rolling_remaining": trait.rolling_remaining,
                "lifetime_path": trait.lifetime_path,
                "lifetime_remaining": trait.lifetime_remaining,
            }
            for trait in item.traits
        ],
    }
