"""SQLAlchemy Unit of Work specialized for Stage 9 model owners."""

from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from satori.application.models.ports import CurrentModelsRepository
from satori.infrastructure.persistence.repositories.models import SQLAlchemyCurrentModelsRepository
from satori.infrastructure.persistence.unit_of_work import SQLAlchemyUnitOfWork


class SQLAlchemyCurrentModelsUnitOfWork(SQLAlchemyUnitOfWork):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        super().__init__(session_factory)
        self._current_models: CurrentModelsRepository | None = None

    @property
    def current_models(self) -> CurrentModelsRepository:
        if self._current_models is None:
            raise RuntimeError("unit of work is not active")
        return self._current_models

    def __enter__(self) -> Self:
        super().__enter__()
        self._current_models = SQLAlchemyCurrentModelsRepository(self.session)
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
            self._current_models = None
