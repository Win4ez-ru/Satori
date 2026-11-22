"""Strict, immutable and content-free boundaries for explicit episode controls."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from satori.domain.memory import MemoryLifecycleStatus
from satori.domain.memory_lifecycle import MemoryLifecycleDecision
from satori.domain.memory_scope import MemoryScope


class MemoryLifecycleError(ValueError):
    """A content-free rejection code suitable for an operator surface."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class _LifecycleBoundary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class MemoryLifecycleEffects(_LifecycleBoundary):
    """Archival controls new admissions while retaining already accepted evidence."""

    recall: bool
    index: bool
    new_semantic: bool
    new_episode_reflection: bool
    canonical_dialogue: bool
    episode_payload_evidence: bool
    compatible_vectors: bool
    formed_semantic: bool
    global_self_relationship: bool
    source_excluded: bool
    physically_erased: bool


class MemoryLifecycleCommand(_LifecycleBoundary):
    """An explicit request bound to one reviewed aggregate version and digest."""

    memory_id: str = Field(min_length=1, max_length=128)
    target_status: MemoryLifecycleStatus
    expected_version: int = Field(gt=0)
    request_id: str = Field(min_length=1, max_length=128)
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("memory_id", "request_id")
    @classmethod
    def identifiers_are_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("identifier_required")
        return value


class MemoryLifecyclePreview(_LifecycleBoundary):
    """Reviewable effects and metadata; no summary, quote or canonical message."""

    schema_version: Literal[1] = 1
    policy_version: Literal[1] = 1
    effects_version: Literal[1] = 1
    scope: MemoryScope
    memory_id: str
    current_status: MemoryLifecycleStatus
    current_version: int = Field(gt=0)
    target_status: MemoryLifecycleStatus
    effects: MemoryLifecycleEffects
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class MemoryLifecycleReceipt(_LifecycleBoundary):
    """The original immutable owner decision, including recorded no-op approval."""

    decision_id: str
    request_id: str
    scope: MemoryScope
    memory_id: str
    from_status: MemoryLifecycleStatus
    to_status: MemoryLifecycleStatus
    expected_version: int = Field(gt=0)
    result_version: int = Field(gt=0)
    policy_version: Literal[1]
    reason_code: Literal["episode_lifecycle_noop", "episode_archived", "episode_reactivated"]
    decided_at: datetime
    trace_id: str
    approved_by: Literal["local_operator"]
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    changed: bool

    @classmethod
    def from_decision(cls, decision: MemoryLifecycleDecision) -> "MemoryLifecycleReceipt":
        return cls.model_validate(
            {
                "decision_id": decision.decision_id,
                "request_id": decision.request_id,
                "scope": decision.scope,
                "memory_id": decision.memory_id,
                "from_status": decision.from_status,
                "to_status": decision.to_status,
                "expected_version": decision.expected_version,
                "result_version": decision.result_version,
                "policy_version": decision.policy_version,
                "reason_code": decision.reason_code,
                "decided_at": decision.decided_at,
                "trace_id": decision.trace_id,
                "approved_by": decision.approved_by,
                "preview_digest": decision.preview_digest,
                "changed": decision.changed,
            }
        )


class MemoryLifecycleResult(_LifecycleBoundary):
    """A historical receipt and the independently reported current episode state."""

    receipt: MemoryLifecycleReceipt
    replayed: bool
    current_status: MemoryLifecycleStatus
    current_version: int = Field(gt=0)
