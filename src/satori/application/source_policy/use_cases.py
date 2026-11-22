"""Floor-first terminal exclusion; retry repairs the original durable decision."""

from satori.application.source_policy.contracts import (
    SourceExclusionCommand,
    SourceExclusionEffects,
    SourceExclusionPreview,
)
from satori.application.source_policy.ports import SourcePolicyControlPort, SourceRepair
from satori.domain.source_policy import (
    CanonicalRootRef,
    SourceExclusionReceipt,
    SourcePolicyError,
    exclusion_effects,
    exclusion_preview_digest,
)


class SourcePolicyControls:
    def __init__(self, runtime: SourcePolicyControlPort) -> None:
        self.runtime = runtime

    def preview(self, root: CanonicalRootRef) -> SourceExclusionPreview:
        with self.runtime.exclusive():
            snapshot = self.runtime.snapshot(verify_mirror=False)
            self.runtime.assert_binding(root.identity_id, snapshot.floor_id, root.lineage_id)
            return SourceExclusionPreview(
                root=root,
                floor_id=snapshot.floor_id,
                installation_id=snapshot.installation_id,
                generation=snapshot.generation,
                already_excluded=root.message_id in snapshot.excluded_message_ids,
                preview_digest=exclusion_preview_digest(root, snapshot),
                effects=SourceExclusionEffects(**exclusion_effects()),
            )

    def apply(
        self,
        command: SourceExclusionCommand,
        root: CanonicalRootRef,
        *,
        repair: SourceRepair | None = None,
        trace_id: str = "local_source_exclusion",
    ) -> SourceExclusionReceipt:
        if command.message_id != root.message_id:
            raise SourcePolicyError("source_request_target_mismatch")
        with self.runtime.exclusive():
            snapshot = self.runtime.snapshot(verify_mirror=False)
            self.runtime.assert_binding(root.identity_id, snapshot.floor_id, root.lineage_id)
            existing = self.runtime.receipt(command.request_id)
            if existing is not None:
                if (
                    existing.root != root
                    or existing.expected_generation != command.expected_generation
                    or existing.preview_digest != command.preview_digest
                ):
                    raise SourcePolicyError("source_request_conflict")
                receipt = existing
            else:
                if snapshot.generation != command.expected_generation:
                    raise SourcePolicyError("source_generation_conflict")
                if exclusion_preview_digest(root, snapshot) != command.preview_digest:
                    raise SourcePolicyError("source_preview_mismatch")
                receipt = self.runtime.commit_exclusion(
                    root,
                    expected_generation=command.expected_generation,
                    request_id=command.request_id,
                    preview_digest=command.preview_digest,
                    trace_id=trace_id,
                )
            receipt = self.runtime.mark_repair_pending(command.request_id)
            if repair is None:
                return receipt
            # Re-run idempotent reconciliation even for a completed receipt: the operator may
            # have selected an older main DB while the independent floor remained current.
            try:
                repair(self.runtime.snapshot(verify_mirror=False), receipt)
            except Exception:
                return receipt
            return self.runtime.mark_repaired(command.request_id)
