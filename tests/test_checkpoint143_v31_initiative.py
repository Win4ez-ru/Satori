"""V31 initiative realization, historical bytes and production boundaries, not character scores."""

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from satori.application.cognition.contracts import ResponseVerbosity
from satori.application.cognition.templates import COGNITION_TEMPLATE_REGISTRY_V3
from satori.application.cognition.use_cases import (
    DeterministicCognitionPlanner,
    SafeCognitionPipeline,
)
from satori.application.conversation.peer_delivery import (
    PEER_DELIVERY_MARKER,
    PEER_DELIVERY_MARKER_V30,
    PEER_DELIVERY_MARKER_V31,
)
from satori.application.conversation.policy import BEHAVIOR_POLICY_V30, BEHAVIOR_POLICY_V31
from satori.composition import build_conversation_services, build_initial_self_services
from satori.core.conversation import ConversationProviderRequest
from satori.infrastructure.persistence.database import Database
from tests.checkpoint143_peer_delivery_corpus import PeerScenario
from tests.checkpoint143_v31_corpus import SCENARIOS, observe
from tests.test_checkpoint143_character_agency_integration import (
    InvalidCompletionPlanner,
    MechanicalConversationProvider,
    _assert_cognition_preserved,
    _talk,
)
from tests.test_checkpoint143_peer_delivery import request_hash
from tests.test_conversation import activate, conversation_settings, skip_episode_provider


def block(request: ConversationProviderRequest) -> str:
    matches = [
        m.content.partition(PEER_DELIVERY_MARKER_V31)[2]
        for m in request.messages
        if PEER_DELIVERY_MARKER_V31 in m.content
    ]
    assert len(matches) == 1
    assert all(PEER_DELIVERY_MARKER not in m.content for m in request.messages)
    return matches[0]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_historical_request_bytes_and_unchanged_typed_decisions(scenario: PeerScenario) -> None:
    frozen = json.loads(
        (Path(__file__).parent / "fixtures/checkpoint143_v30_request_hashes.json").read_text()
    )
    old, before = observe(scenario, BEHAVIOR_POLICY_V30)
    assert request_hash(old) == frozen["sha256"][scenario.name]
    request, after = observe(scenario, BEHAVIOR_POLICY_V31)
    assert request.parameters == old.parameters
    clarified = scenario.name in {
        "established-2",
        "activity-open",
        "activity-fresh",
        "activity-corrected",
        "achievement-corrected",
    }
    assert ("Развей одну конкретную зацепку" in block(request)) is clarified
    if not clarified:
        assert tuple(
            (
                m.role,
                m.content.replace(PEER_DELIVERY_MARKER_V31, PEER_DELIVERY_MARKER_V30).replace(
                    "Trusted policy: satori.conversation.behavior.v31 / v31",
                    "Trusted policy: satori.conversation.behavior.v30 / v30",
                ),
            )
            for m in request.messages
        ) == tuple((m.role, m.content) for m in old.messages)
    a, b = asdict(before), asdict(after)
    # Only provider policy/version and rendered character count can differ in this calibration.
    differing = {key for key in a if a[key] != b[key]}
    assert differing <= {
        "policy_id",
        "policy_schema_version",
        "estimated_trusted_context_chars",
        "trusted_context_chars",
    }
    assert after.character_agency_decision_schema_version == 1
    assert after.character_presence_projection_schema_version == 3
    assert after.cognition_intent_registry_version is not None
    assert after.cognition_preserve_uncertainty is not None
    assert after.cognition_response_verbosity is not None
    boundary = COGNITION_TEMPLATE_REGISTRY_V3.active.render_operational_support(
        intent_registry_version=after.cognition_intent_registry_version,
        intent_tags=after.cognition_intent_tags,
        point_codes=after.cognition_required_point_codes,
        must_not_claim=after.cognition_forbidden_claim_codes,
        preserve_uncertainty=after.cognition_preserve_uncertainty,
        verbosity=ResponseVerbosity(after.cognition_response_verbosity),
    )
    assert block(request).count(boundary) == 1
    for principle in BEHAVIOR_POLICY_V31.principles:
        if principle.code in {"independent_character", "natural_brevity"}:
            assert block(request).count(principle.instruction) == 1


@pytest.mark.parametrize("wire_reply", ["Транспортное утверждение.", "Транспортный вопрос?"])
def test_selected_initiative_commits_either_transport_shape_without_style_judging(
    migrated_database: Database, wire_reply: str
) -> None:
    initial = activate(migrated_database)
    provider = MechanicalConversationProvider(scripted_texts=(wire_reply,))
    services = build_conversation_services(
        migrated_database,
        build_initial_self_services(migrated_database),
        provider,
        skip_episode_provider(),
        conversation_settings(),
        behavior_policy=BEHAVIOR_POLICY_V31,
        source_policy_required=False,
    )
    session = services.start_session.execute().session_id
    try:
        reply = _talk(
            services,
            user_text="Я сейчас смотрю фильм.",
            request_id="initiative",
            session_id=session,
        )
        replay = _talk(
            services,
            user_text="Я сейчас смотрю фильм.",
            request_id="initiative",
            session_id=session,
        )
    finally:
        services.close_session.execute(session)
    assert reply.context_manifest.character_agency_initiative == "advance_current"
    assert reply.text == replay.text == wire_reply
    assert not reply.context_manifest.response_regenerated
    assert replay.replayed
    assert len(provider.requests) == 1
    assert "Развей одну конкретную зацепку" in block(provider.requests[0])
    assert "Вопрос необязателен" in block(provider.requests[0])
    assert build_initial_self_services(migrated_database).get_self.execute() == initial
    _assert_cognition_preserved(reply.context_manifest, reply.cognition_trace)


def test_default_policy_commits_understated_closure_and_replays_without_rewrite(
    migrated_database: Database,
) -> None:
    initial = activate(migrated_database)
    # Exact approved public sample used only as transport output, never as a prompt template.
    provider = MechanicalConversationProvider(scripted_texts=("Ладно. Закрыли эту тему.",))
    services = build_conversation_services(
        migrated_database,
        build_initial_self_services(migrated_database),
        provider,
        skip_episode_provider(),
        conversation_settings(),
        behavior_policy=BEHAVIOR_POLICY_V31,
        source_policy_required=False,
    )
    session = services.start_session.execute().session_id
    try:
        reply = _talk(
            services, user_text="Хорошо, на этом всё.", request_id="understated", session_id=session
        )
        replay = _talk(
            services, user_text="Хорошо, на этом всё.", request_id="understated", session_id=session
        )
    finally:
        services.close_session.execute(session)
    assert reply.text == "Ладно. Закрыли эту тему." == replay.text
    assert reply.context_manifest.policy_schema_version == 31
    assert not reply.context_manifest.response_regenerated
    assert reply.context_manifest.character_agency_act == "close"
    assert replay.replayed
    assert len(provider.requests) == 1
    assert build_initial_self_services(migrated_database).get_self.execute() == initial
    block(provider.requests[0])
    _assert_cognition_preserved(reply.context_manifest, reply.cognition_trace)


def test_v31_shared_retry_keeps_the_exact_expression(migrated_database: Database) -> None:
    activate(migrated_database)
    provider = MechanicalConversationProvider(
        scripted_texts=(
            "Я готов обсудить это.",
            "Транспортный маркер после повтора.",
            "WIRE-UNREACHABLE",
        )
    )
    services = build_conversation_services(
        migrated_database,
        build_initial_self_services(migrated_database),
        provider,
        skip_episode_provider(),
        conversation_settings(),
        behavior_policy=BEHAVIOR_POLICY_V31,
        source_policy_required=False,
    )
    reply = _talk(services, user_text="Давай обсудим эту идею.", request_id="v31-retry")
    assert len(provider.requests) == 2
    assert reply.context_manifest.response_regenerated
    first, retry = provider.requests
    assert first.parameters == retry.parameters
    assert first.messages[:-2] == retry.messages[:-2]
    assert first.messages[-1] == retry.messages[-1]
    assert block(first).encode() == block(retry).encode()
    assert PEER_DELIVERY_MARKER_V31 in retry.messages[-2].content
    _assert_cognition_preserved(reply.context_manifest, reply.cognition_trace)


def test_v31_fallback_does_not_add_style(migrated_database: Database) -> None:
    activate(migrated_database)
    provider = MechanicalConversationProvider()
    services = build_conversation_services(
        migrated_database,
        build_initial_self_services(migrated_database),
        provider,
        skip_episode_provider(),
        conversation_settings(),
        behavior_policy=BEHAVIOR_POLICY_V31,
        source_policy_required=False,
    )
    services = replace(
        services,
        talk=replace(
            services.talk,
            cognition_pipeline=SafeCognitionPipeline(
                planner=InvalidCompletionPlanner(),  # type: ignore[arg-type]
                fallback=DeterministicCognitionPlanner(intent_registry_version=2),
            ),
        ),
    )
    reply = _talk(
        services,
        user_text="Я выжат, но всё равно продолжу работать через силу.",
        request_id="v31-fallback",
    )
    assert reply.context_manifest.character_agency_status == "fallback"
    assert reply.context_manifest.cognition_required_point_codes == ("hold_safety_boundary",)
    assert "Манера:" not in block(provider.requests[0])
    _assert_cognition_preserved(reply.context_manifest, reply.cognition_trace)
