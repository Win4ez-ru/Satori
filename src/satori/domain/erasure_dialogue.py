"""Private versioned dialogue-body actions in the existing monotonic body stream."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from satori.domain.source_policy import CanonicalRootRef
from satori.domain.validation import aware_utc, non_blank, positive_version, sha256_hex

DIALOGUE_BODY_ERASURE_POLICY_VERSION = "dialogue_body_erasure_v1"
MAX_DIALOGUE_TARGETS = 256
MAX_DIALOGUE_WITNESSES = 512


class HistoricalEvidenceTable(StrEnum):
    """Closed owner evidence routes; this is never an arbitrary SQL selector."""

    MEMORY = "memory_evidence"
    USER_MODEL = "user_model_claim_evidence"
    WORLD_MODEL = "world_model_claim_evidence"
    POSITION = "satori_position_evidence"
    INCLINATION = "satori_inclination_evidence"


@dataclass(frozen=True, slots=True)
class HistoricalEvidenceWitness:
    """Exact previously verified retained row; does not certify available quotation text."""

    table: HistoricalEvidenceTable
    evidence_id: str
    digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.table, HistoricalEvidenceTable):
            raise ValueError("invalid_historical_evidence_table")
        object.__setattr__(
            self, "evidence_id", non_blank(self.evidence_id, "evidence_id", maximum=128)
        )
        sha256_hex(self.digest, "digest")


@dataclass(frozen=True, slots=True)
class DialogueBodyTarget:
    message_id: str
    schema_version: int
    body_digest: str
    terminal_digest: str
    witnesses: tuple[HistoricalEvidenceWitness, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "message_id", non_blank(self.message_id, "message_id", maximum=128)
        )
        positive_version(self.schema_version, "schema_version")
        sha256_hex(self.body_digest, "body_digest")
        sha256_hex(self.terminal_digest, "terminal_digest")
        witnesses = tuple(self.witnesses)
        if len(witnesses) > MAX_DIALOGUE_WITNESSES:
            raise ValueError("dialogue_witness_capacity_exceeded")
        if any(not isinstance(item, HistoricalEvidenceWitness) for item in witnesses):
            raise ValueError("invalid_historical_evidence_witness")
        keys = tuple((item.table.value, item.evidence_id) for item in witnesses)
        if tuple(sorted(keys)) != keys or len(set(keys)) != len(keys):
            raise ValueError("dialogue_witnesses_must_be_unique_and_ordered")
        object.__setattr__(self, "witnesses", witnesses)


@dataclass(frozen=True, slots=True)
class DialogueBodyProposal:
    root: CanonicalRootRef
    intent_request_id: str
    request_id: str
    expected_source_generation: int
    expected_erasure_revision: int
    expected_body_revision: int
    approval_digest: str
    store_digest: str
    schema_digest: str
    targets: tuple[DialogueBodyTarget, ...]
    policy_version: str = DIALOGUE_BODY_ERASURE_POLICY_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.root, CanonicalRootRef):
            raise ValueError("invalid_dialogue_root")
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
        if not 1 <= len(targets) <= MAX_DIALOGUE_TARGETS:
            raise ValueError("dialogue_target_capacity_exceeded")
        if any(not isinstance(target, DialogueBodyTarget) for target in targets):
            raise ValueError("invalid_dialogue_target")
        keys = tuple(target.message_id for target in targets)
        if tuple(sorted(keys)) != keys or len(set(keys)) != len(keys):
            raise ValueError("dialogue_targets_must_be_unique_and_ordered")
        witnesses = tuple(item for target in targets for item in target.witnesses)
        if len(witnesses) > MAX_DIALOGUE_WITNESSES:
            raise ValueError("dialogue_witness_capacity_exceeded")
        if any(
            target.witnesses and target.message_id != self.root.message_id for target in targets
        ):
            raise ValueError("dialogue_witness_root_mismatch")
        if self.policy_version != DIALOGUE_BODY_ERASURE_POLICY_VERSION:
            raise ValueError("unsupported_dialogue_policy")
        object.__setattr__(self, "targets", targets)


@dataclass(frozen=True, slots=True)
class DialogueBodyAction:
    proposal: DialogueBodyProposal
    action_id: str
    floor_id: str
    installation_id: str
    result_body_revision: int
    decided_at: datetime
    trace_id: str
    approved_by: str = "local_operator"

    def __post_init__(self) -> None:
        if not isinstance(self.proposal, DialogueBodyProposal):
            raise ValueError("invalid_dialogue_proposal")
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

    def matches(self, proposal: DialogueBodyProposal) -> bool:
        return self.proposal == proposal


@dataclass(frozen=True, slots=True)
class DialogueBodyPlan:
    """Private bounded owner assessment, not a reusable execution permission."""

    root: CanonicalRootRef
    targets: tuple[DialogueBodyTarget, ...]
    store_digest: str
    schema_digest: str
    scanned_rows: int
    user_bodies: int
    assistant_bodies: int
    already_erased: int
    blockers: tuple[str, ...]
