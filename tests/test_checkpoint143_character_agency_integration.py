"""Production-lifecycle integration for the Checkpoint 14.3 agency kernel.

The fake provider is transport-only: its mechanically numbered text is neither a
golden Satori reply nor behavioral evidence.  These tests observe the typed request,
manifest, retry and persistence boundaries around the real ``TalkToSatori`` path.
"""

# ruff: noqa: RUF001  # Public Russian inputs exercise the production analyzers.

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace

import pytest
from sqlalchemy import text

from satori.application.cognition.contracts import (
    INTENT_REGISTRY_VERSION_V2,
    CognitionDialogueSignals,
    CognitionPipelineTrace,
    PreparedCognitionIntake,
)
from satori.application.cognition.use_cases import (
    DeterministicCognitionPlanner,
    SafeCognitionPipeline,
)
from satori.application.conversation.contracts import (
    ConversationContextManifest,
    SatoriReply,
    TalkInput,
)
from satori.application.conversation.policy import BEHAVIOR_POLICY_V28
from satori.composition import (
    ConversationServices,
    build_conversation_services,
    build_initial_self_services,
)
from satori.core.conversation import (
    ConversationProviderFailureReason,
    ConversationProviderRequest,
    ConversationProviderResponse,
    ConversationUsage,
    ProviderUnavailable,
)
from satori.domain.conversation_history import InteractionStatus
from satori.infrastructure.persistence.database import Database
from tests.test_conversation import (
    activate,
    conversation_settings,
    skip_episode_provider,
)

AGENCY_MARKER = "Trusted current-turn agency Сатори"

PUBLIC_FLOWS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "social_to_self_disclosure",
        (
            "Привет, Сатори.",
            "Расскажи о себе: кто ты, что тебе интересно и как ты себя чувствуешь?",
        ),
    ),
    (
        "achievement_to_depletion",
        (
            "Я сегодня наконец закончил сложную часть проекта.",
            "Знаешь, я почему-то почти не рад этому. Скорее просто выжат.",
        ),
    ),
    (
        "disagreement_correction_to_closure",
        (
            "Я думаю, что скорость важнее качества. Ты согласна?",
            "Я с тобой не согласен. Ты недооцениваешь риск.",
            "Не задавай в конце дежурный вопрос. Отвечай прямо.",
            "Ладно, с этим разобрались.",
        ),
    ),
)


@dataclass(slots=True)
class MechanicalConversationProvider:
    """Capture canonical requests and return non-semantic transport markers."""

    scripted_texts: tuple[str, ...] = ()
    requests: list[ConversationProviderRequest] = field(default_factory=list, init=False)

    async def generate(
        self,
        request: ConversationProviderRequest,
        /,
    ) -> ConversationProviderResponse:
        self.requests.append(request)
        call_number = len(self.requests)
        provider_text = (
            self.scripted_texts[call_number - 1]
            if call_number <= len(self.scripted_texts)
            else f"WIRE-{call_number:02d}"
        )
        return ConversationProviderResponse(
            text=provider_text,
            provider="fixture-transport",
            model="fixture-no-network",
            finish_status="stop",
            usage=ConversationUsage(input_tokens=100, output_tokens=8),
        )


class InvalidCompletionPlanner:
    """Apply a valid intake, then violate the completion boundary deterministically."""

    def __init__(self) -> None:
        self._delegate = DeterministicCognitionPlanner(
            intent_registry_version=INTENT_REGISTRY_VERSION_V2
        )

    def prepare_intake(
        self,
        *,
        user_text: str,
        user_message_id: str,
        interaction_id: str,
        dialogue: CognitionDialogueSignals,
    ) -> PreparedCognitionIntake:
        return self._delegate.prepare_intake(
            user_text=user_text,
            user_message_id=user_message_id,
            interaction_id=interaction_id,
            dialogue=dialogue,
        )

    def complete(self, *_: object, **__: object) -> None:
        """Return the wrong contract so ``SafeCognitionPipeline`` must recover."""

        return None


def _services(
    database: Database,
    provider: MechanicalConversationProvider,
) -> ConversationServices:
    return build_conversation_services(
        database,
        # Activation is performed separately so the pre/post snapshot can be compared.
        initial_self=build_initial_self_services(database),
        provider=provider,
        episode_provider=skip_episode_provider(),
        settings=conversation_settings(),
        behavior_policy=BEHAVIOR_POLICY_V28,
    )


def _talk(
    services: ConversationServices,
    *,
    user_text: str,
    request_id: str,
    session_id: str | None = None,
) -> SatoriReply:
    return asyncio.run(
        services.talk.execute(
            TalkInput(
                user_text=user_text,
                trace_id=f"trace-{request_id}",
                client_request_id=request_id,
                session_id=session_id,
            )
        )
    )


def _agency_realization_bytes(request: ConversationProviderRequest) -> bytes:
    layers = tuple(
        message.content for message in request.messages if AGENCY_MARKER in message.content
    )
    assert len(layers) == 1
    _, separator, realization = layers[0].partition(AGENCY_MARKER)
    assert separator == AGENCY_MARKER
    return (separator + realization).encode("utf-8")


def _assert_cognition_preserved(
    manifest: ConversationContextManifest,
    trace: CognitionPipelineTrace | None,
) -> None:
    assert trace is not None
    strategy = trace.response_strategy
    assert manifest.cognition_pipeline_schema_version == trace.schema_version
    assert manifest.cognition_pipeline_status == trace.status.value
    assert manifest.cognition_perception_topics == tuple(
        item.value for item in trace.perception.topics
    )
    assert manifest.cognition_perception_signals == tuple(
        item.value for item in trace.perception.signals
    )
    assert manifest.cognition_need_dimensions == tuple(
        item.dimension.value for item in trace.need_mix.needs
    )
    assert manifest.cognition_position_stance == strategy.position_stance.value
    assert manifest.cognition_preserve_uncertainty is strategy.preserve_uncertainty
    assert manifest.cognition_intent_registry_version == trace.intent.registry_version
    assert manifest.cognition_primary_intent == trace.intent.primary_tag
    assert manifest.cognition_intent_tags == trace.intent.tags
    assert manifest.cognition_required_point_codes == strategy.point_codes
    assert manifest.cognition_forbidden_claim_codes == strategy.must_not_claim
    assert manifest.cognition_strategy_tone == strategy.tone.value
    assert manifest.cognition_response_verbosity == strategy.verbosity.value


def _assert_fresh_v28_turn(
    reply: SatoriReply,
    request: ConversationProviderRequest,
) -> None:
    manifest = reply.context_manifest
    assert manifest.schema_version == 17
    assert manifest.policy_id == BEHAVIOR_POLICY_V28.policy_id
    assert manifest.policy_schema_version == BEHAVIOR_POLICY_V28.schema_version
    assert manifest.included_sections.count("character_agency_decision") == 1
    assert manifest.character_agency_decision_schema_version == 1
    assert manifest.character_agency_status in {"applied", "fallback"}
    assert manifest.character_agency_drive is not None
    assert manifest.character_agency_act is not None
    assert manifest.character_agency_subject is not None
    assert manifest.character_agency_initiative is not None
    assert manifest.character_agency_lead is not None
    assert manifest.character_delivery_decision_schema_version == 5
    assert manifest.character_presence_projection_schema_version == 3
    assert sum(AGENCY_MARKER in message.content for message in request.messages) == 1
    _assert_cognition_preserved(manifest, reply.cognition_trace)


def test_v28_three_public_multi_turn_flows_cross_the_real_talk_lifecycle(
    migrated_database: Database,
) -> None:
    initial_snapshot = activate(migrated_database)
    provider = MechanicalConversationProvider()
    services = _services(migrated_database, provider)
    observed: list[SatoriReply] = []

    for flow_id, public_turns in PUBLIC_FLOWS:
        session_id = services.start_session.execute().session_id
        try:
            for turn_number, user_text in enumerate(public_turns, start=1):
                observed.append(
                    _talk(
                        services,
                        user_text=user_text,
                        request_id=f"checkpoint143-{flow_id}-{turn_number}",
                        session_id=session_id,
                    )
                )
        finally:
            services.close_session.execute(session_id)

    assert len(provider.requests) == sum(len(turns) for _, turns in PUBLIC_FLOWS)
    assert len(observed) == len(provider.requests)
    for reply, request in zip(observed, provider.requests, strict=True):
        _assert_fresh_v28_turn(reply, request)

    # The flows exercise distinct agency moves without making provider prose an oracle.
    assert [reply.context_manifest.character_agency_drive for reply in observed] == [
        "connect",
        "share_self",
        "connect",
        "care",
        "none",
        "challenge",
        "repair",
        "close",
    ]
    assert [reply.context_manifest.recent_conversation_turn_count for reply in observed] == [
        0,
        1,
        0,
        1,
        0,
        1,
        2,
        3,
    ]
    assert build_initial_self_services(migrated_database).get_self.execute() == initial_snapshot


def test_v28_idempotent_replay_omits_transient_agency_and_never_calls_provider_twice(
    migrated_database: Database,
) -> None:
    initial_snapshot = activate(migrated_database)
    provider = MechanicalConversationProvider()
    services = _services(migrated_database, provider)
    request_id = "checkpoint143-idempotent-replay"

    first = _talk(services, user_text="Привет, Сатори.", request_id=request_id)
    post_response = asyncio.run(
        services.post_response.execute(
            first.interaction_id,
            trace_id="trace-checkpoint143-post-response",
        )
    )
    restarted_provider = MechanicalConversationProvider()
    restarted_services = _services(migrated_database, restarted_provider)
    replay = _talk(
        restarted_services,
        user_text="Привет, Сатори.",
        request_id=request_id,
    )

    _assert_fresh_v28_turn(first, provider.requests[0])
    assert post_response.succeeded is True
    assert len(provider.requests) == 1
    assert restarted_provider.requests == []
    assert replay.replayed is True
    assert replay.text == first.text
    assert replay.context_manifest.schema_version == 17
    assert replay.context_manifest.policy_id == BEHAVIOR_POLICY_V28.policy_id
    assert "character_agency_decision" not in replay.context_manifest.included_sections
    assert replay.context_manifest.character_agency_decision_schema_version is None
    assert replay.context_manifest.character_agency_drive is None
    assert replay.context_manifest.character_delivery_decision_schema_version is None
    assert replay.context_manifest.character_presence_projection_schema_version is None
    assert replay.cognition_trace is None
    with migrated_database.engine.connect() as connection:
        agency_tables = connection.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND lower(name) LIKE '%agency%'"
            )
        ).all()
    assert agency_tables == []
    assert build_initial_self_services(migrated_database).get_self.execute() == initial_snapshot


@pytest.mark.parametrize(
    ("user_text", "expected_signal", "expected_reason"),
    [
        (
            "Мне сейчас очень тяжело, я едва держусь. Просто побудь со мной.",
            "high_distress",
            "high_distress",
        ),
        (
            "Я выжат. Просто выслушай меня, без советов.",
            "explicit_listen_request",
            "explicit_listen",
        ),
    ],
)
def test_v28_repeated_vulnerability_keeps_cognition_and_agency_signal_parity(
    migrated_database: Database,
    user_text: str,
    expected_signal: str,
    expected_reason: str,
) -> None:
    activate(migrated_database)
    provider = MechanicalConversationProvider()
    services = _services(migrated_database, provider)
    session_id = services.start_session.execute().session_id
    try:
        first = _talk(
            services,
            user_text=user_text,
            request_id=f"checkpoint143-{expected_signal}-first",
            session_id=session_id,
        )
        repeated = _talk(
            services,
            user_text=user_text,
            request_id=f"checkpoint143-{expected_signal}-repeated",
            session_id=session_id,
        )
    finally:
        services.close_session.execute(session_id)

    assert len(provider.requests) == 2
    _assert_fresh_v28_turn(first, provider.requests[0])
    _assert_fresh_v28_turn(repeated, provider.requests[1])
    manifest = repeated.context_manifest
    assert "repeated_turn" in manifest.cognition_perception_signals
    assert expected_signal in manifest.cognition_perception_signals
    assert manifest.character_agency_status == "applied"
    assert manifest.character_agency_drive == "care"
    assert manifest.character_agency_act == "acknowledge"
    assert manifest.character_agency_subject == "current_exchange"
    assert manifest.character_agency_initiative == "stop"
    assert manifest.character_agency_lead == "owned_move_first"
    assert manifest.character_agency_reason_codes == (
        "repetition_precedence",
        expected_reason,
    )


def test_v28_invalid_cognition_completion_preserves_safety_and_builds_fallback_request(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = MechanicalConversationProvider()
    services = _services(migrated_database, provider)
    cognition_pipeline = SafeCognitionPipeline(
        planner=InvalidCompletionPlanner(),  # type: ignore[arg-type]
        fallback=DeterministicCognitionPlanner(intent_registry_version=INTENT_REGISTRY_VERSION_V2),
    )
    services = replace(
        services,
        talk=replace(services.talk, cognition_pipeline=cognition_pipeline),
    )

    reply = _talk(
        services,
        user_text="Я выжат, но всё равно продолжу работать через силу.",
        request_id="checkpoint143-invalid-cognition-completion",
    )

    assert len(provider.requests) == 1
    _assert_fresh_v28_turn(reply, provider.requests[0])
    trace = reply.cognition_trace
    assert trace is not None
    assert trace.status.value == "fallback"
    assert trace.fallback_reasons == ("completion_invalid_or_failed",)
    assert "harmful_overextension" in tuple(signal.value for signal in trace.perception.signals)
    assert trace.intent.primary_tag == "hold_safety_boundary"
    assert trace.response_strategy.point_codes == ("hold_safety_boundary",)
    manifest = reply.context_manifest
    assert manifest.cognition_pipeline_status == "fallback"
    assert manifest.character_agency_status == "fallback"
    assert manifest.character_agency_drive == "none"
    assert manifest.character_agency_act == "respond"
    assert manifest.character_agency_subject == "user_request"
    assert manifest.character_agency_initiative == "stop"
    assert manifest.character_agency_lead == "obligation_first"
    assert manifest.character_agency_reason_codes == ("cognition_fallback",)
    assert sum(AGENCY_MARKER in message.content for message in provider.requests[0].messages) == 1


def test_v28_max_one_retry_reuses_exact_agency_realization_bytes(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = MechanicalConversationProvider(
        scripted_texts=(
            "Я готов обсудить это.",
            "Транспортный маркер после повтора.",
            "Этот третий ответ не должен быть запрошен.",
        )
    )
    services = _services(migrated_database, provider)

    reply = _talk(
        services,
        user_text="Давай обсудим эту идею.",
        request_id="checkpoint143-bounded-retry",
    )

    assert len(provider.requests) == 2
    assert reply.context_manifest.regeneration_attempted is True
    assert reply.context_manifest.response_regenerated is True
    first, retried = provider.requests
    assert first.parameters == retried.parameters
    assert first.messages[-1] == retried.messages[-1]
    assert _agency_realization_bytes(first) == _agency_realization_bytes(retried)
    _assert_fresh_v28_turn(reply, first)


def test_v28_provider_failure_has_one_call_and_no_agency_persistence(
    migrated_database: Database,
) -> None:
    initial_snapshot = activate(migrated_database)
    provider_error = ProviderUnavailable(
        "fixture-transport",
        "fixture-no-network",
        "offline fixture outage",
        reason=ConversationProviderFailureReason.TRANSPORT_UNAVAILABLE,
    )

    class FailingMechanicalProvider(MechanicalConversationProvider):
        async def generate(
            self,
            request: ConversationProviderRequest,
            /,
        ) -> ConversationProviderResponse:
            self.requests.append(request)
            raise provider_error

    provider = FailingMechanicalProvider()
    services = _services(migrated_database, provider)

    with pytest.raises(ProviderUnavailable) as captured:
        _talk(
            services,
            user_text="Помоги проанализировать архитектуру проекта.",
            request_id="checkpoint143-provider-failure",
        )

    assert captured.value is provider_error
    assert len(provider.requests) == 1
    assert sum(AGENCY_MARKER in message.content for message in provider.requests[0].messages) == 1
    failed = services.history.execute().interactions[0]
    assert failed.status is InteractionStatus.FAILED
    assert failed.assistant_message is None
    with migrated_database.engine.connect() as connection:
        persisted = (
            connection.execute(
                text(
                    "SELECT context_manifest_schema_version, policy_id, policy_schema_version "
                    "FROM conversation_interactions"
                )
            )
            .mappings()
            .one()
        )
        agency_tables = connection.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND lower(name) LIKE '%agency%'"
            )
        ).all()
    assert dict(persisted) == {
        "context_manifest_schema_version": None,
        "policy_id": None,
        "policy_schema_version": None,
    }
    assert agency_tables == []
    assert build_initial_self_services(migrated_database).get_self.execute() == initial_snapshot
