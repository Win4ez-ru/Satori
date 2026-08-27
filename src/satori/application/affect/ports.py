"""Application-owned persistence ports for authoritative affective state."""

from collections.abc import Sequence
from typing import Protocol

from satori.application.conversation.ports import ConversationHistoryRepository
from satori.application.unit_of_work import UnitOfWork
from satori.domain.affect import AffectiveStateSnapshot, AffectiveTransition


class AffectiveStateRepository(Protocol):
    """Persist the one current projection and append-only transition history."""

    def get_state(self, identity_id: str) -> AffectiveStateSnapshot | None:
        """Load the stored (not lazily materialized) projection."""

    def add_initial_state(self, state: AffectiveStateSnapshot) -> bool:
        """Insert deterministic resting state or return False on a concurrent insert."""

    def get_transition_for_interaction(self, interaction_id: str) -> AffectiveTransition | None:
        """Return the unique mutation for one logical interaction, if any."""

    def list_transitions(self, *, limit: int | None = None) -> Sequence[AffectiveTransition]:
        """Return newest-first immutable transition records."""

    def apply_transition(self, transition: AffectiveTransition, *, audit_event_id: str) -> bool:
        """Optimistically apply one transition and audit; False means exact replay."""


class AffectiveStateUnitOfWork(UnitOfWork, Protocol):
    """Unit of Work exposing only the EmotionManager persistence port."""

    @property
    def affective_state(self) -> AffectiveStateRepository:
        """Return the transaction-scoped affective repository."""


class AffectiveConversationUnitOfWork(UnitOfWork, Protocol):
    """Atomic canonical reply + accepted affective transition boundary."""

    @property
    def conversation_history(self) -> ConversationHistoryRepository:
        """Return the canonical history repository."""

    @property
    def affective_state(self) -> AffectiveStateRepository:
        """Return the sole affective write repository in the same transaction."""
