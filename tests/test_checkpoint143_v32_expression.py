"""V32 request, lifecycle and history contracts; generated prose is not an oracle."""

# ruff: noqa: RUF001

import asyncio
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from sqlalchemy import text

from satori.application.cognition.contracts import ResponseVerbosity
from satori.application.cognition.templates import COGNITION_TEMPLATE_REGISTRY_V3
from satori.application.cognition.use_cases import (
    DeterministicCognitionPlanner,
    SafeCognitionPipeline,
)
from satori.application.conversation.character_evidence import (
    CharacterClosureKind,
    analyze_character_request_evidence,
)
from satori.application.conversation.coherence import analyze_dialogue_coherence
from satori.application.conversation.context import plan_conversational_disclosure
from satori.application.conversation.contracts import ConversationContextManifest
from satori.application.conversation.disclosure_contracts import (
    ConversationalDisclosureMode,
    ConversationalDisclosurePlan,
    DisclosureFacet,
    DisclosureRequestKind,
    is_satori_self_disclosure_plan,
)
from satori.application.conversation.peer_delivery import (
    PEER_DELIVERY_MARKER,
    PEER_DELIVERY_MARKER_V30,
    PEER_DELIVERY_MARKER_V31,
    PEER_DELIVERY_MARKER_V32,
)
from satori.application.conversation.policy import BEHAVIOR_POLICY_V31, BEHAVIOR_POLICY_V32
from satori.composition import (
    ConversationServices,
    build_conversation_services,
    build_initial_self_services,
)
from satori.core.conversation import ConversationProviderRequest
from satori.infrastructure.persistence.database import Database
from tests.checkpoint143_peer_delivery_corpus import PUBLIC_TURNS, PeerScenario
from tests.checkpoint143_v31_corpus import SCENARIOS as V31_SCENARIOS
from tests.checkpoint143_v31_corpus import observe as observe_v31
from tests.checkpoint143_v32_corpus import (
    NEW_SCENARIOS,
    SCENARIOS,
    ExpressionScenario,
    observe,
    recent_for,
)
from tests.test_checkpoint143_character_agency_integration import (
    PUBLIC_FLOWS,
    InvalidCompletionPlanner,
    MechanicalConversationProvider,
    _assert_cognition_preserved,
    _talk,
)
from tests.test_checkpoint143_peer_delivery import request_hash
from tests.test_conversation import activate, conversation_settings, skip_episode_provider

MIXED_STYLE_SELF_TURNS = (
    "Не задавай в конце дежурный вопрос. Чем ты сейчас увлечена?",
    "Ответь на каждый вопрос: кто ты, какие у тебя интересы? Не задавай в конце дежурный вопрос.",
    "Ответь на каждый вопрос: чем ты сейчас увлечена и как себя чувствуешь? "
    "Не задавай в конце дежурный вопрос.",
)


def block(request: ConversationProviderRequest) -> str:
    matches = [
        message.content.partition(PEER_DELIVERY_MARKER_V32)[2]
        for message in request.messages
        if PEER_DELIVERY_MARKER_V32 in message.content
    ]
    assert len(matches) == 1
    assert all(
        old not in message.content
        for message in request.messages
        for old in (
            PEER_DELIVERY_MARKER,
            PEER_DELIVERY_MARKER_V30,
            PEER_DELIVERY_MARKER_V31,
            "Trusted current-turn agency Сатори",
        )
    )
    return matches[0]


def assert_complete_boundary(
    request: ConversationProviderRequest, manifest: ConversationContextManifest
) -> None:
    assert manifest.schema_version == 17
    assert manifest.policy_schema_version == 32
    assert manifest.character_agency_decision_schema_version == 1
    assert manifest.character_delivery_decision_schema_version == 5
    assert manifest.character_presence_projection_schema_version == 3
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
    assert block(request).count(boundary) == 1


@pytest.mark.parametrize("scenario", V31_SCENARIOS, ids=lambda scenario: scenario.name)
def test_every_frozen_v31_request_remains_byte_identical(scenario: PeerScenario) -> None:
    frozen = json.loads(
        (Path(__file__).parent / "fixtures/checkpoint143_v31_request_hashes.json").read_text()
    )
    assert frozen["policy_schema_version"] == 31
    assert len(frozen["sha256"]) == len(V31_SCENARIOS) == 58
    old, old_manifest = observe_v31(scenario, BEHAVIOR_POLICY_V31)
    rebuilt, rebuilt_manifest = observe(scenario, BEHAVIOR_POLICY_V31)
    assert request_hash(old) == request_hash(rebuilt) == frozen["sha256"][scenario.name]
    assert asdict(old_manifest) == asdict(rebuilt_manifest)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.name)
def test_v32_keeps_complete_cognition_and_frozen_typed_movement(scenario: PeerScenario) -> None:
    if scenario.name == "explicit-all-new":
        # V31 misreads its completeness prefix as a question ban, producing an
        # invalid STYLE_CALIBRATION/interests plan. Keep that historical control.
        with pytest.raises(
            ValueError, match="interests facet requires a direct Satori self request"
        ):
            observe(scenario, BEHAVIOR_POLICY_V31)
        request, after = observe(scenario, BEHAVIOR_POLICY_V32)
        assert_complete_boundary(request, after)
        assert "address_current_request" in after.cognition_required_point_codes
        return
    old, before = observe(scenario, BEHAVIOR_POLICY_V31)
    request, after = observe(scenario, BEHAVIOR_POLICY_V32)
    assert_complete_boundary(request, after)
    evidence = analyze_character_request_evidence(
        scenario.user_text, recent_for(scenario), policy_schema_version=32
    )
    a, b = asdict(before), asdict(after)
    assert all(b[key] == a[key] for key in a if key.startswith("cognition_"))
    if evidence.closure_kind is not CharacterClosureKind.END_DISCUSSION:
        assert request.parameters == old.parameters
        assert all(b[key] == a[key] for key in a if key.startswith("character_agency_"))
    else:
        assert after.character_agency_initiative == "stop"
        if before.character_agency_initiative == "stop":
            # Explicit stopping leaves higher-priority guarded/correction routes
            # in place; it does not force CLOSE over those existing obligations.
            assert request.parameters == old.parameters
            assert all(b[key] == a[key] for key in a if key.startswith("character_agency_"))
        else:
            assert after.character_agency_act == "close"
            assert after.character_agency_subject == "current_exchange"
            assert after.character_agency_source_refs == (
                "checkpoint143-current-interaction",
                "checkpoint143-current-user",
            )
        assert after.character_agency_subject_ref is None


@pytest.mark.parametrize(
    "scenario",
    [scenario for scenario in NEW_SCENARIOS if isinstance(scenario, ExpressionScenario)],
    ids=lambda scenario: scenario.name,
)
def test_previous_question_is_exact_history_not_a_new_resolution_or_state_owner(
    scenario: ExpressionScenario,
) -> None:
    recent = recent_for(scenario)
    evidence = analyze_character_request_evidence(
        scenario.user_text, recent, policy_schema_version=32
    )
    without_history = analyze_character_request_evidence(
        scenario.user_text, None, policy_schema_version=32
    )
    assert evidence.closure_kind is without_history.closure_kind
    request, manifest = observe(scenario, BEHAVIOR_POLICY_V32)
    assert manifest.recent_conversation_turn_count == len(scenario.previous_user_texts)
    if recent is not None:
        for turn in recent.turns:
            assert any(turn.user_content in message.content for message in request.messages)
            assert any(turn.assistant_content in message.content for message in request.messages)
            assert turn.assistant_message_id not in manifest.character_agency_source_refs
    assert_complete_boundary(request, manifest)


@pytest.mark.parametrize(
    ("name", "act", "initiative", "reason"),
    [
        ("listen-with-interest", "stay_present", "stop", "explicit_listen"),
        ("distress-with-interest", "stay_present", "stop", "high_distress"),
        ("safety-with-interest", "set_boundary", "stop", "safety_precedence"),
        ("correction-with-interest", "repair", "stop", "correction_uptake"),
    ],
)
def test_owned_interest_cannot_suppress_protected_response(
    name: str, act: str, initiative: str, reason: str
) -> None:
    scenario = next(scenario for scenario in SCENARIOS if scenario.name == name)
    request, manifest = observe(scenario, BEHAVIOR_POLICY_V32)
    assert manifest.character_agency_act == act
    assert manifest.character_agency_initiative == initiative
    assert reason in manifest.character_agency_reason_codes
    assert manifest.character_agency_subject_ref is None
    assert_complete_boundary(request, manifest)


@pytest.mark.parametrize("name", ["explicit-self", "explicit-all", "explicit-all-new", "creator"])
def test_factual_or_explicit_complete_request_keeps_required_substance(name: str) -> None:
    scenario = next(scenario for scenario in SCENARIOS if scenario.name == name)
    request, manifest = observe(scenario, BEHAVIOR_POLICY_V32)
    assert "address_current_request" in manifest.cognition_required_point_codes
    assert_complete_boundary(request, manifest)


def test_technical_answer_keeps_relationship_irrelevant() -> None:
    fresh = next(scenario for scenario in SCENARIOS if scenario.name == "technical-fresh")
    familiar = next(scenario for scenario in SCENARIOS if scenario.name == "technical")
    assert request_hash(observe(fresh, BEHAVIOR_POLICY_V32)[0]) == request_hash(
        observe(familiar, BEHAVIOR_POLICY_V32)[0]
    )


def test_direct_completeness_prefix_is_not_a_new_question_ban() -> None:
    scenario = next(scenario for scenario in SCENARIOS if scenario.name == "explicit-all-new")
    historical = analyze_dialogue_coherence(scenario.user_text, None, policy_schema_version=31)
    current = analyze_dialogue_coherence(scenario.user_text, None, policy_schema_version=32)
    default = analyze_dialogue_coherence(scenario.user_text, None)
    assert historical == default
    assert historical.current_no_routine_questions_correction
    assert not current.current_no_routine_questions_correction
    assert not current.active_no_routine_questions_correction


@pytest.mark.parametrize(
    "user_text",
    [
        "Цитата: «Ответь на каждый вопрос: как ты?»",
        "Если я скажу ответь на каждый вопрос: как ты?",
        "Не ответь на каждый вопрос: как ты?",
        "Ответь на каждый вопрос без пропусков: как ты?",
    ],
)
def test_completeness_exception_requires_the_anchored_unquoted_direct_prefix(
    user_text: str,
) -> None:
    old = analyze_dialogue_coherence(user_text, None, policy_schema_version=31)
    current = analyze_dialogue_coherence(user_text, None, policy_schema_version=32)
    assert current == old


def test_completeness_keeps_a_genuine_current_or_previous_question_correction() -> None:
    mixed = "Ответь на каждый вопрос: кто ты? Не задавай в конце дежурный вопрос."
    current = analyze_dialogue_coherence(mixed, None, policy_schema_version=32)
    assert current.current_no_routine_questions_correction
    assert current.active_no_routine_questions_correction
    scenario = next(scenario for scenario in SCENARIOS if scenario.name == "explicit-all-new")
    corrected = replace(
        scenario,
        previous_user_texts=("Не задавай в конце дежурный вопрос. Отвечай прямо.",),
    )
    active = analyze_dialogue_coherence(
        corrected.user_text, recent_for(corrected), policy_schema_version=32
    )
    assert not active.current_no_routine_questions_correction
    assert active.active_no_routine_questions_correction
    request, manifest = observe(corrected, BEHAVIOR_POLICY_V32)
    assert "no_routine_questions" in manifest.active_style_corrections
    assert_complete_boundary(request, manifest)


@pytest.mark.parametrize("user_text", MIXED_STYLE_SELF_TURNS)
def test_mixed_current_correction_keeps_orthogonal_self_scope_and_factual_facets(
    user_text: str,
) -> None:
    with pytest.raises(ValueError, match="interests facet requires a direct Satori self request"):
        plan_conversational_disclosure(user_text, policy_schema_version=31)
    plan = plan_conversational_disclosure(user_text, policy_schema_version=32)
    assert plan.primary_mode is ConversationalDisclosureMode.STYLE_CALIBRATION
    assert plan.request_kind is DisclosureRequestKind.SATORI_SELF
    assert DisclosureFacet.INTERESTS in plan.required_facets
    assert not is_satori_self_disclosure_plan(plan)
    request, manifest = observe(PeerScenario("mixed-style-self", user_text), BEHAVIOR_POLICY_V32)
    assert manifest.disclosure_primary_mode == "style_calibration"
    assert manifest.disclosure_request_kind == "satori_self"
    assert "interests" in manifest.disclosure_facets
    assert "correction" in manifest.cognition_perception_signals
    assert "self_disclosure_request" not in manifest.cognition_perception_signals
    assert manifest.character_agency_act == "repair"
    assert manifest.character_agency_initiative == "stop"
    assert "address_current_request" in manifest.cognition_required_point_codes
    assert "no_routine_questions" in manifest.active_style_corrections
    assert_complete_boundary(request, manifest)


@pytest.mark.parametrize(
    "facets",
    [
        (),
        (DisclosureFacet.ORIGIN,),
        (DisclosureFacet.RELATIONSHIP,),
        (DisclosureFacet.PROVIDER_TECHNICAL,),
    ],
)
def test_forged_style_self_scope_without_authoritative_facets_is_rejected(
    facets: tuple[DisclosureFacet, ...],
) -> None:
    with pytest.raises(ValueError, match="style self scope"):
        ConversationalDisclosurePlan(
            primary_mode=ConversationalDisclosureMode.STYLE_CALIBRATION,
            required_facets=facets,
            policy_schema_version=32,
            request_kind=DisclosureRequestKind.SATORI_SELF,
        )


@pytest.mark.parametrize("version", [25, 28, 29, 30, 31])
def test_historical_style_primary_cannot_acquire_a_new_direct_self_scope(version: int) -> None:
    with pytest.raises(
        ValueError, match="self request kind requires a direct Satori disclosure mode"
    ):
        ConversationalDisclosurePlan(
            primary_mode=ConversationalDisclosureMode.STYLE_CALIBRATION,
            required_facets=(DisclosureFacet.INTERESTS,),
            policy_schema_version=version,
            request_kind=DisclosureRequestKind.SATORI_SELF,
        )


@pytest.mark.parametrize(
    "user_text",
    [
        "Не задавай в конце дежурный вопрос. Отвечай прямо.",
        "В твоём коде прописано заканчивать реплику вопросом?",
        "Не задавай в конце дежурный вопрос. Переведи: «Чем ты сейчас увлечена?»",
        "Не задавай в конце дежурный вопрос. Если бы я спросил, чем ты сейчас увлечена?",
    ],
)
def test_style_probe_or_quoted_topic_does_not_prove_direct_self_scope(user_text: str) -> None:
    before = plan_conversational_disclosure(user_text, policy_schema_version=31)
    plan = plan_conversational_disclosure(user_text, policy_schema_version=32)
    assert plan.primary_mode is ConversationalDisclosureMode.STYLE_CALIBRATION
    assert before.request_kind is plan.request_kind is DisclosureRequestKind.NONE
    assert before.required_facets == plan.required_facets
    assert not is_satori_self_disclosure_plan(plan)


def test_generic_template_feedback_does_not_prove_direct_self_scope() -> None:
    user_text = "Ты отвечаешь по шаблону?"
    before = plan_conversational_disclosure(user_text, policy_schema_version=31)
    plan = plan_conversational_disclosure(user_text, policy_schema_version=32)
    assert before.primary_mode is plan.primary_mode is ConversationalDisclosureMode.GENERAL
    assert before.request_kind is plan.request_kind is DisclosureRequestKind.NONE
    assert before.required_facets == plan.required_facets == ()
    assert not is_satori_self_disclosure_plan(plan)


@pytest.mark.parametrize(
    ("user_text", "required_facets"),
    [
        (
            "Не задавай в конце дежурный вопрос. Как ты технически устроена?",
            {DisclosureFacet.IDENTITY, DisclosureFacet.PROVIDER_TECHNICAL},
        ),
        (
            "Не задавай в конце дежурный вопрос. Способна ли ты любить?",
            {DisclosureFacet.RELATIONSHIP, DisclosureFacet.AFFECT},
        ),
    ],
)
def test_style_primary_keeps_coupled_authority_for_a_proven_direct_self_scope(
    user_text: str, required_facets: set[DisclosureFacet]
) -> None:
    plan = plan_conversational_disclosure(user_text, policy_schema_version=32)
    assert plan.primary_mode is ConversationalDisclosureMode.STYLE_CALIBRATION
    assert plan.request_kind is DisclosureRequestKind.SATORI_SELF
    assert required_facets <= set(plan.required_facets)
    assert not is_satori_self_disclosure_plan(plan)
    request, manifest = observe(PeerScenario("coupled-style-self", user_text), BEHAVIOR_POLICY_V32)
    assert {facet.value for facet in required_facets} <= set(manifest.disclosure_facets)
    assert manifest.character_agency_act == "repair"
    assert "address_current_request" in manifest.cognition_required_point_codes
    assert_complete_boundary(request, manifest)


def test_casual_facets_are_available_without_an_enumerated_reply_checklist() -> None:
    scenario = next(scenario for scenario in SCENARIOS if scenario.name == "established-1")
    request, manifest = observe(scenario, BEHAVIOR_POLICY_V32)
    assert set(manifest.disclosure_facets) == {"affect", "interests"}
    expression = block(request)
    assert "Личные бытовые темы доступны для выбора" in expression
    assert "Личные темы в реплике:" not in expression
    assert "текущее самочувствие, интересы" not in expression
    assert_complete_boundary(request, manifest)


def test_direct_factual_self_request_keeps_fact_route_instead_of_casual_attention() -> None:
    scenario = next(scenario for scenario in SCENARIOS if scenario.name == "direct-identity")
    request, manifest = observe(scenario, BEHAVIOR_POLICY_V32)
    assert "identity" in manifest.disclosure_facets
    expression = block(request)
    assert "Запрошенные фактические темы:" in expression
    assert "Ответь на конкретный личный запрос по доступным фактам" in expression
    assert "Поделись одним доступным предметом собственного внимания" not in expression
    assert_complete_boundary(request, manifest)


def test_contextual_objection_and_unknown_detail_remain_distinct_in_the_same_move() -> None:
    scenario = next(scenario for scenario in SCENARIOS if scenario.name == "established-4")
    request, manifest = observe(scenario, BEHAVIOR_POLICY_V32)
    assert manifest.character_agency_act == "challenge"
    assert manifest.character_delivery_goal == "clarify_uncertainty"
    assert manifest.cognition_preserve_uncertainty
    expression = block(request)
    assert "Приписанные тебе слова" in expression
    assert "Обозначь конкретное неизвестное" in expression
    assert_complete_boundary(request, manifest)


def test_selected_question_does_not_receive_a_second_optional_continuation_direction() -> None:
    scenario = next(scenario for scenario in SCENARIOS if scenario.name == "activity-open")
    request, manifest = observe(scenario, BEHAVIOR_POLICY_V32)
    assert manifest.character_agency_act == "question"
    assert manifest.character_agency_initiative == "advance_current"
    assert "Продвинь текущую тему одной конкретной деталью" not in block(request)
    assert_complete_boundary(request, manifest)


@pytest.mark.parametrize(
    "name",
    ["explicit-self", "explicit-reason", "creative-request", "motivation-request", "distress"],
)
def test_ordinary_continuation_hint_cannot_repurpose_an_explicit_or_protected_task(
    name: str,
) -> None:
    scenario = next(scenario for scenario in SCENARIOS if scenario.name == name)
    request, manifest = observe(scenario, BEHAVIOR_POLICY_V32)
    assert "Продвинь текущую тему одной конкретной деталью" not in block(request)
    assert_complete_boundary(request, manifest)


def services_for(
    database: Database, provider: MechanicalConversationProvider
) -> ConversationServices:
    return build_conversation_services(
        database,
        build_initial_self_services(database),
        provider,
        skip_episode_provider(),
        conversation_settings(),
        behavior_policy=BEHAVIOR_POLICY_V32,
        source_policy_required=False,
    )


def test_historical_v32_public_flows_commit_exact_transport_and_keep_self(
    migrated_database: Database,
) -> None:
    initial = activate(migrated_database)
    provider = MechanicalConversationProvider()
    services = services_for(migrated_database, provider)
    observed = []
    for name, turns in PUBLIC_FLOWS:
        session = services.start_session.execute().session_id
        try:
            for index, user_text in enumerate(turns):
                observed.append(
                    _talk(
                        services,
                        user_text=user_text,
                        request_id=f"v32-{name}-{index}",
                        session_id=session,
                    )
                )
        finally:
            services.close_session.execute(session)
    assert len(provider.requests) == len(observed) == sum(len(turns) for _, turns in PUBLIC_FLOWS)
    for index, (reply, request) in enumerate(
        zip(observed, provider.requests, strict=True), start=1
    ):
        assert reply.text == f"WIRE-{index:02d}"
        assert not reply.context_manifest.response_regenerated
        assert_complete_boundary(request, reply.context_manifest)
        _assert_cognition_preserved(reply.context_manifest, reply.cognition_trace)
    assert build_initial_self_services(migrated_database).get_self.execute() == initial


def test_default_production_completeness_request_reaches_provider_and_commits_exactly(
    migrated_database: Database,
) -> None:
    initial = activate(migrated_database)
    provider = MechanicalConversationProvider(scripted_texts=("WIRE-COMPLETE",))
    services = services_for(migrated_database, provider)
    scenario = next(scenario for scenario in SCENARIOS if scenario.name == "explicit-all-new")
    reply = _talk(services, user_text=scenario.user_text, request_id="v32-explicit-all")
    assert reply.text == "WIRE-COMPLETE"
    assert len(provider.requests) == 1
    assert not reply.context_manifest.regeneration_attempted
    assert "address_current_request" in reply.context_manifest.cognition_required_point_codes
    assert_complete_boundary(provider.requests[0], reply.context_manifest)
    _assert_cognition_preserved(reply.context_manifest, reply.cognition_trace)
    assert build_initial_self_services(migrated_database).get_self.execute() == initial


@pytest.mark.parametrize("user_text", MIXED_STYLE_SELF_TURNS)
def test_default_production_mixed_style_and_interests_reaches_provider_without_losing_correction(
    migrated_database: Database, user_text: str
) -> None:
    initial = activate(migrated_database)
    provider = MechanicalConversationProvider(scripted_texts=("WIRE-MIXED-SCOPE",))
    services = services_for(migrated_database, provider)
    reply = _talk(services, user_text=user_text, request_id="v32-mixed-style-self")
    assert reply.text == "WIRE-MIXED-SCOPE"
    assert len(provider.requests) == 1
    assert not reply.context_manifest.regeneration_attempted
    manifest = reply.context_manifest
    assert manifest.disclosure_request_kind == "satori_self"
    assert "interests" in manifest.disclosure_facets
    assert manifest.character_agency_act == "repair"
    assert "correction" in manifest.cognition_perception_signals
    assert "self_disclosure_request" not in manifest.cognition_perception_signals
    assert "address_current_request" in manifest.cognition_required_point_codes
    assert_complete_boundary(provider.requests[0], manifest)
    _assert_cognition_preserved(manifest, reply.cognition_trace)
    assert build_initial_self_services(migrated_database).get_self.execute() == initial


@pytest.mark.parametrize("wire_reply", ["Транспортное утверждение.", "Транспортный вопрос?"])
def test_casual_shape_is_not_judged_rewritten_or_expanded(
    migrated_database: Database, wire_reply: str
) -> None:
    initial = activate(migrated_database)
    provider = MechanicalConversationProvider(scripted_texts=(wire_reply,))
    services = services_for(migrated_database, provider)
    reply = _talk(services, user_text=PUBLIC_TURNS[1], request_id="v32-casual-shape")
    assert reply.text == wire_reply
    assert len(provider.requests) == 1
    assert not reply.context_manifest.regeneration_attempted
    assert_complete_boundary(provider.requests[0], reply.context_manifest)
    assert build_initial_self_services(migrated_database).get_self.execute() == initial


def test_restart_replay_has_no_new_provider_call_or_transient_authority(
    migrated_database: Database,
) -> None:
    initial = activate(migrated_database)
    # The already public liked closure is transport output only, not a prompt template.
    provider = MechanicalConversationProvider(scripted_texts=("Ладно. Закрыли эту тему.",))
    services = services_for(migrated_database, provider)
    first = _talk(services, user_text="Хорошо, на этом всё.", request_id="v32-replay")
    post_response = asyncio.run(
        services.post_response.execute(first.interaction_id, trace_id="v32-post-response")
    )
    restarted_provider = MechanicalConversationProvider()
    restarted = services_for(migrated_database, restarted_provider)
    replay = _talk(restarted, user_text="Хорошо, на этом всё.", request_id="v32-replay")
    assert post_response.succeeded
    assert first.text == replay.text == "Ладно. Закрыли эту тему."
    assert len(provider.requests) == 1
    assert restarted_provider.requests == []
    assert replay.replayed
    assert replay.context_manifest.policy_schema_version == 32
    assert "character_agency_decision" not in replay.context_manifest.included_sections
    assert replay.context_manifest.character_agency_decision_schema_version is None
    assert replay.context_manifest.character_presence_projection_schema_version is None
    assert replay.cognition_trace is None
    assert build_initial_self_services(migrated_database).get_self.execute() == initial
    with migrated_database.engine.connect() as connection:
        transient_tables = connection.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND (lower(name) LIKE '%agency%' OR lower(name) LIKE '%closure%')"
            )
        ).all()
    assert transient_tables == []


def test_shared_max_one_retry_reuses_exact_v32_movement(migrated_database: Database) -> None:
    initial = activate(migrated_database)
    provider = MechanicalConversationProvider(
        scripted_texts=(
            "Я готов, как человек, обсудить это.",
            "Транспортный маркер после повтора.",
            "WIRE-UNREACHABLE",
        )
    )
    services = services_for(migrated_database, provider)
    reply = _talk(services, user_text="Давай обсудим эту идею.", request_id="v32-retry")
    assert len(provider.requests) == 2
    assert reply.text == "Транспортный маркер после повтора."
    assert reply.context_manifest.regeneration_attempted
    assert reply.context_manifest.response_regenerated
    first, retry = provider.requests
    assert first.parameters == retry.parameters
    assert first.messages[:-2] == retry.messages[:-2]
    assert first.messages[-1] == retry.messages[-1]
    assert block(first).encode() == block(retry).encode()
    assert PEER_DELIVERY_MARKER_V32 in retry.messages[-2].content
    assert_complete_boundary(retry, reply.context_manifest)
    _assert_cognition_preserved(reply.context_manifest, reply.cognition_trace)
    assert build_initial_self_services(migrated_database).get_self.execute() == initial


def test_fallback_keeps_safety_without_adding_style(migrated_database: Database) -> None:
    initial = activate(migrated_database)
    provider = MechanicalConversationProvider()
    services = services_for(migrated_database, provider)
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
        request_id="v32-fallback",
    )
    assert len(provider.requests) == 1
    assert reply.context_manifest.character_agency_status == "fallback"
    assert reply.context_manifest.character_agency_initiative == "stop"
    assert reply.context_manifest.cognition_required_point_codes == ("hold_safety_boundary",)
    assert "Манера:" not in block(provider.requests[0])
    assert_complete_boundary(provider.requests[0], reply.context_manifest)
    _assert_cognition_preserved(reply.context_manifest, reply.cognition_trace)
    assert build_initial_self_services(migrated_database).get_self.execute() == initial
