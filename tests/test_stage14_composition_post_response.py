"""Stage 14 production composition and independent post-response routing."""

import asyncio
import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest

from satori.application.conversation.history import InteractionLog
from satori.application.conversation.post_processing import ProcessPostResponse
from satori.application.memory.use_cases import FormEpisodeForInteraction
from satori.application.reflection.use_cases import (
    ApplyReflectionProposals,
    ProcessReflection,
    ReflectionProcessReport,
)
from satori.composition import build_conversation_services, build_initial_self_services
from satori.core.reflection import (
    ReflectionGenerationRequest,
    ReflectionProposalDocument,
    ReflectionProviderResponse,
    ReflectionPurpose,
)
from satori.domain.conversation_history import InteractionStatus
from satori.domain.reflection import (
    ReflectionRun,
    ReflectionRunStatus,
    ReflectionTriggerKind,
)
from satori.infrastructure.persistence.database import Database, create_database
from tests.fakes import FrozenClock
from tests.test_stage4_conversation_memory import (
    INTERACTION_TIME,
    activate,
    conversation_provider,
    id_sequence,
    skip_episode_provider,
)
from tests.test_stage11_positions_integration import create_interaction, settings
from tests.test_stage14_personality_persistence import NOW, _seed_personality_run

_TEST_NOW = datetime(2026, 8, 23, tzinfo=UTC)


@dataclass(slots=True)
class _InteractionLog:
    def get(self, interaction_id: str) -> SimpleNamespace:
        assert interaction_id == "interaction-1"
        return SimpleNamespace(
            status=InteractionStatus.COMPLETED,
            relationship_processing_required=False,
            model_processing_required=False,
            position_processing_required=False,
        )


@dataclass(slots=True)
class _SkipEpisode:
    async def execute(self, interaction: object, *, trace_id: str) -> SimpleNamespace:
        assert interaction is not None
        assert trace_id == "trace-1"
        return SimpleNamespace(memory=None)


def _run(purpose: ReflectionPurpose) -> ReflectionRun:
    personality = purpose is ReflectionPurpose.PERSONALITY_EVOLUTION
    return ReflectionRun(
        run_id=f"run-{purpose.value}",
        run_key=f"key-{purpose.value}",
        identity_id="identity-1",
        schema_version=3 if personality else 2,
        policy_version=3 if personality else 2,
        trigger_kind=ReflectionTriggerKind.AUTOMATIC,
        source_set_hash="a" * 64,
        status=ReflectionRunStatus.PROPOSALS_READY,
        aggregate_version=2,
        attempt_count=1,
        created_at=_TEST_NOW,
        updated_at=_TEST_NOW,
        purpose=purpose,
    )


@dataclass(slots=True)
class _RoutingProcess:
    events: list[str]

    async def execute(
        self,
        identity_id: str,
        *,
        trigger: ReflectionTriggerKind,
        trace_id: str,
        purpose: ReflectionPurpose = ReflectionPurpose.GENERAL,
    ) -> ReflectionProcessReport:
        assert identity_id == "identity-1"
        assert trigger is ReflectionTriggerKind.AUTOMATIC
        assert trace_id == "trace-1"
        self.events.append(f"process:{purpose.value}")
        return ReflectionProcessReport(_run(purpose), "proposals_ready", True, True)


@dataclass(slots=True)
class _RoutingApply:
    events: list[str]

    def execute(self, run_id: str, *, trace_id: str) -> ReflectionRun:
        assert trace_id == "trace-1"
        self.events.append(f"apply:{run_id}")
        purpose = (
            ReflectionPurpose.PERSONALITY_EVOLUTION
            if run_id.endswith(ReflectionPurpose.PERSONALITY_EVOLUTION.value)
            else ReflectionPurpose.GENERAL
        )
        return replace(
            _run(purpose),
            status=ReflectionRunStatus.COMPLETED,
            aggregate_version=3,
            updated_at=_TEST_NOW,
            completed_at=_TEST_NOW,
        )


@dataclass(slots=True)
class _GeneralFailureProcess:
    purposes: list[ReflectionPurpose] = field(default_factory=list)

    async def execute(
        self,
        identity_id: str,
        *,
        trigger: ReflectionTriggerKind,
        trace_id: str,
        purpose: ReflectionPurpose = ReflectionPurpose.GENERAL,
    ) -> ReflectionProcessReport:
        assert identity_id == "identity-1"
        assert trigger is ReflectionTriggerKind.AUTOMATIC
        assert trace_id == "trace-1"
        self.purposes.append(purpose)
        if purpose is ReflectionPurpose.GENERAL:
            raise RuntimeError("raw source text must never enter logs")
        return ReflectionProcessReport(None, "personality_observation_span_too_short")


@dataclass(slots=True)
class _UnreachableApply:
    def execute(self, run_id: str, *, trace_id: str) -> ReflectionRun:
        raise AssertionError("an absent run must not be routed")


def _post_response(
    process: object,
    apply: object,
) -> ProcessPostResponse:
    return ProcessPostResponse(
        interaction_log=cast(InteractionLog, _InteractionLog()),
        form_episode=cast(FormEpisodeForInteraction, _SkipEpisode()),
        process_reflection=cast(ProcessReflection, process),
        apply_reflection=cast(ApplyReflectionProposals, apply),
        identity_id_provider=lambda: "identity-1",
    )


def test_post_response_routes_general_then_personality_as_independent_runs() -> None:
    events: list[str] = []

    report = asyncio.run(
        _post_response(_RoutingProcess(events), _RoutingApply(events)).execute(
            "interaction-1",
            trace_id="trace-1",
        )
    )

    assert report.succeeded
    assert report.reflection_processing_ms >= 0
    assert report.personality_reflection_processing_ms >= 0
    assert events == [
        "process:general",
        "apply:run-general",
        "process:personality_evolution",
        "apply:run-personality_evolution",
    ]


def test_general_failure_does_not_suppress_personality_attempt_or_leak_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    process = _GeneralFailureProcess()
    caplog.set_level(logging.INFO, logger="satori.post_response")

    report = asyncio.run(
        _post_response(process, _UnreachableApply()).execute(
            "interaction-1",
            trace_id="trace-1",
        )
    )

    assert process.purposes == [
        ReflectionPurpose.GENERAL,
        ReflectionPurpose.PERSONALITY_EVOLUTION,
    ]
    assert report.failure_phases == ("reflection_processing",)
    personality_event = next(
        item
        for item in caplog.records
        if item.message == "personality_reflection_processing_completed"
    )
    assert vars(personality_event)["satori_fields"] == {
        "purpose": "personality_evolution",
        "run_id": None,
        "run_status": None,
        "reason_code": "personality_observation_span_too_short",
        "created": False,
        "provider_called": False,
    }
    assert "raw source text" not in caplog.text


@dataclass(slots=True)
class _ZeroReflectionProvider:
    requests: list[ReflectionGenerationRequest] = field(default_factory=list)

    async def generate_structured(
        self,
        request: ReflectionGenerationRequest,
        /,
    ) -> ReflectionProviderResponse:
        self.requests.append(request)
        return ReflectionProviderResponse(
            document=ReflectionProposalDocument(
                schema_version=request.schema_version,
                proposals=(),
            ),
            provider="fixture-reflection",
            model="fixture-zero",
            formation_method=f"fixture.reflection.v{request.schema_version}",
        )


def test_composed_post_response_calls_no_reflection_provider_before_eligibility(
    migrated_database: Database,
) -> None:
    snapshot = activate(migrated_database)
    interaction_id = create_interaction(
        migrated_database,
        counterparty_id="alice",
        content="Единственное новое наблюдение пока не образует долгий период.",
        prefix="stage14-ineligible",
        day=1,
    )
    provider = _ZeroReflectionProvider()
    services = build_conversation_services(
        migrated_database,
        build_initial_self_services(migrated_database),
        conversation_provider("Канонический ответ."),
        skip_episode_provider(),
        settings("alice"),
        clock=FrozenClock(INTERACTION_TIME),
        id_generator=id_sequence("stage14-ineligible-post"),
        reflection_provider=provider,
    )

    report = asyncio.run(
        services.post_response.execute(interaction_id, trace_id="trace-stage14-ineligible")
    )

    assert report.succeeded
    assert report.reflection_processing_ms >= 0
    assert report.personality_reflection_processing_ms >= 0
    assert provider.requests == []
    inspection = services.personality.evolution.inspect(snapshot.identity.identity_id)
    assert inspection is not None
    assert inspection.personality.aggregate_version == 1


def test_composed_v3_router_commits_owner_and_survives_service_restart(
    migrated_database: Database,
) -> None:
    fixture = _seed_personality_run(migrated_database, prefix="composition-routing")
    provider = _ZeroReflectionProvider()
    services = build_conversation_services(
        migrated_database,
        build_initial_self_services(migrated_database),
        conversation_provider("Канонический ответ."),
        skip_episode_provider(),
        settings("local-default"),
        clock=FrozenClock(NOW),
        id_generator=id_sequence("composition-routing"),
        reflection_provider=provider,
    )

    report = asyncio.run(
        services.post_response.execute(
            "composition-routing-interaction-7",
            trace_id="trace-composition-routing",
        )
    )

    assert report.succeeded
    assert [item.purpose for item in provider.requests] == [ReflectionPurpose.GENERAL]
    inspection = services.personality.evolution.inspect(fixture.identity_id)
    assert inspection is not None
    assert inspection.personality.aggregate_version == 2
    assert len(inspection.revisions) == 1

    restarted_database = create_database(str(migrated_database.engine.url))
    try:
        restarted = build_conversation_services(
            restarted_database,
            build_initial_self_services(restarted_database),
            conversation_provider("Канонический ответ после рестарта."),
            skip_episode_provider(),
            settings("local-default"),
            clock=FrozenClock(NOW),
            id_generator=id_sequence("composition-restart"),
        )
        restarted_inspection = restarted.personality.evolution.inspect(fixture.identity_id)
    finally:
        restarted_database.dispose()

    assert restarted_inspection is not None
    assert restarted_inspection.personality == inspection.personality
    assert restarted_inspection.revisions == inspection.revisions
