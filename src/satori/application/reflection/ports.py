"""Application-owned ports for versioned Stage 12-14 reflection."""

from typing import Protocol

from satori.application.unit_of_work import UnitOfWork
from satori.core.personality import PersonalityChangeProposal, PersonalityStateReference
from satori.core.reflection import (
    ReflectionGenerationRequest,
    ReflectionProviderResponse,
    ReflectionPurpose,
    ReflectionSource,
)
from satori.domain.reflection import (
    ReflectionAttempt,
    ReflectionOutcome,
    ReflectionProposal,
    ReflectionRun,
    ReflectionSourceRecord,
)


class ReflectionGenerationPort(Protocol):
    async def generate_structured(
        self, request: ReflectionGenerationRequest, /
    ) -> ReflectionProviderResponse: ...


class PersonalityReflectionContextPort(Protocol):
    """Read-only Stage 14 context needed before personality-purpose inference."""

    def get_state_reference(self, identity_id: str, /) -> PersonalityStateReference | None: ...

    def list_used_root_message_ids(self, identity_id: str, /) -> frozenset[str]: ...


class PersonalityReflectionRouter(Protocol):
    """Target-owner hook; its implementation owns mutation/outcome atomicity."""

    def execute(
        self,
        identity_id: str,
        *,
        reflection_run_id: str,
        reflection_proposal_id: str,
        proposal: PersonalityChangeProposal,
        trace_id: str,
    ) -> object: ...


class ReflectionRepository(Protocol):
    def list_runs(
        self,
        *,
        identity_id: str,
        limit: int,
        purpose: ReflectionPurpose | None = None,
    ) -> tuple[ReflectionRun, ...]: ...

    def list_eligible_sources(
        self,
        *,
        identity_id: str,
        limit: int,
        purpose: ReflectionPurpose = ReflectionPurpose.GENERAL,
    ) -> tuple[ReflectionSource, ...]: ...

    def load_generation_sources(self, run_id: str) -> tuple[ReflectionSource, ...]: ...

    def get_run(self, run_id: str) -> ReflectionRun | None: ...

    def get_run_by_key(self, run_key: str) -> ReflectionRun | None: ...

    def list_sources(self, run_id: str) -> tuple[ReflectionSourceRecord, ...]: ...

    def list_attempts(self, run_id: str) -> tuple[ReflectionAttempt, ...]: ...

    def list_proposals(self, run_id: str) -> tuple[ReflectionProposal, ...]: ...

    def list_outcomes(self, run_id: str) -> tuple[ReflectionOutcome, ...]: ...

    def create_run(
        self, run: ReflectionRun, sources: tuple[ReflectionSourceRecord, ...]
    ) -> bool: ...

    def record_attempt(
        self,
        run: ReflectionRun,
        attempt: ReflectionAttempt,
        proposals: tuple[ReflectionProposal, ...],
        *,
        expected_run_version: int,
    ) -> None: ...

    def record_outcome(
        self,
        outcome: ReflectionOutcome,
        *,
        identity_id: str,
        trace_id: str,
        audit_event_id: str,
    ) -> bool: ...

    def update_run(self, run: ReflectionRun, *, expected_run_version: int) -> None: ...


class ReflectionUnitOfWork(UnitOfWork, Protocol):
    @property
    def reflection(self) -> ReflectionRepository: ...
