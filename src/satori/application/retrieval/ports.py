"""Persistence ports owned by the rebuildable Stage 5 retrieval index."""

from datetime import datetime
from typing import Protocol

from satori.application.retrieval.contracts import IndexedMemoryCandidate
from satori.application.unit_of_work import UnitOfWork
from satori.core.embedding import EmbeddingSpace
from satori.domain.memory import EpisodicMemory
from satori.domain.memory_scope import MemoryScope


class EpisodicMemoryIndexRepository(Protocol):
    """Read canonical episodes and own only their derived embedding rows."""

    def list_unindexed(
        self, space: EmbeddingSpace, *, scope: MemoryScope, rebuild: bool, limit: int | None = None
    ) -> tuple[EpisodicMemory, ...]:
        """Return active episodes requiring an embedding in this exact space."""

    def get_eligible(self, memory_id: str) -> tuple[MemoryScope, EpisodicMemory] | None:
        """Resolve and reload one proven canonical source for internal indexing."""

    def upsert(
        self,
        memory_id: str,
        space: EmbeddingSpace,
        vector: tuple[float, ...],
        *,
        scope: MemoryScope,
        expected_version: int,
        indexed_at: datetime,
        input_id: str | None = None,
    ) -> None:
        """Idempotently insert or replace one derived vector."""

    def has_candidates(
        self,
        space: EmbeddingSpace,
        *,
        scope: MemoryScope,
        cutoff: datetime,
        excluded_interaction_id: str | None,
    ) -> bool:
        """Return whether query embedding work can possibly retrieve a prior episode."""

    def list_candidates(
        self,
        space: EmbeddingSpace,
        *,
        scope: MemoryScope,
        cutoff: datetime,
        excluded_interaction_id: str | None,
    ) -> tuple[IndexedMemoryCandidate, ...]:
        """Return only compatible, active, prior candidates for exact scanning."""


class EpisodicMemoryIndexUnitOfWork(UnitOfWork, Protocol):
    """Transaction boundary for derived retrieval state."""

    @property
    def memory_index(self) -> EpisodicMemoryIndexRepository:
        """Return the transaction-scoped index repository."""
