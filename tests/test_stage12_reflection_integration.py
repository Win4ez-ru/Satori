"""Stage 12 explicit trigger, fixed sources and owner-routing integration."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from sqlalchemy import text

from satori.application.reflection.use_cases import (
    ApplyReflectionProposals,
    ProcessReflection,
)
from satori.composition import build_conversation_services, build_initial_self_services
from satori.core.positions import PositionEvidenceRole, PositionKind, PositionStance
from satori.core.reflection import (
    ReflectionCitation,
    ReflectionGenerationRequest,
    ReflectionOwnerObservation,
    ReflectionPositionCandidate,
    ReflectionProposalDocument,
    ReflectionProviderError,
    ReflectionProviderResponse,
    ReflectionTargetOwner,
)
from satori.domain.positions import PositionFormationPlan, PositionManager
from satori.domain.reflection import (
    ReflectionOutcome,
    ReflectionRunStatus,
    ReflectionTriggerKind,
)
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.positions_uow import SQLAlchemyPositionsUnitOfWork
from satori.infrastructure.persistence.reflection_uow import SQLAlchemyReflectionUnitOfWork
from satori.infrastructure.persistence.repositories.positions import (
    SQLAlchemyPositionsRepository,
)
from tests.fakes import FrozenClock
from tests.test_stage4_conversation_memory import (
    INTERACTION_TIME,
    activate,
    conversation_provider,
    id_sequence,
    skip_episode_provider,
)
from tests.test_stage11_positions_integration import (
    FakePositionProvider,
    create_interaction,
    former,
    settings,
)


@dataclass(slots=True)
class FakeReflectionProvider:
    requests: list[ReflectionGenerationRequest] = field(default_factory=list)

    async def generate_structured(
        self, request: ReflectionGenerationRequest, /
    ) -> ReflectionProviderResponse:
        self.requests.append(request)
        citations = tuple(
            ReflectionCitation(item.source_id, PositionEvidenceRole.OBSERVATION)
            for item in request.sources[:3]
        )
        return ReflectionProviderResponse(
            document=ReflectionProposalDocument(
                schema_version=request.schema_version,
                proposals=(
                    ReflectionPositionCandidate(
                        target_owner=ReflectionTargetOwner.SATORI_POSITIONS,
                        proposition="Проверяемость оснований системно повышает качество решений",
                        kind=PositionKind.BELIEF,
                        stance=PositionStance.SUPPORT,
                        confidence=0.8,
                        evidence=citations,
                    ),
                    ReflectionOwnerObservation(
                        target_owner=ReflectionTargetOwner.PERSONALITY,
                        observation="Возможно, устойчиво усилилась осторожность",
                        evidence_source_ids=tuple(item.source_id for item in request.sources[:2]),
                    ),
                ),
            ),
            provider="fake-reflection",
            model="fixture-a",
            formation_method="fixture.reflection.v1",
        )


@dataclass(slots=True)
class ZeroReflectionProvider:
    requests: list[ReflectionGenerationRequest] = field(default_factory=list)

    async def generate_structured(
        self, request: ReflectionGenerationRequest, /
    ) -> ReflectionProviderResponse:
        self.requests.append(request)
        return ReflectionProviderResponse(
            document=ReflectionProposalDocument(
                schema_version=request.schema_version,
                proposals=(),
            ),
            provider="fake-reflection",
            model="fixture-zero",
            formation_method="fixture.reflection.v1",
        )


@dataclass(slots=True)
class FailingReflectionProvider:
    requests: list[ReflectionGenerationRequest] = field(default_factory=list)

    async def generate_structured(
        self, request: ReflectionGenerationRequest, /
    ) -> ReflectionProviderResponse:
        self.requests.append(request)
        raise ReflectionProviderError("fake-reflection", "fixture-failure", "offline")


def prepare_position_evidence(
    database: Database,
    *,
    count: int = 4,
    contents: tuple[str, ...] | None = None,
) -> str:
    snapshot = activate(database)
    position_provider = FakePositionProvider()
    position_former = former(database, position_provider)
    if contents is None:
        contents = (
            "Прозрачность важна, потому что позволяет проверить основания решения.",
            "Данные проверки показывают меньше ошибок при открытом обосновании.",
            "Наблюдение подтверждает качество, поскольку аргументы можно перепроверить.",
            "Исследование показывает лучший результат, потому что основания доступны.",
            "Данные эксперимента надёжнее, так как метод проверки опубликован.",
            "Пример ревью показывает меньше дефектов, потому что доводы видны коллегам.",
            "Наблюдения команды устойчивы, поскольку каждый результат перепроверяется.",
            "Исследование воспроизводимо, потому что исходные основания открыты.",
        )
    if len(contents) < count:
        raise ValueError("reflection evidence fixture has too few contents")
    for index, content in enumerate(contents[:count], start=1):
        interaction_id = create_interaction(
            database,
            counterparty_id="alice",
            content=content,
            prefix=f"reflection-{index}",
            day=index,
        )
        asyncio.run(position_former.execute(interaction_id, trace_id=f"position-{index}"))
    return snapshot.identity.identity_id


def test_explicit_run_accepts_position_and_rejects_disabled_owner_atomically(
    migrated_database: Database,
) -> None:
    identity_id = prepare_position_evidence(migrated_database)

    reflection_provider = FakeReflectionProvider()
    process = ProcessReflection(
        reflection_uow_factory=lambda: SQLAlchemyReflectionUnitOfWork(
            migrated_database.session_factory
        ),
        positions_uow_factory=lambda: SQLAlchemyPositionsUnitOfWork(
            migrated_database.session_factory
        ),
        provider=reflection_provider,
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=6)),
        id_generator=id_sequence("reflection-process"),
    )
    report = asyncio.run(
        process.execute(
            identity_id,
            trigger=ReflectionTriggerKind.EXPLICIT_LOCAL,
            trace_id="trace-reflection",
        )
    )
    assert report.run is not None
    assert report.run.status is ReflectionRunStatus.PROPOSALS_READY
    assert len(reflection_provider.requests) == 1
    assert len(reflection_provider.requests[0].sources) == 4

    apply = ApplyReflectionProposals(
        reflection_uow_factory=lambda: SQLAlchemyReflectionUnitOfWork(
            migrated_database.session_factory
        ),
        positions_uow_factory=lambda: SQLAlchemyPositionsUnitOfWork(
            migrated_database.session_factory
        ),
        manager=PositionManager(),
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=6, minutes=1)),
        id_generator=id_sequence("reflection-apply"),
    )
    completed = apply.execute(report.run.run_id, trace_id="trace-reflection-apply")
    replay = apply.execute(report.run.run_id, trace_id="trace-reflection-replay")
    assert completed.status is ReflectionRunStatus.COMPLETED
    assert replay == completed

    with SQLAlchemyReflectionUnitOfWork(migrated_database.session_factory) as unit:
        outcomes = unit.reflection.list_outcomes(completed.run_id)
    assert [(item.decision.value, item.reason_code) for item in outcomes] == [
        ("accepted", "position_owner_accepted"),
        ("rejected", "target_owner_not_enabled"),
    ]
    with migrated_database.engine.connect() as connection:
        reflection_revision_count = connection.execute(
            text(
                "SELECT count(*) FROM satori_position_revisions "
                "WHERE reflection_outcome_id IS NOT NULL AND decision_id IS NULL"
            )
        ).scalar_one()
        audit_count = connection.execute(
            text("SELECT count(*) FROM audit_events WHERE event_type LIKE 'reflection.%'")
        ).scalar_one()
    assert reflection_revision_count == 1
    assert audit_count == 2


def test_automatic_gate_does_not_call_provider_below_rare_trigger_threshold(
    migrated_database: Database,
) -> None:
    identity_id = prepare_position_evidence(migrated_database)
    provider = ZeroReflectionProvider()
    process = ProcessReflection(
        reflection_uow_factory=lambda: SQLAlchemyReflectionUnitOfWork(
            migrated_database.session_factory
        ),
        positions_uow_factory=lambda: SQLAlchemyPositionsUnitOfWork(
            migrated_database.session_factory
        ),
        provider=provider,
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=8)),
        id_generator=id_sequence("automatic-gate"),
    )

    report = asyncio.run(
        process.execute(
            identity_id,
            trigger=ReflectionTriggerKind.AUTOMATIC,
            trace_id="trace-automatic-gate",
        )
    )

    assert report.run is None
    assert report.reason_code == "insufficient_eligible_roots"
    assert provider.requests == []


def test_automatic_gate_calls_provider_at_exact_rare_trigger_boundary(
    migrated_database: Database,
) -> None:
    identity_id = prepare_position_evidence(migrated_database, count=8)
    provider = ZeroReflectionProvider()
    process = ProcessReflection(
        reflection_uow_factory=lambda: SQLAlchemyReflectionUnitOfWork(
            migrated_database.session_factory
        ),
        positions_uow_factory=lambda: SQLAlchemyPositionsUnitOfWork(
            migrated_database.session_factory
        ),
        provider=provider,
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=9)),
        id_generator=id_sequence("automatic-boundary"),
    )

    report = asyncio.run(
        process.execute(
            identity_id,
            trigger=ReflectionTriggerKind.AUTOMATIC,
            trace_id="trace-automatic-boundary",
        )
    )

    assert report.run is not None
    assert report.run.status is ReflectionRunStatus.COMPLETED
    assert report.reason_code == "zero_proposals_completed"
    assert len(provider.requests) == 1
    assert len(provider.requests[0].sources) == 8


def test_serial_post_response_invokes_automatic_reflection_only_after_derived_work(
    migrated_database: Database,
    caplog: pytest.LogCaptureFixture,
) -> None:
    identity_id = prepare_position_evidence(migrated_database, count=8)
    with migrated_database.engine.connect() as connection:
        interaction_id = connection.execute(
            text(
                "SELECT interaction_id FROM conversation_interactions "
                "ORDER BY started_at DESC, interaction_id DESC LIMIT 1"
            )
        ).scalar_one()
    provider = FakeReflectionProvider()
    caplog.set_level(logging.INFO, logger="satori.post_response")
    services = build_conversation_services(
        migrated_database,
        build_initial_self_services(migrated_database),
        conversation_provider("Фоновая проверка."),
        skip_episode_provider(),
        settings("alice"),
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=9)),
        id_generator=id_sequence("post-response-reflection"),
        position_provider=FakePositionProvider(),
        reflection_provider=provider,
    )

    report = asyncio.run(
        services.post_response.execute(interaction_id, trace_id="trace-post-response-reflection")
    )

    assert report.succeeded
    assert report.reflection_processing_ms >= 0
    assert len(provider.requests) == 1
    runs = services.reflections.list(identity_id=identity_id, limit=10)
    assert len(runs) == 1
    assert runs[0].status is ReflectionRunStatus.COMPLETED
    inspection = services.reflections.inspect(runs[0].run_id, identity_id=identity_id)
    assert inspection is not None
    assert [item.decision.value for item in inspection.outcomes] == ["accepted", "rejected"]
    event = next(
        item for item in caplog.records if item.message == "reflection_processing_completed"
    )
    assert vars(event)["satori_fields"]["run_status"] == "completed"


def test_post_response_reports_automatic_reflection_provider_failure(
    migrated_database: Database,
    caplog: pytest.LogCaptureFixture,
) -> None:
    identity_id = prepare_position_evidence(migrated_database, count=8)
    with migrated_database.engine.connect() as connection:
        interaction_id = connection.execute(
            text(
                "SELECT interaction_id FROM conversation_interactions "
                "ORDER BY started_at DESC, interaction_id DESC LIMIT 1"
            )
        ).scalar_one()
    provider = FailingReflectionProvider()
    caplog.set_level(logging.INFO, logger="satori.post_response")
    services = build_conversation_services(
        migrated_database,
        build_initial_self_services(migrated_database),
        conversation_provider("Фоновая проверка после отказа reflection provider."),
        skip_episode_provider(),
        settings("alice"),
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=9)),
        id_generator=id_sequence("post-response-reflection-failure"),
        position_provider=FakePositionProvider(),
        reflection_provider=provider,
    )

    report = asyncio.run(
        services.post_response.execute(
            interaction_id, trace_id="trace-post-response-reflection-failure"
        )
    )

    assert report.failure_phases == ("reflection_processing",)
    assert not report.succeeded
    assert len(provider.requests) == 1
    runs = services.reflections.list(identity_id=identity_id, limit=10)
    assert len(runs) == 1
    assert runs[0].status is ReflectionRunStatus.RETRYABLE_FAILURE
    event = next(
        item for item in caplog.records if item.message == "reflection_processing_completed"
    )
    fields = vars(event)["satori_fields"]
    assert isinstance(fields, dict)
    assert fields == {
        "purpose": "general",
        "run_id": runs[0].run_id,
        "run_status": "retryable_failure",
        "reason_code": "provider_invalid_or_unavailable",
        "created": True,
        "provider_called": True,
    }
    logged_metadata = str(fields)
    assert all(source.quote not in logged_metadata for source in provider.requests[0].sources)


def test_provider_failure_retries_same_fixed_run_once_then_exhausts(
    migrated_database: Database,
) -> None:
    identity_id = prepare_position_evidence(migrated_database)
    provider = FailingReflectionProvider()
    process = ProcessReflection(
        reflection_uow_factory=lambda: SQLAlchemyReflectionUnitOfWork(
            migrated_database.session_factory
        ),
        positions_uow_factory=lambda: SQLAlchemyPositionsUnitOfWork(
            migrated_database.session_factory
        ),
        provider=provider,
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=6)),
        id_generator=id_sequence("failure-retry"),
    )

    first = asyncio.run(
        process.execute(
            identity_id,
            trigger=ReflectionTriggerKind.EXPLICIT_LOCAL,
            trace_id="trace-failure-1",
        )
    )
    second = asyncio.run(
        process.execute(
            identity_id,
            trigger=ReflectionTriggerKind.EXPLICIT_LOCAL,
            trace_id="trace-failure-2",
        )
    )
    third = asyncio.run(
        process.execute(
            identity_id,
            trigger=ReflectionTriggerKind.EXPLICIT_LOCAL,
            trace_id="trace-failure-3",
        )
    )

    assert first.run is not None
    assert second.run is not None
    assert third.run is not None
    assert first.run.run_id == second.run.run_id == third.run.run_id
    assert first.run.source_set_hash == second.run.source_set_hash == third.run.source_set_hash
    assert first.run.status is ReflectionRunStatus.RETRYABLE_FAILURE
    assert second.run.status is ReflectionRunStatus.EXHAUSTED
    assert third.reason_code == "existing_source_set"
    assert len(provider.requests) == 2
    with SQLAlchemyReflectionUnitOfWork(migrated_database.session_factory) as unit:
        assert len(unit.reflection.list_attempts(first.run.run_id)) == 2
        assert unit.reflection.list_proposals(first.run.run_id) == ()
        assert unit.reflection.list_outcomes(first.run.run_id) == ()


def test_zero_proposal_run_completes_and_consumes_source_roots(
    migrated_database: Database,
) -> None:
    identity_id = prepare_position_evidence(migrated_database)
    provider = ZeroReflectionProvider()
    process = ProcessReflection(
        reflection_uow_factory=lambda: SQLAlchemyReflectionUnitOfWork(
            migrated_database.session_factory
        ),
        positions_uow_factory=lambda: SQLAlchemyPositionsUnitOfWork(
            migrated_database.session_factory
        ),
        provider=provider,
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=6)),
        id_generator=id_sequence("zero-proposal"),
    )

    first = asyncio.run(
        process.execute(
            identity_id,
            trigger=ReflectionTriggerKind.EXPLICIT_LOCAL,
            trace_id="trace-zero-1",
        )
    )
    second = asyncio.run(
        process.execute(
            identity_id,
            trigger=ReflectionTriggerKind.EXPLICIT_LOCAL,
            trace_id="trace-zero-2",
        )
    )

    assert first.run is not None
    assert first.run.status is ReflectionRunStatus.COMPLETED
    assert first.reason_code == "zero_proposals_completed"
    assert second.run is None
    assert second.reason_code == "insufficient_eligible_roots"
    assert len(provider.requests) == 1


def test_target_transaction_rolls_back_outcome_and_position_then_resumes(
    migrated_database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity_id = prepare_position_evidence(migrated_database)
    provider = FakeReflectionProvider()
    process = ProcessReflection(
        reflection_uow_factory=lambda: SQLAlchemyReflectionUnitOfWork(
            migrated_database.session_factory
        ),
        positions_uow_factory=lambda: SQLAlchemyPositionsUnitOfWork(
            migrated_database.session_factory
        ),
        provider=provider,
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=6)),
        id_generator=id_sequence("rollback-process"),
    )
    report = asyncio.run(
        process.execute(
            identity_id,
            trigger=ReflectionTriggerKind.EXPLICIT_LOCAL,
            trace_id="trace-rollback-process",
        )
    )
    assert report.run is not None

    original = SQLAlchemyPositionsRepository.record_reflection_decision

    def fail_after_record(
        repository: SQLAlchemyPositionsRepository,
        outcome: ReflectionOutcome,
        plan: PositionFormationPlan,
        *,
        identity_id: str,
        trace_id: str,
        audit_event_id: str,
    ) -> bool:
        original(
            repository,
            outcome,
            plan,
            identity_id=identity_id,
            trace_id=trace_id,
            audit_event_id=audit_event_id,
        )
        raise RuntimeError("controlled failure after target record")

    monkeypatch.setattr(
        SQLAlchemyPositionsRepository,
        "record_reflection_decision",
        fail_after_record,
    )
    apply = ApplyReflectionProposals(
        reflection_uow_factory=lambda: SQLAlchemyReflectionUnitOfWork(
            migrated_database.session_factory
        ),
        positions_uow_factory=lambda: SQLAlchemyPositionsUnitOfWork(
            migrated_database.session_factory
        ),
        manager=PositionManager(),
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=6, minutes=1)),
        id_generator=id_sequence("rollback-apply"),
    )
    with pytest.raises(RuntimeError, match="controlled failure"):
        apply.execute(report.run.run_id, trace_id="trace-rollback-failure")

    with SQLAlchemyReflectionUnitOfWork(migrated_database.session_factory) as unit:
        failed_run = unit.reflection.get_run(report.run.run_id)
        assert failed_run is not None
        assert failed_run.status is ReflectionRunStatus.APPLYING
        assert unit.reflection.list_outcomes(report.run.run_id) == ()
    with migrated_database.engine.connect() as connection:
        belief_count = connection.execute(
            text("SELECT count(*) FROM satori_positions WHERE kind = 'belief'")
        ).scalar_one()
    assert belief_count == 0

    monkeypatch.setattr(
        SQLAlchemyPositionsRepository,
        "record_reflection_decision",
        original,
    )
    completed = apply.execute(report.run.run_id, trace_id="trace-rollback-resume")
    assert completed.status is ReflectionRunStatus.COMPLETED
    assert len(provider.requests) == 1
    with SQLAlchemyReflectionUnitOfWork(migrated_database.session_factory) as unit:
        assert len(unit.reflection.list_outcomes(report.run.run_id)) == 2
