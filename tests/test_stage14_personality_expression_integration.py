"""Stage 14 live personality-expression persistence and replay integration."""

# ruff: noqa: RUF001  # Russian acceptance evidence intentionally uses Cyrillic.

import asyncio
from datetime import datetime, timedelta

import pytest

from satori.application.conversation.contracts import SatoriReply, TalkInput
from satori.application.personality.use_cases import (
    GetPersonalityEvolution,
    RestorePersonalityCheckpoint,
)
from satori.composition import build_conversation_services, build_initial_self_services
from satori.core.personality import PersonalityRestoreProposal
from satori.domain.personality_evolution import PersonalityManager
from satori.domain.reflection import ReflectionOutcomeDecision
from satori.infrastructure.persistence.conversation_uow import (
    SQLAlchemyConversationHistoryUnitOfWork,
)
from satori.infrastructure.persistence.database import Database, create_database
from tests.fakes import FakeConversationProvider, FrozenClock, SequenceIdGenerator
from tests.stage14_real_eval import _snapshots
from tests.test_conversation import (
    activate,
    conversation_settings,
    skip_episode_provider,
    success_response,
)
from tests.test_stage14_personality_persistence import (
    NOW,
    PrefixIds,
    _apply,
    _seed_personality_run,
    _uow,
)


def _talk_at(
    database: Database,
    provider: FakeConversationProvider,
    *,
    client_request_id: str,
    instant: datetime,
) -> SatoriReply:
    initial_self = build_initial_self_services(database)
    conversation = build_conversation_services(
        database,
        initial_self,
        provider,
        skip_episode_provider(),
        conversation_settings(),
        clock=FrozenClock(instant),
        id_generator=SequenceIdGenerator(
            *(f"{client_request_id}-personality-expression-{index}" for index in range(100))
        ),
    )
    return asyncio.run(
        conversation.talk.execute(
            TalkInput(
                user_text="Как ты смотришь на эту задачу?",
                trace_id=f"trace-{client_request_id}",
                client_request_id=client_request_id,
            )
        )
    )


def test_evolved_expression_cue_is_persisted_reloaded_and_replayed_without_provider(
    migrated_database: Database,
) -> None:
    snapshot = activate(migrated_database)
    baseline_provider = FakeConversationProvider(response=success_response("Вижу основу."))
    baseline_reply = _talk_at(
        migrated_database,
        baseline_provider,
        client_request_id="request-personality-expression-baseline",
        instant=NOW - timedelta(days=1),
    )
    assert baseline_reply.context_manifest.personality_aggregate_version == 1
    assert baseline_reply.context_manifest.personality_expression_schema_version == 2
    assert baseline_reply.context_manifest.personality_expression_cues == ()

    fixture = _seed_personality_run(
        migrated_database,
        identity_id=snapshot.identity.identity_id,
        prefix="expression-replay",
        trait_key="optimism",
    )
    evolution = _apply(migrated_database, fixture).apply(
        fixture.identity_id,
        reflection_run_id=fixture.run_id,
        reflection_proposal_id=fixture.proposal_id,
        proposal=fixture.proposal,
        trace_id="trace-expression-evolution",
    )
    assert evolution.outcome.decision is ReflectionOutcomeDecision.ACCEPTED
    assert evolution.personality.aggregate_version == 2
    assert evolution.personality.trait("optimism").value == pytest.approx(0.625)

    provider = FakeConversationProvider(response=success_response("Вижу рабочий путь."))
    reply = _talk_at(
        migrated_database,
        provider,
        client_request_id="request-personality-expression-replay",
        instant=NOW + timedelta(days=1),
    )

    expected_cues = ("grounded_optimism:slightly_stronger",)
    assert reply.context_manifest.personality_aggregate_version == 2
    assert reply.context_manifest.personality_expression_schema_version == 2
    assert reply.context_manifest.personality_expression_cues == expected_cues
    provider_context = "\n".join(message.content for message in provider.requests[0].messages)
    assert "оставь направление вперёд без принудительной бодрости" in provider_context
    assert "Trusted current-turn presence Сатори / operational move v2" in provider_context
    assert "сохранять спокойный реалистичный оптимизм" not in provider_context
    assert "сейчас чуть заметнее исходного уровня" not in provider_context
    assert "Единая request-local режиссура реплики Сатори" not in provider_context
    assert "Чуть заметнее проявляй спокойный реалистичный оптимизм." not in provider_context

    with SQLAlchemyConversationHistoryUnitOfWork(migrated_database.session_factory) as unit_of_work:
        stored = unit_of_work.conversation_history.get_by_client_request_id(
            "request-personality-expression-replay"
        )
    assert stored is not None
    assert stored.provider_metadata is not None
    assert stored.provider_metadata.personality_aggregate_version == 2
    assert stored.provider_metadata.personality_expression_schema_version == 2
    assert stored.provider_metadata.personality_expression_cues == expected_cues

    replay_provider = FakeConversationProvider(response=success_response("Не вызывать."))
    replay = _talk_at(
        migrated_database,
        replay_provider,
        client_request_id="request-personality-expression-replay",
        instant=NOW + timedelta(days=1),
    )

    assert replay_provider.requests == []
    assert replay.text == reply.text
    assert replay.context_manifest.personality_aggregate_version == 2
    assert replay.context_manifest.personality_expression_schema_version == 2
    assert replay.context_manifest.personality_expression_cues == expected_cues

    inspector = GetPersonalityEvolution(
        unit_of_work_factory=lambda: _uow(migrated_database),
        clock=FrozenClock(NOW + timedelta(days=2)),
    )
    evolved = inspector.inspect(fixture.identity_id)
    assert evolved is not None
    activation = evolved.activation_checkpoint.snapshot
    restored = RestorePersonalityCheckpoint(
        unit_of_work_factory=lambda: _uow(migrated_database),
        manager=PersonalityManager(),
        clock=FrozenClock(NOW + timedelta(days=2)),
        id_generator=PrefixIds("expression-restore"),
    ).execute(
        fixture.identity_id,
        PersonalityRestoreProposal(
            checkpoint_id=activation.checkpoint_id,
            checkpoint_hash=activation.checkpoint_hash,
            expected_personality_version=2,
            reason="Restore the reviewed activation anchor.",
        ),
        trace_id="trace-expression-restore",
    )
    assert restored.restored is True
    assert restored.personality.aggregate_version == 3
    assert restored.personality.trait("optimism").value == pytest.approx(0.62)

    restored_provider = FakeConversationProvider(response=success_response("Основа сохранена."))
    restored_reply = _talk_at(
        migrated_database,
        restored_provider,
        client_request_id="request-personality-expression-restored",
        instant=NOW + timedelta(days=3),
    )
    assert restored_reply.context_manifest.personality_aggregate_version == 3
    assert restored_reply.context_manifest.personality_expression_schema_version == 2
    assert restored_reply.context_manifest.personality_expression_cues == ()
    assert "Чуть заметнее проявляй спокойный реалистичный оптимизм." not in "\n".join(
        message.content for message in restored_provider.requests[0].messages
    )

    restarted_database = create_database(str(migrated_database.engine.url))
    try:
        restarted_inspector = GetPersonalityEvolution(
            unit_of_work_factory=lambda: _uow(restarted_database),
            clock=FrozenClock(NOW + timedelta(days=4)),
        )
        restarted_state = restarted_inspector.inspect(fixture.identity_id)
        restarted_provider = FakeConversationProvider(response=success_response("Не вызывать."))
        restarted_replay = _talk_at(
            restarted_database,
            restarted_provider,
            client_request_id="request-personality-expression-restored",
            instant=NOW + timedelta(days=4),
        )
    finally:
        restarted_database.dispose()

    assert restarted_state is not None
    assert restarted_state.personality == restored.personality
    assert restarted_provider.requests == []
    assert restarted_replay.text == restored_reply.text
    assert restarted_replay.context_manifest.personality_aggregate_version == 3
    assert restarted_replay.context_manifest.personality_expression_cues == ()


def test_manual_anchor_fixture_uses_exact_owner_evolution_and_restore() -> None:
    snapshots = dict(_snapshots())

    assert snapshots["baseline"].personality.aggregate_version == 1
    assert snapshots["baseline"].personality.trait("optimism").value == pytest.approx(0.62)
    assert snapshots["evolved"].personality.aggregate_version == 2
    assert snapshots["evolved"].personality.trait("optimism").value == pytest.approx(0.625)
    assert snapshots["restored"].personality.aggregate_version == 3
    assert snapshots["restored"].personality.trait("optimism").value == pytest.approx(0.62)
    assert all(
        trait.baseline_value == snapshots["baseline"].personality.trait(trait.key).baseline_value
        for snapshot in snapshots.values()
        for trait in snapshot.personality.traits
    )
