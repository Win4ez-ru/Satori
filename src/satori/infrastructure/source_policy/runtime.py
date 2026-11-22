"""Bound installation admission, with mode-aware short reentrant OS leases."""

import os
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

from satori.domain.erasure_appraisal import AppraisalBodyAction, AppraisalBodyProposal
from satori.domain.erasure_bodies import (
    BodyAction,
    BodyErasureAction,
    BodyErasureProposal,
    action_identity,
)
from satori.domain.erasure_dialogue import DialogueBodyAction, DialogueBodyProposal
from satori.domain.erasure_intent import ErasureIntent, ErasureIntentProposal
from satori.domain.erasure_knowledge import KnowledgeBodyAction, KnowledgeBodyProposal
from satori.domain.erasure_personality_reasons import (
    CheckpointReasonAction,
    CheckpointReasonProposal,
)
from satori.domain.erasure_reflection import ReflectionBodyAction, ReflectionBodyProposal
from satori.domain.source_policy import (
    CanonicalRootRef,
    ExclusionPhase,
    FootprintStatus,
    ProtectedContentFootprint,
    SourceEligibilitySnapshot,
    SourceExclusionReceipt,
    SourcePolicyError,
)
from satori.domain.validation import non_blank
from satori.infrastructure.source_policy.barrier import PolicyBarrier
from satori.infrastructure.source_policy.ledger import InstallationPolicyLedger
from satori.infrastructure.source_policy.migrations import (
    APPRAISAL_POLICY_SCHEMA_REVISION,
    BODY_POLICY_SCHEMA_REVISION,
    CHECKPOINT_REASON_POLICY_SCHEMA_REVISION,
    DIALOGUE_POLICY_SCHEMA_REVISION,
    KNOWLEDGE_POLICY_SCHEMA_REVISION,
    REFLECTION_POLICY_SCHEMA_REVISION,
)


class SourcePolicyRuntime:
    """Acquire this barrier before constructing any main/floor DB transaction."""

    def __init__(
        self,
        directory: Path,
        identity_id: str,
        *,
        verify_mirror: Callable[[SourceEligibilitySnapshot], None] | None = None,
        timeout_seconds: float = 2.0,
    ) -> None:
        self.ledger = InstallationPolicyLedger(directory)
        self.identity_id = non_blank(identity_id, "identity_id", maximum=128)
        self.verify_mirror = verify_mirror
        self.timeout_seconds = timeout_seconds
        self._local = threading.local()

    @property
    def reserved_paths(self) -> tuple[Path, ...]:
        return self.ledger.reserved_paths

    def _require_lease(self, *, exclusive: bool = False) -> None:
        mode = getattr(self._local, "mode", None)
        if mode is None:
            raise SourcePolicyError("source_policy_lease_required")
        if exclusive and mode != "exclusive":
            raise SourcePolicyError("source_policy_exclusive_lease_required")

    @contextmanager
    def _lease(self, *, exclusive: bool) -> Iterator[SourceEligibilitySnapshot]:
        current = getattr(self._local, "mode", None)
        if current is not None:
            if exclusive and current != "exclusive":
                raise SourcePolicyError("source_policy_lock_upgrade_forbidden")
            yield self.snapshot(verify_mirror=not exclusive)
            return
        binding = self.ledger.validate_binding()
        barrier = PolicyBarrier(
            self.ledger.lock_path,
            expected_inode=binding.lock_inode,
            timeout_seconds=self.timeout_seconds,
        )
        with barrier.lease(exclusive=exclusive):
            self._local.mode = "exclusive" if exclusive else "shared"
            try:
                yield self.snapshot(verify_mirror=not exclusive)
            finally:
                self._local.mode = None

    def shared(self) -> AbstractContextManager[SourceEligibilitySnapshot]:
        return self._lease(exclusive=False)

    def exclusive(self) -> AbstractContextManager[SourceEligibilitySnapshot]:
        return self._lease(exclusive=True)

    def upgrade_policy(self) -> SourceEligibilitySnapshot:
        """Explicit migration uses the existing fixed barrier without implicit admission."""
        return self._upgrade_policy()

    def upgrade_body_policy(self) -> SourceEligibilitySnapshot:
        """Upgrade the durable body-action floor only after explicit reviewed control."""
        return self._upgrade_policy(revision=BODY_POLICY_SCHEMA_REVISION)

    def upgrade_dialogue_policy(self) -> SourceEligibilitySnapshot:
        """Explicitly admit the new dialogue envelope without rewriting episode receipts."""
        return self._upgrade_policy(revision=DIALOGUE_POLICY_SCHEMA_REVISION)

    def upgrade_knowledge_policy(self) -> SourceEligibilitySnapshot:
        """Explicit closed knowledge envelope; existing body approvals remain unchanged."""
        return self._upgrade_policy(revision=KNOWLEDGE_POLICY_SCHEMA_REVISION)

    def upgrade_reflection_policy(self) -> SourceEligibilitySnapshot:
        """Admit Reflection payload actions without changing earlier approvals."""
        return self._upgrade_policy(revision=REFLECTION_POLICY_SCHEMA_REVISION)

    def upgrade_appraisal_policy(self) -> SourceEligibilitySnapshot:
        """Admit paired appraisal explanations without changing earlier approvals."""
        return self._upgrade_policy(revision=APPRAISAL_POLICY_SCHEMA_REVISION)

    def upgrade_checkpoint_reason_policy(self) -> SourceEligibilitySnapshot:
        """Admit identity-owned reasons without manufacturing source attribution."""
        return self._upgrade_policy(revision=CHECKPOINT_REASON_POLICY_SCHEMA_REVISION)

    def _upgrade_policy(self, *, revision: str | None = None) -> SourceEligibilitySnapshot:
        if getattr(self._local, "mode", None) is not None:
            raise SourcePolicyError("policy_migration_requires_outer_lease")
        binding = self.ledger.validate_binding()
        barrier = PolicyBarrier(
            self.ledger.lock_path,
            expected_inode=binding.lock_inode,
            timeout_seconds=self.timeout_seconds,
        )
        with barrier.lease(exclusive=True):
            self._local.mode = "exclusive"
            try:
                self.snapshot(verify_mirror=False)
                if revision is None:
                    self.ledger.upgrade()
                else:
                    self.ledger.upgrade(revision=revision)
                return self.snapshot(verify_mirror=False)
            finally:
                self._local.mode = None

    def enroll(
        self,
        identity_id: str | None = None,
        *,
        reviewed: bool,
        lineage_id: str | None = None,
        new_installation: bool = False,
    ) -> SourceEligibilitySnapshot:
        if reviewed is not True:
            raise SourcePolicyError("policy_enrollment_review_required")
        selected = identity_id or self.identity_id
        if selected != self.identity_id:
            raise SourcePolicyError("policy_identity_binding_mismatch")
        if lineage_id is not None:
            lineage_id = non_blank(lineage_id, "lineage_id", maximum=128)
        if getattr(self._local, "mode", None) is not None:
            raise SourcePolicyError("policy_enrollment_requires_outer_lease")
        if self.ledger.binding_exists():
            binding = self.ledger.validate_binding()
            barrier = PolicyBarrier(
                self.ledger.lock_path,
                expected_inode=binding.lock_inode,
                timeout_seconds=self.timeout_seconds,
            )
        else:
            if not new_installation:
                raise SourcePolicyError("policy_installation_not_enrolled")
            if any(os.path.lexists(path) for path in self.ledger.reserved_paths):
                raise SourcePolicyError("policy_recovery_required")
            inode = PolicyBarrier.create(self.ledger.lock_path)
            barrier = PolicyBarrier(
                self.ledger.lock_path, expected_inode=inode, timeout_seconds=self.timeout_seconds
            )
        with barrier.lease(exclusive=True):
            self._local.mode = "exclusive"
            try:
                if not self.ledger.binding_exists():
                    if barrier.expected_inode is None:
                        raise SourcePolicyError("policy_lock_binding_invalid")
                    self.ledger.initialize(barrier.expected_inode)
                return self.ledger.enroll(selected, lineage_id=lineage_id)
            finally:
                self._local.mode = None

    def snapshot(
        self, identity_id: str | None = None, *, verify_mirror: bool = True
    ) -> SourceEligibilitySnapshot:
        self._require_lease()
        if identity_id is not None and identity_id != self.identity_id:
            raise SourcePolicyError("policy_identity_binding_mismatch")
        if not verify_mirror:
            self._require_lease(exclusive=True)
        snapshot = self.ledger.snapshot(self.identity_id)
        if verify_mirror and self.verify_mirror is not None:
            try:
                self.verify_mirror(snapshot)
            except SourcePolicyError:
                raise
            except Exception:
                raise SourcePolicyError("source_mirror_unavailable") from None
        return snapshot

    def assert_binding(self, identity_id: str, floor_id: str, lineage_id: str) -> None:
        snapshot = self.snapshot(verify_mirror=getattr(self._local, "mode", None) != "exclusive")
        if (identity_id, floor_id, lineage_id) != (
            snapshot.identity_id,
            snapshot.floor_id,
            snapshot.lineage_id,
        ):
            raise SourcePolicyError("policy_lineage_binding_mismatch")

    def check_root(self, root: CanonicalRootRef, purpose: str = "read") -> None:
        snapshot = self.snapshot()
        self.assert_binding(root.identity_id, snapshot.floor_id, root.lineage_id)
        self._check_purpose(purpose)
        if root.message_id in snapshot.excluded_message_ids:
            raise SourcePolicyError("canonical_source_excluded")

    @staticmethod
    def _check_purpose(purpose: str) -> None:
        if purpose not in {"read", "dispatch", "commit", "delivery", "replay", "evidence"}:
            raise SourcePolicyError("source_purpose_not_supported")

    def check_footprint(self, footprint: ProtectedContentFootprint, purpose: str = "read") -> None:
        snapshot = self.snapshot()
        self._check_purpose(purpose)
        if set(footprint.root_message_ids) & snapshot.excluded_message_ids:
            raise SourcePolicyError("canonical_source_excluded")
        if footprint.status is FootprintStatus.OVERFLOW:
            raise SourcePolicyError("source_footprint_overflow")
        if footprint.status is FootprintStatus.UNKNOWN and (
            not footprint.legacy or snapshot.excluded_message_ids
        ):
            raise SourcePolicyError("source_dependency_unknown")

    def receipt(self, request_id: str) -> SourceExclusionReceipt | None:
        self._require_lease()
        receipt = self.ledger.receipt(request_id)
        if receipt is not None and receipt.root.identity_id != self.identity_id:
            raise SourcePolicyError("source_request_conflict")
        return receipt

    def erasure_intent(self, request_id: str) -> ErasureIntent | None:
        self._require_lease()
        intent = self.ledger.erasure_intent(request_id)
        if intent is not None and intent.root.identity_id != self.identity_id:
            raise SourcePolicyError("erasure_request_conflict")
        return intent

    def erasure_intents(self) -> tuple[ErasureIntent, ...]:
        self._require_lease()
        return self.ledger.erasure_intents(self.identity_id)

    def body_action(self, request_id: str) -> BodyAction | None:
        self._require_lease()
        action = self.ledger.body_action(request_id)
        if action is not None and action_identity(action) != self.identity_id:
            raise SourcePolicyError("body_erasure_request_conflict")
        return action

    def body_actions(self) -> tuple[BodyAction, ...]:
        self._require_lease()
        return self.ledger.body_actions(self.identity_id)

    def body_policy_ready(self) -> bool:
        self._require_lease()
        return self.ledger.body_policy_ready()

    def commit_body_action(
        self, proposal: BodyErasureProposal, *, trace_id: str = "local_body_erasure"
    ) -> BodyErasureAction:
        self._require_lease(exclusive=True)
        self.assert_binding(
            proposal.root.identity_id,
            self.snapshot(verify_mirror=False).floor_id,
            proposal.root.lineage_id,
        )
        return self.ledger.commit_body_action(
            proposal, trace_id=non_blank(trace_id, "trace_id", maximum=128)
        )

    def dialogue_policy_ready(self) -> bool:
        self._require_lease()
        return self.ledger.dialogue_policy_ready()

    def commit_dialogue_action(
        self, proposal: DialogueBodyProposal, *, trace_id: str = "local_dialogue_erasure"
    ) -> DialogueBodyAction:
        self._require_lease(exclusive=True)
        self.assert_binding(
            proposal.root.identity_id,
            self.snapshot(verify_mirror=False).floor_id,
            proposal.root.lineage_id,
        )
        return self.ledger.commit_dialogue_action(
            proposal, trace_id=non_blank(trace_id, "trace_id", maximum=128)
        )

    def knowledge_policy_ready(self) -> bool:
        self._require_lease()
        return self.ledger.knowledge_policy_ready()

    def commit_knowledge_action(
        self, proposal: KnowledgeBodyProposal, *, trace_id: str = "local_knowledge_erasure"
    ) -> KnowledgeBodyAction:
        self._require_lease(exclusive=True)
        self.assert_binding(
            proposal.root.identity_id,
            self.snapshot(verify_mirror=False).floor_id,
            proposal.root.lineage_id,
        )
        return self.ledger.commit_knowledge_action(
            proposal, trace_id=non_blank(trace_id, "trace_id", maximum=128)
        )

    def reflection_policy_ready(self) -> bool:
        self._require_lease()
        return self.ledger.reflection_policy_ready()

    def commit_reflection_action(
        self, proposal: ReflectionBodyProposal, *, trace_id: str = "local_reflection_erasure"
    ) -> ReflectionBodyAction:
        self._require_lease(exclusive=True)
        self.assert_binding(
            proposal.root.identity_id,
            self.snapshot(verify_mirror=False).floor_id,
            proposal.root.lineage_id,
        )
        return self.ledger.commit_reflection_action(
            proposal, trace_id=non_blank(trace_id, "trace_id", maximum=128)
        )

    def appraisal_policy_ready(self) -> bool:
        self._require_lease()
        return self.ledger.appraisal_policy_ready()

    def commit_appraisal_action(
        self, proposal: AppraisalBodyProposal, *, trace_id: str = "local_appraisal_erasure"
    ) -> AppraisalBodyAction:
        self._require_lease(exclusive=True)
        self.assert_binding(
            proposal.root.identity_id,
            self.snapshot(verify_mirror=False).floor_id,
            proposal.root.lineage_id,
        )
        return self.ledger.commit_appraisal_action(
            proposal, trace_id=non_blank(trace_id, "trace_id", maximum=128)
        )

    def checkpoint_reason_policy_ready(self) -> bool:
        self._require_lease()
        return self.ledger.checkpoint_reason_policy_ready()

    def commit_checkpoint_reason_action(
        self,
        proposal: CheckpointReasonProposal,
        *,
        trace_id: str = "local_checkpoint_reason_erasure",
    ) -> CheckpointReasonAction:
        self._require_lease(exclusive=True)
        self.assert_binding(
            proposal.scope.identity_id,
            self.snapshot(verify_mirror=False).floor_id,
            proposal.scope.lineage_id,
        )
        return self.ledger.commit_checkpoint_reason_action(
            proposal, trace_id=non_blank(trace_id, "trace_id", maximum=128)
        )

    def commit_erasure_intent(
        self, proposal: ErasureIntentProposal, *, trace_id: str = "local_erasure_intent"
    ) -> ErasureIntent:
        self._require_lease(exclusive=True)
        self.assert_binding(
            proposal.root.identity_id,
            self.snapshot(verify_mirror=False).floor_id,
            proposal.root.lineage_id,
        )
        return self.ledger.commit_erasure_intent(
            proposal, trace_id=non_blank(trace_id, "trace_id", maximum=128)
        )

    def commit_exclusion(
        self,
        root: CanonicalRootRef,
        *,
        expected_generation: int,
        request_id: str,
        preview_digest: str,
        trace_id: str,
    ) -> SourceExclusionReceipt:
        self._require_lease(exclusive=True)
        self.assert_binding(
            root.identity_id, self.snapshot(verify_mirror=False).floor_id, root.lineage_id
        )
        return self.ledger.commit_exclusion(
            root,
            expected_generation=expected_generation,
            request_id=request_id,
            preview_digest=preview_digest,
            trace_id=trace_id,
        )

    def mark_repaired(self, request_id: str) -> SourceExclusionReceipt:
        self._require_lease(exclusive=True)
        self.receipt(request_id)
        return self.ledger.set_phase(request_id, ExclusionPhase.COMPLETE)

    def mark_repair_pending(self, request_id: str) -> SourceExclusionReceipt:
        self._require_lease(exclusive=True)
        self.receipt(request_id)
        return self.ledger.set_phase(request_id, ExclusionPhase.REPAIR_PENDING)
