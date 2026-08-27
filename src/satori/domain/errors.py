"""Typed expected errors for the initial self lifecycle."""


class SatoriDomainError(Exception):
    """Base class for expected SATORI domain/application outcomes."""


class NotActivated(SatoriDomainError):
    """No primary Satori identity exists in the installation."""

    def __init__(self) -> None:
        super().__init__("Satori is not activated")


class AlreadyActivated(SatoriDomainError):
    """Explicit activation was requested for an active installation."""

    def __init__(self, identity_id: str | None = None) -> None:
        self.identity_id = identity_id
        suffix = f": {identity_id}" if identity_id is not None else ""
        super().__init__(f"Satori is already activated{suffix}")


class InvalidSeed(SatoriDomainError):
    """A seed resource is malformed or violates its versioned schema."""


class UnsupportedSeedVersion(InvalidSeed):
    """The loader cannot interpret the seed schema version."""

    def __init__(self, actual: object, supported: tuple[int, ...]) -> None:
        self.actual = actual
        self.supported = supported
        super().__init__(f"unsupported seed schema version {actual!r}; supported: {supported}")


class CorruptSatoriState(SatoriDomainError):
    """Persistent identity exists but its required Stage 2 state is incomplete."""
