"""Exact Reflection payload actions in the existing approved body stream."""

from dataclasses import dataclass
from datetime import datetime

from satori.domain.source_policy import CanonicalRootRef
from satori.domain.validation import aware_utc, non_blank, positive_version, sha256_hex

REFLECTION_BODY_ERASURE_POLICY_VERSION = "reflection_body_erasure_v1"
MAX_REFLECTION_TARGETS = 256
MAX_REFLECTION_METADATA_REFERENCES = 1_000_000


@dataclass(frozen=True, slots=True)
class ReflectionBodyTarget:
    """Exact reviewed proposal and its frozen retained parent material."""

    proposal_id: str
    run_id: str
    run_version: int
    payload_version: int
    body_digest: str
    terminal_digest: str
    metadata_reference_count: int

    def __post_init__(self) -> None:
        for name in ("proposal_id", "run_id"):
            object.__setattr__(self, name, non_blank(getattr(self, name), name, maximum=128))
        positive_version(self.run_version, "run_version")
        if type(self.payload_version) is not int or self.payload_version != 1:
            raise ValueError("unsupported_reflection_payload_version")
        for name in ("body_digest", "terminal_digest"):
            sha256_hex(getattr(self, name), name)
        if (
            type(self.metadata_reference_count) is not int
            or not 1 <= self.metadata_reference_count <= MAX_REFLECTION_METADATA_REFERENCES
        ):
            raise ValueError("reflection_metadata_capacity_exceeded")


@dataclass(frozen=True, slots=True)
class ReflectionBodyProposal:
    root: CanonicalRootRef
    intent_request_id: str
    request_id: str
    expected_source_generation: int
    expected_erasure_revision: int
    expected_body_revision: int
    approval_digest: str
    store_digest: str
    schema_digest: str
    targets: tuple[ReflectionBodyTarget, ...]
    policy_version: str = REFLECTION_BODY_ERASURE_POLICY_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.root, CanonicalRootRef):
            raise ValueError("invalid_reflection_root")
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
        if not 1 <= len(targets) <= MAX_REFLECTION_TARGETS:
            raise ValueError("reflection_target_capacity_exceeded")
        if any(not isinstance(item, ReflectionBodyTarget) for item in targets):
            raise ValueError("invalid_reflection_body_target")
        keys = tuple(item.proposal_id for item in targets)
        if tuple(sorted(keys)) != keys or len(set(keys)) != len(keys):
            raise ValueError("reflection_targets_must_be_unique_and_ordered")
        if self.policy_version != REFLECTION_BODY_ERASURE_POLICY_VERSION:
            raise ValueError("unsupported_reflection_policy")
        object.__setattr__(self, "targets", targets)


@dataclass(frozen=True, slots=True)
class ReflectionBodyAction:
    proposal: ReflectionBodyProposal
    action_id: str
    floor_id: str
    installation_id: str
    result_body_revision: int
    decided_at: datetime
    trace_id: str
    approved_by: str = "local_operator"

    def __post_init__(self) -> None:
        if not isinstance(self.proposal, ReflectionBodyProposal):
            raise ValueError("invalid_reflection_body_proposal")
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

    def matches(self, proposal: ReflectionBodyProposal) -> bool:
        return self.proposal == proposal


@dataclass(frozen=True, slots=True)
class ReflectionProposalHeader:
    """Terminal metadata; it grants no available payload or learning authority."""

    proposal_id: str
    run_id: str
    ordinal: int
    target_owner: str
    evidence_source_ids: tuple[str, ...]
    created_at: datetime
    body_action_id: str


@dataclass(frozen=True, slots=True)
class ReflectionBodyPlan:
    root: CanonicalRootRef
    targets: tuple[ReflectionBodyTarget, ...]
    store_digest: str
    schema_digest: str
    scanned_rows: int
    reflection_proposals: int
    parent_runs: int
    source_rows: int
    attempt_rows: int
    outcome_rows: int
    metadata_references: int
    already_erased: int
    blockers: tuple[str, ...]
