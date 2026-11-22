"""V33 reference delivery and production contracts; reply semantics stay human-reviewed."""

# ruff: noqa: RUF001

import asyncio
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from sqlalchemy import text

from satori.application.cognition.contracts import ResponseVerbosity
from satori.application.cognition.templates import (
    COGNITION_TEMPLATE_REGISTRY_V3,
    CognitionStrategyTemplate,
)
from satori.application.cognition.use_cases import (
    DeterministicCognitionPlanner,
    SafeCognitionPipeline,
)
from satori.application.conversation import context as context_module
from satori.application.conversation.character_delivery_contracts import CharacterPresenceProjection
from satori.application.conversation.character_evidence import (
    CharacterClosureKind,
    CharacterRequestEvidence,
    analyze_character_request_evidence,
)
from satori.application.conversation.contracts import BehaviorPolicy, ConversationContextManifest
from satori.application.conversation.peer_delivery import (
    _V33_CARE,
    _V33_CLAIMED_RESOLUTION,
    _V33_NEWS,
    PEER_DELIVERY_MARKER,
    PEER_DELIVERY_MARKER_V30,
    PEER_DELIVERY_MARKER_V31,
    PEER_DELIVERY_MARKER_V32,
    PEER_DELIVERY_MARKER_V33,
    render_peer_presence,
)
from satori.application.conversation.policy import (
    BEHAVIOR_POLICY_V31,
    BEHAVIOR_POLICY_V32,
    BEHAVIOR_POLICY_V33,
)
from satori.composition import (
    ConversationServices,
    build_conversation_services,
    build_initial_self_services,
)
from satori.core.conversation import ConversationProviderRequest
from satori.infrastructure.persistence.database import Database
from tests.checkpoint143_peer_delivery_corpus import PUBLIC_TURNS, PeerScenario
from tests.checkpoint143_v32_corpus import SCENARIOS as V32_SCENARIOS
from tests.checkpoint143_v32_corpus import observe as observe_v32
from tests.checkpoint143_v33_corpus import (
    INHERITED_FAILURE_SCENARIOS,
    MIXED_STYLE_SELF_TURNS,
    SCENARIOS,
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


def block(request: ConversationProviderRequest) -> str:
    matches = [
        message.content.partition(PEER_DELIVERY_MARKER_V33)[2]
        for message in request.messages
        if PEER_DELIVERY_MARKER_V33 in message.content
    ]
    assert len(matches) == 1
    assert all(
        old not in message.content
        for message in request.messages
        for old in (
            PEER_DELIVERY_MARKER,
            PEER_DELIVERY_MARKER_V30,
            PEER_DELIVERY_MARKER_V31,
            PEER_DELIVERY_MARKER_V32,
            "Trusted current-turn agency Сатори",
        )
    )
    return matches[0]


def assert_complete_boundary(
    request: ConversationProviderRequest,
    manifest: ConversationContextManifest,
    *,
    reference_principles: bool = True,
) -> None:
    assert manifest.schema_version == 17
    assert manifest.policy_schema_version == 33
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
    if reference_principles:
        for principle in BEHAVIOR_POLICY_V31.principles:
            if principle.code in {"independent_character", "natural_brevity"}:
                assert block(request).count(principle.instruction) == 1


@pytest.mark.parametrize("scenario", V32_SCENARIOS, ids=lambda scenario: scenario.name)
def test_every_frozen_v32_request_remains_byte_identical(scenario: PeerScenario) -> None:
    frozen = json.loads(
        (Path(__file__).parent / "fixtures/checkpoint143_v32_request_hashes.json").read_text()
    )
    assert frozen["policy_schema_version"] == 32
    assert len(frozen["sha256"]) == len(V32_SCENARIOS) == 79
    old, old_manifest = observe_v32(scenario, BEHAVIOR_POLICY_V32)
    rebuilt, rebuilt_manifest = observe(scenario, BEHAVIOR_POLICY_V32)
    assert request_hash(old) == request_hash(rebuilt) == frozen["sha256"][scenario.name]
    assert asdict(old_manifest) == asdict(rebuilt_manifest)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.name)
def test_v33_keeps_v32_selection_cognition_parameters_and_schema(scenario: PeerScenario) -> None:
    old, before = observe(scenario, BEHAVIOR_POLICY_V32)
    request, after = observe(scenario, BEHAVIOR_POLICY_V33)
    assert request.parameters == old.parameters
    a, b = asdict(before), asdict(after)
    differing = {key for key in a if a[key] != b[key]}
    assert differing <= {
        "policy_id",
        "policy_schema_version",
        "estimated_trusted_context_chars",
        "trusted_context_chars",
    }
    assert_complete_boundary(request, after)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.name)
def test_local_guidance_is_confined_to_unprotected_selected_news_or_care(
    scenario: PeerScenario,
) -> None:
    request, manifest = observe(scenario, BEHAVIOR_POLICY_V33)
    protected = bool({"request", "question"}.intersection(manifest.cognition_perception_signals))
    news = (
        not protected
        and manifest.character_agency_act == "acknowledge"
        and manifest.character_agency_subject == "current_exchange"
        and manifest.character_delivery_goal == "celebrate_and_continue"
    )
    care = (
        not protected
        and manifest.character_agency_act == "care"
        and manifest.character_agency_subject == "user_explicit_state"
        and manifest.character_delivery_goal == "practical_care"
    )
    current = block(request)
    assert current.count(_V33_NEWS) == int(news)
    assert current.count(_V33_CARE) == int(care)
    assert (
        "Продолжение уже выбрано: используй одну конкретную зацепку этой новости" in current
    ) == (news and manifest.character_agency_initiative == "advance_current")
    evidence = analyze_character_request_evidence(
        scenario.user_text, recent_for(scenario), policy_schema_version=33
    )
    assert current.count(_V33_CLAIMED_RESOLUTION) == int(
        manifest.character_agency_act == "close"
        and evidence.closure_kind is CharacterClosureKind.CLAIMED_RESOLUTION
    )
    if evidence.closure_kind is CharacterClosureKind.END_DISCUSSION:
        assert manifest.character_agency_initiative == "stop"
        assert manifest.character_agency_subject_ref is None
        assert "Не удерживай человека вопросом, спором или новой темой." in current


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.name)
def test_nonlocal_v33_requests_keep_exact_v31_reference_except_retained_bounds(
    scenario: PeerScenario,
) -> None:
    request, after = observe(scenario, BEHAVIOR_POLICY_V33)
    current = block(request)
    if _V33_NEWS in current or _V33_CARE in current:
        return
    if scenario.name in {
        "explicit-all-new",
        "mixed-style-self-1",
        "mixed-style-self-2",
        "mixed-style-self-3",
    }:
        # These narrow V32 crash repairs intentionally retain historical failure.
        with pytest.raises(
            ValueError, match="interests facet requires a direct Satori self request"
        ):
            observe(scenario, BEHAVIOR_POLICY_V31)
        return
    old, before = observe(scenario, BEHAVIOR_POLICY_V31)
    a, b = asdict(before), asdict(after)
    if any(b[key] != a[key] for key in a if key.startswith("character_agency_")):
        evidence = analyze_character_request_evidence(
            scenario.user_text, recent_for(scenario), policy_schema_version=33
        )
        assert evidence.closure_kind is CharacterClosureKind.END_DISCUSSION
        assert after.character_agency_initiative == "stop"
        return
    assert tuple(
        (
            message.role,
            message.content.replace(PEER_DELIVERY_MARKER_V33, PEER_DELIVERY_MARKER_V31)
            .replace(
                "Trusted policy: satori.conversation.behavior.v33 / v33",
                "Trusted policy: satori.conversation.behavior.v31 / v31",
            )
            .replace(_V33_CLAIMED_RESOLUTION + "\n", ""),
        )
        for message in request.messages
    ) == tuple((message.role, message.content) for message in old.messages)


@pytest.mark.parametrize(
    "name",
    [
        "news-with-complete-task",
        "care-with-complete-task",
        "care-with-explicit-help",
        "news-with-direct-question",
        "care-with-direct-question",
    ],
)
def test_compound_tasks_and_questions_keep_existing_answer_scope(name: str) -> None:
    scenario = next(scenario for scenario in SCENARIOS if scenario.name == name)
    request, manifest = observe(scenario, BEHAVIOR_POLICY_V33)
    assert {"request", "question"}.intersection(manifest.cognition_perception_signals)
    assert "address_current_request" in manifest.cognition_required_point_codes
    assert _V33_NEWS not in block(request)
    assert _V33_CARE not in block(request)
    assert "Продолжение уже выбрано:" not in block(request)
    assert_complete_boundary(request, manifest)


@pytest.mark.parametrize(
    "name",
    [
        "news-with-unrecognized-clarification-command",
        "care-with-unrecognized-clarification-command",
        "recognized-news-with-unrecognized-clarification-command",
    ],
)
def test_unrecognized_commands_remain_explicit_intake_limits_with_identical_metadata(
    name: str,
) -> None:
    scenario = next(scenario for scenario in SCENARIOS if scenario.name == name)
    _, before = observe(scenario, BEHAVIOR_POLICY_V32)
    request, after = observe(scenario, BEHAVIOR_POLICY_V33)
    assert not {"request", "question"}.intersection(before.cognition_perception_signals)
    assert before.cognition_perception_signals == after.cognition_perception_signals
    assert before.cognition_required_point_codes == after.cognition_required_point_codes
    assert before.character_agency_act == after.character_agency_act
    current = block(request)
    if _V33_NEWS in current or _V33_CARE in current:
        # The intake gap is retained, while short-response sufficiency remains
        # conditional in the provider's actual authoritative guidance.
        assert "отсутствии запроса фактов, объяснения или конкретной работы" in current
        assert "в этой же реплике выполни полностью, не заменяй его" in current
    assert_complete_boundary(request, after)


@pytest.mark.parametrize(
    "scenario", INHERITED_FAILURE_SCENARIOS, ids=lambda scenario: scenario.name
)
def test_known_inherited_mixed_repair_failure_is_retained_exactly(scenario: PeerScenario) -> None:
    with pytest.raises(
        ValueError, match="character agency must preserve cognition-owned repair intent"
    ) as old:
        observe(scenario, BEHAVIOR_POLICY_V32)
    with pytest.raises(
        ValueError, match="character agency must preserve cognition-owned repair intent"
    ) as current:
        observe(scenario, BEHAVIOR_POLICY_V33)
    assert str(old.value) == str(current.value)


def test_direct_v33_rendering_rejects_missing_authoritative_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[CharacterPresenceProjection] = []

    def capture(
        projection: CharacterPresenceProjection,
        *,
        cognition_template: CognitionStrategyTemplate,
        policy: BehaviorPolicy,
        evidence: CharacterRequestEvidence | None = None,
        protected_request: bool = False,
    ) -> str:
        captured.append(projection)
        return render_peer_presence(
            projection,
            cognition_template=cognition_template,
            policy=policy,
            evidence=evidence,
            protected_request=protected_request,
        )

    monkeypatch.setattr(context_module, "render_peer_presence", capture)
    observe(SCENARIOS[0], BEHAVIOR_POLICY_V33)
    assert len(captured) == 1
    with pytest.raises(ValueError, match="requires typed current-turn evidence"):
        render_peer_presence(
            captured[0],
            cognition_template=COGNITION_TEMPLATE_REGISTRY_V3.active,
            policy=BEHAVIOR_POLICY_V33,
        )


def services_for(
    database: Database, provider: MechanicalConversationProvider
) -> ConversationServices:
    return build_conversation_services(
        database,
        build_initial_self_services(database),
        provider,
        skip_episode_provider(),
        conversation_settings(),
        behavior_policy=BEHAVIOR_POLICY_V33,
        source_policy_required=False,
    )


def test_default_v33_public_flows_commit_exact_transport_and_keep_self(
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
                        request_id=f"v33-{name}-{index}",
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
    reply = _talk(services, user_text=scenario.user_text, request_id="v33-explicit-all")
    assert reply.text == "WIRE-COMPLETE"
    assert len(provider.requests) == 1
    assert not reply.context_manifest.regeneration_attempted
    assert "address_current_request" in reply.context_manifest.cognition_required_point_codes
    assert_complete_boundary(provider.requests[0], reply.context_manifest)
    _assert_cognition_preserved(reply.context_manifest, reply.cognition_trace)
    assert build_initial_self_services(migrated_database).get_self.execute() == initial


@pytest.mark.parametrize("user_text", MIXED_STYLE_SELF_TURNS)
def test_default_mixed_style_self_scope_preserves_correction_and_factual_bounds(
    migrated_database: Database, user_text: str
) -> None:
    initial = activate(migrated_database)
    provider = MechanicalConversationProvider(scripted_texts=("WIRE-MIXED-SCOPE",))
    services = services_for(migrated_database, provider)
    reply = _talk(services, user_text=user_text, request_id="v33-mixed-style-self")
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
@pytest.mark.parametrize("user_text", [PUBLIC_TURNS[2], PUBLIC_TURNS[3]])
def test_news_and_care_commit_either_transport_shape_without_judging_or_rewriting(
    migrated_database: Database, user_text: str, wire_reply: str
) -> None:
    initial = activate(migrated_database)
    provider = MechanicalConversationProvider(scripted_texts=(wire_reply,))
    services = services_for(migrated_database, provider)
    reply = _talk(services, user_text=user_text, request_id="v33-ordinary-shape")
    assert reply.text == wire_reply
    assert len(provider.requests) == 1
    assert not reply.context_manifest.regeneration_attempted
    assert_complete_boundary(provider.requests[0], reply.context_manifest)
    assert build_initial_self_services(migrated_database).get_self.execute() == initial


def test_restart_replay_has_no_new_provider_call_or_transient_authority(
    migrated_database: Database,
) -> None:
    initial = activate(migrated_database)
    provider = MechanicalConversationProvider(scripted_texts=("WIRE-CLOSED",))
    services = services_for(migrated_database, provider)
    first = _talk(services, user_text="Хорошо, на этом всё.", request_id="v33-replay")
    post_response = asyncio.run(
        services.post_response.execute(first.interaction_id, trace_id="v33-post-response")
    )
    restarted_provider = MechanicalConversationProvider()
    restarted = services_for(migrated_database, restarted_provider)
    replay = _talk(restarted, user_text="Хорошо, на этом всё.", request_id="v33-replay")
    assert post_response.succeeded
    assert first.text == replay.text == "WIRE-CLOSED"
    assert len(provider.requests) == 1
    assert restarted_provider.requests == []
    assert replay.replayed
    assert replay.context_manifest.policy_schema_version == 33
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


@pytest.mark.parametrize(
    "second_wire", ["Транспортный маркер после повтора.", "Я готов, как человек, обсудить это."]
)
def test_shared_max_one_retry_reuses_exact_v33_movement(
    migrated_database: Database, second_wire: str
) -> None:
    initial = activate(migrated_database)
    provider = MechanicalConversationProvider(
        scripted_texts=(
            "Я готов, как человек, обсудить это.",
            second_wire,
            "WIRE-UNREACHABLE",
        )
    )
    services = services_for(migrated_database, provider)
    reply = _talk(services, user_text="Давай обсудим эту идею.", request_id="v33-retry")
    # A second invalid fixture cannot expand the existing retry budget or become
    # semantic acceptance evidence. The existing production validator is unchanged.
    assert len(provider.requests) == 2
    assert reply.text == second_wire
    assert reply.context_manifest.regeneration_attempted
    assert reply.context_manifest.response_regenerated
    first, retry = provider.requests
    assert first.parameters == retry.parameters
    assert first.messages[:-2] == retry.messages[:-2]
    assert first.messages[-1] == retry.messages[-1]
    assert block(first).encode() == block(retry).encode()
    assert PEER_DELIVERY_MARKER_V33 in retry.messages[-2].content
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
        request_id="v33-fallback",
    )
    assert len(provider.requests) == 1
    assert reply.context_manifest.character_agency_status == "fallback"
    assert reply.context_manifest.character_agency_initiative == "stop"
    assert reply.context_manifest.cognition_required_point_codes == ("hold_safety_boundary",)
    assert "Манера:" not in block(provider.requests[0])
    # Fallback omits peer principles/style, while the whole cognition support
    # must still appear once on the actual transport request.
    assert_complete_boundary(
        provider.requests[0], reply.context_manifest, reference_principles=False
    )
    _assert_cognition_preserved(reply.context_manifest, reply.cognition_trace)
    assert build_initial_self_services(migrated_database).get_self.execute() == initial
