"""Identifier generation boundary."""

from typing import Protocol, runtime_checkable
from uuid import uuid4


@runtime_checkable
class IdGenerator(Protocol):
    """Create opaque stable identifiers."""

    def new(self) -> str:
        """Return a new identifier."""


class Uuid4Generator:
    """Generate UUID4 identifiers as canonical strings."""

    def new(self) -> str:
        """Return a new UUID4 string."""

        return str(uuid4())
