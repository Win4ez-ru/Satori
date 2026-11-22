"""Protected read-only inventory and deterministic freshness checks."""

import hashlib
import json
from dataclasses import asdict

from satori.application.erasure.contracts import (
    ErasureCheckRequest,
    ErasureCheckResult,
    ErasurePreview,
    ErasurePreviewRequest,
)
from satori.application.erasure.ports import ErasureInventoryPort
from satori.application.source_policy.ports import SourceAdmissionPort
from satori.domain.erasure import ERASURE_POLICY_VERSION
from satori.domain.source_policy import SourcePolicyError

UNASSESSED_SURFACES = (
    "sqlite_wal_shm_journal_and_free_pages",
    "exported_archives_and_partial_exports",
    "application_and_os_memory_caches",
    "logs_and_private_evaluation_artifacts",
    "already_disclosed_provider_and_external_copies",
)
EXECUTION_BLOCKERS = (
    "physical_erasure_executor_not_implemented",
    "durable_erasure_intent_not_committed",
    "external_retention_not_assessed",
    "access_and_key_recovery_not_implemented",
)


class ErasurePlanner:
    def __init__(self, runtime: SourceAdmissionPort, inventory: ErasureInventoryPort) -> None:
        self.runtime = runtime
        self.inventory = inventory

    def preview(self, request: ErasurePreviewRequest) -> ErasurePreview:
        with self.runtime.shared() as snapshot:
            inventory = self.inventory.inspect(
                request.message_id,
                counterparty_id=request.counterparty_id,
                snapshot=snapshot,
            )
            if (
                inventory.root.message_id != request.message_id
                or inventory.root.counterparty_id != request.counterparty_id
            ):
                raise SourcePolicyError("erasure_inventory_target_mismatch")
            self.runtime.assert_binding(
                inventory.root.identity_id, snapshot.floor_id, inventory.root.lineage_id
            )
            if inventory.root.message_id not in snapshot.excluded_message_ids:
                raise SourcePolicyError("erasure_requires_source_exclusion")
            routes = tuple(
                sorted(
                    inventory.routes, key=lambda item: (item.owner, item.table, item.disposition)
                )
            )
            values = {
                "root": asdict(inventory.root),
                "floor_id": snapshot.floor_id,
                "installation_id": snapshot.installation_id,
                "generation": snapshot.generation,
                "policy_version": ERASURE_POLICY_VERSION,
                "source_policy_version": snapshot.policy_version,
                "schema_revision": inventory.schema_revision,
                "schema_digest": inventory.schema_digest,
                "store_digest": inventory.store_digest,
                "routing_digest": inventory.routing_digest,
                "routes": [asdict(route) for route in routes],
                "scanned_rows": inventory.scanned_rows,
                "affected_payload_versions": inventory.affected_payload_versions,
                "uncertain_payload_versions": inventory.uncertain_payload_versions,
                "assessment_blockers": sorted(set(inventory.blockers)),
                "unassessed_surfaces": UNASSESSED_SURFACES,
                "execution_blockers": EXECUTION_BLOCKERS,
                "physical_erasure": False,
                "execution_authorized": False,
                "historical_numeric_state_retained": True,
            }
            digest = hashlib.sha256(
                json.dumps(
                    values, sort_keys=True, ensure_ascii=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            return ErasurePreview(
                root=inventory.root,
                floor_id=snapshot.floor_id,
                installation_id=snapshot.installation_id,
                generation=snapshot.generation,
                policy_version=ERASURE_POLICY_VERSION,
                source_policy_version=snapshot.policy_version,
                schema_revision=inventory.schema_revision,
                schema_digest=inventory.schema_digest,
                store_digest=inventory.store_digest,
                routing_digest=inventory.routing_digest,
                preview_digest=digest,
                routes=routes,
                scanned_rows=inventory.scanned_rows,
                affected_payload_versions=inventory.affected_payload_versions,
                uncertain_payload_versions=inventory.uncertain_payload_versions,
                assessment_status="blocked" if inventory.blockers else "complete",
                assessment_blockers=tuple(sorted(set(inventory.blockers))),
                unassessed_surfaces=UNASSESSED_SURFACES,
                execution_blockers=EXECUTION_BLOCKERS,
            )

    def check(self, request: ErasureCheckRequest) -> ErasureCheckResult:
        preview = self.preview(
            ErasurePreviewRequest(
                message_id=request.message_id, counterparty_id=request.counterparty_id
            )
        )
        if request.expected_generation != preview.generation:
            raise SourcePolicyError("erasure_generation_conflict")
        if request.preview_digest != preview.preview_digest:
            raise SourcePolicyError("erasure_preview_stale")
        return ErasureCheckResult(
            preview_digest=preview.preview_digest,
            generation=preview.generation,
            assessment_status=preview.assessment_status,
        )
