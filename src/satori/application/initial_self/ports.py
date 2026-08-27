"""Application-owned persistence ports for Stage 2 initial self state."""

from typing import Protocol

from satori.application.unit_of_work import UnitOfWork
from satori.domain.audit import ActivationAuditEvent
from satori.domain.initial_self import InitialSelfSnapshot


class InitialSelfRepository(Protocol):
    """Read or atomically stage the complete Stage 2 state."""

    def get(self) -> InitialSelfSnapshot | None:
        """Return the complete persistent self, or None before activation."""

    def add(self, snapshot: InitialSelfSnapshot, event: ActivationAuditEvent) -> bool:
        """Stage initial state and return False if the primary slot is already claimed."""


class InitialSelfUnitOfWork(UnitOfWork, Protocol):
    """Unit of Work exposing only the Stage 2 repository port."""

    @property
    def initial_self(self) -> InitialSelfRepository:
        """Return the repository bound to this active transaction."""
