"""Application-owned persistence ports for Stage 9 models."""

from typing import Protocol

from satori.application.unit_of_work import UnitOfWork
from satori.core.models import ModelSourceMessage
from satori.domain.models import (
    ModelClaimRevision,
    ModelFormationDecision,
    OwnerFormationPlan,
    UserModelClaim,
    WorldModelClaim,
)


class CurrentModelsRepository(Protocol):
    def get_decision(self, idempotency_key: str) -> ModelFormationDecision | None: ...

    def get_source_messages(
        self, source_interaction_id: str, *, limit: int
    ) -> tuple[ModelSourceMessage, ...]: ...

    def list_user_claims(
        self,
        *,
        identity_id: str,
        counterparty_id: str,
        current_only: bool = False,
    ) -> tuple[UserModelClaim, ...]: ...

    def list_world_claims(
        self,
        *,
        identity_id: str,
        counterparty_id: str,
        current_only: bool = False,
    ) -> tuple[WorldModelClaim, ...]: ...

    def get_user_claim(self, claim_id: str) -> UserModelClaim | None: ...

    def get_world_claim(self, claim_id: str) -> WorldModelClaim | None: ...

    def list_user_revisions(self, claim_id: str) -> tuple[ModelClaimRevision, ...]: ...

    def list_world_revisions(self, claim_id: str) -> tuple[ModelClaimRevision, ...]: ...

    def list_unprocessed_interaction_ids(self, *, limit: int) -> tuple[str, ...]: ...

    def record_decision(
        self,
        decision: ModelFormationDecision,
        user_plan: OwnerFormationPlan[UserModelClaim],
        world_plan: OwnerFormationPlan[WorldModelClaim],
        *,
        user_audit_event_id: str,
        world_audit_event_id: str,
    ) -> bool: ...


class CurrentModelsUnitOfWork(UnitOfWork, Protocol):
    @property
    def current_models(self) -> CurrentModelsRepository: ...
