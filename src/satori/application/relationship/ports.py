"""Application-owned ports for the relationship aggregate."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from satori.application.unit_of_work import UnitOfWork
from satori.domain.relationship import (
    RelationshipDecision,
    RelationshipDelta,
    RelationshipState,
    RelationshipTransition,
)


@dataclass(frozen=True, slots=True)
class RelationshipSource:
    """Canonical root evidence; assistant/provider/retrieval data is absent by design."""

    interaction_id: str
    user_message_id: str
    user_content: str
    session_id: str
    identity_id: str
    counterparty_id: str
    trace_id: str
    started_at: datetime
    completed_at: datetime
    processing_required: bool


class RelationshipRepository(Protocol):
    def get_state(self, identity_id: str, counterparty_id: str) -> RelationshipState | None: ...

    def add_initial_state(self, state: RelationshipState) -> bool: ...

    def get_source(self, interaction_id: str) -> RelationshipSource | None: ...

    def get_counterparty_for_session(self, session_id: str) -> tuple[str, str] | None: ...

    def get_decision(self, interaction_id: str) -> RelationshipDecision | None: ...

    def has_earlier_undecided_source(self, source: RelationshipSource) -> bool: ...

    def session_delta(self, relationship_id: str, session_id: str) -> RelationshipDelta: ...

    def session_has_qualified_evidence(self, relationship_id: str, session_id: str) -> bool: ...

    def record(
        self,
        *,
        decision: RelationshipDecision,
        before: RelationshipState,
        after: RelationshipState,
        transition: RelationshipTransition | None,
        audit_event_id: str,
    ) -> bool: ...

    def list_transitions(
        self, relationship_id: str, *, limit: int | None = None
    ) -> Sequence[RelationshipTransition]: ...

    def list_unprocessed_source_ids(
        self, identity_id: str, counterparty_id: str, *, limit: int
    ) -> Sequence[str]: ...


class RelationshipUnitOfWork(UnitOfWork, Protocol):
    @property
    def relationship(self) -> RelationshipRepository: ...
