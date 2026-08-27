"""SQLAlchemy Unit of Work specialized for the InteractionLog owner."""

from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from satori.application.conversation.ports import ConversationHistoryRepository
from satori.infrastructure.persistence.repositories.conversation import (
    SQLAlchemyConversationHistoryRepository,
)
from satori.infrastructure.persistence.unit_of_work import SQLAlchemyUnitOfWork


class SQLAlchemyConversationHistoryUnitOfWork(SQLAlchemyUnitOfWork):
    """Bind conversation-history persistence to one transaction-scoped session."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        super().__init__(session_factory)
        self._conversation_history: ConversationHistoryRepository | None = None

    @property
    def conversation_history(self) -> ConversationHistoryRepository:
        if self._conversation_history is None:
            raise RuntimeError("unit of work is not active")
        return self._conversation_history

    def __enter__(self) -> Self:
        super().__enter__()
        self._conversation_history = SQLAlchemyConversationHistoryRepository(self.session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            super().__exit__(exc_type, exc_value, traceback)
        finally:
            self._conversation_history = None
