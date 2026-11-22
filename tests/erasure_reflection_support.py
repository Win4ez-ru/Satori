"""Managed Reflection formation before withdrawal; no real provider operations."""

import asyncio
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from satori.application.erasure.body_contracts import BodyErasureCommand, BodyErasureRequest
from satori.application.erasure.reflection_use_cases import ReflectionBodyManager
from satori.application.reflection.use_cases import ApplyReflectionProposals, ProcessReflection
from satori.core.positions import PositionEvidenceRole, PositionKind, PositionStance
from satori.core.reflection import (
    ReflectionCitation,
    ReflectionGenerationRequest,
    ReflectionPositionCandidate,
    ReflectionProposalDocument,
    ReflectionProviderResponse,
    ReflectionTargetOwner,
)
from satori.domain.positions import PositionManager
from satori.domain.reflection import ReflectionRun, ReflectionRunStatus, ReflectionTriggerKind
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure_reflection import SQLiteReflectionBodyStore
from satori.infrastructure.persistence.models import ReflectionProposalRow
from satori.infrastructure.persistence.positions_uow import SQLAlchemyPositionsUnitOfWork
from satori.infrastructure.persistence.reflection_uow import SQLAlchemyReflectionUnitOfWork
from tests.erasure_support import ErasureCase, create_case, provider_counts
from tests.fakes import FrozenClock
from tests.test_erasure_knowledge_integration import knowledge_command, prepare_knowledge
from tests.test_source_exclusion_production import SECRET, _exclude, _services, _turn
from tests.test_stage4_conversation_memory import (
    INTERACTION_TIME,
    conversation_provider,
    id_sequence,
)


@dataclass(slots=True)
class RetainedReflectionProvider:
    """One source is read but deliberately omitted from both proposals' citations."""

    requests: list[ReflectionGenerationRequest] = field(default_factory=list)

    async def generate_structured(
        self, request: ReflectionGenerationRequest, /
    ) -> ReflectionProviderResponse:
        self.requests.append(request)
        assert len(request.sources) == 4
        assert SECRET in repr(request.sources)
        # A is the first chronological root. Both bodies retain its information,
        # while their declared citation subset contains only the other roots.
        evidence = tuple(
            ReflectionCitation(source.source_id, PositionEvidenceRole.OBSERVATION)
            for source in request.sources[1:]
        )
        return ReflectionProviderResponse(
            document=ReflectionProposalDocument(
                schema_version=request.schema_version,
                proposals=tuple(
                    ReflectionPositionCandidate(
                        target_owner=ReflectionTargetOwner.SATORI_POSITIONS,
                        proposition=f"Проект {SECRET} требует проверяемых оснований: {ordinal}.",
                        kind=PositionKind.HYPOTHESIS,
                        stance=PositionStance.SUPPORT,
                        confidence=0.65,
                        evidence=evidence,
                    )
                    for ordinal in (1, 2)
                ),
            ),
            provider="fake-retained-reflection",
            model="fixture",
            formation_method="synthetic.reflection.v1",
        )


@dataclass(frozen=True, slots=True)
class ReflectionFixture:
    case: ErasureCase
    run: ReflectionRun
    proposal_ids: tuple[str, ...]
    provider: RetainedReflectionProvider


def reflection_applier(database: Database) -> ApplyReflectionProposals:
    return ApplyReflectionProposals(
        reflection_uow_factory=lambda: SQLAlchemyReflectionUnitOfWork(database.session_factory),
        positions_uow_factory=lambda: SQLAlchemyPositionsUnitOfWork(database.session_factory),
        manager=PositionManager(),
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=6, minutes=1)),
        id_generator=id_sequence("retained-apply"),
    )


def create_reflection_fixture(database: Database, directory: Path) -> ReflectionFixture:
    case = create_case(database, directory, excluded=False)
    extras = []
    for day in (3, 4):
        service, _, _ = _services(
            database,
            directory,
            f"retained-independent-{day}",
            day,
            foreground=conversation_provider("Основания можно проверить."),
        )
        _turn(
            service, f"Проверяемость оснований решения полезна, наблюдение {day}.", f"extra-{day}"
        )
        extras.append(service)
    case = replace(case, services=(*case.services, *extras))
    case = replace(case, initial_provider_calls=provider_counts(case.services))
    provider = RetainedReflectionProvider()
    process = ProcessReflection(
        reflection_uow_factory=lambda: SQLAlchemyReflectionUnitOfWork(database.session_factory),
        positions_uow_factory=lambda: SQLAlchemyPositionsUnitOfWork(database.session_factory),
        provider=provider,
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=6)),
        id_generator=id_sequence("retained-generation"),
    )
    report = asyncio.run(
        process.execute(
            case.scope.identity_id,
            trigger=ReflectionTriggerKind.EXPLICIT_LOCAL,
            trace_id="retained-generation",
        )
    )
    assert report.run is not None, report.reason_code
    run = reflection_applier(database).execute(report.run.run_id, trace_id="retained-apply")
    assert run.status is ReflectionRunStatus.COMPLETED
    with database.session_factory() as session:
        proposals = tuple(
            session.scalars(
                select(ReflectionProposalRow.proposal_id)
                .where(ReflectionProposalRow.run_id == run.run_id)
                .order_by(ReflectionProposalRow.proposal_id)
            )
        )
    assert len(proposals) == 2
    return ReflectionFixture(case, run, proposals, provider)


def prepare_reflection(
    fixture: ReflectionFixture,
) -> tuple[ReflectionBodyManager, BodyErasureRequest]:
    case = fixture.case
    _exclude(case.database, case.runtime, case.message_id)
    knowledge, request = prepare_knowledge(case)
    assert knowledge.commit(knowledge_command(knowledge, request)).main_projection_current
    case.runtime.upgrade_reflection_policy()
    return ReflectionBodyManager(case.runtime, SQLiteReflectionBodyStore(case.database)), request


def reflection_command(
    manager: ReflectionBodyManager,
    request: BodyErasureRequest,
    request_id: str = "reflection-action",
) -> BodyErasureCommand:
    preview = manager.preview(request)
    assert preview.readiness == "ready", preview.blockers
    return BodyErasureCommand(
        **request.model_dump(),
        request_id=request_id,
        expected_generation=preview.generation,
        expected_erasure_revision=preview.erasure_revision,
        expected_body_revision=preview.body_revision,
        approval_digest=preview.approval_digest,
    )
