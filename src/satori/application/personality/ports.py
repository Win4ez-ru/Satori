"""Application-owned persistence boundary for Stage 14 personality evolution."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from satori.application.unit_of_work import UnitOfWork
from satori.core.personality import (
    PersonalityChangeProposal,
    PersonalityCitationRole,
    PersonalityTraitKey,
)
from satori.core.reflection import ReflectionLineageKind
from satori.domain.personality import Personality
from satori.domain.personality_evolution import (
    PersonalityChangeEvaluation,
    PersonalityCheckpointSnapshot,
    PersonalityEvidenceSource,
    PersonalityEvolutionRecord,
    PersonalityRestoreEvaluation,
    TraitDistance,
)
from satori.domain.reflection import ReflectionOutcome


@dataclass(frozen=True, slots=True)
class ResolvedPersonalitySource:
    """Canonical V3 source re-resolved by the personality owner boundary."""

    source: PersonalityEvidenceSource
    evidence_edge_version: int
    upstream_lineage_kind: ReflectionLineageKind


@dataclass(frozen=True, slots=True)
class PersonalityCheckpointRecord:
    snapshot: PersonalityCheckpointSnapshot
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PersonalityTraitDiff:
    trait_key: PersonalityTraitKey
    before_value: float
    after_value: float


@dataclass(frozen=True, slots=True)
class PersonalityRevisionRecord:
    revision_id: str
    identity_id: str
    revision_kind: str
    before_aggregate_version: int
    after_aggregate_version: int
    trait_key: PersonalityTraitKey | None
    direction: str | None
    before_value: float | None
    after_value: float | None
    applied_delta: float | None
    decision_confidence: float | None
    policy_version: int
    reason_code: str
    source_checkpoint_id: str
    resulting_checkpoint_id: str
    reflection_outcome_id: str | None
    trait_diffs: tuple[PersonalityTraitDiff, ...]
    activation_distance_linf: float
    activation_distance_l1: float
    approved_checkpoint_distance_linf: float
    approved_checkpoint_distance_l1: float
    rolling_trait_path: float | None
    rolling_total_path: float
    lifetime_trait_path: float | None
    lifetime_total_path: float
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class PersonalityEvidenceRecord:
    """Quote-free accepted personality evidence projection."""

    evidence_id: str
    revision_id: str
    identity_id: str
    trait_key: PersonalityTraitKey
    direction: str
    reflection_run_id: str
    reflection_proposal_id: str
    reflection_source_id: str
    evidence_edge_id: str
    evidence_edge_version: int
    root_interaction_id: str
    root_message_id: str
    root_session_id: str
    root_counterparty_id: str
    upstream_lineage_kind: ReflectionLineageKind
    upstream_lineage_id: str
    content_hash: str
    normalized_signature: str
    citation_role: PersonalityCitationRole
    observed_at: datetime
    accepted_at: datetime


@dataclass(frozen=True, slots=True)
class PersonalityCheckpointApprovalRecord:
    approval_id: str
    identity_id: str
    checkpoint_id: str
    checkpoint_hash: str
    expected_aggregate_version: int
    reason: str
    approved_at: datetime


@dataclass(frozen=True, slots=True)
class PersonalityRestoreEventRecord:
    restore_id: str
    revision_id: str
    identity_id: str
    source_checkpoint_id: str
    source_checkpoint_hash: str
    resulting_checkpoint_id: str
    before_aggregate_version: int
    after_aggregate_version: int
    trait_diffs: tuple[PersonalityTraitDiff, ...]
    reason: str
    restored_at: datetime


@dataclass(frozen=True, slots=True)
class PersonalityEvidenceWrite:
    evidence_id: str
    source: ResolvedPersonalitySource
    normalized_signature: str
    citation_role: PersonalityCitationRole


@dataclass(frozen=True, slots=True)
class PersonalityEvolutionWrite:
    before_personality: Personality
    evaluation: PersonalityChangeEvaluation
    source_checkpoint: PersonalityCheckpointSnapshot
    resulting_checkpoint: PersonalityCheckpointSnapshot
    revision_id: str
    evidence: tuple[PersonalityEvidenceWrite, ...]
    reflection_run_id: str
    reflection_proposal_id: str


@dataclass(frozen=True, slots=True)
class PersonalityRestoreWrite:
    before_personality: Personality
    evaluation: PersonalityRestoreEvaluation
    source_checkpoint: PersonalityCheckpointSnapshot
    approved_checkpoint: PersonalityCheckpointSnapshot
    resulting_checkpoint: PersonalityCheckpointSnapshot
    prior_evolution: tuple[PersonalityEvolutionRecord, ...]
    activation_distance: TraitDistance
    approved_checkpoint_distance: TraitDistance
    rolling_total_path: float
    lifetime_total_path: float
    revision_id: str
    restore_id: str


class PersonalityRepository(Protocol):
    def get_current(self, identity_id: str) -> Personality | None: ...

    def get_checkpoint(self, checkpoint_id: str) -> PersonalityCheckpointRecord | None: ...

    def get_checkpoint_for_version(
        self, identity_id: str, aggregate_version: int
    ) -> PersonalityCheckpointRecord | None: ...

    def get_activation_checkpoint(self, identity_id: str) -> PersonalityCheckpointRecord | None: ...

    def get_approved_checkpoint(self, identity_id: str) -> PersonalityCheckpointRecord | None: ...

    def list_checkpoints(self, identity_id: str) -> tuple[PersonalityCheckpointRecord, ...]: ...

    def list_revisions(self, identity_id: str) -> tuple[PersonalityRevisionRecord, ...]: ...

    def list_evidence(self, identity_id: str) -> tuple[PersonalityEvidenceRecord, ...]: ...

    def list_checkpoint_approvals(
        self, identity_id: str
    ) -> tuple[PersonalityCheckpointApprovalRecord, ...]: ...

    def list_restore_events(
        self, identity_id: str
    ) -> tuple[PersonalityRestoreEventRecord, ...]: ...

    def list_evolution_records(
        self, identity_id: str
    ) -> tuple[PersonalityEvolutionRecord, ...]: ...

    def list_used_root_message_ids(self, identity_id: str) -> frozenset[str]: ...

    def get_reflection_outcome(
        self, reflection_proposal_id: str, target_policy_version: int
    ) -> ReflectionOutcome | None: ...

    def resolve_reflection_sources(
        self,
        *,
        identity_id: str,
        reflection_run_id: str,
        reflection_proposal_id: str,
        proposal: PersonalityChangeProposal,
    ) -> tuple[ResolvedPersonalitySource, ...]: ...

    def record_reflection_decision(
        self,
        outcome: ReflectionOutcome,
        evolution: PersonalityEvolutionWrite | None,
        *,
        identity_id: str,
        trace_id: str,
        audit_event_id: str,
    ) -> bool: ...

    def record_checkpoint_approval(
        self,
        approval: PersonalityCheckpointApprovalRecord,
        *,
        trace_id: str,
        audit_event_id: str,
    ) -> bool: ...

    def record_restore(
        self,
        restore: PersonalityRestoreWrite,
        *,
        reason: str,
        restored_at: datetime,
        trace_id: str,
        audit_event_id: str,
    ) -> None: ...


class PersonalityUnitOfWork(UnitOfWork, Protocol):
    @property
    def personality(self) -> PersonalityRepository: ...
