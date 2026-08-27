"""Deterministic explicit activation and read-only use cases."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from satori.application.initial_self.ports import InitialSelfUnitOfWork
from satori.core.clock import Clock
from satori.core.ids import IdGenerator
from satori.domain.audit import ActivationAuditEvent
from satori.domain.errors import AlreadyActivated, NotActivated
from satori.domain.identity import Identity
from satori.domain.initial_self import InitialSatoriSeed, InitialSelfSnapshot, activate_from_seed

ACTIVATION_AUDIT_SCHEMA_VERSION = 1
InitialSelfUnitOfWorkFactory = Callable[[], InitialSelfUnitOfWork]


def _log_fields(**fields: object) -> dict[str, object]:
    """Build fields consumed by the structured logging formatter."""

    return {"satori_fields": fields}


@dataclass(slots=True)
class ActivateSatori:
    """Create exactly one initial Satori self in an explicit transaction."""

    unit_of_work_factory: InitialSelfUnitOfWorkFactory
    clock: Clock
    id_generator: IdGenerator
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("satori.activation"))

    def execute(self, seed: InitialSatoriSeed, *, trace_id: str) -> InitialSelfSnapshot:
        """Activate once; a repeated request raises typed AlreadyActivated."""

        self.logger.info(
            "activation_attempted",
            extra=_log_fields(seed_id=seed.seed_id, seed_schema_version=seed.schema_version),
        )
        identity_id: str | None = None
        try:
            with self.unit_of_work_factory() as unit_of_work:
                existing = unit_of_work.initial_self.get()
                if existing is not None:
                    raise AlreadyActivated(existing.identity.identity_id)

                identity_id = self.id_generator.new()
                activation_time = self.clock.now()
                snapshot = activate_from_seed(
                    seed,
                    identity_id=identity_id,
                    activation_time=activation_time,
                )
                event = ActivationAuditEvent(
                    event_id=self.id_generator.new(),
                    schema_version=ACTIVATION_AUDIT_SCHEMA_VERSION,
                    identity_id=identity_id,
                    occurred_at=activation_time,
                    trace_id=trace_id,
                    seed_provenance=snapshot.identity.seed_provenance,
                )
                if not unit_of_work.initial_self.add(snapshot, event):
                    raise AlreadyActivated()
                unit_of_work.commit()
        except AlreadyActivated as error:
            self.logger.info(
                "activation_already_completed",
                extra=_log_fields(
                    identity_id=error.identity_id,
                    seed_id=seed.seed_id,
                    seed_schema_version=seed.schema_version,
                ),
            )
            raise
        except Exception:
            self.logger.exception(
                "activation_failed",
                extra=_log_fields(
                    identity_id=identity_id,
                    seed_id=seed.seed_id,
                    seed_schema_version=seed.schema_version,
                ),
            )
            raise

        self.logger.info(
            "activation_succeeded",
            extra=_log_fields(
                identity_id=snapshot.identity.identity_id,
                seed_id=seed.seed_id,
                seed_schema_version=seed.schema_version,
            ),
        )
        return snapshot


@dataclass(frozen=True, slots=True)
class GetInitialSelfSnapshot:
    """Load the complete immutable Stage 2 read model."""

    unit_of_work_factory: InitialSelfUnitOfWorkFactory

    def execute(self) -> InitialSelfSnapshot:
        """Return the self or raise typed NotActivated without creating it."""

        with self.unit_of_work_factory() as unit_of_work:
            snapshot = unit_of_work.initial_self.get()
        if snapshot is None:
            raise NotActivated()
        return snapshot


@dataclass(frozen=True, slots=True)
class GetSatoriIdentity:
    """Load only the immutable identity view."""

    get_self: GetInitialSelfSnapshot

    def execute(self) -> Identity:
        """Return identity without exposing persistence objects."""

        return self.get_self.execute().identity
