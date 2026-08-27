"""SQLAlchemy Unit of Work specialized for the Stage 14 PersonalityManager."""

from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from satori.application.personality.ports import PersonalityRepository
from satori.infrastructure.persistence.repositories.personality import (
    SQLAlchemyPersonalityRepository,
)
from satori.infrastructure.persistence.unit_of_work import SQLAlchemyUnitOfWork


class SQLAlchemyPersonalityUnitOfWork(SQLAlchemyUnitOfWork):
    """Expose the sole post-activation personality owner repository."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        super().__init__(session_factory)
        self._personality: PersonalityRepository | None = None

    @property
    def personality(self) -> PersonalityRepository:
        if self._personality is None:
            raise RuntimeError("unit of work is not active")
        return self._personality

    def __enter__(self) -> Self:
        super().__enter__()
        self._personality = SQLAlchemyPersonalityRepository(self.session)
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
            self._personality = None
