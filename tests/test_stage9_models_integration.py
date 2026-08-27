"""Stage 9 persistence, replay, restart, retention and partition isolation."""

import asyncio
import json
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from satori.application.conversation.contracts import TalkInput
from satori.application.conversation.post_processing import PostResponseReport
from satori.application.models.use_cases import FormCurrentModels, GetCurrentModels
from satori.composition import build_conversation_services, build_initial_self_services
from satori.config import Environment, Settings
from satori.core.models import (
    ModelEpistemicKind,
    ModelEvidenceCitation,
    ModelFormationProposal,
    ModelFormationProviderResponse,
    ModelFormationRequest,
    ModelValueKind,
    UserModelClaimProposal,
    WorldModelClaimProposal,
)
from satori.domain.models import ModelClaimStatus, UserModelManager, WorldModelManager
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.models_uow import SQLAlchemyCurrentModelsUnitOfWork
from tests.fakes import FakeModelFormationProvider, FrozenClock
from tests.test_stage4_conversation_memory import (
    INTERACTION_TIME,
    activate,
    conversation_provider,
    id_sequence,
    skip_episode_provider,
)


def conversation_settings(counterparty_id: str) -> Settings:
    return Settings(
        environment=Environment.TEST,
        database_url="sqlite+pysqlite:///:memory:",
        default_counterparty_id=counterparty_id,
    )


def create_interaction(
    database: Database,
    *,
    counterparty_id: str,
    content: str,
    prefix: str,
    day: int,
) -> str:
    services = build_conversation_services(
        database,
        build_initial_self_services(database),
        conversation_provider("Приняла обновление проекта."),
        skip_episode_provider(),
        conversation_settings(counterparty_id),
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=day)),
        id_generator=id_sequence(prefix),
    )

    async def execute() -> str:
        reply = await services.talk.execute(
            TalkInput(content, f"trace-{prefix}", f"request-{prefix}")
        )
        return reply.interaction_id

    return asyncio.run(execute())


def proposal_for_request(request: ModelFormationRequest) -> ModelFormationProviderResponse:
    current = next(
        item for item in request.messages if item.message_id == request.source_message_id
    )
    normalized = current.content.casefold()
    status = (
        "planned"
        if "запланирован" in normalized
        else "completed"
        if "заверш" in normalized
        else "active"
    )
    citation = ModelEvidenceCitation(current.message_id, current.content)
    return ModelFormationProviderResponse(
        proposal=ModelFormationProposal(
            schema_version=1,
            user_claims=(
                UserModelClaimProposal(
                    predicate="project",
                    value_kind=ModelValueKind.TEXT,
                    value="Аврора",
                    epistemic_kind=ModelEpistemicKind.EXPLICIT_FACT,
                    confidence=0.99,
                    evidence=(citation,),
                ),
            ),
            world_claims=(
                WorldModelClaimProposal(
                    subject_kind="project",
                    subject_label="Аврора",
                    predicate="status",
                    value_kind=ModelValueKind.TEXT,
                    value=status,
                    epistemic_kind=ModelEpistemicKind.EXPLICIT_FACT,
                    confidence=0.99,
                    evidence=(citation,),
                ),
            ),
        ),
        provider="fake-models",
        model="fixture",
        formation_method="fixture.models.v1",
    )


def build_former(
    database: Database,
    provider: FakeModelFormationProvider,
    *,
    prefix: str,
    day: int,
) -> FormCurrentModels:
    return FormCurrentModels(
        unit_of_work_factory=lambda: SQLAlchemyCurrentModelsUnitOfWork(database.session_factory),
        provider=provider,
        user_manager=UserModelManager(),
        world_manager=WorldModelManager(),
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=day)),
        id_generator=id_sequence(prefix),
    )


def test_persistence_replay_restart_retention_and_counterparty_isolation(
    migrated_database: Database,
) -> None:
    snapshot = activate(migrated_database)
    alice_interaction = create_interaction(
        migrated_database,
        counterparty_id="alice",
        content="Проект Аврора запланирован",
        prefix="alice-turn",
        day=1,
    )
    provider = FakeModelFormationProvider(response_factory=proposal_for_request)
    former = build_former(migrated_database, provider, prefix="alice-model", day=1)
    alice_decision = asyncio.run(former.execute(alice_interaction, trace_id="trace-alice-model"))
    replay = asyncio.run(former.execute(alice_interaction, trace_id="trace-alice-replay"))
    assert replay == alice_decision
    assert len(provider.requests) == 1

    reads = GetCurrentModels(
        lambda: SQLAlchemyCurrentModelsUnitOfWork(migrated_database.session_factory)
    )
    alice_user = reads.list_user(identity_id=snapshot.identity.identity_id, counterparty_id="alice")
    alice_world = reads.list_world(
        identity_id=snapshot.identity.identity_id, counterparty_id="alice"
    )
    assert len(alice_user) == len(alice_world) == 1
    assert alice_user[0].value == "Аврора"
    assert alice_world[0].value == "planned"
    assert alice_world[0].evidence[0].source_interaction_id == alice_interaction
    inspected = reads.inspect_world(
        alice_world[0].claim_id,
        identity_id=snapshot.identity.identity_id,
        counterparty_id="alice",
    )
    assert inspected is not None
    assert inspected[1][0].new_status is ModelClaimStatus.CURRENT

    bob_interaction = create_interaction(
        migrated_database,
        counterparty_id="bob",
        content="Проект Аврора теперь активен",
        prefix="bob-turn",
        day=2,
    )
    bob_provider = FakeModelFormationProvider(response_factory=proposal_for_request)
    bob_former = build_former(migrated_database, bob_provider, prefix="bob-model", day=2)
    asyncio.run(bob_former.execute(bob_interaction, trace_id="trace-bob-model"))
    assert (
        reads.list_world(identity_id=snapshot.identity.identity_id, counterparty_id="alice")[
            0
        ].value
        == "planned"
    )
    assert (
        reads.list_world(identity_id=snapshot.identity.identity_id, counterparty_id="bob")[0].value
        == "active"
    )

    with migrated_database.engine.connect() as connection:
        audit_count = connection.execute(
            text("SELECT count(*) FROM audit_events WHERE event_type LIKE 'models.%'")
        ).scalar_one()
        assert audit_count == 4
        source_message_id = alice_world[0].evidence[0].source_message_id
    with pytest.raises(IntegrityError), migrated_database.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM conversation_messages WHERE message_id = :message_id"),
            {"message_id": source_message_id},
        )

    restarted_database = create_database(str(migrated_database.engine.url))
    try:
        restarted_reads = GetCurrentModels(
            lambda: SQLAlchemyCurrentModelsUnitOfWork(restarted_database.session_factory)
        )
        assert restarted_reads.list_world(
            identity_id=snapshot.identity.identity_id,
            counterparty_id="alice",
            current_only=False,
        ) == reads.list_world(
            identity_id=snapshot.identity.identity_id,
            counterparty_id="alice",
            current_only=False,
        )
        exported = json.loads(
            restarted_reads.export_json(
                identity_id=snapshot.identity.identity_id,
                counterparty_id="alice",
                as_of=INTERACTION_TIME + timedelta(days=3),
            )
        )
        assert exported["counterparty_id"] == "alice"
        assert len(exported["user_claims"]) == len(exported["world_claims"]) == 1
        assert exported["world_claims"][0]["revisions"][0]["kind"] == "created"
        assert "content" not in exported["world_claims"][0]["evidence"][0]
    finally:
        restarted_database.dispose()

    relevant = reads.project_context(
        identity_id=snapshot.identity.identity_id,
        counterparty_id="alice",
        user_text="Как продвигается проект Аврора?",
        as_of=INTERACTION_TIME + timedelta(days=3),
    )
    unrelated = reads.project_context(
        identity_id=snapshot.identity.identity_id,
        counterparty_id="alice",
        user_text="Расскажи шутку про космос",
        as_of=INTERACTION_TIME + timedelta(days=3),
    )
    assert relevant.status == "available"
    assert relevant.world_claim_ids == (alice_world[0].claim_id,)
    assert unrelated.status == "empty"

    captured_provider = conversation_provider("Проект пока остаётся запланированным.")
    context_services = build_conversation_services(
        migrated_database,
        build_initial_self_services(migrated_database),
        captured_provider,
        skip_episode_provider(),
        conversation_settings("alice"),
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=3)),
        id_generator=id_sequence("alice-context"),
    )
    context_reply = asyncio.run(
        context_services.talk.execute(
            TalkInput(
                "Как продвигается проект Аврора?",
                "trace-alice-context",
                "request-alice-context",
            )
        )
    )
    assert context_reply.context_manifest.model_context_status == "available"
    assert context_reply.context_manifest.world_model_context_claim_ids == (
        alice_world[0].claim_id,
    )
    assert "current_user_world_models" in context_reply.context_manifest.included_sections
    assert any(
        "Current user/world model data" in item.content
        for item in captured_provider.requests[0].messages
    )

    replayed = asyncio.run(
        context_services.talk.execute(
            TalkInput(
                "Как продвигается проект Аврора?",
                "trace-alice-context",
                "request-alice-context",
            )
        )
    )
    assert replayed.replayed
    assert replayed.context_manifest.world_model_context_claim_ids == (alice_world[0].claim_id,)


def test_post_response_wiring_forms_models_without_affecting_committed_reply(
    migrated_database: Database,
) -> None:
    snapshot = activate(migrated_database)
    model_provider = FakeModelFormationProvider(response_factory=proposal_for_request)
    services = build_conversation_services(
        migrated_database,
        build_initial_self_services(migrated_database),
        conversation_provider("Ответ уже сохранён."),
        skip_episode_provider(),
        conversation_settings("alice"),
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=1)),
        id_generator=id_sequence("post-model"),
        model_provider=model_provider,
    )

    async def execute() -> tuple[str, PostResponseReport]:
        reply = await services.talk.execute(
            TalkInput(
                "Проект Аврора запланирован",
                "trace-post-model",
                "request-post-model",
            )
        )
        report = await services.post_response.execute(
            reply.interaction_id, trace_id="trace-post-model"
        )
        return reply.text, report

    reply_text, report = asyncio.run(execute())
    assert reply_text == "Ответ уже сохранён."
    assert report.succeeded
    assert report.model_formation_ms >= 0.0
    assert len(model_provider.requests) == 1
    world = services.current_models.list_world(
        identity_id=snapshot.identity.identity_id,
        counterparty_id="alice",
    )
    assert world[0].value == "planned"


def test_persisted_project_lifecycle_keeps_planned_active_completed_lineage(
    migrated_database: Database,
) -> None:
    snapshot = activate(migrated_database)
    reads = GetCurrentModels(
        lambda: SQLAlchemyCurrentModelsUnitOfWork(migrated_database.session_factory)
    )
    statuses = (
        ("Проект Аврора запланирован", "planned"),
        ("Проект Аврора теперь активен", "active"),
        ("Проект Аврора завершён", "completed"),
    )
    for day, (content, expected_status) in enumerate(statuses, start=1):
        interaction_id = create_interaction(
            migrated_database,
            counterparty_id="alice",
            content=content,
            prefix=f"lifecycle-turn-{day}",
            day=day,
        )
        provider = FakeModelFormationProvider(response_factory=proposal_for_request)
        decision = asyncio.run(
            build_former(
                migrated_database,
                provider,
                prefix=f"lifecycle-model-{day}",
                day=day,
            ).execute(interaction_id, trace_id=f"trace-lifecycle-{day}")
        )
        assert decision.kind.value == "applied"
        current = reads.list_world(
            identity_id=snapshot.identity.identity_id,
            counterparty_id="alice",
            current_only=True,
        )
        assert len(current) == 1
        assert current[0].value == expected_status

    history = reads.list_world(
        identity_id=snapshot.identity.identity_id,
        counterparty_id="alice",
        current_only=False,
    )
    by_value = {claim.value: claim for claim in history}
    assert set(by_value) == {"planned", "active", "completed"}
    assert by_value["planned"].status is ModelClaimStatus.SUPERSEDED
    assert by_value["planned"].superseded_by_claim_id == by_value["active"].claim_id
    assert by_value["active"].status is ModelClaimStatus.SUPERSEDED
    assert by_value["active"].superseded_by_claim_id == by_value["completed"].claim_id
    assert by_value["completed"].status is ModelClaimStatus.CURRENT
    inspection = reads.inspect_world(
        by_value["completed"].claim_id,
        identity_id=snapshot.identity.identity_id,
        counterparty_id="alice",
    )
    assert inspection is not None
    assert inspection[1][0].kind.value == "created"
