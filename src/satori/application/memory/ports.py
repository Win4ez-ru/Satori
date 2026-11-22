"""Application-owned persistence ports for the Stage 4 MemoryManager boundary."""

from typing import Protocol

from satori.application.memory.contracts import MemoryScopeStatus
from satori.application.unit_of_work import UnitOfWork
from satori.domain.conversation_history import ConversationInteraction
from satori.domain.memory import EpisodeFormationDecision, EpisodicMemory
from satori.domain.memory_lifecycle import MemoryLifecycleDecision
from satori.domain.memory_scope import MemoryScope


class EpisodicMemoryRepository(Protocol):
    """Persist terminal formation decisions and their optional episode atomically."""

    def get_source_interaction(self, interaction_id: str) -> ConversationInteraction | None:
        """Read a verified completed canonical source before extraction/commit."""

    def scope_status(self, scope: MemoryScope) -> MemoryScopeStatus:
        """Inspect counts only; unverified roots are not assigned to a person."""

    def get_decision(self, idempotency_key: str) -> EpisodeFormationDecision | None:
        """Load a prior terminal decision for replay."""

    def record_decision(
        self,
        decision: EpisodeFormationDecision,
        *,
        audit_event_id: str,
        input_id: str | None = None,
    ) -> bool:
        """Stage decision, memory/evidence, and audit; False means a concurrent replay won."""

    def list_memories(
        self,
        *,
        scope: MemoryScope,
        interaction_id: str | None = None,
        include_archived: bool = False,
    ) -> tuple[EpisodicMemory, ...]:
        """Return immutable episodic records for explicit debug/read use only."""

    def get_memory(
        self, memory_id: str, *, scope: MemoryScope, include_archived: bool = False
    ) -> EpisodicMemory | None:
        """Read one fully proven target; archived inspection cannot release quarantine."""

    def get_lifecycle_decision(self, request_id: str) -> MemoryLifecycleDecision | None:
        """Load a receipt only after the application proves the requested target scope."""

    def record_lifecycle_decision(
        self, decision: MemoryLifecycleDecision, *, audit_event_id: str
    ) -> bool:
        """Commit guarded status/version, receipt and audit; False means request replay won."""


class EpisodicMemoryUnitOfWork(UnitOfWork, Protocol):
    """Unit of Work exposing only the MemoryManager repository port."""

    @property
    def episodic_memory(self) -> EpisodicMemoryRepository:
        """Return the transaction-scoped episodic-memory repository."""
