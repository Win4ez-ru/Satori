"""Application-owned persistence ports for Stage 4 conversation history."""

from datetime import datetime
from typing import Protocol

from satori.application.unit_of_work import UnitOfWork
from satori.domain.conversation_history import (
    ConversationHistorySnapshot,
    ConversationInteraction,
    ConversationSession,
    HistoricalMessage,
    InteractionFailureMetadata,
    InteractionProviderMetadata,
)


class ConversationHistoryRepository(Protocol):
    """Own append-only sessions/messages and interaction lifecycle transitions."""

    def get_session(self, session_id: str) -> ConversationSession | None:
        """Load one session read model."""

    def add_session(self, session: ConversationSession) -> bool:
        """Stage a new session; return False if its stable ID already exists."""

    def close_session(self, session_id: str, *, ended_at: datetime) -> ConversationSession | None:
        """Close one open session and return its updated read model."""

    def get_by_client_request_id(self, client_request_id: str) -> ConversationInteraction | None:
        """Load the canonical interaction for an idempotency key."""

    def get_interaction(self, interaction_id: str) -> ConversationInteraction | None:
        """Load one interaction by stable ID."""

    def add_interaction(self, interaction: ConversationInteraction) -> bool:
        """Stage a pending interaction and exact user message atomically."""

    def mark_failed(self, interaction_id: str, *, failure: InteractionFailureMetadata) -> None:
        """Mark an incomplete interaction as retryable failed."""

    def complete_interaction(
        self,
        interaction_id: str,
        *,
        assistant_message: HistoricalMessage,
        completed_at: datetime,
        provider_metadata: InteractionProviderMetadata,
        close_session: bool,
    ) -> ConversationInteraction:
        """Atomically add the reply and complete the canonical interaction."""

    def get_history(self, *, session_id: str | None = None) -> ConversationHistorySnapshot:
        """Return immutable history without ORM objects or hidden prompt content."""

    def list_recent_completed(
        self,
        *,
        session_id: str,
        excluded_interaction_id: str,
        limit: int,
    ) -> tuple[ConversationInteraction, ...]:
        """Return only the newest bounded completed interactions in chronological order."""


class ConversationHistoryUnitOfWork(UnitOfWork, Protocol):
    """Unit of Work exposing only the InteractionLog owner port."""

    @property
    def conversation_history(self) -> ConversationHistoryRepository:
        """Return the transaction-scoped history repository."""
