"""Exact appraisal explanation actions in the existing approved body stream."""

from dataclasses import dataclass
from datetime import datetime

from satori.domain.source_policy import CanonicalRootRef
from satori.domain.validation import aware_utc, non_blank, sha256_hex

APPRAISAL_BODY_ERASURE_POLICY_VERSION = "appraisal_explanation_erasure_v1"
MAX_APPRAISAL_TARGETS = 256
APPRAISAL_METADATA_REFERENCES = 3


@dataclass(frozen=True, slots=True)
class AppraisalBodyTarget:
    """Exact paired explanations and their immutable actual-input receipt."""

    transition_id: str
    source_input_id: str
    audit_event_id: str
    payload_version: int
    body_digest: str
    terminal_digest: str
    metadata_reference_count: int
    input_closure_digest: str

    def __post_init__(self) -> None:
        for name in ("transition_id", "source_input_id", "audit_event_id"):
            object.__setattr__(self, name, non_blank(getattr(self, name), name, maximum=128))
        if type(self.payload_version) is not int or self.payload_version != 1:
            raise ValueError("unsupported_appraisal_payload_version")
        for name in ("body_digest", "terminal_digest", "input_closure_digest"):
            sha256_hex(getattr(self, name), name)
        if (
            type(self.metadata_reference_count) is not int
            or self.metadata_reference_count != APPRAISAL_METADATA_REFERENCES
        ):
            raise ValueError("invalid_appraisal_metadata_reference_count")


@dataclass(frozen=True, slots=True)
class AppraisalBodyProposal:
    root: CanonicalRootRef
    intent_request_id: str
    request_id: str
    expected_source_generation: int
    expected_erasure_revision: int
    expected_body_revision: int
    approval_digest: str
    store_digest: str
    schema_digest: str
    targets: tuple[AppraisalBodyTarget, ...]
    policy_version: str = APPRAISAL_BODY_ERASURE_POLICY_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.root, CanonicalRootRef):
            raise ValueError("invalid_appraisal_root")
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
        if not 1 <= len(targets) <= MAX_APPRAISAL_TARGETS:
            raise ValueError("appraisal_target_capacity_exceeded")
        if any(not isinstance(item, AppraisalBodyTarget) for item in targets):
            raise ValueError("invalid_appraisal_body_target")
        keys = tuple(item.transition_id for item in targets)
        if tuple(sorted(keys)) != keys or len(set(keys)) != len(keys):
            raise ValueError("appraisal_targets_must_be_unique_and_ordered")
        if self.policy_version != APPRAISAL_BODY_ERASURE_POLICY_VERSION:
            raise ValueError("unsupported_appraisal_policy")
        object.__setattr__(self, "targets", targets)


@dataclass(frozen=True, slots=True)
class AppraisalBodyAction:
    proposal: AppraisalBodyProposal
    action_id: str
    floor_id: str
    installation_id: str
    result_body_revision: int
    decided_at: datetime
    trace_id: str
    approved_by: str = "local_operator"

    def __post_init__(self) -> None:
        if not isinstance(self.proposal, AppraisalBodyProposal):
            raise ValueError("invalid_appraisal_body_proposal")
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

    def matches(self, proposal: AppraisalBodyProposal) -> bool:
        return self.proposal == proposal


@dataclass(frozen=True, slots=True)
class AppraisalBodyPlan:
    root: CanonicalRootRef
    targets: tuple[AppraisalBodyTarget, ...]
    store_digest: str
    schema_digest: str
    scanned_rows: int
    appraisal_transitions: int
    audit_duplicates: int
    input_receipts: int
    metadata_references: int
    already_erased: int
    blockers: tuple[str, ...]
