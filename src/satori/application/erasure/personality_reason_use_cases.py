"""Digest-bound identity reason review over the same durable mixed body head."""

import hashlib
import json
from dataclasses import asdict, replace

from satori.application.erasure.personality_reason_contracts import (
    CheckpointReasonCommand,
    CheckpointReasonPreview,
    CheckpointReasonRequest,
    CheckpointReasonResult,
)
from satori.application.erasure.personality_reason_ports import (
    CheckpointReasonControlPort,
    CheckpointReasonStorePort,
)
from satori.domain.erasure import MAX_ERASURE_ASSESSMENT_WORK
from satori.domain.erasure_bodies import body_reference_count
from satori.domain.erasure_personality_reasons import (
    CHECKPOINT_REASON_ERASURE_POLICY_VERSION,
    CheckpointReasonAction,
    CheckpointReasonPlan,
    CheckpointReasonProposal,
    CheckpointReasonScope,
)
from satori.domain.source_policy import SourceEligibilitySnapshot, SourcePolicyError


class CheckpointReasonManager:
    def __init__(
        self, control: CheckpointReasonControlPort, store: CheckpointReasonStorePort
    ) -> None:
        self.control, self.store = control, store

    def _scope(
        self, request: CheckpointReasonRequest, snapshot: SourceEligibilitySnapshot
    ) -> CheckpointReasonScope:
        return self.store.resolve_scope(
            request.identity_id,
            snapshot=snapshot,
            approved_actions=self.control.body_actions(),
        )

    def _plan(
        self,
        scope: CheckpointReasonScope,
        request: CheckpointReasonRequest,
        snapshot: SourceEligibilitySnapshot,
    ) -> CheckpointReasonPlan:
        if not request.selectors:
            raise SourcePolicyError("checkpoint_reason_selectors_required")
        plan = self.store.preview(
            scope,
            request.selectors,
            snapshot=snapshot,
            approved_actions=self.control.body_actions(),
        )
        blockers = set(plan.blockers)
        if not self.control.checkpoint_reason_policy_ready():
            blockers.add("policy_checkpoint_reason_schema_upgrade_required")
        projected = (
            body_reference_count(self.control.body_actions())
            + len(plan.targets)
            + sum(t.metadata_reference_count for t in plan.targets)
        )
        if plan.scanned_rows * projected > MAX_ERASURE_ASSESSMENT_WORK:
            blockers.add("body_application_capacity_exceeded")
        return replace(plan, blockers=tuple(sorted(blockers)))

    @staticmethod
    def _approval(plan: CheckpointReasonPlan, snapshot: SourceEligibilitySnapshot) -> str:
        material = {
            "operation": CHECKPOINT_REASON_ERASURE_POLICY_VERSION,
            "plan": asdict(plan),
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

    def preview(self, request: CheckpointReasonRequest) -> CheckpointReasonPreview:
        with self.control.exclusive() as snapshot:
            scope = self._scope(request, snapshot)
            plan = self._plan(scope, request, snapshot)
            return CheckpointReasonPreview(
                scope=scope,
                generation=snapshot.generation,
                erasure_revision=snapshot.erasure_revision,
                body_revision=snapshot.body_revision,
                approval_digest=self._approval(plan, snapshot),
                scanned_rows=plan.scanned_rows,
                approval_reasons=plan.approval_reasons,
                restore_reasons=plan.restore_reasons,
                metadata_references=plan.metadata_references,
                already_erased=plan.already_erased,
                readiness="blocked" if plan.blockers else "ready",
                blockers=plan.blockers,
            )

    def _action(
        self, request: CheckpointReasonRequest, scope: CheckpointReasonScope, request_id: str
    ) -> CheckpointReasonAction:
        action = self.control.body_action(request_id)
        if (
            not isinstance(action, CheckpointReasonAction)
            or action.proposal.scope != scope
            or (
                request.selectors
                and tuple(t.selector for t in action.proposal.targets) != request.selectors
            )
        ):
            raise SourcePolicyError("body_action_unavailable_in_scope")
        return action

    def commit(self, command: CheckpointReasonCommand) -> CheckpointReasonResult:
        request = CheckpointReasonRequest(
            identity_id=command.identity_id, selectors=command.selectors
        )
        if not request.selectors:
            raise SourcePolicyError("checkpoint_reason_selectors_required")
        with self.control.exclusive() as snapshot:
            scope = self._scope(request, snapshot)
            existing = self.control.body_action(command.request_id)
            reviewed = None
            if existing is not None:
                action = self._action(request, scope, command.request_id)
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
                plan = self._plan(scope, request, snapshot)
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
                ) or command.approval_digest != self._approval(plan, snapshot):
                    raise SourcePolicyError("body_erasure_review_stale")
                action = self.control.commit_checkpoint_reason_action(
                    CheckpointReasonProposal(
                        scope=scope,
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
            return self._apply(scope, action, snapshot, reviewed)

    def resume(
        self, request: CheckpointReasonRequest, *, request_id: str
    ) -> CheckpointReasonResult:
        with self.control.exclusive() as snapshot:
            scope = self._scope(request, snapshot)
            return self._apply(scope, self._action(request, scope, request_id), snapshot, None)

    def status(
        self, request: CheckpointReasonRequest, *, request_id: str
    ) -> CheckpointReasonResult:
        with self.control.exclusive() as snapshot:
            scope = self._scope(request, snapshot)
            action = self._action(request, scope, request_id)
            return self._result(
                scope,
                action,
                snapshot,
                self.store.current(self.control.body_actions(), snapshot=snapshot),
                None,
            )

    def _apply(
        self,
        scope: CheckpointReasonScope,
        action: CheckpointReasonAction,
        snapshot: SourceEligibilitySnapshot,
        reviewed: str | None,
    ) -> CheckpointReasonResult:
        try:
            self.store.apply(
                self.control.body_actions(), snapshot=snapshot, reviewed_store_digest=reviewed
            )
            current = self.store.current(self.control.body_actions(), snapshot=snapshot)
            return self._result(scope, action, snapshot, current, None)
        except SourcePolicyError as error:
            allowed = {
                "body_target_changed_review_required",
                "body_application_capacity_exceeded",
                "body_application_schema_required",
                "body_erasure_review_stale",
            }
            reason = (
                error.reason_code
                if error.reason_code in allowed
                else "body_application_unavailable"
            )
            return self._result(scope, action, snapshot, False, reason)
        except (OSError, ValueError, RuntimeError):
            return self._result(scope, action, snapshot, False, "body_application_unavailable")

    @staticmethod
    def _result(
        scope: CheckpointReasonScope,
        action: CheckpointReasonAction,
        snapshot: SourceEligibilitySnapshot,
        current: bool,
        reason: str | None,
    ) -> CheckpointReasonResult:
        return CheckpointReasonResult(
            scope=scope,
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
