"""SQLAlchemy Unit of Work specialized for reflection lifecycle records."""

from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from satori.application.reflection.ports import ReflectionRepository
from satori.infrastructure.persistence.repositories.reflection import (
    SQLAlchemyReflectionRepository,
)
from satori.infrastructure.persistence.unit_of_work import SQLAlchemyUnitOfWork


class SQLAlchemyReflectionUnitOfWork(SQLAlchemyUnitOfWork):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        super().__init__(session_factory)
        self._reflection: ReflectionRepository | None = None

    @property
    def reflection(self) -> ReflectionRepository:
        if self._reflection is None:
            raise RuntimeError("unit of work is not active")
        return self._reflection

    def __enter__(self) -> Self:
        super().__enter__()
        self._reflection = SQLAlchemyReflectionRepository(self.session)
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
            self._reflection = None
