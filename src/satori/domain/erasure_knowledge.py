"""Private owner body actions and explicit historical evidence continuity."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from satori.domain.erasure_dialogue import HistoricalEvidenceTable
from satori.domain.source_policy import CanonicalRootRef
from satori.domain.validation import aware_utc, non_blank, positive_version, sha256_hex

KNOWLEDGE_BODY_ERASURE_POLICY_VERSION = "knowledge_body_erasure_v1"
MAX_KNOWLEDGE_TARGETS = 256
MAX_KNOWLEDGE_WITNESSES = 512


class KnowledgeBodyKind(StrEnum):
    SEMANTIC_CLAIM = "semantic_claim"
    USER_MODEL_CLAIM = "user_model_claim"
    WORLD_MODEL_CLAIM = "world_model_claim"
    POSITION = "position"
    INCLINATION = "inclination"


class KnowledgeWitnessOrigin(StrEnum):
    DIALOGUE = "dialogue"
    KNOWLEDGE = "knowledge"
    LIVE = "live"


@dataclass(frozen=True, slots=True)
class KnowledgeWitnessTransition:
    """Exact retained-row transition; historical attribution, never learning authority."""

    table: HistoricalEvidenceTable
    evidence_id: str
    root: CanonicalRootRef
    before_digest: str
    after_digest: str
    origin: KnowledgeWitnessOrigin
    predecessor_action_id: str | None = None

    def __post_init__(self) -> None:
        if self.table is not HistoricalEvidenceTable.POSITION:
            raise ValueError("unsupported_knowledge_evidence_table")
        if not isinstance(self.root, CanonicalRootRef):
            raise ValueError("invalid_knowledge_witness_root")
        if not isinstance(self.origin, KnowledgeWitnessOrigin):
            raise ValueError("invalid_knowledge_witness_origin")
        object.__setattr__(
            self, "evidence_id", non_blank(self.evidence_id, "evidence_id", maximum=128)
        )
        sha256_hex(self.before_digest, "before_digest")
        sha256_hex(self.after_digest, "after_digest")
        if self.origin is KnowledgeWitnessOrigin.LIVE:
            if self.predecessor_action_id is not None:
                raise ValueError("live_knowledge_witness_has_no_predecessor")
        elif self.predecessor_action_id is None:
            raise ValueError("knowledge_witness_predecessor_required")
        else:
            object.__setattr__(
                self,
                "predecessor_action_id",
                non_blank(self.predecessor_action_id, "predecessor_action_id", maximum=128),
            )


@dataclass(frozen=True, slots=True)
class KnowledgeBodyTarget:
    kind: KnowledgeBodyKind
    aggregate_id: str
    aggregate_version: int
    body_digest: str
    terminal_digest: str
    witness_transitions: tuple[KnowledgeWitnessTransition, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, KnowledgeBodyKind):
            raise ValueError("invalid_knowledge_body_kind")
        object.__setattr__(
            self, "aggregate_id", non_blank(self.aggregate_id, "aggregate_id", maximum=128)
        )
        positive_version(self.aggregate_version, "aggregate_version")
        sha256_hex(self.body_digest, "body_digest")
        sha256_hex(self.terminal_digest, "terminal_digest")
        transitions = tuple(self.witness_transitions)
        if len(transitions) > MAX_KNOWLEDGE_WITNESSES:
            raise ValueError("knowledge_witness_capacity_exceeded")
        if any(not isinstance(item, KnowledgeWitnessTransition) for item in transitions):
            raise ValueError("invalid_knowledge_witness_transition")
        if transitions and self.kind is not KnowledgeBodyKind.POSITION:
            raise ValueError("knowledge_witness_owner_mismatch")
        keys = tuple((item.table.value, item.evidence_id) for item in transitions)
        if tuple(sorted(keys)) != keys or len(set(keys)) != len(keys):
            raise ValueError("knowledge_witnesses_must_be_unique_and_ordered")
        object.__setattr__(self, "witness_transitions", transitions)


@dataclass(frozen=True, slots=True)
class KnowledgeBodyProposal:
    root: CanonicalRootRef
    intent_request_id: str
    request_id: str
    expected_source_generation: int
    expected_erasure_revision: int
    expected_body_revision: int
    approval_digest: str
    store_digest: str
    schema_digest: str
    targets: tuple[KnowledgeBodyTarget, ...]
    policy_version: str = KNOWLEDGE_BODY_ERASURE_POLICY_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.root, CanonicalRootRef):
            raise ValueError("invalid_knowledge_root")
        for name in ("intent_request_id", "request_id"):
            object.__setattr__(self, name, non_blank(getattr(self, name), name, maximum=128))
        for name in (
            "expected_source_generation",
            "expected_erasure_revision",
            "expected_body_revision",
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError("invalid_body_revision")
        for name in ("approval_digest", "store_digest", "schema_digest"):
            sha256_hex(getattr(self, name), name)
        targets = tuple(self.targets)
        if not 1 <= len(targets) <= MAX_KNOWLEDGE_TARGETS:
            raise ValueError("knowledge_target_capacity_exceeded")
        if any(not isinstance(item, KnowledgeBodyTarget) for item in targets):
            raise ValueError("invalid_knowledge_body_target")
        keys = tuple((item.kind.value, item.aggregate_id) for item in targets)
        if tuple(sorted(keys)) != keys or len(set(keys)) != len(keys):
            raise ValueError("knowledge_targets_must_be_unique_and_ordered")
        witnesses = tuple(witness for target in targets for witness in target.witness_transitions)
        if len(witnesses) > MAX_KNOWLEDGE_WITNESSES:
            raise ValueError("knowledge_witness_capacity_exceeded")
        witness_keys = tuple((item.table, item.evidence_id) for item in witnesses)
        if len(set(witness_keys)) != len(witness_keys):
            raise ValueError("duplicate_knowledge_witness")
        scope = self.root.lineage_id, self.root.identity_id, self.root.counterparty_id
        if any(
            (item.root.lineage_id, item.root.identity_id, item.root.counterparty_id) != scope
            for item in witnesses
        ):
            raise ValueError("knowledge_witness_scope_mismatch")
        if self.policy_version != KNOWLEDGE_BODY_ERASURE_POLICY_VERSION:
            raise ValueError("unsupported_knowledge_policy")
        object.__setattr__(self, "targets", targets)


@dataclass(frozen=True, slots=True)
class KnowledgeBodyAction:
    proposal: KnowledgeBodyProposal
    action_id: str
    floor_id: str
    installation_id: str
    result_body_revision: int
    decided_at: datetime
    trace_id: str
    approved_by: str = "local_operator"

    def __post_init__(self) -> None:
        if not isinstance(self.proposal, KnowledgeBodyProposal):
            raise ValueError("invalid_knowledge_body_proposal")
        for name in ("action_id", "floor_id", "installation_id", "trace_id"):
            object.__setattr__(self, name, non_blank(getattr(self, name), name, maximum=128))
        if (
            type(self.result_body_revision) is not int
            or self.result_body_revision != self.proposal.expected_body_revision + 1
        ):
            raise ValueError("invalid_body_transition")
        if self.approved_by != "local_operator":
            raise ValueError("invalid_body_approval")
        object.__setattr__(self, "decided_at", aware_utc(self.decided_at, "decided_at"))

    def matches(self, proposal: KnowledgeBodyProposal) -> bool:
        return self.proposal == proposal


@dataclass(frozen=True, slots=True)
class ErasedKnowledgeHeader:
    """Unavailable owner content; retained attribution only, never an ordinary claim."""

    kind: KnowledgeBodyKind
    aggregate_id: str
    aggregate_version: int
    action_id: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class KnowledgeBodyPlan:
    root: CanonicalRootRef
    targets: tuple[KnowledgeBodyTarget, ...]
    store_digest: str
    schema_digest: str
    scanned_rows: int
    semantic_claims: int
    user_model_claims: int
    world_model_claims: int
    positions: int
    inclinations: int
    position_quotes: int
    witness_transitions: int
    already_erased: int
    blockers: tuple[str, ...]
