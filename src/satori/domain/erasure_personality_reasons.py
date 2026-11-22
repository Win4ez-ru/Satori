"""Identity-owned operator reasons in the existing mixed body-action stream."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from satori.domain.validation import aware_utc, non_blank, sha256_hex

CHECKPOINT_REASON_ERASURE_POLICY_VERSION = "checkpoint_reason_erasure_v1"
MAX_CHECKPOINT_REASON_TARGETS = 256


class CheckpointReasonKind(StrEnum):
    CHECKPOINT_APPROVAL = "checkpoint_approval"
    PERSONALITY_RESTORE = "personality_restore"


@dataclass(frozen=True, slots=True)
class CheckpointReasonScope:
    identity_id: str
    lineage_id: str

    def __post_init__(self) -> None:
        for name in ("identity_id", "lineage_id"):
            object.__setattr__(self, name, non_blank(getattr(self, name), name, maximum=128))


@dataclass(frozen=True, slots=True)
class CheckpointReasonSelector:
    kind: CheckpointReasonKind
    record_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, CheckpointReasonKind):
            raise ValueError("invalid_checkpoint_reason_kind")
        object.__setattr__(self, "record_id", non_blank(self.record_id, "record_id", maximum=128))

    @property
    def key(self) -> tuple[str, str]:
        return self.kind.value, self.record_id


@dataclass(frozen=True, slots=True)
class CheckpointReasonTarget:
    selector: CheckpointReasonSelector
    payload_version: int
    body_digest: str
    terminal_digest: str
    metadata_reference_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.selector, CheckpointReasonSelector):
            raise ValueError("invalid_checkpoint_reason_selector")
        if type(self.payload_version) is not int or self.payload_version != 1:
            raise ValueError("unsupported_checkpoint_reason_payload_version")
        for name in ("body_digest", "terminal_digest"):
            sha256_hex(getattr(self, name), name)
        if (
            type(self.metadata_reference_count) is not int
            or not 1 <= self.metadata_reference_count <= 100_000
        ):
            raise ValueError("invalid_checkpoint_reason_reference_count")


@dataclass(frozen=True, slots=True)
class CheckpointReasonProposal:
    scope: CheckpointReasonScope
    request_id: str
    expected_source_generation: int
    expected_erasure_revision: int
    expected_body_revision: int
    approval_digest: str
    store_digest: str
    schema_digest: str
    targets: tuple[CheckpointReasonTarget, ...]
    policy_version: str = CHECKPOINT_REASON_ERASURE_POLICY_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.scope, CheckpointReasonScope):
            raise ValueError("invalid_checkpoint_reason_scope")
        object.__setattr__(
            self, "request_id", non_blank(self.request_id, "request_id", maximum=128)
        )
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
        if not 1 <= len(targets) <= MAX_CHECKPOINT_REASON_TARGETS:
            raise ValueError("checkpoint_reason_target_capacity_exceeded")
        if any(not isinstance(item, CheckpointReasonTarget) for item in targets):
            raise ValueError("invalid_checkpoint_reason_target")
        keys = tuple(item.selector.key for item in targets)
        if tuple(sorted(keys)) != keys or len(set(keys)) != len(keys):
            raise ValueError("checkpoint_reason_targets_must_be_unique_and_ordered")
        if self.policy_version != CHECKPOINT_REASON_ERASURE_POLICY_VERSION:
            raise ValueError("unsupported_checkpoint_reason_policy")
        object.__setattr__(self, "targets", targets)


@dataclass(frozen=True, slots=True)
class CheckpointReasonAction:
    proposal: CheckpointReasonProposal
    action_id: str
    floor_id: str
    installation_id: str
    result_body_revision: int
    decided_at: datetime
    trace_id: str
    approved_by: str = "local_operator"

    def __post_init__(self) -> None:
        if not isinstance(self.proposal, CheckpointReasonProposal):
            raise ValueError("invalid_checkpoint_reason_proposal")
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

    def matches(self, proposal: CheckpointReasonProposal) -> bool:
        return self.proposal == proposal


@dataclass(frozen=True, slots=True)
class CheckpointReasonPlan:
    scope: CheckpointReasonScope
    targets: tuple[CheckpointReasonTarget, ...]
    store_digest: str
    schema_digest: str
    scanned_rows: int
    approval_reasons: int
    restore_reasons: int
    metadata_references: int
    already_erased: int
    blockers: tuple[str, ...]
