"""Deterministic, metadata-only episode archival policy and immutable receipts."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from satori.domain.memory import EpisodicMemory, MemoryLifecycleStatus
from satori.domain.memory_scope import MemoryScope
from satori.domain.validation import aware_utc, non_blank, positive_version, sha256_hex

MEMORY_LIFECYCLE_POLICY_VERSION = 1
MEMORY_LIFECYCLE_OPERATOR = "local_operator"


def _require_scope(scope: MemoryScope) -> None:
    if not isinstance(scope, MemoryScope):
        raise ValueError("memory_scope_required")


def _require_status(status: MemoryLifecycleStatus) -> None:
    if not isinstance(status, MemoryLifecycleStatus):
        raise ValueError("memory_lifecycle_status_invalid")


@dataclass(frozen=True, slots=True)
class MemoryLifecycleProposal:
    """Explicit local command tied to one proven episode version and preview."""

    memory_id: str
    scope: MemoryScope
    target_status: MemoryLifecycleStatus
    expected_version: int
    request_id: str
    preview_digest: str

    def __post_init__(self) -> None:
        _require_scope(self.scope)
        _require_status(self.target_status)
        positive_version(self.expected_version, "expected_version")
        for name in ("memory_id", "request_id"):
            object.__setattr__(self, name, non_blank(getattr(self, name), name, maximum=128))
        object.__setattr__(
            self, "preview_digest", sha256_hex(self.preview_digest, "preview_digest")
        )


@dataclass(frozen=True, slots=True)
class MemoryLifecycleDecision:
    """Append-only approval, including an accepted repeated-target no-op."""

    decision_id: str
    request_id: str
    scope: MemoryScope
    memory_id: str
    from_status: MemoryLifecycleStatus
    to_status: MemoryLifecycleStatus
    expected_version: int
    result_version: int
    policy_version: int
    reason_code: str
    decided_at: datetime
    trace_id: str
    approved_by: str
    preview_digest: str

    def __post_init__(self) -> None:
        _require_scope(self.scope)
        _require_status(self.from_status)
        _require_status(self.to_status)
        for name in ("expected_version", "result_version", "policy_version"):
            positive_version(getattr(self, name), name)
        for name in ("decision_id", "request_id", "memory_id", "trace_id"):
            object.__setattr__(self, name, non_blank(getattr(self, name), name, maximum=128))
        if self.policy_version != MEMORY_LIFECYCLE_POLICY_VERSION:
            raise ValueError("memory_lifecycle_policy_invalid")
        if self.approved_by != MEMORY_LIFECYCLE_OPERATOR:
            raise ValueError("memory_lifecycle_approval_invalid")
        if self.result_version != self.expected_version + int(self.changed):
            raise ValueError("memory_lifecycle_result_version_invalid")
        if self.reason_code != _reason(self.from_status, self.to_status):
            raise ValueError("memory_lifecycle_reason_invalid")
        object.__setattr__(self, "decided_at", aware_utc(self.decided_at, "decided_at"))
        object.__setattr__(
            self, "preview_digest", sha256_hex(self.preview_digest, "preview_digest")
        )

    @property
    def changed(self) -> bool:
        """A recorded no-op preserves the aggregate version."""

        return self.from_status is not self.to_status

    def matches(self, proposal: MemoryLifecycleProposal) -> bool:
        """Compare immutable request payload without reevaluating current episode state."""

        return (
            self.request_id == proposal.request_id
            and self.memory_id == proposal.memory_id
            and self.scope == proposal.scope
            and self.to_status is proposal.target_status
            and self.expected_version == proposal.expected_version
            and self.preview_digest == proposal.preview_digest
        )


def _reason(before: MemoryLifecycleStatus, after: MemoryLifecycleStatus) -> str:
    if before is after:
        return "episode_lifecycle_noop"
    return "episode_archived" if after is MemoryLifecycleStatus.ARCHIVED else "episode_reactivated"


def memory_lifecycle_effects(target_status: MemoryLifecycleStatus) -> dict[str, bool]:
    """Describe exact recall controls and retained state for a metadata-only preview."""

    _require_status(target_status)
    active = target_status is MemoryLifecycleStatus.ACTIVE
    return {
        "recall": active,
        "index": active,
        "new_semantic": active,
        "new_episode_reflection": active,
        "canonical_dialogue": True,
        "episode_payload_evidence": True,
        "compatible_vectors": True,
        "formed_semantic": True,
        "global_self_relationship": True,
        "source_excluded": False,
        "physically_erased": False,
    }


def memory_lifecycle_preview_digest(
    memory: EpisodicMemory, scope: MemoryScope, target_status: MemoryLifecycleStatus
) -> str:
    """Bind a preview to status/version and fixed effects, never memory content."""

    _require_scope(scope)
    _require_status(target_status)
    material = {
        "policy_version": MEMORY_LIFECYCLE_POLICY_VERSION,
        "memory_id": memory.memory_id,
        "identity_id": scope.identity_id,
        "counterparty_id": scope.counterparty_id,
        "from_status": memory.lifecycle_status.value,
        "target_status": target_status.value,
        "expected_version": memory.aggregate_version,
        "effects_version": 1,
        "effects": memory_lifecycle_effects(target_status),
    }
    payload = json.dumps(material, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def evaluate_memory_lifecycle(
    proposal: MemoryLifecycleProposal,
    memory: EpisodicMemory,
    *,
    scope: MemoryScope,
    decision_id: str,
    decided_at: datetime,
    trace_id: str,
    approved_by: str = MEMORY_LIFECYCLE_OPERATOR,
) -> MemoryLifecycleDecision:
    """Validate an already admitted scope and explicitly approve one local transition."""

    _require_scope(scope)
    if proposal.scope != scope or proposal.memory_id != memory.memory_id:
        raise ValueError("memory_lifecycle_target_mismatch")
    if proposal.expected_version != memory.aggregate_version:
        raise ValueError("memory_lifecycle_version_conflict")
    if proposal.preview_digest != memory_lifecycle_preview_digest(
        memory, scope, proposal.target_status
    ):
        raise ValueError("memory_lifecycle_preview_mismatch")
    return MemoryLifecycleDecision(
        decision_id=decision_id,
        request_id=proposal.request_id,
        scope=scope,
        memory_id=memory.memory_id,
        from_status=memory.lifecycle_status,
        to_status=proposal.target_status,
        expected_version=memory.aggregate_version,
        result_version=memory.aggregate_version
        + int(memory.lifecycle_status is not proposal.target_status),
        policy_version=MEMORY_LIFECYCLE_POLICY_VERSION,
        reason_code=_reason(memory.lifecycle_status, proposal.target_status),
        decided_at=decided_at,
        trace_id=trace_id,
        approved_by=approved_by,
        preview_digest=proposal.preview_digest,
    )
