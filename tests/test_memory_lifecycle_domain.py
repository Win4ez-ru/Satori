"""Local archival policy binds approval, scope and a monotonic episode snapshot."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

import pytest

from satori.domain.memory import (
    EpisodicMemory,
    EpisodicMemoryEvidence,
    MemoryLifecycleStatus,
    MemoryManager,
    MemoryProvenanceKind,
)
from satori.domain.memory_lifecycle import (
    MemoryLifecycleProposal,
    memory_lifecycle_effects,
    memory_lifecycle_preview_digest,
)
from satori.domain.memory_scope import MemoryScope

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
SCOPE = MemoryScope("satori", "alice")


def _memory() -> EpisodicMemory:
    return EpisodicMemory(
        memory_id="episode",
        schema_version=1,
        source_interaction_id="interaction",
        occurred_at=NOW,
        summary="Private synthetic episode detail",
        importance=0.8,
        confidence=0.9,
        created_at=NOW,
        formation_method="synthetic",
        formation_version=1,
        lifecycle_status=MemoryLifecycleStatus.ACTIVE,
        evidence=(
            EpisodicMemoryEvidence(
                evidence_id="evidence",
                memory_id="episode",
                source_message_id="user-message",
                provenance_kind=MemoryProvenanceKind.EXPLICIT_USER_STATEMENT,
                quote="Private synthetic exact quote",
                observed_at=NOW,
            ),
        ),
    )


def _proposal(
    memory: EpisodicMemory, target: MemoryLifecycleStatus, *, request_id: str = "request"
) -> MemoryLifecycleProposal:
    return MemoryLifecycleProposal(
        memory_id=memory.memory_id,
        scope=SCOPE,
        target_status=target,
        expected_version=memory.aggregate_version,
        request_id=request_id,
        preview_digest=memory_lifecycle_preview_digest(memory, SCOPE, target),
    )


def test_archive_reactivate_and_noop_preserve_content_and_version_semantics() -> None:
    manager = MemoryManager()
    memory = _memory()
    proposal = _proposal(memory, MemoryLifecycleStatus.ARCHIVED)
    archived = manager.evaluate_lifecycle(
        proposal, memory, scope=SCOPE, decision_id="archive", decided_at=NOW, trace_id="trace"
    )
    assert archived.changed
    assert archived.result_version == 2
    assert archived.reason_code == "episode_archived"
    assert archived.matches(proposal)
    assert memory.lifecycle_status is MemoryLifecycleStatus.ACTIVE
    archived_memory = replace(
        memory, lifecycle_status=archived.to_status, aggregate_version=archived.result_version
    )
    noop = manager.evaluate_lifecycle(
        _proposal(archived_memory, MemoryLifecycleStatus.ARCHIVED, request_id="noop"),
        archived_memory,
        scope=SCOPE,
        decision_id="noop",
        decided_at=NOW,
        trace_id="trace",
    )
    assert not noop.changed
    assert noop.result_version == 2
    assert noop.reason_code == "episode_lifecycle_noop"
    reactivated = manager.evaluate_lifecycle(
        _proposal(archived_memory, MemoryLifecycleStatus.ACTIVE, request_id="reactivate"),
        archived_memory,
        scope=SCOPE,
        decision_id="reactivate",
        decided_at=NOW,
        trace_id="trace",
    )
    assert reactivated.changed
    assert reactivated.result_version == 3
    assert reactivated.reason_code == "episode_reactivated"
    assert (
        replace(archived_memory, lifecycle_status=memory.lifecycle_status, aggregate_version=1)
        == memory
    )
    # Replay matches the historical approval after an inverse transition, without reapplying it.
    assert archived.matches(proposal)
    assert not archived.matches(replace(proposal, request_id="different"))
    assert not archived.matches(replace(proposal, target_status=MemoryLifecycleStatus.ACTIVE))
    assert not archived.matches(replace(proposal, preview_digest="0" * 64))


def test_preview_is_metadata_only_but_binds_all_controlled_dimensions() -> None:
    memory = _memory()
    target = MemoryLifecycleStatus.ARCHIVED
    digest = memory_lifecycle_preview_digest(memory, SCOPE, target)
    assert len(digest) == 64
    assert digest == memory_lifecycle_preview_digest(
        replace(memory, summary="Different private content", confidence=0.7), SCOPE, target
    )
    assert digest != memory_lifecycle_preview_digest(memory, MemoryScope("satori", "bob"), target)
    assert digest != memory_lifecycle_preview_digest(memory, MemoryScope("other", "alice"), target)
    assert digest != memory_lifecycle_preview_digest(
        replace(memory, aggregate_version=2), SCOPE, target
    )
    assert digest != memory_lifecycle_preview_digest(memory, SCOPE, MemoryLifecycleStatus.ACTIVE)
    assert digest != memory_lifecycle_preview_digest(
        replace(
            memory, memory_id="other", evidence=(replace(memory.evidence[0], memory_id="other"),)
        ),
        SCOPE,
        target,
    )
    effects = memory_lifecycle_effects(target)
    assert not effects["recall"]
    assert not effects["new_semantic"]
    assert effects["episode_payload_evidence"]
    assert effects["formed_semantic"]
    assert not effects["source_excluded"]
    assert not effects["physically_erased"]
    assert memory_lifecycle_effects(MemoryLifecycleStatus.ACTIVE)["recall"]


def test_owner_rejects_scope_version_preview_and_operator_mismatch() -> None:
    memory = _memory()
    proposal = _proposal(memory, MemoryLifecycleStatus.ARCHIVED)
    manager = MemoryManager()
    with pytest.raises(ValueError, match="memory_lifecycle_target_mismatch"):
        manager.evaluate_lifecycle(
            proposal,
            memory,
            scope=MemoryScope("satori", "bob"),
            decision_id="d",
            decided_at=NOW,
            trace_id="t",
        )
    with pytest.raises(ValueError, match="memory_lifecycle_version_conflict"):
        manager.evaluate_lifecycle(
            proposal,
            replace(memory, aggregate_version=3),
            scope=SCOPE,
            decision_id="d",
            decided_at=NOW,
            trace_id="t",
        )
    with pytest.raises(ValueError, match="memory_lifecycle_preview_mismatch"):
        manager.evaluate_lifecycle(
            replace(proposal, preview_digest="0" * 64),
            memory,
            scope=SCOPE,
            decision_id="d",
            decided_at=NOW,
            trace_id="t",
        )
    with pytest.raises(ValueError, match="memory_lifecycle_approval_invalid"):
        manager.evaluate_lifecycle(
            proposal,
            memory,
            scope=SCOPE,
            decision_id="d",
            decided_at=NOW,
            trace_id="t",
            approved_by="provider",
        )


@pytest.mark.parametrize("version", [0, -1, True, 1.5])
def test_episode_and_proposal_reject_invalid_version(version: object) -> None:
    with pytest.raises(ValueError, match="aggregate_version must be positive"):
        replace(_memory(), aggregate_version=cast(int, version))
    with pytest.raises(ValueError, match="expected_version must be positive"):
        replace(
            _proposal(_memory(), MemoryLifecycleStatus.ARCHIVED),
            expected_version=cast(int, version),
        )


def test_receipt_rejects_corrupt_transition_and_untyped_status() -> None:
    memory = _memory()
    proposal = _proposal(memory, MemoryLifecycleStatus.ARCHIVED)
    decision = MemoryManager().evaluate_lifecycle(
        proposal, memory, scope=SCOPE, decision_id="d", decided_at=NOW, trace_id="t"
    )
    with pytest.raises(ValueError, match="memory_lifecycle_result_version_invalid"):
        replace(decision, result_version=1)
    with pytest.raises(ValueError, match="memory_lifecycle_reason_invalid"):
        replace(decision, reason_code="episode_reactivated")
    with pytest.raises(ValueError, match="memory_lifecycle_policy_invalid"):
        replace(decision, policy_version=2)
    with pytest.raises(ValueError, match="memory_lifecycle_status_invalid"):
        replace(proposal, target_status=cast(MemoryLifecycleStatus, "archived"))
    with pytest.raises(ValueError, match="preview_digest must be a lowercase SHA-256 digest"):
        replace(proposal, preview_digest="A" * 64)
