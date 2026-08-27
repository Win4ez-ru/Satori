"""Stage 11 atomic persistence, replay, restart, provenance and identity-global scope."""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from satori.application.conversation.contracts import TalkInput
from satori.application.conversation.post_processing import PostResponseReport
from satori.application.positions.use_cases import FormSatoriPositions, GetSatoriPositions
from satori.composition import build_conversation_services, build_initial_self_services
from satori.config import Environment, Settings
from satori.core.positions import (
    PositionEvidenceCitation,
    PositionEvidenceRole,
    PositionFormationProposal,
    PositionFormationProviderResponse,
    PositionFormationRequest,
    PositionKind,
    PositionProposal,
    PositionStance,
)
from satori.domain.positions import PositionManager
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.positions_uow import SQLAlchemyPositionsUnitOfWork
from tests.fakes import FrozenClock
from tests.test_stage4_conversation_memory import (
    INTERACTION_TIME,
    activate,
    conversation_provider,
    id_sequence,
    skip_episode_provider,
)


@dataclass(slots=True)
class FakePositionProvider:
    requests: list[PositionFormationRequest] = field(default_factory=list)

    async def generate_structured(
        self, request: PositionFormationRequest, /
    ) -> PositionFormationProviderResponse:
        self.requests.append(request)
        current = next(
            item for item in request.messages if item.message_id == request.source_message_id
        )
        prior = next(
            (item for item in request.messages if item.message_id != current.message_id), None
        )
        positions: tuple[PositionProposal, ...] = ()
        if prior is not None:
            positions = (
                PositionProposal(
                    proposition="Прозрачные основания улучшают качество решений",
                    kind=PositionKind.OPINION,
                    stance=PositionStance.SUPPORT,
                    confidence=0.99,
                    evidence=(
                        PositionEvidenceCitation(
                            prior.message_id,
                            prior.content,
                            PositionEvidenceRole.ARGUMENT,
                        ),
                        PositionEvidenceCitation(
                            current.message_id,
                            current.content,
                            PositionEvidenceRole.OBSERVATION,
                        ),
                    ),
                    value_key="intellectual_honesty",
                ),
            )
        return PositionFormationProviderResponse(
            proposal=PositionFormationProposal(schema_version=1, positions=positions),
            provider="fake-positions",
            model="fixture-a",
            formation_method="fixture.positions.v1",
        )


@dataclass(slots=True)
class FailingPositionProvider:
    requests: list[PositionFormationRequest] = field(default_factory=list)

    async def generate_structured(
        self, request: PositionFormationRequest, /
    ) -> PositionFormationProviderResponse:
        self.requests.append(request)
        raise RuntimeError("controlled position-provider outage")


def settings(counterparty_id: str) -> Settings:
    return Settings(
        environment=Environment.TEST,
        database_url="sqlite+pysqlite:///:memory:",
        default_counterparty_id=counterparty_id,
    )


def create_interaction(
    database: Database, *, counterparty_id: str, content: str, prefix: str, day: int
) -> str:
    services = build_conversation_services(
        database,
        build_initial_self_services(database),
        conversation_provider("Приняла аргумент."),
        skip_episode_provider(),
        settings(counterparty_id),
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=day)),
        id_generator=id_sequence(prefix),
    )
    reply = asyncio.run(
        services.talk.execute(TalkInput(content, f"trace-{prefix}", f"request-{prefix}"))
    )
    return reply.interaction_id


def former(database: Database, provider: FakePositionProvider) -> FormSatoriPositions:
    return FormSatoriPositions(
        unit_of_work_factory=lambda: SQLAlchemyPositionsUnitOfWork(database.session_factory),
        provider=provider,
        manager=PositionManager(),
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=3)),
        id_generator=id_sequence("position"),
    )


def test_atomic_replay_restart_export_and_cross_counterparty_evidence(
    migrated_database: Database,
) -> None:
    snapshot = activate(migrated_database)
    first = create_interaction(
        migrated_database,
        counterparty_id="alice",
        content="Прозрачность важна, потому что позволяет проверить основания решения.",
        prefix="position-a",
        day=1,
    )
    second = create_interaction(
        migrated_database,
        counterparty_id="bob",
        content="Данные ретроспективы показывают меньше ошибок при открытом обосновании.",
        prefix="position-b",
        day=2,
    )
    provider = FakePositionProvider()
    service = former(migrated_database, provider)

    empty_decision = asyncio.run(service.execute(first, trace_id="trace-position-first"))
    decision = asyncio.run(service.execute(second, trace_id="trace-position-second"))
    replay = asyncio.run(service.execute(second, trace_id="trace-position-replay"))

    assert empty_decision.kind.value == "skipped"
    assert decision.kind.value == "applied"
    assert replay == decision
    assert len(provider.requests) == 2

    reads = GetSatoriPositions(
        lambda: SQLAlchemyPositionsUnitOfWork(migrated_database.session_factory)
    )
    positions = reads.list(identity_id=snapshot.identity.identity_id)
    assert len(positions) == 1
    assert positions[0].kind is PositionKind.OPINION
    assert positions[0].confidence == 0.5
    assert {item.source_counterparty_id for item in positions[0].evidence} == {"alice", "bob"}
    assert {item.source_interaction_id for item in positions[0].evidence} == {first, second}
    inspected = reads.inspect(positions[0].position_id, identity_id=snapshot.identity.identity_id)
    assert inspected is not None
    assert inspected[1][0].kind.value == "created"

    captured_provider = conversation_provider("Я поддерживаю проверяемые основания.")
    context_services = build_conversation_services(
        migrated_database,
        build_initial_self_services(migrated_database),
        captured_provider,
        skip_episode_provider(),
        settings("alice"),
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=4)),
        id_generator=id_sequence("position-context"),
    )
    context_reply = asyncio.run(
        context_services.talk.execute(
            TalkInput(
                "Что ты думаешь про прозрачные основания решений?",
                "trace-position-context",
                "request-position-context",
            )
        )
    )
    assert context_reply.context_manifest.position_context_status == "available"
    assert context_reply.context_manifest.position_context_ids == (positions[0].position_id,)
    assert "satori_epistemic_positions" in context_reply.context_manifest.included_sections
    position_prompt = next(
        item.content
        for item in captured_provider.requests[0].messages
        if "Canonical Satori epistemic positions" in item.content
    )
    assert positions[0].proposition in position_prompt
    assert positions[0].evidence[0].quote not in position_prompt

    replayed = asyncio.run(
        context_services.talk.execute(
            TalkInput(
                "Что ты думаешь про прозрачные основания решений?",
                "trace-position-context-replay",
                "request-position-context",
            )
        )
    )
    assert replayed.replayed
    assert replayed.context_manifest.position_context_ids == (positions[0].position_id,)

    with migrated_database.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM audit_events WHERE event_type LIKE 'positions.%'")
            ).scalar_one()
            == 2
        )
    with pytest.raises(IntegrityError), migrated_database.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM conversation_messages WHERE message_id = :message_id"),
            {"message_id": positions[0].evidence[0].source_message_id},
        )

    restarted = create_database(str(migrated_database.engine.url))
    try:
        restarted_reads = GetSatoriPositions(
            lambda: SQLAlchemyPositionsUnitOfWork(restarted.session_factory)
        )
        assert restarted_reads.list(
            identity_id=snapshot.identity.identity_id, current_only=False
        ) == reads.list(identity_id=snapshot.identity.identity_id, current_only=False)
        exported = json.loads(
            restarted_reads.export_json(identity_id=snapshot.identity.identity_id)
        )
        assert exported["identity_id"] == snapshot.identity.identity_id
        assert exported["positions"][0]["evidence"][0]["quote"]
        assert exported["positions"][0]["value_key"] == "intellectual_honesty"
    finally:
        restarted.dispose()


def test_post_response_position_failure_preserves_committed_reply(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = FailingPositionProvider()
    services = build_conversation_services(
        migrated_database,
        build_initial_self_services(migrated_database),
        conversation_provider("Канонический ответ сохранён."),
        skip_episode_provider(),
        settings("alice"),
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=1)),
        id_generator=id_sequence("position-failure"),
        position_provider=provider,
    )

    async def execute() -> tuple[str, str, PostResponseReport]:
        reply = await services.talk.execute(
            TalkInput(
                "Это аргумент, потому что он проверяем.",
                "trace-position-failure",
                "request-position-failure",
            )
        )
        report = await services.post_response.execute(
            reply.interaction_id, trace_id="trace-position-failure"
        )
        replay = await services.talk.execute(
            TalkInput(
                "Это аргумент, потому что он проверяем.",
                "trace-position-replay",
                "request-position-failure",
            )
        )
        return reply.text, replay.text, report

    reply_text, replay_text, report = asyncio.run(execute())

    assert reply_text == replay_text == "Канонический ответ сохранён."
    assert report.failure_phases == ("satori_positions",)
    assert report.position_formation_ms >= 0.0
    assert len(provider.requests) == 1
