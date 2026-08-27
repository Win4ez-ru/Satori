"""SQLAlchemy Unit of Work for the derived Stage 5 retrieval index."""

from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from satori.application.retrieval.ports import EpisodicMemoryIndexRepository
from satori.infrastructure.persistence.repositories.retrieval import (
    SQLAlchemyEpisodicMemoryIndexRepository,
)
from satori.infrastructure.persistence.unit_of_work import SQLAlchemyUnitOfWork


class SQLAlchemyEpisodicMemoryIndexUnitOfWork(SQLAlchemyUnitOfWork):
    """Bind rebuildable vector operations to one short transaction."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        super().__init__(session_factory)
        self._memory_index: EpisodicMemoryIndexRepository | None = None

    @property
    def memory_index(self) -> EpisodicMemoryIndexRepository:
        if self._memory_index is None:
            raise RuntimeError("unit of work is not active")
        return self._memory_index

    def __enter__(self) -> Self:
        super().__enter__()
        self._memory_index = SQLAlchemyEpisodicMemoryIndexRepository(self.session)
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
            self._memory_index = None
