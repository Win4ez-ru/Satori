"""Explicit dialogue approval capability; existing shared body application capability."""

from typing import Protocol

from satori.application.erasure.body_ports import BodyErasureControlPort
from satori.domain.erasure_bodies import BodyAction
from satori.domain.erasure_dialogue import (
    DialogueBodyAction,
    DialogueBodyPlan,
    DialogueBodyProposal,
)
from satori.domain.source_policy import CanonicalRootRef, SourceEligibilitySnapshot


class DialogueBodyControlPort(BodyErasureControlPort, Protocol):
    def dialogue_policy_ready(self) -> bool: ...
    def upgrade_dialogue_policy(self) -> SourceEligibilitySnapshot: ...
    def commit_dialogue_action(
        self, proposal: DialogueBodyProposal, *, trace_id: str = "local_dialogue_erasure"
    ) -> DialogueBodyAction: ...


class DialogueBodyStorePort(Protocol):
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
    ) -> DialogueBodyPlan: ...
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
