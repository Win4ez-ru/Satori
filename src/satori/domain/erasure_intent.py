"""Terminal retention intent metadata; never permission to alter content."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from satori.domain.source_policy import CanonicalRootRef
from satori.domain.validation import aware_utc, non_blank, sha256_hex

ERASURE_INTENT_POLICY_VERSION = "canonical_erasure_intent_v1"
ERASURE_RETENTION_POLICY_VERSION = "retain_numeric_and_structural_history_v1"


@dataclass(frozen=True, slots=True)
class ErasureIntentProposal:
    root: CanonicalRootRef
    request_id: str
    expected_source_generation: int
    expected_erasure_revision: int
    reviewed_preview_digest: str
    approval_digest: str
    schema_revision: str
    schema_digest: str
    store_digest: str
    routing_digest: str
    assessment_status: Literal["complete", "blocked"]
    retention_policy: str = ERASURE_RETENTION_POLICY_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.root, CanonicalRootRef):
            raise ValueError("invalid_erasure_root")
        for name in ("request_id", "schema_revision"):
            object.__setattr__(self, name, non_blank(getattr(self, name), name, maximum=128))
        for name in ("expected_source_generation", "expected_erasure_revision"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError("invalid_erasure_revision")
        for name in (
            "reviewed_preview_digest",
            "approval_digest",
            "schema_digest",
            "store_digest",
            "routing_digest",
        ):
            sha256_hex(getattr(self, name), name)
        if self.assessment_status not in {"complete", "blocked"}:
            raise ValueError("invalid_erasure_assessment")
        if self.retention_policy != ERASURE_RETENTION_POLICY_VERSION:
            raise ValueError("unsupported_erasure_retention_policy")


@dataclass(frozen=True, slots=True)
class ErasureIntent:
    root: CanonicalRootRef
    request_id: str
    decision_id: str
    floor_id: str
    installation_id: str
    expected_source_generation: int
    expected_erasure_revision: int
    result_erasure_revision: int
    reviewed_preview_digest: str
    approval_digest: str
    schema_revision: str
    schema_digest: str
    store_digest: str
    routing_digest: str
    assessment_status: Literal["complete", "blocked"]
    retention_policy: str
    decided_at: datetime
    trace_id: str
    policy_version: str = ERASURE_INTENT_POLICY_VERSION
    approved_by: str = "local_operator"
    physical_erasure: Literal[False] = False
    execution_authorized: Literal[False] = False

    def __post_init__(self) -> None:
        self.proposal()
        for name in ("decision_id", "floor_id", "installation_id", "trace_id"):
            object.__setattr__(self, name, non_blank(getattr(self, name), name, maximum=128))
        if (
            type(self.result_erasure_revision) is not int
            or self.result_erasure_revision != self.expected_erasure_revision + 1
        ):
            raise ValueError("invalid_erasure_revision_transition")
        if (
            self.policy_version != ERASURE_INTENT_POLICY_VERSION
            or self.approved_by != "local_operator"
        ):
            raise ValueError("invalid_erasure_intent_authority")
        if self.physical_erasure is not False or self.execution_authorized is not False:
            raise ValueError("erasure_intent_never_authorizes_execution")
        object.__setattr__(self, "decided_at", aware_utc(self.decided_at, "decided_at"))

    def proposal(self) -> ErasureIntentProposal:
        return ErasureIntentProposal(
            self.root,
            self.request_id,
            self.expected_source_generation,
            self.expected_erasure_revision,
            self.reviewed_preview_digest,
            self.approval_digest,
            self.schema_revision,
            self.schema_digest,
            self.store_digest,
            self.routing_digest,
            self.assessment_status,
            self.retention_policy,
        )

    def matches(self, proposal: ErasureIntentProposal) -> bool:
        return self.proposal() == proposal
