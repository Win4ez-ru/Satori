"""Appraisal approval capability over the single existing body stream."""

from typing import Protocol

from satori.application.erasure.body_ports import BodyErasureControlPort
from satori.domain.erasure_appraisal import (
    AppraisalBodyAction,
    AppraisalBodyPlan,
    AppraisalBodyProposal,
)
from satori.domain.erasure_bodies import BodyAction
from satori.domain.source_policy import CanonicalRootRef, SourceEligibilitySnapshot


class AppraisalBodyControlPort(BodyErasureControlPort, Protocol):
    def appraisal_policy_ready(self) -> bool: ...
    def upgrade_appraisal_policy(self) -> SourceEligibilitySnapshot: ...
    def commit_appraisal_action(
        self, proposal: AppraisalBodyProposal, *, trace_id: str = "local_appraisal_erasure"
    ) -> AppraisalBodyAction: ...


class AppraisalBodyStorePort(Protocol):
    def resolve_root(
        self,
        message_id: str,
        *,
        counterparty_id: str,
        snapshot: SourceEligibilitySnapshot,
        approved_actions: tuple[BodyAction, ...] | None = None,
    ) -> CanonicalRootRef: ...
    def preview(
        self,
        root: CanonicalRootRef,
        *,
        snapshot: SourceEligibilitySnapshot,
        approved_actions: tuple[BodyAction, ...] | None = None,
    ) -> AppraisalBodyPlan: ...
    def apply(
        self,
        actions: tuple[BodyAction, ...],
        *,
        snapshot: SourceEligibilitySnapshot,
        reviewed_store_digest: str | None = None,
    ) -> None: ...
    def current(
        self, actions: tuple[BodyAction, ...], *, snapshot: SourceEligibilitySnapshot
    ) -> bool: ...
