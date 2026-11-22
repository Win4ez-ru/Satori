"""Explicit knowledge approval capability; existing shared body application capability."""

from typing import Protocol

from satori.application.erasure.body_ports import BodyErasureControlPort
from satori.domain.erasure_bodies import BodyAction
from satori.domain.erasure_knowledge import (
    KnowledgeBodyAction,
    KnowledgeBodyPlan,
    KnowledgeBodyProposal,
)
from satori.domain.source_policy import CanonicalRootRef, SourceEligibilitySnapshot


class KnowledgeBodyControlPort(BodyErasureControlPort, Protocol):
    def knowledge_policy_ready(self) -> bool: ...
    def upgrade_knowledge_policy(self) -> SourceEligibilitySnapshot: ...
    def commit_knowledge_action(
        self, proposal: KnowledgeBodyProposal, *, trace_id: str = "local_knowledge_erasure"
    ) -> KnowledgeBodyAction: ...


class KnowledgeBodyStorePort(Protocol):
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
    ) -> KnowledgeBodyPlan: ...
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
