"""Application-owned persistence ports for the Stage 4 MemoryManager boundary."""

from typing import Protocol

from satori.application.unit_of_work import UnitOfWork
from satori.domain.memory import EpisodeFormationDecision, EpisodicMemory


class EpisodicMemoryRepository(Protocol):
    """Persist terminal formation decisions and their optional episode atomically."""

    def get_decision(self, idempotency_key: str) -> EpisodeFormationDecision | None:
        """Load a prior terminal decision for replay."""

    def record_decision(self, decision: EpisodeFormationDecision, *, audit_event_id: str) -> bool:
        """Stage decision, memory/evidence, and audit; False means a concurrent replay won."""

    def list_memories(self, *, interaction_id: str | None = None) -> tuple[EpisodicMemory, ...]:
        """Return immutable episodic records for explicit debug/read use only."""


class EpisodicMemoryUnitOfWork(UnitOfWork, Protocol):
    """Unit of Work exposing only the MemoryManager repository port."""

    @property
    def episodic_memory(self) -> EpisodicMemoryRepository:
        """Return the transaction-scoped episodic-memory repository."""
