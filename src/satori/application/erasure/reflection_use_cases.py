"""Fresh reflection-specific review over the shared durable body stream."""

import hashlib
import json
from dataclasses import asdict, replace

from satori.application.erasure.body_contracts import (
    BodyErasureCommand,
    BodyErasureRequest,
    BodyErasureResult,
)
from satori.application.erasure.reflection_contracts import (
    REFLECTION_OWNERS,
    REFLECTION_RESIDUALS,
    ReflectionBodyPreview,
)
from satori.application.erasure.reflection_ports import (
    ReflectionBodyControlPort,
    ReflectionBodyStorePort,
)
from satori.domain.erasure import MAX_ERASURE_ASSESSMENT_WORK
from satori.domain.erasure_bodies import body_reference_count
from satori.domain.erasure_reflection import (
    REFLECTION_BODY_ERASURE_POLICY_VERSION,
    ReflectionBodyAction,
    ReflectionBodyPlan,
    ReflectionBodyProposal,
)
from satori.domain.source_policy import (
    CanonicalRootRef,
    SourceEligibilitySnapshot,
    SourcePolicyError,
)


class ReflectionBodyManager:
    def __init__(self, control: ReflectionBodyControlPort, store: ReflectionBodyStorePort) -> None:
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
    ) -> ReflectionBodyPlan:
        plan = self.store.preview(
            root, snapshot=snapshot, approved_actions=self.control.body_actions()
        )
        if not self.control.reflection_policy_ready():
            plan = replace(
                plan,
                blockers=tuple(
                    sorted(set(plan.blockers) | {"policy_reflection_schema_upgrade_required"})
                ),
            )
        projected = (
            body_reference_count(self.control.body_actions())
            + len(plan.targets)
            + sum(t.metadata_reference_count for t in plan.targets)
        )
        if plan.scanned_rows * projected > MAX_ERASURE_ASSESSMENT_WORK:
            plan = replace(
                plan,
                blockers=tuple(sorted(set(plan.blockers) | {"body_application_capacity_exceeded"})),
            )
        return plan

    @staticmethod
    def _approval(
        plan: ReflectionBodyPlan, request: BodyErasureRequest, snapshot: SourceEligibilitySnapshot
    ) -> str:
        material = {
            "operation": REFLECTION_BODY_ERASURE_POLICY_VERSION,
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

    def preview(self, request: BodyErasureRequest) -> ReflectionBodyPreview:
        with self.control.exclusive() as snapshot:
            root = self._root(request, snapshot)
            plan = self._plan(root, snapshot)
            return ReflectionBodyPreview(
                **request.model_dump(),
                root=root,
                generation=snapshot.generation,
                erasure_revision=snapshot.erasure_revision,
                body_revision=snapshot.body_revision,
                approval_digest=self._approval(plan, request, snapshot),
                scanned_rows=plan.scanned_rows,
                reflection_proposals=plan.reflection_proposals,
                parent_runs=plan.parent_runs,
                source_rows=plan.source_rows,
                attempt_rows=plan.attempt_rows,
                outcome_rows=plan.outcome_rows,
                metadata_references=plan.metadata_references,
                already_erased=plan.already_erased,
                readiness="blocked" if plan.blockers else "ready",
                blockers=plan.blockers,
            )

    def _action(
        self, request: BodyErasureRequest, root: CanonicalRootRef, request_id: str
    ) -> ReflectionBodyAction:
        action = self.control.body_action(request_id)
        if (
            not isinstance(action, ReflectionBodyAction)
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
                action = self.control.commit_reflection_action(
                    ReflectionBodyProposal(
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
        action: ReflectionBodyAction,
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
        action: ReflectionBodyAction,
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
            owners=REFLECTION_OWNERS,
            residual_surfaces=REFLECTION_RESIDUALS,
        )
