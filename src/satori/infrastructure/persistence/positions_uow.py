"""SQLAlchemy Unit of Work specialized for Stage 11 PositionManager."""

from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from satori.application.positions.ports import PositionsRepository
from satori.infrastructure.persistence.repositories.positions import SQLAlchemyPositionsRepository
from satori.infrastructure.persistence.unit_of_work import SQLAlchemyUnitOfWork


class SQLAlchemyPositionsUnitOfWork(SQLAlchemyUnitOfWork):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        super().__init__(session_factory)
        self._positions: PositionsRepository | None = None

    @property
    def positions(self) -> PositionsRepository:
        if self._positions is None:
            raise RuntimeError("unit of work is not active")
        return self._positions

    def __enter__(self) -> Self:
        super().__enter__()
        self._positions = SQLAlchemyPositionsRepository(self.session)
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
            self._positions = None
