"""Transaction boundary owned by the application layer."""

from types import TracebackType
from typing import Protocol, Self, runtime_checkable


@runtime_checkable
class UnitOfWork(Protocol):
    """Explicit begin/work/commit-or-rollback boundary."""

    def __enter__(self) -> Self:
        """Open the transaction-scoped resources."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Roll back uncommitted work and close resources."""

    def commit(self) -> None:
        """Commit the current transaction."""

    def rollback(self) -> None:
        """Roll back the current transaction."""
