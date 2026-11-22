"""Identity reason controls reuse the existing installation barrier and owner projection."""

from typing import Protocol

from satori.application.erasure.body_ports import BodyErasureControlPort
from satori.domain.erasure_bodies import BodyAction
from satori.domain.erasure_personality_reasons import (
    CheckpointReasonAction,
    CheckpointReasonPlan,
    CheckpointReasonProposal,
    CheckpointReasonScope,
    CheckpointReasonSelector,
)
from satori.domain.source_policy import SourceEligibilitySnapshot


class CheckpointReasonControlPort(BodyErasureControlPort, Protocol):
    def checkpoint_reason_policy_ready(self) -> bool: ...
    def upgrade_checkpoint_reason_policy(self) -> SourceEligibilitySnapshot: ...
    def commit_checkpoint_reason_action(
        self,
        proposal: CheckpointReasonProposal,
        *,
        trace_id: str = "local_checkpoint_reason_erasure",
    ) -> CheckpointReasonAction: ...


class CheckpointReasonStorePort(Protocol):
    def resolve_scope(
        self,
        identity_id: str,
        *,
        snapshot: SourceEligibilitySnapshot,
        approved_actions: tuple[BodyAction, ...] | None = None,
    ) -> CheckpointReasonScope: ...
    def preview(
        self,
        scope: CheckpointReasonScope,
        selectors: tuple[CheckpointReasonSelector, ...],
        *,
        snapshot: SourceEligibilitySnapshot,
        approved_actions: tuple[BodyAction, ...] | None = None,
    ) -> CheckpointReasonPlan: ...
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
