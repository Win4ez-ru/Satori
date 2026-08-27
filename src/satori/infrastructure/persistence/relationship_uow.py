"""SQLAlchemy Unit of Work for Stage 8 relationship ownership."""

from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from satori.application.relationship.ports import RelationshipRepository
from satori.infrastructure.persistence.repositories.relationship import (
    SQLAlchemyRelationshipRepository,
)
from satori.infrastructure.persistence.unit_of_work import SQLAlchemyUnitOfWork


class SQLAlchemyRelationshipUnitOfWork(SQLAlchemyUnitOfWork):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        super().__init__(session_factory)
        self._relationship: RelationshipRepository | None = None

    @property
    def relationship(self) -> RelationshipRepository:
        if self._relationship is None:
            raise RuntimeError("unit of work is not active")
        return self._relationship

    def __enter__(self) -> Self:
        super().__enter__()
        self._relationship = SQLAlchemyRelationshipRepository(self.session)
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
            self._relationship = None
