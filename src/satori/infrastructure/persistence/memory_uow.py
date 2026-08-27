"""SQLAlchemy Unit of Work specialized for the MemoryManager owner."""

from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from satori.application.memory.ports import EpisodicMemoryRepository
from satori.infrastructure.persistence.repositories.memory import (
    SQLAlchemyEpisodicMemoryRepository,
)
from satori.infrastructure.persistence.unit_of_work import SQLAlchemyUnitOfWork


class SQLAlchemyEpisodicMemoryUnitOfWork(SQLAlchemyUnitOfWork):
    """Bind episodic-memory persistence to one transaction-scoped session."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        super().__init__(session_factory)
        self._episodic_memory: EpisodicMemoryRepository | None = None

    @property
    def episodic_memory(self) -> EpisodicMemoryRepository:
        if self._episodic_memory is None:
            raise RuntimeError("unit of work is not active")
        return self._episodic_memory

    def __enter__(self) -> Self:
        super().__enter__()
        self._episodic_memory = SQLAlchemyEpisodicMemoryRepository(self.session)
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
            self._episodic_memory = None
