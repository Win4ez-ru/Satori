"""Intent control is owned by SourcePolicyManager, independently of main storage."""

from typing import Protocol

from satori.application.erasure.ports import ErasureInventoryPort
from satori.application.source_policy.ports import SourceAdmissionPort
from satori.domain.erasure import ErasureInventory
from satori.domain.erasure_bodies import BodyAction
from satori.domain.erasure_intent import ErasureIntent, ErasureIntentProposal
from satori.domain.erasure_preparation import ErasureOwnerPreparation
from satori.domain.source_policy import CanonicalRootRef, SourceEligibilitySnapshot


class ErasureIntentControlPort(SourceAdmissionPort, Protocol):
    def body_actions(self) -> tuple[BodyAction, ...]: ...

    def erasure_intent(self, request_id: str) -> ErasureIntent | None: ...

    def erasure_intents(self) -> tuple[ErasureIntent, ...]: ...

    def commit_erasure_intent(
        self, proposal: ErasureIntentProposal, *, trace_id: str = "local_erasure_intent"
    ) -> ErasureIntent: ...

    def upgrade_policy(self) -> SourceEligibilitySnapshot: ...


class ErasureIntentInventoryPort(ErasureInventoryPort, Protocol):
    def inspect_many(
        self,
        roots: tuple[CanonicalRootRef, ...],
        *,
        snapshot: SourceEligibilitySnapshot,
        approved_actions: tuple[BodyAction, ...] | None = None,
    ) -> tuple[ErasureInventory, ...]: ...

    def resolve_root(
        self,
        message_id: str,
        *,
        counterparty_id: str,
        snapshot: SourceEligibilitySnapshot,
        approved_actions: tuple[BodyAction, ...] | None = None,
    ) -> CanonicalRootRef: ...


class ErasurePreparationPort(Protocol):
    def admit(self, snapshot: SourceEligibilitySnapshot) -> None: ...

    def verify_current(self, snapshot: SourceEligibilitySnapshot) -> None: ...

    def persist(
        self,
        preparations: tuple[ErasureOwnerPreparation, ...],
        *,
        snapshot: SourceEligibilitySnapshot,
    ) -> None: ...

    def inspect(
        self, request_id: str, *, root: CanonicalRootRef, snapshot: SourceEligibilitySnapshot
    ) -> ErasureOwnerPreparation | None: ...
