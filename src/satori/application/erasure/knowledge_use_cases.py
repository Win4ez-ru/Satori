"""Fresh knowledge-specific review over the shared durable body stream."""

import hashlib
import json
from dataclasses import asdict, replace

from satori.application.erasure.body_contracts import (
    BodyErasureCommand,
    BodyErasureRequest,
    BodyErasureResult,
)
from satori.application.erasure.knowledge_contracts import (
    KNOWLEDGE_OWNERS,
    KNOWLEDGE_RESIDUALS,
    KnowledgeBodyPreview,
)
from satori.application.erasure.knowledge_ports import (
    KnowledgeBodyControlPort,
    KnowledgeBodyStorePort,
)
from satori.domain.erasure import MAX_ERASURE_ASSESSMENT_WORK
from satori.domain.erasure_bodies import body_reference_count
from satori.domain.erasure_knowledge import (
    KNOWLEDGE_BODY_ERASURE_POLICY_VERSION,
    KnowledgeBodyAction,
    KnowledgeBodyPlan,
    KnowledgeBodyProposal,
)
from satori.domain.source_policy import (
    CanonicalRootRef,
    SourceEligibilitySnapshot,
    SourcePolicyError,
)


class KnowledgeBodyManager:
    def __init__(self, control: KnowledgeBodyControlPort, store: KnowledgeBodyStorePort) -> None:
        self.control, self.store = control, store

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

    def _plan(
        self, root: CanonicalRootRef, snapshot: SourceEligibilitySnapshot
    ) -> KnowledgeBodyPlan:
        plan = self.store.preview(
            root, snapshot=snapshot, approved_actions=self.control.body_actions()
        )
        if not self.control.knowledge_policy_ready():
            plan = replace(
                plan,
                blockers=tuple(
                    sorted(set(plan.blockers) | {"policy_knowledge_schema_upgrade_required"})
                ),
            )
        projected = (
            body_reference_count(self.control.body_actions())
            + len(plan.targets)
            + sum(len(t.witness_transitions) for t in plan.targets)
        )
        if plan.scanned_rows * projected > MAX_ERASURE_ASSESSMENT_WORK:
            plan = replace(
                plan,
                blockers=tuple(sorted(set(plan.blockers) | {"body_application_capacity_exceeded"})),
            )
        return plan

    @staticmethod
    def _approval(
        plan: KnowledgeBodyPlan, request: BodyErasureRequest, snapshot: SourceEligibilitySnapshot
    ) -> str:
        material = {
            "operation": KNOWLEDGE_BODY_ERASURE_POLICY_VERSION,
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

    def preview(self, request: BodyErasureRequest) -> KnowledgeBodyPreview:
        with self.control.exclusive() as snapshot:
            root = self._root(request, snapshot)
            plan = self._plan(root, snapshot)
            return KnowledgeBodyPreview(
                **request.model_dump(),
                root=root,
                generation=snapshot.generation,
                erasure_revision=snapshot.erasure_revision,
                body_revision=snapshot.body_revision,
                approval_digest=self._approval(plan, request, snapshot),
                scanned_rows=plan.scanned_rows,
                semantic_claims=plan.semantic_claims,
                user_model_claims=plan.user_model_claims,
                world_model_claims=plan.world_model_claims,
                positions=plan.positions,
                inclinations=plan.inclinations,
                position_quotes=plan.position_quotes,
                witness_transitions=plan.witness_transitions,
                already_erased=plan.already_erased,
                readiness="blocked" if plan.blockers else "ready",
                blockers=plan.blockers,
            )

    def _action(
        self, request: BodyErasureRequest, root: CanonicalRootRef, request_id: str
    ) -> KnowledgeBodyAction:
        action = self.control.body_action(request_id)
        if (
            not isinstance(action, KnowledgeBodyAction)
            or action.proposal.root != root
            or action.proposal.intent_request_id != request.intent_request_id
        ):
            raise SourcePolicyError("body_action_unavailable_in_scope")
        return action

    def commit(self, command: BodyErasureCommand) -> BodyErasureResult:
        request = BodyErasureRequest(
            message_id=command.message_id,
            counterparty_id=command.counterparty_id,
            intent_request_id=command.intent_request_id,
        )
        with self.control.exclusive() as snapshot:
            root = self._root(request, snapshot)
            existing = self.control.body_action(command.request_id)
            reviewed = None
            if existing is not None:
                action = self._action(request, root, command.request_id)
                proposal = action.proposal
                if (
                    proposal.expected_source_generation,
                    proposal.expected_erasure_revision,
                    proposal.expected_body_revision,
                    proposal.approval_digest,
                ) != (
                    command.expected_generation,
                    command.expected_erasure_revision,
                    command.expected_body_revision,
                    command.approval_digest,
                ):
                    raise SourcePolicyError("body_erasure_request_conflict")
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
                action = self.control.commit_knowledge_action(
                    KnowledgeBodyProposal(
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
                reviewed = plan.store_digest
            return self._apply(request, root, action, snapshot, reviewed)

    def resume(self, request: BodyErasureRequest, *, request_id: str) -> BodyErasureResult:
        with self.control.exclusive() as snapshot:
            root = self._root(request, snapshot)
            return self._apply(
                request, root, self._action(request, root, request_id), snapshot, None
            )

    def status(self, request: BodyErasureRequest, *, request_id: str) -> BodyErasureResult:
        with self.control.exclusive() as snapshot:
            root = self._root(request, snapshot)
            action = self._action(request, root, request_id)
            return self._result(
                request,
                root,
                action,
                snapshot,
                self.store.current(self.control.body_actions(), snapshot=snapshot),
                None,
            )

    def _apply(
        self,
        request: BodyErasureRequest,
        root: CanonicalRootRef,
        action: KnowledgeBodyAction,
        snapshot: SourceEligibilitySnapshot,
        reviewed: str | None,
    ) -> BodyErasureResult:
        try:
            self.store.apply(
                self.control.body_actions(), snapshot=snapshot, reviewed_store_digest=reviewed
            )
            current = self.store.current(self.control.body_actions(), snapshot=snapshot)
            return self._result(request, root, action, snapshot, current, None)
        except SourcePolicyError as error:
            allowed = {
                "body_target_changed_review_required",
                "body_application_capacity_exceeded",
                "body_application_schema_required",
                "body_erasure_review_stale",
            }
            return self._result(
                request,
                root,
                action,
                snapshot,
                False,
                error.reason_code
                if error.reason_code in allowed
                else "body_application_unavailable",
            )
        except (OSError, ValueError, RuntimeError):
            return self._result(
                request, root, action, snapshot, False, "body_application_unavailable"
            )

    @staticmethod
    def _result(
        request: BodyErasureRequest,
        root: CanonicalRootRef,
        action: KnowledgeBodyAction,
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
            owners=KNOWLEDGE_OWNERS,
            residual_surfaces=KNOWLEDGE_RESIDUALS,
        )
