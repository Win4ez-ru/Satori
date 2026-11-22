"""Floor authority and owner mutation remain separate capabilities."""

from typing import Protocol

from satori.application.erasure.intent_ports import ErasureIntentControlPort
from satori.domain.erasure_bodies import (
    BodyAction,
    BodyErasureAction,
    BodyErasureProposal,
    BodyPlan,
)
from satori.domain.source_policy import CanonicalRootRef, SourceEligibilitySnapshot


class BodyErasureControlPort(ErasureIntentControlPort, Protocol):
    def body_policy_ready(self) -> bool: ...
    def body_actions(self) -> tuple[BodyAction, ...]: ...
    def body_action(self, request_id: str) -> BodyAction | None: ...
    def commit_body_action(
        self, proposal: BodyErasureProposal, *, trace_id: str = "local_body_erasure"
    ) -> BodyErasureAction: ...
    def upgrade_body_policy(self) -> SourceEligibilitySnapshot: ...


class BodyErasureStorePort(Protocol):
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
    ) -> BodyPlan: ...
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
