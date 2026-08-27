"""Focused Stage 14 Reflection V3 runtime gates without persistence/provider daemons."""

import asyncio
import hashlib
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from types import TracebackType

from satori.application.positions.ports import PositionsUnitOfWork
from satori.application.reflection.ports import ReflectionGenerationPort
from satori.application.reflection.use_cases import ProcessReflection
from satori.core.personality import (
    PersonalityCitationRole,
    PersonalityDirection,
    PersonalityStateReference,
)
from satori.core.reflection import (
    ReflectionGenerationRequest,
    ReflectionLineageKind,
    ReflectionPersonalityCandidate,
    ReflectionPersonalityCitation,
    ReflectionProposalDocument,
    ReflectionProviderResponse,
    ReflectionPurpose,
    ReflectionSource,
    ReflectionSourceKind,
    ReflectionTargetOwner,
)
from satori.domain.personality_evolution import PERSONALITY_EVIDENCE_RESERVOIR_LIMIT
from satori.domain.reflection import (
    ReflectionAttempt,
    ReflectionOutcome,
    ReflectionProposal,
    ReflectionRun,
    ReflectionRunStatus,
    ReflectionSourceRecord,
    ReflectionTriggerKind,
)
from tests.fakes import FrozenClock, SequenceIdGenerator

NOW = datetime(2026, 4, 10, tzinfo=UTC)


def _sources() -> tuple[ReflectionSource, ...]:
    quotes = (
        "The raw data was checked again after an unexpected discrepancy appeared.",
        "A new study prompted a precise question about the method boundaries.",
        "Alternative designs were compared through independently testable assumptions.",
        "A counterexample led to a calm revision of the initial working hypothesis.",
        "The review clearly separated the observation from its later interpretation.",
        "Several independent primary sources were gathered for an unfamiliar domain.",
        "After an error, the criterion was refined and the calculation was repeated.",
        "The long experiment ended with an explicit account of inference limitations.",
    )
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return tuple(
        ReflectionSource(
            source_id=f"candidate-{index}",
            kind=ReflectionSourceKind.POSITION_EVIDENCE,
            evidence_edge_id=f"edge-{index}",
            evidence_edge_version=1,
            root_interaction_id=f"interaction-{index}",
            root_message_id=f"message-{index}",
            root_counterparty_id="counterparty-1",
            observed_at=start + timedelta(days=index * 14),
            content_hash=hashlib.sha256(quotes[index].encode()).hexdigest(),
            quote=quotes[index],
            root_session_id=f"session-{index}",
            upstream_lineage_kind=ReflectionLineageKind.POSITION,
            upstream_lineage_id=f"position-{index // 2}",
        )
        for index in range(8)
    )


@dataclass(slots=True)
class _Repository:
    candidates: tuple[ReflectionSource, ...]
    runs: dict[str, ReflectionRun] = field(default_factory=dict)
    sources: dict[str, tuple[ReflectionSourceRecord, ...]] = field(default_factory=dict)
    attempts: dict[str, tuple[ReflectionAttempt, ...]] = field(default_factory=dict)
    proposals: dict[str, tuple[ReflectionProposal, ...]] = field(default_factory=dict)
    requested_limits: list[int] = field(default_factory=list)
    returned_source_ids: tuple[str, ...] = ()

    def list_runs(
        self,
        *,
        identity_id: str,
        limit: int,
        purpose: ReflectionPurpose | None = None,
    ) -> tuple[ReflectionRun, ...]:
        matching = tuple(
            item
            for item in self.runs.values()
            if item.identity_id == identity_id and (purpose is None or item.purpose is purpose)
        )
        return tuple(sorted(matching, key=lambda item: item.created_at, reverse=True)[:limit])

    def list_eligible_sources(
        self,
        *,
        identity_id: str,
        limit: int,
        purpose: ReflectionPurpose = ReflectionPurpose.GENERAL,
    ) -> tuple[ReflectionSource, ...]:
        assert identity_id == "identity-1"
        assert purpose is ReflectionPurpose.PERSONALITY_EVOLUTION
        self.requested_limits.append(limit)
        result = self.candidates[:limit]
        self.returned_source_ids = tuple(item.source_id for item in result)
        return result

    def load_generation_sources(self, run_id: str) -> tuple[ReflectionSource, ...]:
        candidate_by_edge = {item.evidence_edge_id: item for item in self.candidates}
        return tuple(
            replace(
                candidate_by_edge[item.evidence_edge_id],
                source_id=item.source_id,
            )
            for item in self.sources.get(run_id, ())
        )

    def get_run(self, run_id: str) -> ReflectionRun | None:
        return self.runs.get(run_id)

    def get_run_by_key(self, run_key: str) -> ReflectionRun | None:
        return next((item for item in self.runs.values() if item.run_key == run_key), None)

    def list_sources(self, run_id: str) -> tuple[ReflectionSourceRecord, ...]:
        return self.sources.get(run_id, ())

    def list_attempts(self, run_id: str) -> tuple[ReflectionAttempt, ...]:
        return self.attempts.get(run_id, ())

    def list_proposals(self, run_id: str) -> tuple[ReflectionProposal, ...]:
        return self.proposals.get(run_id, ())

    def list_outcomes(self, run_id: str) -> tuple[ReflectionOutcome, ...]:
        return ()

    def create_run(
        self,
        run: ReflectionRun,
        sources: tuple[ReflectionSourceRecord, ...],
    ) -> bool:
        if self.get_run_by_key(run.run_key) is not None:
            return False
        self.runs[run.run_id] = run
        self.sources[run.run_id] = sources
        return True

    def record_attempt(
        self,
        run: ReflectionRun,
        attempt: ReflectionAttempt,
        proposals: tuple[ReflectionProposal, ...],
        *,
        expected_run_version: int,
    ) -> None:
        assert run.aggregate_version == expected_run_version + 1
        self.runs[run.run_id] = run
        self.attempts[run.run_id] = (*self.attempts.get(run.run_id, ()), attempt)
        self.proposals[run.run_id] = proposals

    def record_outcome(
        self,
        outcome: ReflectionOutcome,
        *,
        identity_id: str,
        trace_id: str,
        audit_event_id: str,
    ) -> bool:
        raise AssertionError("zero-proposal V3 run cannot record an outcome")

    def update_run(self, run: ReflectionRun, *, expected_run_version: int) -> None:
        assert run.aggregate_version == expected_run_version + 1
        self.runs[run.run_id] = run


@dataclass(slots=True)
class _UnitOfWork:
    reflection: _Repository

    def __enter__(self) -> "_UnitOfWork":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class _PersonalityContext:
    def get_state_reference(self, identity_id: str, /) -> PersonalityStateReference | None:
        return PersonalityStateReference(identity_id=identity_id, aggregate_version=7)

    def list_used_root_message_ids(self, identity_id: str, /) -> frozenset[str]:
        assert identity_id == "identity-1"
        return frozenset()


@dataclass(slots=True)
class _ZeroProvider:
    requests: list[ReflectionGenerationRequest] = field(default_factory=list)

    async def generate_structured(
        self,
        request: ReflectionGenerationRequest,
        /,
    ) -> ReflectionProviderResponse:
        self.requests.append(request)
        return ReflectionProviderResponse(
            document=ReflectionProposalDocument(schema_version=3, proposals=()),
            provider="fake-reflection",
            model="fixture-v3",
            formation_method="fixture.reflection.v3",
        )


@dataclass(slots=True)
class _DirectionProvider:
    provider_name: str
    model_name: str
    requests: list[ReflectionGenerationRequest] = field(default_factory=list)

    async def generate_structured(
        self,
        request: ReflectionGenerationRequest,
        /,
    ) -> ReflectionProviderResponse:
        self.requests.append(request)
        assert request.personality_state is not None
        candidate = ReflectionPersonalityCandidate(
            target_owner=ReflectionTargetOwner.PERSONALITY,
            trait_key="curiosity",
            direction=PersonalityDirection.INCREASE,
            confidence=0.9,
            citations=tuple(
                ReflectionPersonalityCitation(
                    source_id=source.source_id,
                    role=PersonalityCitationRole.SUPPORT,
                )
                for source in request.sources
            ),
            expected_personality_version=request.personality_state.aggregate_version,
        )
        return ReflectionProviderResponse(
            document=ReflectionProposalDocument(schema_version=3, proposals=(candidate,)),
            provider=self.provider_name,
            model=self.model_name,
            formation_method="fixture.reflection.v3",
        )


def _unreachable_positions_uow() -> PositionsUnitOfWork:
    raise AssertionError("Reflection V3 must not read general position/value/inclination state")


def _process(
    repository: _Repository,
    provider: ReflectionGenerationPort,
) -> ProcessReflection:
    return ProcessReflection(
        reflection_uow_factory=lambda: _UnitOfWork(repository),
        positions_uow_factory=_unreachable_positions_uow,
        provider=provider,
        clock=FrozenClock(NOW),
        id_generator=SequenceIdGenerator("attempt-1"),
        personality_context=_PersonalityContext(),
    )


def test_replaceable_v3_providers_persist_the_same_typed_owner_proposal() -> None:
    repository_a = _Repository(candidates=_sources())
    repository_b = _Repository(candidates=_sources())
    provider_a = _DirectionProvider("provider-a", "model-a")
    provider_b = _DirectionProvider("provider-b", "model-b")

    report_a = asyncio.run(
        _process(repository_a, provider_a).execute(
            "identity-1",
            trigger=ReflectionTriggerKind.EXPLICIT_LOCAL,
            trace_id="trace-provider-a",
            purpose=ReflectionPurpose.PERSONALITY_EVOLUTION,
        )
    )
    report_b = asyncio.run(
        _process(repository_b, provider_b).execute(
            "identity-1",
            trigger=ReflectionTriggerKind.EXPLICIT_LOCAL,
            trace_id="trace-provider-b",
            purpose=ReflectionPurpose.PERSONALITY_EVOLUTION,
        )
    )

    assert report_a.run is not None
    assert report_b.run is not None
    assert report_a.run.status is report_b.run.status is ReflectionRunStatus.PROPOSALS_READY
    proposals_a = repository_a.list_proposals(report_a.run.run_id)
    proposals_b = repository_b.list_proposals(report_b.run.run_id)
    assert proposals_a == proposals_b
    assert len(proposals_a) == 1
    assert proposals_a[0].target_owner is ReflectionTargetOwner.PERSONALITY
    assert proposals_a[0].payload == {
        "target_owner": "personality",
        "trait_key": "curiosity",
        "direction": "increase",
        "confidence": 0.9,
        "citations": [
            {"source_id": source.source_id, "role": "support"}
            for source in provider_a.requests[0].sources
        ],
        "expected_personality_version": 7,
    }
    attempt_a = repository_a.list_attempts(report_a.run.run_id)[0]
    attempt_b = repository_b.list_attempts(report_b.run.run_id)[0]
    assert (attempt_a.provider, attempt_a.model) == ("provider-a", "model-a")
    assert (attempt_b.provider, attempt_b.model) == ("provider-b", "model-b")


def test_v3_gate_prevents_provider_call_before_exact_longitudinal_diversity() -> None:
    repository = _Repository(candidates=_sources()[:7])
    provider = _ZeroProvider()

    report = asyncio.run(
        _process(repository, provider).execute(
            "identity-1",
            trigger=ReflectionTriggerKind.EXPLICIT_LOCAL,
            trace_id="trace-ineligible-v3",
            purpose=ReflectionPurpose.PERSONALITY_EVOLUTION,
        )
    )

    assert report.run is None
    assert report.reason_code == "personality_observation_span_too_short"
    assert provider.requests == []
    assert repository.runs == {}


def test_v3_eligible_run_uses_separate_purpose_and_no_general_target_state() -> None:
    repository = _Repository(candidates=_sources())
    provider = _ZeroProvider()

    report = asyncio.run(
        _process(repository, provider).execute(
            "identity-1",
            trigger=ReflectionTriggerKind.EXPLICIT_LOCAL,
            trace_id="trace-eligible-v3",
            purpose=ReflectionPurpose.PERSONALITY_EVOLUTION,
        )
    )

    assert report.run is not None
    assert report.run.status is ReflectionRunStatus.COMPLETED
    assert report.run.purpose is ReflectionPurpose.PERSONALITY_EVOLUTION
    assert (report.run.schema_version, report.run.policy_version) == (3, 3)
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.max_proposals == 1
    assert request.personality_state is not None
    assert request.personality_state.aggregate_version == 7
    assert request.current_positions == ()
    assert request.values == ()
    assert request.current_inclinations == ()
    assert all(item.affective is None for item in request.sources)
    assert all(item.upstream_lineage_id is not None for item in request.sources)


def _prior_run(
    *,
    status: ReflectionRunStatus,
    created_at: datetime,
    completed_at: datetime | None,
) -> ReflectionRun:
    return ReflectionRun(
        run_id=f"prior-{status.value}",
        run_key=f"prior-key-{status.value}",
        identity_id="identity-1",
        schema_version=3,
        policy_version=3,
        trigger_kind=ReflectionTriggerKind.AUTOMATIC,
        source_set_hash="a" * 64,
        status=status,
        aggregate_version=2,
        attempt_count=1,
        created_at=created_at,
        updated_at=completed_at or created_at,
        completed_at=completed_at,
        purpose=ReflectionPurpose.PERSONALITY_EVOLUTION,
    )


def test_v3_daily_cap_applies_to_explicit_new_runs_and_precedes_provider() -> None:
    recent = _prior_run(
        status=ReflectionRunStatus.EXHAUSTED,
        created_at=NOW - timedelta(hours=12),
        completed_at=None,
    )
    repository = _Repository(candidates=_sources(), runs={recent.run_id: recent})
    provider = _ZeroProvider()

    report = asyncio.run(
        _process(repository, provider).execute(
            "identity-1",
            trigger=ReflectionTriggerKind.EXPLICIT_LOCAL,
            trace_id="trace-daily-cap-v3",
            purpose=ReflectionPurpose.PERSONALITY_EVOLUTION,
        )
    )

    assert report.reason_code == "personality_rolling_daily_cap"
    assert provider.requests == []


def test_v3_automatic_cooldown_is_waived_only_for_explicit_processing() -> None:
    completed = _prior_run(
        status=ReflectionRunStatus.COMPLETED,
        created_at=NOW - timedelta(days=11),
        completed_at=NOW - timedelta(days=10),
    )
    automatic_repository = _Repository(
        candidates=_sources(),
        runs={completed.run_id: completed},
    )
    automatic_provider = _ZeroProvider()

    automatic = asyncio.run(
        _process(automatic_repository, automatic_provider).execute(
            "identity-1",
            trigger=ReflectionTriggerKind.AUTOMATIC,
            trace_id="trace-cooldown-v3",
            purpose=ReflectionPurpose.PERSONALITY_EVOLUTION,
        )
    )

    assert automatic.reason_code == "personality_reflection_cooldown"
    assert automatic_provider.requests == []

    explicit_repository = _Repository(
        candidates=_sources(),
        runs={completed.run_id: completed},
    )
    explicit_provider = _ZeroProvider()
    explicit = asyncio.run(
        _process(explicit_repository, explicit_provider).execute(
            "identity-1",
            trigger=ReflectionTriggerKind.EXPLICIT_LOCAL,
            trace_id="trace-explicit-waiver-v3",
            purpose=ReflectionPurpose.PERSONALITY_EVOLUTION,
        )
    )

    assert explicit.run is not None
    assert explicit.run.status is ReflectionRunStatus.COMPLETED
    assert len(explicit_provider.requests) == 1


def test_v3_reservoir_boundary_reads_256_and_never_materializes_candidate_257() -> None:
    base = _sources()
    extras: list[ReflectionSource] = []
    for index in range(8, 257):
        quote = f"Independent canonical observation with distinct marker number {index}."
        extras.append(
            replace(
                base[-1],
                source_id=f"candidate-{index}",
                evidence_edge_id=f"edge-{index}",
                root_interaction_id=f"interaction-{index}",
                root_message_id=f"message-{index}",
                root_session_id=f"session-{index}",
                upstream_lineage_id=f"position-{index}",
                observed_at=NOW,
                content_hash=hashlib.sha256(quote.encode()).hexdigest(),
                quote=quote,
            )
        )
    candidates = (*base, *extras)
    assert len(candidates) == PERSONALITY_EVIDENCE_RESERVOIR_LIMIT + 1
    repository = _Repository(candidates=candidates)
    provider = _ZeroProvider()

    report = asyncio.run(
        _process(repository, provider).execute(
            "identity-1",
            trigger=ReflectionTriggerKind.EXPLICIT_LOCAL,
            trace_id="trace-reservoir-boundary-v3",
            purpose=ReflectionPurpose.PERSONALITY_EVOLUTION,
        )
    )

    assert report.run is not None
    assert repository.requested_limits == [PERSONALITY_EVIDENCE_RESERVOIR_LIMIT]
    assert len(repository.returned_source_ids) == PERSONALITY_EVIDENCE_RESERVOIR_LIMIT
    assert "candidate-255" in repository.returned_source_ids
    assert "candidate-256" not in repository.returned_source_ids


def test_v3_future_run_timestamps_fail_closed_before_provider() -> None:
    future_created = _prior_run(
        status=ReflectionRunStatus.EXHAUSTED,
        created_at=NOW + timedelta(microseconds=1),
        completed_at=None,
    )
    future_completed = _prior_run(
        status=ReflectionRunStatus.COMPLETED,
        created_at=NOW - timedelta(days=40),
        completed_at=NOW + timedelta(microseconds=1),
    )

    for prior in (future_created, future_completed):
        repository = _Repository(candidates=_sources(), runs={prior.run_id: prior})
        provider = _ZeroProvider()
        report = asyncio.run(
            _process(repository, provider).execute(
                "identity-1",
                trigger=ReflectionTriggerKind.EXPLICIT_LOCAL,
                trace_id=f"trace-future-{prior.status.value}",
                purpose=ReflectionPurpose.PERSONALITY_EVOLUTION,
            )
        )

        assert report.run is None
        assert report.reason_code == "personality_run_timestamp_from_future"
        assert provider.requests == []
