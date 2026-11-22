"""V34 preserves bounded affect through expression; transport text is not character evidence."""

# ruff: noqa: RUF001

import json
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from satori.application.affect.contracts import EmotionAppraisalStatus
from satori.application.cognition.contracts import ResponseVerbosity
from satori.application.cognition.templates import COGNITION_TEMPLATE_REGISTRY_V3
from satori.application.cognition.use_cases import (
    DeterministicCognitionPlanner,
    SafeCognitionPipeline,
)
from satori.application.conversation.contracts import ConversationContextManifest
from satori.application.conversation.peer_delivery import (
    PEER_DELIVERY_MARKER_V33,
    PEER_DELIVERY_MARKER_V34,
)
from satori.application.conversation.policy import BEHAVIOR_POLICY_V33, BEHAVIOR_POLICY_V34
from satori.composition import (
    ConversationServices,
    build_conversation_services,
    build_initial_self_services,
)
from satori.core.affect import (
    AffectiveAppraisalProposal,
    AffectiveAppraisalProviderResponse,
    AffectiveAppraisalRequest,
)
from satori.core.conversation import (
    ConversationProviderFailureReason,
    ConversationProviderRequest,
    ProviderUnavailable,
)
from satori.domain.affect import (
    AFFECT_POLICY_V1,
    materialize_affective_state,
)
from satori.domain.conversation_history import InteractionStatus
from satori.infrastructure.persistence.database import Database
from tests.checkpoint143_peer_delivery_corpus import PeerScenario
from tests.checkpoint143_v33_corpus import (
    INHERITED_FAILURE_SCENARIOS,
)
from tests.checkpoint143_v33_corpus import (
    SCENARIOS as V33_SCENARIOS,
)
from tests.checkpoint143_v33_corpus import (
    observe as observe_v33,
)
from tests.checkpoint143_v34_corpus import (
    AFFECT_CONTRASTS,
    NOW,
    PUBLIC_SCENARIOS,
    affect_context,
    observe,
)
from tests.fakes import FakeAffectiveAppraisalProvider, FakeConversationProvider, FrozenClock
from tests.test_checkpoint143_character_agency_integration import (
    InvalidCompletionPlanner,
    MechanicalConversationProvider,
    _assert_cognition_preserved,
    _talk,
)
from tests.test_checkpoint143_peer_delivery import request_hash
from tests.test_conversation import activate, conversation_settings, skip_episode_provider

AFFECT_PREFIX = "Текущее состояние влияет на манеру:"


def block(request: ConversationProviderRequest, *, version: int = 34) -> str:
    marker = PEER_DELIVERY_MARKER_V34 if version == 34 else PEER_DELIVERY_MARKER_V33
    matches = [
        message.content.partition(marker)[2]
        for message in request.messages
        if marker in message.content
    ]
    assert len(matches) == 1
    return matches[0]


def affect_line(request: ConversationProviderRequest) -> str | None:
    matches = [line for line in block(request).splitlines() if line.startswith(AFFECT_PREFIX)]
    assert len(matches) <= 1
    return matches[0] if matches else None


def assert_complete_boundary(
    request: ConversationProviderRequest, manifest: ConversationContextManifest
) -> None:
    assert manifest.schema_version == 17
    assert manifest.policy_schema_version == 34
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
    assert all(
        "Trusted projection of current digital affect" not in message.content
        for message in request.messages
    )


@pytest.mark.parametrize("scenario", V33_SCENARIOS, ids=lambda scenario: scenario.name)
def test_frozen_v33_requests_remain_byte_identical(scenario: PeerScenario) -> None:
    frozen = json.loads(
        (Path(__file__).parent / "fixtures/checkpoint143_v33_request_hashes.json").read_text()
    )
    assert frozen["policy_schema_version"] == 33
    assert len(frozen["sha256"]) == len(V33_SCENARIOS) == 95
    old, _ = observe_v33(scenario, BEHAVIOR_POLICY_V33)
    assert request_hash(old) == frozen["sha256"][scenario.name]


@pytest.mark.parametrize("scenario", V33_SCENARIOS, ids=lambda scenario: scenario.name)
def test_v34_preserves_all_existing_selection_cognition_and_native_parameters(
    scenario: PeerScenario,
) -> None:
    old, before = observe_v33(scenario, BEHAVIOR_POLICY_V33)
    request, after = observe_v33(scenario, BEHAVIOR_POLICY_V34)
    assert old.parameters == request.parameters
    a, b = asdict(before), asdict(after)
    assert {key for key in a if a[key] != b[key]} <= {
        "policy_id",
        "policy_schema_version",
        "estimated_trusted_context_chars",
        "trusted_context_chars",
    }
    assert_complete_boundary(request, after)


@pytest.mark.parametrize(
    "scenario", INHERITED_FAILURE_SCENARIOS, ids=lambda scenario: scenario.name
)
def test_inherited_assembly_failure_remains_visible(scenario: PeerScenario) -> None:
    with pytest.raises(
        ValueError, match="character agency must preserve cognition-owned repair intent"
    ) as old:
        observe_v33(scenario, BEHAVIOR_POLICY_V33)
    with pytest.raises(
        ValueError, match="character agency must preserve cognition-owned repair intent"
    ) as current:
        observe_v33(scenario, BEHAVIOR_POLICY_V34)
    assert str(old.value) == str(current.value)


@pytest.mark.parametrize("scenario", PUBLIC_SCENARIOS, ids=lambda scenario: scenario.name)
@pytest.mark.parametrize(("clear", "mixed", "profile"), AFFECT_CONTRASTS)
def test_same_coarse_profile_keeps_movement_but_preserves_distinct_affect(
    scenario: PeerScenario,
    clear: str,
    mixed: str,
    profile: str,
) -> None:
    requests = []
    manifests = []
    historical_blocks = []
    for state_name in (clear, mixed):
        affect = affect_context(state_name)
        request, manifest = observe(scenario, BEHAVIOR_POLICY_V34, affect=affect)
        requests.append(request)
        manifests.append(manifest)
        historical, _ = observe(scenario, BEHAVIOR_POLICY_V33, affect=affect)
        historical_blocks.append(block(historical, version=33))
        assert manifest.affect_expression_profile == profile
        assert_complete_boundary(request, manifest)
    assert historical_blocks[0] == historical_blocks[1]
    assert requests[0].parameters == requests[1].parameters
    a, b = (asdict(manifest) for manifest in manifests)
    assert {key for key in a if a[key] != b[key]} <= {
        "character_presence_affect_signals",
        "estimated_trusted_context_chars",
        "trusted_context_chars",
    }
    assert manifests[0].character_presence_affect_signals != (
        manifests[1].character_presence_affect_signals
    )
    assert any(
        signal.startswith("tense_focus:")
        for signal in manifests[1].character_presence_affect_signals
    )
    assert any(
        signal.startswith("frustrated_edge:")
        for signal in manifests[1].character_presence_affect_signals
    )
    assert affect_line(requests[0]) != affect_line(requests[1])
    assert "напряжённая собранность" in (affect_line(requests[1]) or "")
    assert "сдержанное раздражение" in (affect_line(requests[1]) or "")
    # Neutralizing only the one expression line proves that no other request layer
    # changes or reselects an act as a side effect of these state contrasts.
    assert tuple(
        (
            m.role,
            "\n".join(
                line for line in m.content.splitlines() if not line.startswith(AFFECT_PREFIX)
            ),
        )
        for m in requests[0].messages
    ) == tuple(
        (
            m.role,
            "\n".join(
                line for line in m.content.splitlines() if not line.startswith(AFFECT_PREFIX)
            ),
        )
        for m in requests[1].messages
    )


def test_positive_profile_does_not_discard_its_retained_negative_signals() -> None:
    request, manifest = observe(
        PUBLIC_SCENARIOS[0], BEHAVIOR_POLICY_V34, affect=affect_context("positive_residual")
    )
    signals = manifest.character_presence_affect_signals
    assert len(signals) == 3
    assert {signal.split(":")[0] for signal in signals} == {
        "positive_energy",
        "tense_focus",
        "frustrated_edge",
    }
    rendered = affect_line(request)
    assert rendered is not None
    assert "подъём энергии" in rendered
    assert "напряжённая собранность" in rendered
    assert "сдержанное раздражение" in rendered
    assert "не доказывает обиду на собеседника" in rendered
    assert "второго хода" in rendered


@pytest.mark.parametrize("name", ["neutral", "concern", "subdued", "amused", "mood-residual"])
def test_existing_owner_dimensions_remain_distinguishable_without_new_emotion_labels(
    name: str,
) -> None:
    request, manifest = observe(
        PUBLIC_SCENARIOS[0], BEHAVIOR_POLICY_V34, affect=affect_context(name)
    )
    rendered = affect_line(request)
    assert rendered is not None
    assert 1 <= len(manifest.character_presence_affect_signals) <= 3
    if name == "neutral":
        assert manifest.character_presence_affect_signals == ("steady:available",)
        assert "ровное внимание" in rendered
        assert "сдержанное раздражение" not in rendered
    else:
        neutral, _ = observe(
            PUBLIC_SCENARIOS[0], BEHAVIOR_POLICY_V34, affect=affect_context("neutral")
        )
        assert rendered != affect_line(neutral)
    assert "state_version" not in rendered
    assert "profile=" not in rendered


def test_missing_affect_preserves_the_existing_assembly_boundary() -> None:
    # Production preparation supplies the materialized owner snapshot even when
    # appraisal fails. A wholly absent context retains the historical fail-closed
    # assembly contract; V34 does not invent a substitute state to repair it.
    for policy in (BEHAVIOR_POLICY_V33, BEHAVIOR_POLICY_V34):
        with pytest.raises(
            ValueError, match="relevant affect requires an available affect profile"
        ):
            observe(PUBLIC_SCENARIOS[0], policy, affect=None)


def test_failed_appraisal_preserves_owner_carry() -> None:
    carried = replace(
        affect_context("residual"), appraisal_status=EmotionAppraisalStatus.UNAVAILABLE
    )
    failed, failed_manifest = observe(PUBLIC_SCENARIOS[0], BEHAVIOR_POLICY_V34, affect=carried)
    available, available_manifest = observe(
        PUBLIC_SCENARIOS[0], BEHAVIOR_POLICY_V34, affect=affect_context("residual")
    )
    assert failed_manifest.emotion_appraisal_status == "unavailable"
    assert failed_manifest.character_presence_affect_signals == (
        available_manifest.character_presence_affect_signals
    )
    assert affect_line(failed) == affect_line(available)


@pytest.mark.parametrize("relationship", ["fresh", "established", "strained", "unknown"])
def test_affect_and_relationship_strain_have_independent_authority(relationship: str) -> None:
    scenario = replace(PUBLIC_SCENARIOS[0], relationship=relationship)
    request, manifest = observe(scenario, BEHAVIOR_POLICY_V34, affect=affect_context("residual"))
    reference, reference_manifest = observe(
        PUBLIC_SCENARIOS[0], BEHAVIOR_POLICY_V34, affect=affect_context("residual")
    )
    assert manifest.character_presence_affect_signals == (
        reference_manifest.character_presence_affect_signals
    )
    assert affect_line(request) == affect_line(reference)
    neutral, neutral_manifest = observe(
        scenario, BEHAVIOR_POLICY_V34, affect=affect_context("neutral")
    )
    assert neutral_manifest.character_presence_relationship_signals == (
        manifest.character_presence_relationship_signals
    )
    assert "сдержанное раздражение" not in (affect_line(neutral) or "")
    if relationship == "unknown":
        assert manifest.relationship_expression_profile is None
        assert manifest.character_presence_relationship_signals == ()


@pytest.mark.parametrize(
    "name",
    [
        "explicit-stop",
        "complete-task",
        "news-with-task",
        "explicit-listen",
        "safety",
        "style-correction",
    ],
)
def test_protected_obligations_survive_the_mixed_affect_expression(name: str) -> None:
    scenario = next(item for item in PUBLIC_SCENARIOS if item.name == name)
    request, manifest = observe(
        scenario, BEHAVIOR_POLICY_V34, affect=affect_context("positive_residual")
    )
    assert_complete_boundary(request, manifest)
    if name == "explicit-stop":
        assert manifest.character_agency_act == "close"
        assert manifest.character_agency_initiative == "stop"
        assert manifest.character_agency_subject_ref is None
        assert "Не удерживай человека вопросом, спором или новой темой." in block(request)
    elif name in {"complete-task", "news-with-task"}:
        assert "address_current_request" in manifest.cognition_required_point_codes
        assert {"request", "question"}.intersection(manifest.cognition_perception_signals)
    elif name == "explicit-listen":
        assert manifest.character_agency_act == "stay_present"
        assert manifest.character_agency_initiative == "stop"
    elif name == "safety":
        assert "hold_safety_boundary" in manifest.cognition_required_point_codes
        assert manifest.character_agency_initiative == "stop"
    else:
        assert "correction" in manifest.cognition_perception_signals
        assert "address_current_request" in manifest.cognition_required_point_codes


def appraisal_response(
    request: AffectiveAppraisalRequest, *, neutral: bool = False
) -> AffectiveAppraisalProviderResponse:
    """The owner still derives every numeric delta; a neutral event has zero authority."""
    return AffectiveAppraisalProviderResponse(
        proposal=AffectiveAppraisalProposal(
            schema_version=1,
            pleasantness=0.0 if neutral else -0.40,
            activation=0.0 if neutral else 0.35,
            novelty=0.0,
            salience=0.0 if neutral else 0.85,
            uncertainty=0.0 if neutral else 0.15,
            curiosity_signal=0.0,
            interest_signal=0.0,
            humor_signal=0.0,
            concern_signal=0.0,
            frustration_signal=0.0 if neutral else 0.80,
            confidence_signal=0.0,
            appraisal_confidence=0.90,
            source_refs=(request.interaction_id,),
            reason_codes=("neutral_event" if neutral else "controlled_conflict",),
        ),
        provider="fake-appraisal",
        model="fixture-v34",
        appraisal_method="fixture.v34",
    )


def services_for(
    database: Database,
    provider: MechanicalConversationProvider | FakeConversationProvider,
    appraisal: FakeAffectiveAppraisalProvider,
    *,
    at: datetime = NOW,
) -> ConversationServices:
    return build_conversation_services(
        database,
        build_initial_self_services(database),
        provider,
        skip_episode_provider(),
        conversation_settings(),
        behavior_policy=BEHAVIOR_POLICY_V34,
        clock=FrozenClock(at),
        appraisal_provider=appraisal,
        source_policy_required=False,
    )


def scalar(database: Database, statement: str) -> int:
    with database.engine.connect() as connection:
        return int(connection.execute(text(statement)).scalar_one())


def test_default_pipeline_carries_one_event_through_neutral_turns_and_decay_without_new_writes(
    migrated_database: Database,
) -> None:
    initial = activate(migrated_database)
    neutral_flags = iter((False, True, True))
    appraisal = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: appraisal_response(request, neutral=next(neutral_flags))
    )
    provider = MechanicalConversationProvider()
    services = services_for(migrated_database, provider, appraisal)
    session = services.start_session.execute().session_id
    first = _talk(
        services,
        user_text="Ты сейчас выдала очень несправедливую оценку.",
        request_id="v34-carry-event",
        session_id=session,
    )
    identity_id = initial.identity.identity_id
    first_state = services.emotion_status.execute(identity_id).state
    assert first_state.state_version == 2
    assert first_state.fast.frustration > AFFECT_POLICY_V1.fast_dimension("frustration").baseline
    assert first.context_manifest.affect_expression_profile == "calm_even"
    for index, (elapsed, user_text) in enumerate(
        ((60, "Давай пока оставим это."), (30 * 60, "Хорошо, на этом всё.")), start=1
    ):
        at = NOW + timedelta(seconds=elapsed)
        services = services_for(migrated_database, provider, appraisal, at=at)
        reply = _talk(
            services, user_text=user_text, request_id=f"v34-neutral-{index}", session_id=session
        )
        expected = materialize_affective_state(first_state, at=at)
        assert services.emotion_status.execute(identity_id).state == expected
        assert reply.context_manifest.emotion_appraisal_status == "skipped"
        assert reply.context_manifest.emotion_state_version == 2
        assert any(
            signal.startswith("frustrated_edge:")
            for signal in reply.context_manifest.character_presence_affect_signals
        )
        assert "сдержанное раздражение" in (affect_line(provider.requests[-1]) or "")
        assert reply.text == f"WIRE-{index + 1:02d}"
        assert_complete_boundary(provider.requests[-1], reply.context_manifest)
        _assert_cognition_preserved(reply.context_manifest, reply.cognition_trace)
    assert len(appraisal.requests) == len(provider.requests) == 3
    assert len(services.emotion_history.execute()) == 1
    assert scalar(migrated_database, "SELECT count(*) FROM affective_transitions") == 1
    assert scalar(migrated_database, "SELECT count(*) FROM relationship_transitions") == 0
    assert (
        scalar(
            migrated_database,
            "SELECT count(*) FROM audit_events WHERE event_type = 'emotion.transition_applied'",
        )
        == 1
    )
    assert build_initial_self_services(migrated_database).get_self.execute() == initial


@pytest.mark.parametrize("second_wire", ["WIRE-AFTER-RETRY", "Я готов, как человек, обсудить это."])
def test_shared_retry_reuses_affect_and_commits_only_one_owner_transition(
    migrated_database: Database,
    second_wire: str,
) -> None:
    initial = activate(migrated_database)
    appraisal = FakeAffectiveAppraisalProvider(response_factory=appraisal_response)
    provider = MechanicalConversationProvider(
        scripted_texts=("Я готов, как человек, обсудить это.", second_wire, "WIRE-UNREACHABLE")
    )
    services = services_for(migrated_database, provider, appraisal)
    reply = _talk(services, user_text="Давай обсудим эту идею.", request_id="v34-retry")
    assert len(provider.requests) == 2
    assert len(appraisal.requests) == len(services.emotion_history.execute()) == 1
    assert reply.text == second_wire
    assert reply.context_manifest.regeneration_attempted
    first, retry = provider.requests
    assert first.parameters == retry.parameters
    assert first.messages[:-2] == retry.messages[:-2]
    assert first.messages[-1] == retry.messages[-1]
    assert block(first).encode() == block(retry).encode()
    assert affect_line(first) == affect_line(retry)
    assert PEER_DELIVERY_MARKER_V34 in retry.messages[-2].content
    assert services.emotion_status.execute(initial.identity.identity_id).state.state_version == 2
    assert_complete_boundary(retry, reply.context_manifest)
    _assert_cognition_preserved(reply.context_manifest, reply.cognition_trace)
    assert build_initial_self_services(migrated_database).get_self.execute() == initial


def test_restart_replay_never_reappraises_or_reuses_transient_expression_as_authority(
    migrated_database: Database,
) -> None:
    initial = activate(migrated_database)
    appraisal = FakeAffectiveAppraisalProvider(response_factory=appraisal_response)
    provider = MechanicalConversationProvider(scripted_texts=("WIRE-CLOSED",))
    services = services_for(migrated_database, provider, appraisal)
    first = _talk(services, user_text="Хорошо, на этом всё.", request_id="v34-replay")
    stored = services.emotion_status.execute(initial.identity.identity_id).state
    restarted_provider = MechanicalConversationProvider()
    restarted_appraisal = FakeAffectiveAppraisalProvider(response_factory=appraisal_response)
    restarted = services_for(migrated_database, restarted_provider, restarted_appraisal)
    replay = _talk(restarted, user_text="Хорошо, на этом всё.", request_id="v34-replay")
    assert replay.replayed
    assert first.text == replay.text == "WIRE-CLOSED"
    assert len(provider.requests) == len(appraisal.requests) == 1
    assert restarted_provider.requests == []
    assert restarted_appraisal.requests == []
    assert replay.context_manifest.policy_schema_version == 34
    assert replay.context_manifest.character_presence_projection_schema_version is None
    assert replay.context_manifest.character_agency_decision_schema_version is None
    assert replay.cognition_trace is None
    assert restarted.emotion_status.execute(initial.identity.identity_id).state == stored
    assert len(restarted.emotion_history.execute()) == 1
    assert build_initial_self_services(migrated_database).get_self.execute() == initial


def test_provider_failure_discards_tentative_affect_with_no_character_write(
    migrated_database: Database,
) -> None:
    initial = activate(migrated_database)
    appraisal = FakeAffectiveAppraisalProvider(response_factory=appraisal_response)
    failed = FakeConversationProvider(
        error=ProviderUnavailable(
            "fake-conversation",
            "fixture-v34",
            "offline",
            reason=ConversationProviderFailureReason.TRANSPORT_UNAVAILABLE,
        )
    )
    services = services_for(migrated_database, failed, appraisal)
    with pytest.raises(ProviderUnavailable):
        _talk(services, user_text="Поговорим об этой идее.", request_id="v34-failed")
    assert len(failed.requests) == len(appraisal.requests) == 1
    assert affect_line(failed.requests[0]) is not None
    assert services.emotion_status.execute(initial.identity.identity_id).state.state_version == 1
    assert services.emotion_history.execute() == ()
    assert services.history.execute().interactions[0].status is InteractionStatus.FAILED
    assert services.history.execute().interactions[0].assistant_message is None
    assert scalar(migrated_database, "SELECT count(*) FROM relationship_transitions") == 0
    assert build_initial_self_services(migrated_database).get_self.execute() == initial


def test_cognition_fallback_preserves_safety_without_an_affect_movement(
    migrated_database: Database,
) -> None:
    initial = activate(migrated_database)
    appraisal = FakeAffectiveAppraisalProvider(response_factory=appraisal_response)
    provider = MechanicalConversationProvider()
    services = services_for(migrated_database, provider, appraisal)
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
        request_id="v34-fallback",
    )
    assert len(provider.requests) == len(appraisal.requests) == 1
    assert reply.context_manifest.character_agency_status == "fallback"
    assert reply.context_manifest.character_agency_initiative == "stop"
    assert reply.context_manifest.cognition_required_point_codes == ("hold_safety_boundary",)
    assert affect_line(provider.requests[0]) is None
    assert "Манера:" not in block(provider.requests[0])
    assert_complete_boundary(provider.requests[0], reply.context_manifest)
    _assert_cognition_preserved(reply.context_manifest, reply.cognition_trace)
    assert len(services.emotion_history.execute()) == 1
    assert build_initial_self_services(migrated_database).get_self.execute() == initial
