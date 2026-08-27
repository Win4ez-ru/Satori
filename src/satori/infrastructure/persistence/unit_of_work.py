"""SQLAlchemy implementation of the application transaction boundary."""

from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session, sessionmaker


class SQLAlchemyUnitOfWork:
    """Keep one SQLAlchemy session scoped to an explicit unit of work."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._committed = False

    @property
    def session(self) -> Session:
        """Expose the session to infrastructure repositories inside the boundary."""

        if self._session is None:
            raise RuntimeError("unit of work is not active")
        return self._session

    def __enter__(self) -> Self:
        """Open a fresh session for this unit of work."""

        if self._session is not None:
            raise RuntimeError("unit of work is already active")
        self._session = self._session_factory()
        self._committed = False
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Roll back any uncommitted work, then close the session."""

        if self._session is None:
            return
        try:
            if exc_type is not None or not self._committed:
                self._session.rollback()
        finally:
            self._session.close()
            self._session = None
            self._committed = False

    def commit(self) -> None:
        """Commit the active transaction."""

        self.session.commit()
        self._committed = True

    def rollback(self) -> None:
        """Roll back the active transaction."""

        self.session.rollback()
        self._committed = False
