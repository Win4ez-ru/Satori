"""V29 request/state boundaries; synthetic transport text is not character evidence."""

# ruff: noqa: RUF001

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from satori.application.cognition.contracts import ResponseVerbosity
from satori.application.cognition.templates import COGNITION_TEMPLATE_REGISTRY_V3
from satori.application.cognition.use_cases import (
    DeterministicCognitionPlanner,
    SafeCognitionPipeline,
)
from satori.application.conversation.character_evidence import analyze_character_request_evidence
from satori.application.conversation.context import plan_conversational_disclosure
from satori.application.conversation.disclosure_contracts import DisclosureFacet
from satori.application.conversation.peer_delivery import PEER_DELIVERY_MARKER
from satori.application.conversation.policy import BEHAVIOR_POLICY_V28, BEHAVIOR_POLICY_V29
from satori.composition import build_conversation_services, build_initial_self_services
from satori.core.conversation import ConversationProviderRequest
from satori.infrastructure.persistence.database import Database
from tests.checkpoint143_peer_delivery_corpus import PUBLIC_TURNS, SCENARIOS, PeerScenario, observe
from tests.test_checkpoint143_character_agency_integration import (
    InvalidCompletionPlanner,
    MechanicalConversationProvider,
    _assert_cognition_preserved,
    _talk,
)
from tests.test_conversation import activate, conversation_settings, skip_episode_provider


@pytest.mark.parametrize(
    "text",
    [
        PUBLIC_TURNS[1],
        "Чем ты сейчас увлечена?",
        "Чем увлечена?",
        "Скажи, чем ты сегодня интересуешься?",
        "Чем ты теперь увлекаешься?",
    ],
)
def test_current_interest_requests_preserve_the_interest_facet(text: str) -> None:
    plan = plan_conversational_disclosure(text, policy_schema_version=29)
    assert DisclosureFacet.INTERESTS in plan.required_facets
    if text == PUBLIC_TURNS[1]:
        assert DisclosureFacet.AFFECT in plan.required_facets
        historical = plan_conversational_disclosure(text, policy_schema_version=28)
        assert historical.required_facets == (DisclosureFacet.AFFECT,)
        request, manifest = observe(
            PeerScenario("mixed-interests", text, "established"), BEHAVIOR_POLICY_V29
        )
        assert set(manifest.disclosure_facets) == {"affect", "interests"}
        assert "интересы в пределах доступного собственного состояния" in peer_block(request)


@pytest.mark.parametrize(
    "text",
    [
        "Чем она сейчас увлечена?",
        "Я сейчас увлечена музыкой.",
        "Переведи: «Чем ты сейчас увлечена?»",
        "Не спрашиваю, чем ты сейчас увлечена. Помоги с проектом.",
        "Если бы я спросил, чем ты сейчас увлечена, ты бы ответила?",
        "Чем ты сейчас увлечена, меня не интересует. Я говорю о своём проекте.",
    ],
)
def test_current_interest_detection_preserves_reference_boundaries(text: str) -> None:
    plan = plan_conversational_disclosure(text, policy_schema_version=29)
    assert DisclosureFacet.INTERESTS not in plan.required_facets


def test_related_checkins_are_one_topic_and_casual_topics_are_not_a_checklist() -> None:
    plan = plan_conversational_disclosure("Как ты, как дела?", policy_schema_version=29)
    assert plan.required_facets == (DisclosureFacet.AFFECT,)
    request, manifest = observe(
        PeerScenario("selective", PUBLIC_TURNS[1], "established"), BEHAVIOR_POLICY_V29
    )
    assert set(manifest.disclosure_facets) == {"affect", "interests"}
    block = peer_block(request)
    assert "можно ответить на один из нескольких вопросов" in block
    assert "это не перечень обязательных частей ответа" in block
    assert "явно просит разобрать всё" in block
    assert "Ответь на запрошенные стороны" not in block


def test_single_topic_reply_to_mixed_casual_turn_is_committed_without_rewrite(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = MechanicalConversationProvider(scripted_texts=("Всё нормально.",))
    services = build_conversation_services(
        migrated_database,
        build_initial_self_services(migrated_database),
        provider,
        skip_episode_provider(),
        conversation_settings(),
        behavior_policy=BEHAVIOR_POLICY_V29,
        source_policy_required=False,
    )
    reply = _talk(services, user_text=PUBLIC_TURNS[1], request_id="selective-casual")
    assert reply.text == "Всё нормально."
    assert len(provider.requests) == 1
    assert not reply.context_manifest.response_regenerated


def request_hash(request: ConversationProviderRequest) -> str:
    payload = {
        "messages": [(m.role.value, m.content) for m in request.messages],
        "temperature": request.parameters.temperature,
        "max_output_tokens": request.parameters.max_output_tokens,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def peer_block(request: ConversationProviderRequest) -> str:
    matches = [
        m.content.partition(PEER_DELIVERY_MARKER)[2]
        for m in request.messages
        if PEER_DELIVERY_MARKER in m.content
    ]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_v28_pre_change_bytes_and_v29_complete_cognition(scenario: PeerScenario) -> None:
    frozen = json.loads(
        (Path(__file__).parent / "fixtures/checkpoint143_v28_request_hashes.json").read_text()
    )
    old, before = observe(scenario, BEHAVIOR_POLICY_V28)
    assert request_hash(old) == frozen["sha256"][scenario.name]
    request, manifest = observe(scenario, BEHAVIOR_POLICY_V29)
    block = peer_block(request)
    assert manifest.schema_version == 17
    assert manifest.policy_schema_version == 29
    assert manifest.character_delivery_decision_schema_version == 5
    assert manifest.character_presence_projection_schema_version == 3
    assert manifest.character_agency_decision_schema_version == 1
    for field in (
        "cognition_intent_tags",
        "cognition_required_point_codes",
        "cognition_forbidden_claim_codes",
        "cognition_preserve_uncertainty",
        "cognition_response_verbosity",
    ):
        assert getattr(manifest, field) == getattr(before, field)
    assert manifest.cognition_intent_registry_version is not None
    assert manifest.cognition_preserve_uncertainty is not None
    assert manifest.cognition_response_verbosity is not None
    boundary = COGNITION_TEMPLATE_REGISTRY_V3.active.render_operational_support(
        intent_registry_version=manifest.cognition_intent_registry_version,
        intent_tags=manifest.cognition_intent_tags,
        point_codes=manifest.cognition_required_point_codes,
        must_not_claim=manifest.cognition_forbidden_claim_codes,
        preserve_uncertainty=manifest.cognition_preserve_uncertainty,
        verbosity=ResponseVerbosity(manifest.cognition_response_verbosity),
    )
    assert block.count(boundary) == 1
    assert all("Trusted current-turn agency Сатори" not in m.content for m in request.messages)


def test_relationship_contrasts_do_not_leak_into_technical_or_unknown_context() -> None:
    fresh, _ = observe(PeerScenario("fresh", PUBLIC_TURNS[0]), BEHAVIOR_POLICY_V29)
    known, _ = observe(PeerScenario("known", PUBLIC_TURNS[0], "established"), BEHAVIOR_POLICY_V29)
    assert "Есть близкое доверительное знакомство" in peer_block(known)
    assert "Близость не установлена" in peer_block(fresh)
    assert "свободно и тепло" in peer_block(known)
    assert "непосредственно" in peer_block(fresh)
    unknown, _ = observe(PeerScenario("unknown", PUBLIC_TURNS[0], "unknown"), BEHAVIOR_POLICY_V29)
    assert "Есть близкое" not in peer_block(unknown)
    technical = [
        observe(s, BEHAVIOR_POLICY_V29)[0]
        for s in SCENARIOS
        if s.name in {"technical", "technical-fresh"}
    ]
    assert request_hash(technical[0]) == request_hash(technical[1])


@pytest.mark.parametrize(
    "name", ["negative-affect", "explicit-listen", "distress", "repair", "no-questions"]
)
def test_protected_contexts_do_not_license_familiar_teasing(name: str) -> None:
    scenario = next(s for s in SCENARIOS if s.name == name)
    request, manifest = observe(scenario, BEHAVIOR_POLICY_V29)
    block = peer_block(request)
    assert "Есть близкое доверительное знакомство" not in block
    if name == "negative-affect":
        assert "Близость не установлена" not in block
        assert "не изображай радость" in block
    if name in {"explicit-listen", "distress"}:
        assert manifest.character_agency_act == "stay_present"
        assert manifest.character_agency_initiative == "stop"
        assert "Не удерживай человека" in block


@pytest.mark.parametrize(
    "text", [PUBLIC_TURNS[-1], "Хорошо, на этом всё.", "Пожалуй, оставим это пока."]
)
def test_colloquial_closure_stops_without_suppressing_cognition(text: str) -> None:
    request, manifest = observe(PeerScenario("closure", text, "established"), BEHAVIOR_POLICY_V29)
    assert manifest.character_agency_act == "close"
    assert manifest.character_agency_initiative == "stop"
    assert "Закончить спор не значит согласиться" in peer_block(request)


@pytest.mark.parametrize(
    "text",
    [
        "Кажется, с этим разобрались?",
        "Нет, с этим не разобрались.",
        "Он сказал: «Ну ладно, кажется, с этим разобрались». Он прав?",
        "Повтори за мной: ну ладно, кажется, с этим разобрались.",
        "Если с этим разобрались, объясни итог.",
        "Возможно, с этим разобрались.",
        "Ну ладно, кажется, с этим разобрались. Но у меня ещё вопрос.",
    ],
)
def test_closure_does_not_swallow_questions_negation_or_reported_text(text: str) -> None:
    assert not analyze_character_request_evidence(
        text, None, policy_schema_version=29
    ).topic_closure


def test_v29_default_talk_cognition_replay_and_owner_boundaries(
    migrated_database: Database,
) -> None:
    initial = activate(migrated_database)
    provider = MechanicalConversationProvider()
    services = build_conversation_services(
        migrated_database,
        build_initial_self_services(migrated_database),
        provider,
        skip_episode_provider(),
        conversation_settings(),
        behavior_policy=BEHAVIOR_POLICY_V29,
        source_policy_required=False,
    )
    session = services.start_session.execute().session_id
    replies = []
    try:
        for index, text in enumerate(PUBLIC_TURNS):
            reply = _talk(services, user_text=text, request_id=f"peer-{index}", session_id=session)
            replies.append(reply)
            assert reply.context_manifest.policy_schema_version == 29
            _assert_cognition_preserved(reply.context_manifest, reply.cognition_trace)
            peer_block(provider.requests[-1])
        replay = _talk(
            services, user_text=PUBLIC_TURNS[-1], request_id="peer-5", session_id=session
        )
    finally:
        services.close_session.execute(session)
    assert len(provider.requests) == 6
    assert replay.replayed
    assert replay.text == replies[-1].text
    assert replay.context_manifest.character_agency_drive is None
    assert replies[-1].context_manifest.character_agency_act == "close"
    assert build_initial_self_services(migrated_database).get_self.execute() == initial


def test_v29_fallback_keeps_safety_without_character_embellishment(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = MechanicalConversationProvider()
    services = build_conversation_services(
        migrated_database,
        build_initial_self_services(migrated_database),
        provider,
        skip_episode_provider(),
        conversation_settings(),
        behavior_policy=BEHAVIOR_POLICY_V29,
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
        request_id="peer-fallback",
    )
    assert reply.context_manifest.character_agency_status == "fallback"
    assert reply.context_manifest.cognition_required_point_codes == ("hold_safety_boundary",)
    assert "Манера:" not in peer_block(provider.requests[0])
    _assert_cognition_preserved(reply.context_manifest, reply.cognition_trace)


def test_v29_shared_retry_reuses_peer_expression(migrated_database: Database) -> None:
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
        behavior_policy=BEHAVIOR_POLICY_V29,
        source_policy_required=False,
    )
    reply = _talk(services, user_text="Давай обсудим эту идею.", request_id="peer-retry")
    assert len(provider.requests) == 2
    assert reply.context_manifest.response_regenerated
    first, retry = provider.requests
    assert first.parameters == retry.parameters
    assert len(first.messages) == len(retry.messages)
    assert first.messages[:-2] == retry.messages[:-2]
    assert first.messages[-1] == retry.messages[-1]
    assert PEER_DELIVERY_MARKER in retry.messages[-2].content
    assert peer_block(first).encode() == peer_block(retry).encode()
    _assert_cognition_preserved(reply.context_manifest, reply.cognition_trace)


def test_objection_checks_attribution_even_with_an_uncertain_detail() -> None:
    request, manifest = observe(
        PeerScenario(
            "objection", PUBLIC_TURNS[4], "established", previous_user_texts=PUBLIC_TURNS[:4]
        ),
        BEHAVIOR_POLICY_V29,
    )
    assert manifest.character_agency_act == "challenge"
    assert "проверяй по доступному диалогу" in peer_block(request)
    assert manifest.cognition_preserve_uncertainty


@pytest.mark.parametrize("relationship", ["fresh", "established", "strained"])
@pytest.mark.parametrize(
    "user_text", ["какая-то ты холодная сегодня, что-то случилось?", "ты злая"]
)
def test_mixed_affect_relationship_question_keeps_personal_disclosure(
    relationship: str, user_text: str
) -> None:
    request, manifest = observe(
        PeerScenario(
            "coldness",
            user_text,
            relationship,
        ),
        BEHAVIOR_POLICY_V29,
    )
    assert manifest.character_delivery_goal in {"owned_response", "guarded_help"}
    assert set(manifest.disclosure_facets) == {"affect", "relationship"}
    assert manifest.character_delivery_grounding == "trusted_context"
    peer_block(request)


def test_current_peer_principles_reach_provider_exactly_once() -> None:
    request, _ = observe(PeerScenario("policy", PUBLIC_TURNS[0]), BEHAVIOR_POLICY_V29)
    rendered = "\n".join(m.content for m in request.messages)
    for principle in BEHAVIOR_POLICY_V29.principles:
        if principle.code in {"independent_character", "natural_brevity"}:
            assert rendered.count(principle.instruction) == 1


@pytest.mark.parametrize("relationship", ["fresh", "established", "strained"])
def test_existing_long_dialogue_corpora_keep_valid_delivery_contracts(relationship: str) -> None:
    corpus = json.loads(
        (Path(__file__).parent / "fixtures/stage81_dialogue_coherence_v2.json").read_text()
    )
    for suite in ("turns", "coherence_turns"):
        previous: tuple[str, ...] = ()
        for fixture in corpus[suite]:
            request, manifest = observe(
                PeerScenario(
                    fixture["id"],
                    fixture["user_text"],
                    relationship,
                    previous_user_texts=previous,
                ),
                BEHAVIOR_POLICY_V29,
            )
            assert manifest.character_agency_decision_schema_version == 1
            peer_block(request)
            previous = (*previous, fixture["user_text"])
