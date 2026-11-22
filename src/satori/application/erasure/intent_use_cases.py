"""Floor-first terminal intent and resumable metadata-only owner preparation."""

import hashlib
import json
import re
from datetime import UTC, datetime

from satori.application.erasure.intent_contracts import (
    ErasureIntentCommand,
    ErasureIntentPreview,
    ErasureIntentRequest,
    ErasureIntentResult,
)
from satori.application.erasure.intent_ports import (
    ErasureIntentControlPort,
    ErasureIntentInventoryPort,
    ErasurePreparationPort,
)
from satori.application.erasure.use_cases import ErasurePlanner
from satori.domain.erasure import MAX_ERASURE_ASSESSMENT_WORK
from satori.domain.erasure_intent import (
    ERASURE_INTENT_POLICY_VERSION,
    ERASURE_RETENTION_POLICY_VERSION,
    ErasureIntent,
    ErasureIntentProposal,
)
from satori.domain.erasure_preparation import (
    ErasureBodyReadiness,
    ErasureOwnerPreparation,
    prepare_owner_contracts,
)
from satori.domain.source_policy import (
    CanonicalRootRef,
    SourceEligibilitySnapshot,
    SourcePolicyError,
)
from satori.domain.validation import non_blank


class ErasureIntentManager:
    def __init__(
        self,
        runtime: ErasureIntentControlPort,
        inventory: ErasureIntentInventoryPort,
        preparations: ErasurePreparationPort,
    ) -> None:
        self.runtime = runtime
        self.inventory = inventory
        self.preparations = preparations
        self.planner = ErasurePlanner(runtime, inventory)

    def preview(self, request: ErasureIntentRequest) -> ErasureIntentPreview:
        with self.runtime.shared() as snapshot:
            self.preparations.admit(snapshot)
            self.preparations.verify_current(snapshot)
            inventory = self.planner.preview(request)
            if (
                snapshot.erasure_revision + 1
            ) * inventory.scanned_rows > MAX_ERASURE_ASSESSMENT_WORK:
                raise SourcePolicyError("erasure_assessment_capacity_exceeded")
            material = {
                "inventory": inventory.model_dump(mode="json"),
                "erasure_revision": snapshot.erasure_revision,
                "intent_policy": ERASURE_INTENT_POLICY_VERSION,
                "retention_policy": ERASURE_RETENTION_POLICY_VERSION,
                "operation": "record_terminal_erasure_intent",
                "execution_authorized": False,
            }
            approval = hashlib.sha256(
                json.dumps(
                    material, sort_keys=True, ensure_ascii=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            return ErasureIntentPreview(
                inventory=inventory,
                erasure_revision=snapshot.erasure_revision,
                approval_digest=approval,
                retention_policy=ERASURE_RETENTION_POLICY_VERSION,
            )

    def _root(
        self, request: ErasureIntentRequest, snapshot: SourceEligibilitySnapshot
    ) -> CanonicalRootRef:
        root = self.inventory.resolve_root(
            request.message_id,
            counterparty_id=request.counterparty_id,
            snapshot=snapshot,
            approved_actions=self.runtime.body_actions() if snapshot.body_revision else (),
        )
        if root.message_id != request.message_id or root.counterparty_id != request.counterparty_id:
            raise SourcePolicyError("erasure_inventory_target_mismatch")
        self.runtime.assert_binding(root.identity_id, snapshot.floor_id, root.lineage_id)
        return root

    def _existing(self, root: CanonicalRootRef, request_id: str) -> ErasureIntent:
        request_id = non_blank(request_id, "request_id", maximum=128)
        intent = self.runtime.erasure_intent(request_id)
        if intent is None:
            raise SourcePolicyError("erasure_intent_not_found")
        if intent.root != root:
            raise SourcePolicyError("erasure_request_conflict")
        return intent

    def commit(self, request: ErasureIntentCommand) -> ErasureIntentResult:
        with self.runtime.exclusive() as snapshot:
            root = self._root(request, snapshot)
            existing = self.runtime.erasure_intent(request.request_id)
            if existing is not None:
                if existing.root != root or (
                    existing.expected_source_generation,
                    existing.expected_erasure_revision,
                    existing.approval_digest,
                ) != (
                    request.expected_generation,
                    request.expected_erasure_revision,
                    request.approval_digest,
                ):
                    raise SourcePolicyError("erasure_request_conflict")
                return self._prepare_all(existing)
            preview = self.preview(request)
            if request.expected_generation != preview.inventory.generation:
                raise SourcePolicyError("erasure_generation_conflict")
            if request.expected_erasure_revision != preview.erasure_revision:
                raise SourcePolicyError("erasure_revision_conflict")
            if request.approval_digest != preview.approval_digest:
                raise SourcePolicyError("erasure_intent_preview_stale")
            reviewed = preview.inventory
            intent = self.runtime.commit_erasure_intent(
                ErasureIntentProposal(
                    root=root,
                    request_id=request.request_id,
                    expected_source_generation=request.expected_generation,
                    expected_erasure_revision=request.expected_erasure_revision,
                    reviewed_preview_digest=reviewed.preview_digest,
                    approval_digest=preview.approval_digest,
                    schema_revision=reviewed.schema_revision,
                    schema_digest=reviewed.schema_digest,
                    store_digest=reviewed.store_digest,
                    routing_digest=reviewed.routing_digest,
                    assessment_status=reviewed.assessment_status,
                )
            )
            return self._prepare_all(intent)

    def resume(self, request: ErasureIntentRequest, *, request_id: str) -> ErasureIntentResult:
        with self.runtime.exclusive() as snapshot:
            root = self._root(request, snapshot)
            return self._prepare_all(self._existing(root, request_id))

    def _fresh_preparation(
        self, intent: ErasureIntent, snapshot: SourceEligibilitySnapshot
    ) -> ErasureOwnerPreparation:
        inventory = self.inventory.inspect(
            intent.root.message_id,
            counterparty_id=intent.root.counterparty_id,
            snapshot=snapshot,
        )
        return prepare_owner_contracts(
            intent, inventory, source_generation=snapshot.generation, prepared_at=datetime.now(UTC)
        )

    def _prepare_all(self, selected: ErasureIntent) -> ErasureIntentResult:
        try:
            snapshot = self.runtime.snapshot(verify_mirror=False)
            self.preparations.admit(snapshot)
            # ACK refers to the complete floor head. Every prior approved root is
            # privately re-assessed; the public result exposes only the selected one.
            intents = self.runtime.erasure_intents()
            inventories = self.inventory.inspect_many(
                tuple(intent.root for intent in intents),
                snapshot=snapshot,
                approved_actions=self.runtime.body_actions() if snapshot.body_revision else (),
            )
            if len(inventories) != len(intents):
                raise SourcePolicyError("erasure_preparation_intent_coverage_incomplete")
            preparations = tuple(
                prepare_owner_contracts(
                    intent,
                    inventory,
                    source_generation=snapshot.generation,
                    prepared_at=datetime.now(UTC),
                )
                for intent, inventory in zip(intents, inventories, strict=True)
            )
            self.preparations.persist(preparations, snapshot=snapshot)
            self.preparations.verify_current(snapshot)
            self.runtime.snapshot()
            preparation = self.preparations.inspect(
                selected.request_id, root=selected.root, snapshot=snapshot
            )
            if preparation is None:
                raise SourcePolicyError("erasure_preparation_missing")
            return self._result(selected, preparation, current=True)
        except SourcePolicyError as error:
            return self._pending(selected, error.reason_code)
        except Exception:
            # The floor commitment survives storage failures. Do not print a SQL
            # exception or claim completion based on an older preparation receipt.
            return self._pending(selected, "erasure_preparation_unavailable")

    def status(self, request: ErasureIntentRequest, *, request_id: str) -> ErasureIntentResult:
        with self.runtime.exclusive() as snapshot:
            root = self._root(request, snapshot)
            intent = self._existing(root, request_id)
            try:
                preparation = self.preparations.inspect(request_id, root=root, snapshot=snapshot)
                fresh = self._fresh_preparation(intent, snapshot)
                self.preparations.verify_current(snapshot)
                self.runtime.snapshot()
                current = preparation is not None and (
                    preparation.preparation_digest == fresh.preparation_digest
                )
                return self._result(intent, preparation, current=current)
            except SourcePolicyError as error:
                return self._pending(intent, error.reason_code)
            except Exception:
                return self._pending(intent, "erasure_preparation_unavailable")

    @staticmethod
    def _pending(intent: ErasureIntent, reason: str) -> ErasureIntentResult:
        if len(reason) > 128 or re.fullmatch(r"[a-z0-9_]+", reason) is None:
            reason = "erasure_preparation_unavailable"
        return ErasureIntentResult(
            intent=intent,
            main_projection_current=False,
            phase="intent_committed_preparation_pending",
            failure_reason=reason,
        )

    @staticmethod
    def _result(
        intent: ErasureIntent, preparation: ErasureOwnerPreparation | None, *, current: bool
    ) -> ErasureIntentResult:
        blocked = (
            preparation is None
            or preparation.assessment_status == "blocked"
            or any(
                item.body_readiness is not ErasureBodyReadiness.RETAIN_ONLY
                for item in preparation.assessments
            )
        )
        return ErasureIntentResult(
            intent=intent,
            main_projection_current=True,
            preparation=preparation,
            preparation_current=current,
            phase=(
                "intent_committed_preparation_blocked"
                if blocked
                else "intent_committed_preparation_retention_only"
            ),
        )
