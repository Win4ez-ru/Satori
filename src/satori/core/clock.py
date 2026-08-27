"""Clock boundary used to keep future time policies deterministic in tests."""

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Return an aware UTC instant."""

    def now(self) -> datetime:
        """Return the current time."""


class SystemClock:
    """Production clock backed by the operating system."""

    def now(self) -> datetime:
        """Return the current aware UTC time."""

        return datetime.now(UTC)
