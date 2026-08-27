"""SQLAlchemy Unit of Work specialized for Stage 2 repositories."""

from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from satori.application.initial_self.ports import InitialSelfRepository
from satori.infrastructure.persistence.repositories.initial_self import (
    SQLAlchemyInitialSelfRepository,
)
from satori.infrastructure.persistence.unit_of_work import SQLAlchemyUnitOfWork


class SQLAlchemyInitialSelfUnitOfWork(SQLAlchemyUnitOfWork):
    """Bind the initial-self repository to one transaction-scoped session."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        super().__init__(session_factory)
        self._initial_self: InitialSelfRepository | None = None

    @property
    def initial_self(self) -> InitialSelfRepository:
        """Return the active transaction's initial-self repository."""

        if self._initial_self is None:
            raise RuntimeError("unit of work is not active")
        return self._initial_self

    def __enter__(self) -> Self:
        """Open the session and bind its repository."""

        super().__enter__()
        self._initial_self = SQLAlchemyInitialSelfRepository(self.session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close transaction resources and forget the repository."""

        try:
            super().__exit__(exc_type, exc_value, traceback)
        finally:
            self._initial_self = None
