"""Application-owned persistence ports for SemanticMemoryManager."""

from datetime import datetime
from typing import Protocol

from satori.application.unit_of_work import UnitOfWork
from satori.domain.erasure_knowledge import (
    ErasedKnowledgeHeader,
    KnowledgeBodyKind,
    KnowledgeBodyTarget,
)
from satori.domain.memory import EpisodicMemory
from satori.domain.memory_scope import MemoryScope
from satori.domain.semantic_memory import (
    SemanticClaim,
    SemanticClaimRevision,
    SemanticFormationDecision,
    SemanticFormationPlan,
)


class SemanticMemoryRepository(Protocol):
    """Persist semantic owner decisions and expose immutable canonical inputs."""

    def erase_knowledge_bodies(
        self, targets: tuple[KnowledgeBodyTarget, ...], *, action_id: str
    ) -> None:
        """Apply an exact approved owner body transition in the current transaction."""

    def get_erased_knowledge_header(
        self, kind: KnowledgeBodyKind, aggregate_id: str
    ) -> ErasedKnowledgeHeader | None:
        """Read retained terminal metadata; this grants no live-content eligibility."""

    def get_decision(self, idempotency_key: str) -> SemanticFormationDecision | None:
        """Load a prior terminal processing decision."""

    def resolve_source_scope(self, source_memory_id: str) -> MemoryScope | None:
        """Resolve only a fully proven active canonical source partition."""

    def get_source_memories(
        self, source_memory_id: str, *, scope: MemoryScope, limit: int
    ) -> tuple[EpisodicMemory, ...]:
        """Load source plus a bounded recent evidence window."""

    def list_claims(
        self, *, scope: MemoryScope, active_only: bool = False, predicate: str | None = None
    ) -> tuple[SemanticClaim, ...]:
        """Load immutable semantic claims for owner policy or explicit reads."""

    def get_claim(self, claim_id: str, *, scope: MemoryScope) -> SemanticClaim | None:
        """Load one claim with complete evidence lineage."""

    def list_revisions(self, claim_id: str) -> tuple[SemanticClaimRevision, ...]:
        """Load append-only lifecycle history for one claim."""

    def list_unprocessed_memory_ids(self, *, scope: MemoryScope, limit: int) -> tuple[str, ...]:
        """Return deterministic Stage 4 sources missing a v1 terminal decision."""

    def record_decision(
        self,
        decision: SemanticFormationDecision,
        plan: SemanticFormationPlan,
        *,
        scope: MemoryScope,
        source_memories: tuple[EpisodicMemory, ...],
        audit_event_id: str,
        input_id: str | None = None,
    ) -> bool:
        """Atomically commit plan and terminal decision; False means replay won."""

    def withdraw_source_support(
        self,
        *,
        excluded_message_ids: frozenset[str],
        request_id: str,
        policy_generation: int,
        now: datetime,
    ) -> int:
        """Internal owner repair; preserve historical confidence and source evidence."""


class SemanticMemoryUnitOfWork(UnitOfWork, Protocol):
    """Unit of Work exposing only the semantic-memory owner repository."""

    @property
    def semantic_memory(self) -> SemanticMemoryRepository:
        """Return the transaction-scoped semantic repository."""
