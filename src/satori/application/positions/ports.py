"""Application-owned persistence ports for Stage 11 positions and Stage 13 inclinations."""

from typing import Protocol

from satori.application.unit_of_work import UnitOfWork
from satori.core.inclinations import InclinationStateReference
from satori.core.positions import PositionSourceMessage, PositionValueReference
from satori.domain.inclinations import InclinationEvaluation, SatoriInclination
from satori.domain.positions import (
    PositionFormationDecision,
    PositionFormationPlan,
    PositionRevision,
    SatoriPosition,
)
from satori.domain.reflection import ReflectionOutcome


class PositionsRepository(Protocol):
    def get_decision(self, idempotency_key: str) -> PositionFormationDecision | None: ...

    def get_source_messages(
        self, source_interaction_id: str, *, limit: int
    ) -> tuple[PositionSourceMessage, ...]: ...

    def get_value_references(self, identity_id: str) -> tuple[PositionValueReference, ...]: ...

    def list_positions(
        self, *, identity_id: str, current_only: bool = False
    ) -> tuple[SatoriPosition, ...]: ...

    def get_position(self, position_id: str) -> SatoriPosition | None: ...

    def list_inclination_references(
        self, *, identity_id: str
    ) -> tuple[InclinationStateReference, ...]: ...

    def list_inclinations(self, *, identity_id: str) -> tuple[SatoriInclination, ...]: ...

    def get_inclination(self, inclination_id: str) -> SatoriInclination | None: ...

    def list_revisions(self, position_id: str) -> tuple[PositionRevision, ...]: ...

    def list_unprocessed_interaction_ids(self, *, limit: int) -> tuple[str, ...]: ...

    def record_decision(
        self,
        decision: PositionFormationDecision,
        plan: PositionFormationPlan,
        *,
        audit_event_id: str,
    ) -> bool: ...

    def record_reflection_decision(
        self,
        outcome: ReflectionOutcome,
        plan: PositionFormationPlan,
        *,
        identity_id: str,
        trace_id: str,
        audit_event_id: str,
    ) -> bool: ...

    def record_inclination_reflection_decision(
        self,
        outcome: ReflectionOutcome,
        evaluation: InclinationEvaluation,
        *,
        identity_id: str,
        trace_id: str,
        audit_event_id: str,
    ) -> bool: ...


class PositionsUnitOfWork(UnitOfWork, Protocol):
    @property
    def positions(self) -> PositionsRepository: ...
