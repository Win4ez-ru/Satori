"""Reflection approval capability over the single existing body stream."""

from typing import Protocol

from satori.application.erasure.body_ports import BodyErasureControlPort
from satori.domain.erasure_bodies import BodyAction
from satori.domain.erasure_reflection import (
    ReflectionBodyAction,
    ReflectionBodyPlan,
    ReflectionBodyProposal,
)
from satori.domain.source_policy import CanonicalRootRef, SourceEligibilitySnapshot


class ReflectionBodyControlPort(BodyErasureControlPort, Protocol):
    def reflection_policy_ready(self) -> bool: ...
    def upgrade_reflection_policy(self) -> SourceEligibilitySnapshot: ...
    def commit_reflection_action(
        self, proposal: ReflectionBodyProposal, *, trace_id: str = "local_reflection_erasure"
    ) -> ReflectionBodyAction: ...


class ReflectionBodyStorePort(Protocol):
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
    ) -> ReflectionBodyPlan: ...
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
