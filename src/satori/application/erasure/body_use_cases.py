"""Fresh plan approval, floor-first decision, resumable exact owner body handling."""

import hashlib
import json
from dataclasses import asdict, replace

from satori.application.erasure.body_contracts import (
    BodyErasureCommand,
    BodyErasurePreview,
    BodyErasureRequest,
    BodyErasureResult,
)
from satori.application.erasure.body_ports import BodyErasureControlPort, BodyErasureStorePort
from satori.domain.erasure import MAX_ERASURE_ASSESSMENT_WORK
from satori.domain.erasure_bodies import (
    BODY_ERASURE_POLICY_VERSION,
    BodyErasureAction,
    BodyErasureProposal,
    BodyPlan,
    body_reference_count,
)
from satori.domain.source_policy import (
    CanonicalRootRef,
    SourceEligibilitySnapshot,
    SourcePolicyError,
)


class BodyErasureManager:
    def __init__(self, control: BodyErasureControlPort, store: BodyErasureStorePort) -> None:
        self.control = control
        self.store = store

    def _root(
        self, request: BodyErasureRequest, snapshot: SourceEligibilitySnapshot
    ) -> CanonicalRootRef:
        root = self.store.resolve_root(
            request.message_id,
            counterparty_id=request.counterparty_id,
            snapshot=snapshot,
            approved_actions=self.control.body_actions(),
        )
        intent = self.control.erasure_intent(request.intent_request_id)
        if intent is None or intent.root != root:
            raise SourcePolicyError("body_erasure_intent_unavailable_in_scope")
        return root

    @staticmethod
    def _approval(
        plan: BodyPlan, request: BodyErasureRequest, snapshot: SourceEligibilitySnapshot
    ) -> str:
        material = {
            "operation": BODY_ERASURE_POLICY_VERSION,
            "plan": asdict(plan),
            "intent_request_id": request.intent_request_id,
            "floor_id": snapshot.floor_id,
            "installation_id": snapshot.installation_id,
            "source_generation": snapshot.generation,
            "erasure_revision": snapshot.erasure_revision,
            "body_revision": snapshot.body_revision,
            "body_head_digest": snapshot.body_head_digest,
        }
        return hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def preview(self, request: BodyErasureRequest) -> BodyErasurePreview:
        with self.control.exclusive() as snapshot:
            root = self._root(request, snapshot)
            plan = self._plan(root, snapshot)
            return BodyErasurePreview(
                **request.model_dump(),
                root=root,
                generation=snapshot.generation,
                erasure_revision=snapshot.erasure_revision,
                body_revision=snapshot.body_revision,
                approval_digest=self._approval(plan, request, snapshot),
                scanned_rows=plan.scanned_rows,
                summaries=len(plan.targets),
                quotes=plan.quotes,
                vectors=plan.vectors,
                already_erased=plan.already_erased,
                readiness="blocked" if plan.blockers else "ready",
                blockers=plan.blockers,
            )

    def commit(self, command: BodyErasureCommand) -> BodyErasureResult:
        request = BodyErasureRequest(
            message_id=command.message_id,
            counterparty_id=command.counterparty_id,
            intent_request_id=command.intent_request_id,
        )
        with self.control.exclusive() as snapshot:
            root = self._root(request, snapshot)
            existing = self.control.body_action(command.request_id)
            reviewed_store_digest = None
            if existing is not None:
                if not isinstance(existing, BodyErasureAction):
                    raise SourcePolicyError("body_erasure_request_conflict")
                proposal = existing.proposal
                if (
                    proposal.root,
                    proposal.intent_request_id,
                    proposal.expected_source_generation,
                    proposal.expected_erasure_revision,
                    proposal.expected_body_revision,
                    proposal.approval_digest,
                ) != (
                    root,
                    request.intent_request_id,
                    command.expected_generation,
                    command.expected_erasure_revision,
                    command.expected_body_revision,
                    command.approval_digest,
                ):
                    raise SourcePolicyError("body_erasure_request_conflict")
                action = existing
            else:
                plan = self._plan(root, snapshot)
                if plan.blockers:
                    raise SourcePolicyError("body_erasure_plan_blocked")
                if (
                    command.expected_generation,
                    command.expected_erasure_revision,
                    command.expected_body_revision,
                ) != (
                    snapshot.generation,
                    snapshot.erasure_revision,
                    snapshot.body_revision,
                ) or command.approval_digest != self._approval(plan, request, snapshot):
                    raise SourcePolicyError("body_erasure_review_stale")
                action = self.control.commit_body_action(
                    BodyErasureProposal(
                        root=root,
                        intent_request_id=request.intent_request_id,
                        request_id=command.request_id,
                        expected_source_generation=snapshot.generation,
                        expected_erasure_revision=snapshot.erasure_revision,
                        expected_body_revision=snapshot.body_revision,
                        approval_digest=command.approval_digest,
                        store_digest=plan.store_digest,
                        schema_digest=plan.schema_digest,
                        targets=plan.targets,
                    )
                )
                snapshot = self.control.snapshot(verify_mirror=False)
                reviewed_store_digest = plan.store_digest
            return self._apply(
                request, root, action, snapshot, reviewed_store_digest=reviewed_store_digest
            )

    def _plan(self, root: CanonicalRootRef, snapshot: SourceEligibilitySnapshot) -> BodyPlan:
        plan = self.store.preview(
            root, snapshot=snapshot, approved_actions=self.control.body_actions()
        )
        if not self.control.body_policy_ready():
            plan = replace(
                plan,
                blockers=tuple(
                    sorted(set(plan.blockers) | {"policy_body_schema_upgrade_required"})
                ),
            )
        projected_references = body_reference_count(self.control.body_actions()) + len(plan.targets)
        if plan.scanned_rows * projected_references > MAX_ERASURE_ASSESSMENT_WORK:
            return replace(
                plan,
                blockers=tuple(sorted(set(plan.blockers) | {"body_application_capacity_exceeded"})),
            )
        return plan

    def resume(self, request: BodyErasureRequest, *, request_id: str) -> BodyErasureResult:
        with self.control.exclusive() as snapshot:
            root = self._root(request, snapshot)
            action = self._action(request, root, request_id)
            return self._apply(request, root, action, snapshot)

    def status(self, request: BodyErasureRequest, *, request_id: str) -> BodyErasureResult:
        with self.control.exclusive() as snapshot:
            root = self._root(request, snapshot)
            action = self._action(request, root, request_id)
            current = self.store.current(self.control.body_actions(), snapshot=snapshot)
            return self._result(request, root, action, snapshot, current, None)

    def _action(
        self, request: BodyErasureRequest, root: CanonicalRootRef, request_id: str
    ) -> BodyErasureAction:
        action = self.control.body_action(request_id)
        if (
            not isinstance(action, BodyErasureAction)
            or action.proposal.root != root
            or action.proposal.intent_request_id != request.intent_request_id
        ):
            raise SourcePolicyError("body_action_unavailable_in_scope")
        return action

    def _apply(
        self,
        request: BodyErasureRequest,
        root: CanonicalRootRef,
        action: BodyErasureAction,
        snapshot: SourceEligibilitySnapshot,
        *,
        reviewed_store_digest: str | None = None,
    ) -> BodyErasureResult:
        try:
            self.store.apply(
                self.control.body_actions(),
                snapshot=snapshot,
                reviewed_store_digest=reviewed_store_digest,
            )
            current = self.store.current(self.control.body_actions(), snapshot=snapshot)
            return self._result(request, root, action, snapshot, current, None)
        except SourcePolicyError as error:
            reason = (
                error.reason_code
                if error.reason_code
                in {
                    "body_target_changed_review_required",
                    "body_application_capacity_exceeded",
                    "body_application_schema_required",
                    "body_erasure_review_stale",
                }
                else "body_application_unavailable"
            )
            return self._result(request, root, action, snapshot, False, reason)
        except (OSError, ValueError, RuntimeError):
            return self._result(
                request, root, action, snapshot, False, "body_application_unavailable"
            )

    @staticmethod
    def _result(
        request: BodyErasureRequest,
        root: CanonicalRootRef,
        action: BodyErasureAction,
        snapshot: SourceEligibilitySnapshot,
        current: bool,
        reason: str | None,
    ) -> BodyErasureResult:
        return BodyErasureResult(
            **request.model_dump(),
            root=root,
            request_id=action.proposal.request_id,
            action_id=action.action_id,
            body_revision=action.result_body_revision,
            materialized_body_revision=snapshot.body_revision if current else 0,
            effective_body_head_digest=snapshot.body_head_digest,
            main_projection_current=current,
            selected_bodies_removed=current,
            phase="body_application_complete" if current else "body_application_pending",
            failure_reason=reason,
        )
