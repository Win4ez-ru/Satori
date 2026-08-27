"""SQLAlchemy Unit of Work specialized for SemanticMemoryManager."""

from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from satori.application.semantic.ports import SemanticMemoryRepository
from satori.infrastructure.persistence.repositories.semantic import (
    SQLAlchemySemanticMemoryRepository,
)
from satori.infrastructure.persistence.unit_of_work import SQLAlchemyUnitOfWork


class SQLAlchemySemanticMemoryUnitOfWork(SQLAlchemyUnitOfWork):
    """Bind semantic-memory persistence to one transaction-scoped session."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        super().__init__(session_factory)
        self._semantic_memory: SemanticMemoryRepository | None = None

    @property
    def semantic_memory(self) -> SemanticMemoryRepository:
        if self._semantic_memory is None:
            raise RuntimeError("unit of work is not active")
        return self._semantic_memory

    def __enter__(self) -> Self:
        super().__enter__()
        self._semantic_memory = SQLAlchemySemanticMemoryRepository(self.session)
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
            self._semantic_memory = None
