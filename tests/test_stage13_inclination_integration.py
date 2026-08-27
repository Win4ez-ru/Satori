"""Stage 13 SQLite lifecycle coverage for reflection-owned inclinations."""

# ruff: noqa: RUF001  # Russian policy fixtures intentionally use Cyrillic.

import asyncio
import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from satori.application.positions.use_cases import GetSatoriPositions
from satori.application.reflection.use_cases import (
    ApplyReflectionProposals,
    ProcessReflection,
)
from satori.core.inclinations import InclinationKind
from satori.core.reflection import (
    ReflectionGenerationRequest,
    ReflectionInclinationCandidate,
    ReflectionProposalDocument,
    ReflectionProviderResponse,
    ReflectionSource,
    ReflectionTargetOwner,
)
from satori.domain.inclinations import (
    InclinationEvaluation,
    materialize_inclination_score,
)
from satori.domain.positions import PositionManager
from satori.domain.reflection import (
    REFLECTION_POLICY_VERSION,
    REFLECTION_POLICY_VERSION_V1,
    REFLECTION_SCHEMA_VERSION,
    REFLECTION_SCHEMA_VERSION_V1,
    ReflectionAttempt,
    ReflectionAttemptStatus,
    ReflectionOutcome,
    ReflectionRun,
    ReflectionRunStatus,
    ReflectionSourceRecord,
    ReflectionTriggerKind,
    reflection_run_key,
    source_set_hash,
)
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.models.affect import AffectiveTransitionRow
from satori.infrastructure.persistence.models.conversation import (
    ConversationInteractionRow,
    ConversationMessageRow,
    ConversationSessionRow,
)
from satori.infrastructure.persistence.models.positions import (
    InclinationEvidenceRow,
    InclinationRevisionRow,
    PositionEvidenceRow,
    SatoriInclinationRow,
    SatoriPositionRow,
)
from satori.infrastructure.persistence.models.reflection import ReflectionSourceRow
from satori.infrastructure.persistence.positions_uow import SQLAlchemyPositionsUnitOfWork
from satori.infrastructure.persistence.reflection_uow import SQLAlchemyReflectionUnitOfWork
from satori.infrastructure.persistence.repositories.positions import (
    SQLAlchemyPositionsRepository,
)
from tests.fakes import FakeAffectiveAppraisalProvider, FrozenClock
from tests.test_stage4_conversation_memory import (
    INTERACTION_TIME,
    activate,
    conversation_provider,
    id_sequence,
)
from tests.test_stage7_affect_integration import (
    build as build_affective_conversation,
)
from tests.test_stage7_affect_integration import proposal_for, run_talk
from tests.test_stage11_positions_integration import create_interaction

INTEREST_CONTENTS = (
    "Архитектура помогает исследовать ясные границы модулей.",
    "Архитектура интересна явными зависимостями компонентов.",
    "Архитектура раскрывается через проверяемые контракты подсистем.",
    "Архитектура позволяет замечать устойчивые схемы взаимодействия.",
)
DIRECT_ASSIGNMENT_CONTENTS = (
    "Мне нравится архитектура с ясными границами модулей.",
    "Мне интересна архитектура с явными зависимостями компонентов.",
    "Я люблю архитектуру с проверяемыми контрактами подсистем.",
    "Я предпочитаю архитектуру с устойчивыми схемами взаимодействия.",
)
SOURCE_DAYS = (1, 4, 8, 9)
ASSISTANT_REPLY = "Я услышала этот архитектурный пример."


@dataclass(slots=True)
class InterestReflectionProvider:
    """Return one V2 interest candidate citing only affect-attached sources."""

    requests: list[ReflectionGenerationRequest] = field(default_factory=list)

    async def generate_structured(
        self, request: ReflectionGenerationRequest, /
    ) -> ReflectionProviderResponse:
        self.requests.append(request)
        attached = tuple(item for item in request.sources if item.affective is not None)
        if len(attached) < 3:
            raise AssertionError("fixture requires three affect-attached sources")
        return ReflectionProviderResponse(
            document=ReflectionProposalDocument(
                schema_version=request.schema_version,
                proposals=(
                    ReflectionInclinationCandidate(
                        target_owner=ReflectionTargetOwner.SATORI_INCLINATIONS,
                        kind=InclinationKind.INTEREST,
                        topic="архитектура",
                        alternative_topic=None,
                        confidence=0.9,
                        source_ids=tuple(item.source_id for item in attached[:3]),
                    ),
                ),
            ),
            provider="fake-reflection",
            model="fixture-inclination",
            formation_method="fixture.reflection.v2",
        )


@dataclass(slots=True)
class V1ZeroReflectionProvider:
    """Capture a legacy request and complete it without proposals."""

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
            model="fixture-v1",
            formation_method="fixture.reflection.v1",
        )


def _create_affective_interactions(
    database: Database,
    *,
    contents: tuple[str, ...],
) -> tuple[str, tuple[str, ...], FakeAffectiveAppraisalProvider]:
    snapshot = activate(database)
    appraisal = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: proposal_for(
            request.interaction_id,
            salience=0.95,
        )
    )
    interaction_ids: list[str] = []
    for index, (content, day) in enumerate(zip(contents, SOURCE_DAYS, strict=True), start=1):
        services, _, _ = build_affective_conversation(
            database,
            appraisal,
            clock=FrozenClock(INTERACTION_TIME + timedelta(days=day)),
            conversation=conversation_provider(ASSISTANT_REPLY),
        )
        reply = run_talk(
            services,
            request_id=f"stage13-inclination-{index}",
            text_value=content,
        )
        assert reply.context_manifest.emotion_appraisal_status == "applied"
        interaction_ids.append(reply.interaction_id)
    assert len(appraisal.requests) == len(contents)
    return snapshot.identity.identity_id, tuple(interaction_ids), appraisal


def _add_position_sources(
    database: Database,
    *,
    identity_id: str,
    interaction_ids: tuple[str, ...],
    position_id: str = "stage13-inclination-source-position",
) -> dict[str, tuple[str, str, str]]:
    """Expose canonical user roots through the existing reflection source query."""

    roots: dict[str, tuple[str, str, str]] = {}
    with database.session_factory() as session:
        existing = session.get(SatoriPositionRow, position_id)
        pending: list[tuple[str, ConversationMessageRow, ConversationSessionRow]] = []
        for interaction_id in interaction_ids:
            interaction = session.get(ConversationInteractionRow, interaction_id)
            assert interaction is not None
            conversation_session = session.get(ConversationSessionRow, interaction.session_id)
            assert conversation_session is not None
            user_message = session.scalar(
                select(ConversationMessageRow).where(
                    ConversationMessageRow.interaction_id == interaction_id,
                    ConversationMessageRow.role == "user",
                )
            )
            assert user_message is not None
            pending.append((interaction_id, user_message, conversation_session))
            roots[interaction_id] = (
                user_message.message_id,
                conversation_session.session_id,
                user_message.content,
            )
        if existing is None:
            created_at = min(item[1].created_at for item in pending)
            session.add(
                SatoriPositionRow(
                    position_id=position_id,
                    position_key=hashlib.sha256(position_id.encode()).hexdigest(),
                    identity_id=identity_id,
                    schema_version=1,
                    aggregate_version=1,
                    policy_version=1,
                    formation_version=1,
                    normalization_version=1,
                    proposition="Ясные архитектурные границы полезны.",
                    normalized_proposition="ясные архитектурные границы полезны",
                    kind="belief",
                    stance="support",
                    confidence=0.7,
                    status="active",
                    value_key=None,
                    competing_with_position_id=None,
                    superseded_by_position_id=None,
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
            session.flush()
        for interaction_id, user_message, conversation_session in pending:
            signature = hashlib.sha256(user_message.content.encode()).hexdigest()
            session.add(
                PositionEvidenceRow(
                    evidence_id=f"stage13-position-evidence-{signature[:32]}",
                    position_id=position_id,
                    source_message_id=user_message.message_id,
                    source_interaction_id=interaction_id,
                    source_counterparty_id=conversation_session.counterparty_id,
                    quote=user_message.content,
                    normalized_signature=signature,
                    role="observation",
                    observed_at=user_message.created_at,
                )
            )
        session.commit()
    return roots


def _prepare_sources(
    database: Database,
    *,
    contents: tuple[str, ...] = INTEREST_CONTENTS,
    include_unaffected: bool = False,
) -> tuple[str, tuple[str, ...], dict[str, tuple[str, str, str]]]:
    identity_id, interaction_ids, _ = _create_affective_interactions(
        database,
        contents=contents,
    )
    all_interactions = list(interaction_ids)
    if include_unaffected:
        all_interactions.append(
            create_interaction(
                database,
                counterparty_id="alice",
                content="Архитектура без committed affect не даёт сигнала склонности.",
                prefix="stage13-no-affect",
                day=10,
            )
        )
    roots = _add_position_sources(
        database,
        identity_id=identity_id,
        interaction_ids=tuple(all_interactions),
    )
    return identity_id, tuple(all_interactions), roots


def _process_interest(
    database: Database,
    *,
    identity_id: str,
    provider: InterestReflectionProvider,
) -> ReflectionRun:
    process = ProcessReflection(
        reflection_uow_factory=lambda: SQLAlchemyReflectionUnitOfWork(database.session_factory),
        positions_uow_factory=lambda: SQLAlchemyPositionsUnitOfWork(database.session_factory),
        provider=provider,
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=10)),
        id_generator=id_sequence("stage13-reflection-process"),
    )
    report = asyncio.run(
        process.execute(
            identity_id,
            trigger=ReflectionTriggerKind.EXPLICIT_LOCAL,
            trace_id="trace-stage13-reflection-process",
        )
    )
    assert report.run is not None
    assert report.run.status is ReflectionRunStatus.PROPOSALS_READY
    return report.run


def _apply_interest(database: Database) -> ApplyReflectionProposals:
    return ApplyReflectionProposals(
        reflection_uow_factory=lambda: SQLAlchemyReflectionUnitOfWork(database.session_factory),
        positions_uow_factory=lambda: SQLAlchemyPositionsUnitOfWork(database.session_factory),
        manager=PositionManager(),
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=10, minutes=1)),
        id_generator=id_sequence("stage13-reflection-apply"),
    )


def _assert_exact_affect_provenance(
    database: Database,
    *,
    identity_id: str,
    sources: tuple[ReflectionSource, ...],
    roots: dict[str, tuple[str, str, str]],
) -> None:
    with database.session_factory() as session:
        for source in sources:
            expected_message_id, expected_session_id, _ = roots[source.root_interaction_id]
            assert source.root_message_id == expected_message_id
            assert source.root_session_id == expected_session_id
            message = session.get(ConversationMessageRow, source.root_message_id)
            interaction = session.get(
                ConversationInteractionRow,
                source.root_interaction_id,
            )
            conversation_session = session.get(
                ConversationSessionRow,
                expected_session_id,
            )
            assert message is not None
            assert message.role == "user"
            assert interaction is not None
            assert interaction.session_id == expected_session_id
            assert conversation_session is not None
            assert conversation_session.identity_id == identity_id
            transition = session.scalar(
                select(AffectiveTransitionRow).where(
                    AffectiveTransitionRow.interaction_id == source.root_interaction_id
                )
            )
            if source.affective is None:
                assert transition is None
                continue
            assert transition is not None
            assert transition.committed_at is not None
            assert transition.identity_id == identity_id
            assert transition.interaction_id == source.root_interaction_id
            assert transition.source_message_id == source.root_message_id
            assert transition.transition_id == source.affective.transition_id
            assert transition.resulting_state_version == source.affective.resulting_state_version


def test_v2_committed_affect_apply_rollback_replay_restart_and_future_sources(
    migrated_database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity_id, interaction_ids, roots = _prepare_sources(
        migrated_database,
        include_unaffected=True,
    )
    with SQLAlchemyReflectionUnitOfWork(migrated_database.session_factory) as unit:
        candidates = unit.reflection.list_eligible_sources(identity_id=identity_id, limit=12)
    assert len(candidates) == 5
    assert sum(item.affective is not None for item in candidates) == 4
    assert len({item.root_session_id for item in candidates}) == 5
    _assert_exact_affect_provenance(
        migrated_database,
        identity_id=identity_id,
        sources=candidates,
        roots=roots,
    )

    provider = InterestReflectionProvider()
    run = _process_interest(
        migrated_database,
        identity_id=identity_id,
        provider=provider,
    )
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.schema_version == REFLECTION_SCHEMA_VERSION == 2
    assert request.policy_version == REFLECTION_POLICY_VERSION == 2
    cited = tuple(item for item in request.sources if item.affective is not None)[:3]
    assert len(cited) == 3
    assert len({item.root_session_id for item in cited}) == 3
    assert max(item.observed_at for item in cited) - min(
        item.observed_at for item in cited
    ) >= timedelta(days=7)

    original = SQLAlchemyPositionsRepository.record_inclination_reflection_decision

    def fail_after_owner_record(
        repository: SQLAlchemyPositionsRepository,
        outcome: ReflectionOutcome,
        evaluation: InclinationEvaluation,
        *,
        identity_id: str,
        trace_id: str,
        audit_event_id: str,
    ) -> bool:
        persisted = original(
            repository,
            outcome,
            evaluation,
            identity_id=identity_id,
            trace_id=trace_id,
            audit_event_id=audit_event_id,
        )
        assert persisted
        raise RuntimeError("controlled failure after inclination owner record")

    monkeypatch.setattr(
        SQLAlchemyPositionsRepository,
        "record_inclination_reflection_decision",
        fail_after_owner_record,
    )
    apply = _apply_interest(migrated_database)
    with pytest.raises(RuntimeError, match="controlled failure"):
        apply.execute(run.run_id, trace_id="trace-stage13-apply-failure")
    with migrated_database.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(SatoriInclinationRow)) == 0
        assert session.scalar(select(func.count()).select_from(InclinationEvidenceRow)) == 0
        assert session.scalar(select(func.count()).select_from(InclinationRevisionRow)) == 0
    with SQLAlchemyReflectionUnitOfWork(migrated_database.session_factory) as unit:
        applying = unit.reflection.get_run(run.run_id)
        assert applying is not None
        assert applying.status is ReflectionRunStatus.APPLYING
        assert unit.reflection.list_outcomes(run.run_id) == ()

    monkeypatch.setattr(
        SQLAlchemyPositionsRepository,
        "record_inclination_reflection_decision",
        original,
    )
    completed = apply.execute(run.run_id, trace_id="trace-stage13-apply-resume")
    assert completed.status is ReflectionRunStatus.COMPLETED
    assert len(provider.requests) == 1
    reads = GetSatoriPositions(
        lambda: SQLAlchemyPositionsUnitOfWork(migrated_database.session_factory)
    )
    inclinations = reads.list_inclinations(identity_id=identity_id)
    assert len(inclinations) == 1
    inclination = inclinations[0]
    assert inclination.kind is InclinationKind.INTEREST
    assert inclination.normalized_topic == "архитектура"
    assert inclination.aggregate_version == 1
    assert len(inclination.evidence) == 3
    assert len(inclination.revisions) == 1
    assert inclination.score == inclination.revisions[0].applied_delta

    replay = apply.execute(run.run_id, trace_id="trace-stage13-apply-replay")
    assert replay == completed
    replayed = reads.list_inclinations(identity_id=identity_id)
    assert replayed == inclinations
    assert len(provider.requests) == 1

    database_url = str(migrated_database.engine.url)
    restarted = create_database(database_url)
    try:
        restarted_reads = GetSatoriPositions(
            lambda: SQLAlchemyPositionsUnitOfWork(restarted.session_factory)
        )
        restarted_inclinations = restarted_reads.list_inclinations(identity_id=identity_id)
        assert restarted_inclinations == inclinations
        as_of = inclination.state_as_of + timedelta(days=30)
        exported = restarted_reads.export_inclinations_json(
            identity_id=identity_id,
            as_of=as_of,
        )
    finally:
        restarted.dispose()
    payload = json.loads(exported)
    exported_inclination = payload["inclinations"][0]
    assert exported_inclination["score_at_state_as_of"] == inclination.score
    assert exported_inclination["state_as_of"] == inclination.state_as_of.isoformat()
    assert exported_inclination["effective_score"] == round(
        materialize_inclination_score(inclination, at=as_of),
        6,
    )
    raw_quotes = tuple(item[2] for item in roots.values())
    assert all(quote not in exported for quote in raw_quotes)
    assert ASSISTANT_REPLY not in exported

    future_content = "Архитектура будущего интересна проверяемыми границами."
    appraisal = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: proposal_for(request.interaction_id, salience=0.95)
    )
    future_services, _, _ = build_affective_conversation(
        migrated_database,
        appraisal,
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=12)),
        conversation=conversation_provider(ASSISTANT_REPLY),
    )
    future_reply = run_talk(
        future_services,
        request_id="stage13-future-source",
        text_value=future_content,
    )
    _add_position_sources(
        migrated_database,
        identity_id=identity_id,
        interaction_ids=(future_reply.interaction_id,),
    )
    with SQLAlchemyReflectionUnitOfWork(migrated_database.session_factory) as unit:
        future_sources = unit.reflection.list_eligible_sources(
            identity_id=identity_id,
            limit=12,
        )
    assert len(future_sources) == 1
    assert future_sources[0].root_interaction_id == future_reply.interaction_id
    assert future_sources[0].quote == future_content
    assert ASSISTANT_REPLY not in future_sources[0].quote
    inclination_artifacts = {item.evidence_id for item in inclination.evidence}
    assert inclination_artifacts.isdisjoint(item.evidence_edge_id for item in future_sources)
    assert set(interaction_ids).isdisjoint(item.root_interaction_id for item in future_sources)


def test_direct_assignment_cannot_create_an_inclination(
    migrated_database: Database,
) -> None:
    identity_id, _, _ = _prepare_sources(
        migrated_database,
        contents=DIRECT_ASSIGNMENT_CONTENTS,
    )
    provider = InterestReflectionProvider()
    run = _process_interest(
        migrated_database,
        identity_id=identity_id,
        provider=provider,
    )
    completed = _apply_interest(migrated_database).execute(
        run.run_id,
        trace_id="trace-stage13-direct-assignment",
    )
    assert completed.status is ReflectionRunStatus.COMPLETED
    with SQLAlchemyReflectionUnitOfWork(migrated_database.session_factory) as unit:
        outcomes = unit.reflection.list_outcomes(run.run_id)
    assert len(outcomes) == 1
    assert outcomes[0].decision.value == "rejected"
    assert outcomes[0].reason_code == "insufficient_inclination_evidence_diversity"
    with migrated_database.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(SatoriInclinationRow)) == 0


@pytest.mark.parametrize("tamper", ["missing", "signal_hash"])
def test_v2_missing_or_tampered_affect_attachment_fails_closed(
    migrated_database: Database,
    tamper: str,
) -> None:
    identity_id, _, _ = _prepare_sources(migrated_database)
    provider = InterestReflectionProvider()
    run = _process_interest(
        migrated_database,
        identity_id=identity_id,
        provider=provider,
    )
    cited_source_id = next(
        item.source_id for item in provider.requests[0].sources if item.affective is not None
    )
    with migrated_database.session_factory() as session:
        row = session.get(ReflectionSourceRow, cited_source_id)
        assert row is not None
        if tamper == "missing":
            row.affective_transition_id = None
            row.affective_state_version = None
            row.affective_signal_hash = None
        else:
            row.affective_signal_hash = "f" * 64
        session.commit()

    apply = _apply_interest(migrated_database)
    if tamper == "signal_hash":
        with pytest.raises(ValueError, match="attachment signal hash mismatch"):
            apply.execute(run.run_id, trace_id="trace-stage13-tampered-attachment")
        with SQLAlchemyReflectionUnitOfWork(migrated_database.session_factory) as unit:
            assert unit.reflection.list_outcomes(run.run_id) == ()
    else:
        completed = apply.execute(
            run.run_id,
            trace_id="trace-stage13-missing-attachment",
        )
        assert completed.status is ReflectionRunStatus.COMPLETED
        with SQLAlchemyReflectionUnitOfWork(migrated_database.session_factory) as unit:
            outcomes = unit.reflection.list_outcomes(run.run_id)
        assert len(outcomes) == 1
        assert outcomes[0].decision.value == "rejected"
        assert outcomes[0].reason_code == "inclination_source_outside_fixed_set"
    with migrated_database.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(SatoriInclinationRow)) == 0
        assert session.scalar(select(func.count()).select_from(InclinationRevisionRow)) == 0


def _persist_v1_run(
    database: Database,
    *,
    identity_id: str,
    status: ReflectionRunStatus,
) -> ReflectionRun:
    with SQLAlchemyReflectionUnitOfWork(database.session_factory) as unit:
        candidates = unit.reflection.list_eligible_sources(identity_id=identity_id, limit=12)
    assert len(candidates) == 4
    provisional = tuple(
        ReflectionSourceRecord(
            source_id=f"stage13-v1-source-{ordinal}",
            run_id="stage13-v1-run",
            ordinal=ordinal,
            kind=item.kind,
            evidence_edge_id=item.evidence_edge_id,
            evidence_edge_version=item.evidence_edge_version,
            root_interaction_id=item.root_interaction_id,
            root_message_id=item.root_message_id,
            root_counterparty_id=item.root_counterparty_id,
            observed_at=item.observed_at,
            content_hash=item.content_hash,
        )
        for ordinal, item in enumerate(candidates)
    )
    digest = source_set_hash(provisional, schema_version=REFLECTION_SCHEMA_VERSION_V1)
    run = ReflectionRun(
        run_id="stage13-v1-run",
        run_key=reflection_run_key(
            identity_id=identity_id,
            source_hash=digest,
            schema_version=REFLECTION_SCHEMA_VERSION_V1,
            policy_version=REFLECTION_POLICY_VERSION_V1,
        ),
        identity_id=identity_id,
        schema_version=REFLECTION_SCHEMA_VERSION_V1,
        policy_version=REFLECTION_POLICY_VERSION_V1,
        trigger_kind=ReflectionTriggerKind.EXPLICIT_LOCAL,
        source_set_hash=digest,
        status=ReflectionRunStatus.PENDING_GENERATION,
        aggregate_version=1,
        attempt_count=0,
        created_at=INTERACTION_TIME + timedelta(days=10),
        updated_at=INTERACTION_TIME + timedelta(days=10),
    )
    with SQLAlchemyReflectionUnitOfWork(database.session_factory) as unit:
        assert unit.reflection.create_run(run, provisional)
        unit.commit()
    if status is ReflectionRunStatus.RETRYABLE_FAILURE:
        failed = replace(
            run,
            status=status,
            aggregate_version=2,
            attempt_count=1,
            updated_at=INTERACTION_TIME + timedelta(days=10, seconds=1),
        )
        attempt = ReflectionAttempt(
            attempt_id="stage13-v1-failed-attempt",
            run_id=run.run_id,
            ordinal=1,
            status=ReflectionAttemptStatus.FAILED,
            reason_code="provider_invalid_or_unavailable",
            provider="fake-reflection",
            model="fixture-v1",
            formation_method="reflection.failed_before_valid_document",
            started_at=run.created_at,
            finished_at=failed.updated_at,
            metrics={},
        )
        with SQLAlchemyReflectionUnitOfWork(database.session_factory) as unit:
            unit.reflection.record_attempt(
                failed,
                attempt,
                (),
                expected_run_version=run.aggregate_version,
            )
            unit.commit()
        return failed
    assert status is ReflectionRunStatus.PENDING_GENERATION
    return run


@pytest.mark.parametrize(
    "status",
    [
        ReflectionRunStatus.PENDING_GENERATION,
        ReflectionRunStatus.RETRYABLE_FAILURE,
    ],
)
def test_v1_pending_and_retryable_runs_resume_with_legacy_versions(
    migrated_database: Database,
    status: ReflectionRunStatus,
) -> None:
    identity_id, _, _ = _prepare_sources(migrated_database)
    legacy = _persist_v1_run(
        migrated_database,
        identity_id=identity_id,
        status=status,
    )
    assert REFLECTION_SCHEMA_VERSION == 2
    assert REFLECTION_POLICY_VERSION == 2
    provider = V1ZeroReflectionProvider()
    process = ProcessReflection(
        reflection_uow_factory=lambda: SQLAlchemyReflectionUnitOfWork(
            migrated_database.session_factory
        ),
        positions_uow_factory=lambda: SQLAlchemyPositionsUnitOfWork(
            migrated_database.session_factory
        ),
        provider=provider,
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=10, minutes=1)),
        id_generator=id_sequence("stage13-v1-resume"),
    )
    report = asyncio.run(
        process.execute(
            identity_id,
            trigger=ReflectionTriggerKind.EXPLICIT_LOCAL,
            trace_id="trace-stage13-v1-resume",
        )
    )
    assert report.run is not None
    assert report.run.run_id == legacy.run_id
    assert report.run.status is ReflectionRunStatus.COMPLETED
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.schema_version == REFLECTION_SCHEMA_VERSION_V1
    assert request.policy_version == REFLECTION_POLICY_VERSION_V1
    assert request.current_inclinations == ()
    assert all(item.affective is None for item in request.sources)
    assert all(item.root_session_id is not None for item in request.sources)
