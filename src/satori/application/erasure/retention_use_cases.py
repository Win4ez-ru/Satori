"""Read-only global coverage with a separate current-head/store review digest."""

import hashlib
import json
from dataclasses import asdict

from satori.application.erasure.retention_contracts import (
    RetentionCheckRequest,
    RetentionCheckResult,
    RetentionStatus,
    RetentionStatusRequest,
)
from satori.application.erasure.retention_ports import RetentionInventoryPort
from satori.application.erasure.use_cases import UNASSESSED_SURFACES
from satori.application.source_policy.ports import SourceAdmissionPort
from satori.domain.retention import RETENTION_POLICY_VERSION
from satori.domain.source_policy import SourcePolicyError


class RetentionPlanner:
    def __init__(self, runtime: SourceAdmissionPort, inventory: RetentionInventoryPort) -> None:
        self.runtime = runtime
        self.inventory = inventory

    def status(self, request: RetentionStatusRequest) -> RetentionStatus:
        with self.runtime.shared() as snapshot:
            if request.identity_id != snapshot.identity_id:
                raise SourcePolicyError("retention_identity_unavailable")
            self.runtime.assert_binding(request.identity_id, snapshot.floor_id, snapshot.lineage_id)
            inventory = self.inventory.inspect(request, snapshot)
            if (
                inventory.identity_id,
                inventory.lineage_id,
                inventory.source_generation,
                inventory.erasure_revision,
                inventory.body_revision,
                inventory.body_head_digest,
            ) != (
                snapshot.identity_id,
                snapshot.lineage_id,
                snapshot.generation,
                snapshot.erasure_revision,
                snapshot.body_revision,
                snapshot.body_head_digest,
            ):
                raise SourcePolicyError("retention_inventory_binding_mismatch")
            tables = tuple(
                sorted(inventory.table_counts, key=lambda item: (item.owner, item.table))
            )
            reasons = tuple(sorted(inventory.reason_counts, key=lambda item: item.reason))
            retained = sum(item.supported_retained_rows for item in tables)
            content = sum(item.owned_content_rows for item in tables)
            unknown = sum(item.unknown_rows for item in tables)
            material = {
                "policy_version": RETENTION_POLICY_VERSION,
                "source_policy_version": snapshot.policy_version,
                "identity_id": snapshot.identity_id,
                "lineage_id": snapshot.lineage_id,
                "floor_id": snapshot.floor_id,
                "installation_id": snapshot.installation_id,
                "generation": snapshot.generation,
                "erasure_revision": snapshot.erasure_revision,
                "body_revision": snapshot.body_revision,
                "body_head_digest": snapshot.body_head_digest,
                "schema_revision": inventory.schema_revision,
                "schema_digest": inventory.schema_digest,
                "store_digest": inventory.store_digest,
                "classification_digest": inventory.classification_digest,
                "tables": [asdict(item) for item in tables],
                "reasons": [asdict(item) for item in reasons],
                "unassessed_surfaces": UNASSESSED_SURFACES,
                "physical_erasure": False,
                "execution_authorized": False,
            }
            review_digest = hashlib.sha256(
                json.dumps(
                    material,
                    sort_keys=True,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
            ).hexdigest()
            return RetentionStatus(
                policy_version=RETENTION_POLICY_VERSION,
                source_policy_version=snapshot.policy_version,
                identity_id=snapshot.identity_id,
                lineage_id=snapshot.lineage_id,
                floor_id=snapshot.floor_id,
                installation_id=snapshot.installation_id,
                generation=snapshot.generation,
                erasure_revision=snapshot.erasure_revision,
                body_revision=snapshot.body_revision,
                body_head_digest=snapshot.body_head_digest,
                schema_revision=inventory.schema_revision,
                schema_digest=inventory.schema_digest,
                store_digest=inventory.store_digest,
                classification_digest=inventory.classification_digest,
                review_digest=review_digest,
                scanned_rows=inventory.scanned_rows,
                work_units=inventory.work_units,
                total_rows=inventory.scanned_rows,
                supported_retained_rows=retained,
                owned_content_rows=content,
                unknown_rows=unknown,
                table_counts=tables,
                reason_counts=reasons,
                coverage="partial" if unknown else "complete",
                logical_readiness="blocked_unknown"
                if unknown
                else "owned_content_remaining"
                if content
                else "retained_only",
                unassessed_surfaces=UNASSESSED_SURFACES,
            )

    def check(self, request: RetentionCheckRequest) -> RetentionCheckResult:
        current = self.status(RetentionStatusRequest(identity_id=request.identity_id))
        if (
            request.expected_generation,
            request.expected_erasure_revision,
            request.expected_body_revision,
        ) != (current.generation, current.erasure_revision, current.body_revision):
            raise SourcePolicyError("retention_head_conflict")
        if request.review_digest != current.review_digest:
            raise SourcePolicyError("retention_review_stale")
        return RetentionCheckResult(
            review_digest=current.review_digest,
            generation=current.generation,
            erasure_revision=current.erasure_revision,
            body_revision=current.body_revision,
            coverage=current.coverage,
            logical_readiness=current.logical_readiness,
        )
