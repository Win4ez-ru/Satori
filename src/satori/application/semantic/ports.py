"""Application-owned persistence ports for SemanticMemoryManager."""

from typing import Protocol

from satori.application.unit_of_work import UnitOfWork
from satori.domain.memory import EpisodicMemory
from satori.domain.semantic_memory import (
    SemanticClaim,
    SemanticClaimRevision,
    SemanticFormationDecision,
    SemanticFormationPlan,
)


class SemanticMemoryRepository(Protocol):
    """Persist semantic owner decisions and expose immutable canonical inputs."""

    def get_decision(self, idempotency_key: str) -> SemanticFormationDecision | None:
        """Load a prior terminal processing decision."""

    def get_source_memories(
        self, source_memory_id: str, *, limit: int
    ) -> tuple[EpisodicMemory, ...]:
        """Load source plus a bounded recent evidence window."""

    def list_claims(
        self, *, active_only: bool = False, predicate: str | None = None
    ) -> tuple[SemanticClaim, ...]:
        """Load immutable semantic claims for owner policy or explicit reads."""

    def get_claim(self, claim_id: str) -> SemanticClaim | None:
        """Load one claim with complete evidence lineage."""

    def list_revisions(self, claim_id: str) -> tuple[SemanticClaimRevision, ...]:
        """Load append-only lifecycle history for one claim."""

    def list_unprocessed_memory_ids(self, *, limit: int) -> tuple[str, ...]:
        """Return deterministic Stage 4 sources missing a v1 terminal decision."""

    def record_decision(
        self,
        decision: SemanticFormationDecision,
        plan: SemanticFormationPlan,
        *,
        audit_event_id: str,
    ) -> bool:
        """Atomically commit plan and terminal decision; False means replay won."""


class SemanticMemoryUnitOfWork(UnitOfWork, Protocol):
    """Unit of Work exposing only the semantic-memory owner repository."""

    @property
    def semantic_memory(self) -> SemanticMemoryRepository:
        """Return the transaction-scoped semantic repository."""
