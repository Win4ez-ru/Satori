"""V37 assembly/lifecycle scope; synthetic prose cannot establish Character quality."""

# ruff: noqa: RUF001

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from satori.application.cognition.contracts import ResponseVerbosity
from satori.application.cognition.templates import COGNITION_TEMPLATE_REGISTRY_V3
from satori.application.cognition.use_cases import (
    DeterministicCognitionPlanner,
    SafeCognitionPipeline,
)
from satori.application.conversation.character_agency import (
    CharacterAgencyAct,
    CharacterAgencyReason,
    CharacterAgencyStatus,
    CharacterAgencySubject,
)
from satori.application.conversation.character_evidence import analyze_character_request_evidence
from satori.application.conversation.coherence import analyze_dialogue_coherence
from satori.application.conversation.context import ConversationRequestBuilder
from satori.application.conversation.peer_delivery import (
    _V37_OWNED_RESPONSE,
    PEER_DELIVERY_MARKER_V35,
    PEER_DELIVERY_MARKER_V36,
    PEER_DELIVERY_MARKER_V37,
)
from satori.application.conversation.policy import (
    BEHAVIOR_POLICY_V35,
    BEHAVIOR_POLICY_V36,
    BEHAVIOR_POLICY_V37,
)
from satori.application.conversation.response_validation import ResponseRegenerationReason
from satori.composition import (
    ConversationServices,
    build_conversation_services,
    build_initial_self_services,
)
from satori.core.conversation import ConversationProviderRequest
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.models.source_policy import SourcePayloadRow
from satori.infrastructure.source_policy.installation import enroll_installation
from tests.checkpoint143_peer_delivery_corpus import PeerScenario
from tests.checkpoint143_v32_corpus import recent_for
from tests.checkpoint143_v33_corpus import SCENARIOS as HISTORICAL_SCENARIOS
from tests.checkpoint143_v33_corpus import observe as historical_observe
from tests.checkpoint143_v34_corpus import affect_context
from tests.checkpoint143_v35_corpus import SelectionObservation, mixed_affect, observe
from tests.checkpoint143_v36_corpus import SCENARIOS as V36_SCENARIOS
from tests.checkpoint143_v36_corpus import scenario as prior_question_scenario
from tests.checkpoint143_v37_corpus import (
    BY_NAME,
    CORPUS_VERSION,
    LOAD_LEVELS,
    RELATIONSHIPS,
    SCENARIOS,
    ContextUseScenario,
)
from tests.fakes import FakeAffectiveAppraisalProvider, FrozenClock, SequenceIdGenerator
from tests.test_checkpoint142_character_delivery_v9 import (
    _inclinations,
    _positions,
    _runtime_context,
)
from tests.test_checkpoint142_character_delivery_v9 import _relationship as relationship_fixture
from tests.test_checkpoint143_character_agency_integration import (
    InvalidCompletionPlanner,
    MechanicalConversationProvider,
    _assert_cognition_preserved,
    _talk,
)
from tests.test_checkpoint143_peer_delivery import request_hash
from tests.test_checkpoint143_v34_affect_expression import appraisal_response, scalar
from tests.test_conversation import activate, conversation_settings, skip_episode_provider

NOW = datetime(2026, 10, 6, 14, tzinfo=UTC)
VERSION_FIELDS = {
    "schema_version",
    "policy_id",
    "policy_schema_version",
    "character_agency_decision_schema_version",
}


def eligible(observation: SelectionObservation) -> bool:
    return (
        observation.agency.status is CharacterAgencyStatus.APPLIED
        and observation.agency.act is CharacterAgencyAct.RESPOND
        and observation.agency.subject is CharacterAgencySubject.CURRENT_EXCHANGE
        and observation.manifest.character_delivery_goal == "owned_response"
    )


def peer(request: ConversationProviderRequest, marker: str) -> str:
    matches = [message.content for message in request.messages if marker in message.content]
    assert len(matches) == 1
    assert matches[0].count(marker) == 1
    return matches[0].partition(marker)[2]


def normalized_wire(
    current: SelectionObservation, previous: SelectionObservation
) -> tuple[tuple[str, str], ...]:
    """Remove only the declared version metadata and the one eligible movement replacement."""

    old_peer = peer(previous.request, PEER_DELIVERY_MARKER_V35)
    old_movement = next(line for line in old_peer.splitlines() if line.startswith("Ход:"))
    return tuple(
        (
            message.role.value,
            message.content.replace(PEER_DELIVERY_MARKER_V37, PEER_DELIVERY_MARKER_V35)
            .replace(
                "Trusted policy: satori.conversation.behavior.v37 / v37",
                "Trusted policy: satori.conversation.behavior.v35 / v35",
            )
            .replace(_V37_OWNED_RESPONSE, old_movement),
        )
        for message in current.request.messages
    )


def test_versioned_corpus_has_public_controls_and_default_remains_v35() -> None:
    assert CORPUS_VERSION == 1
    assert len(SCENARIOS) == len(BY_NAME) == 22
    assert {item.family for item in SCENARIOS} >= {
        "ordinary",
        "summary",
        "quotation",
        "reasoning",
        "clarification",
        "protected",
    }
    assert BEHAVIOR_POLICY_V37.principles == BEHAVIOR_POLICY_V35.principles
    assert (
        inspect.signature(build_conversation_services).parameters["behavior_policy"].default
        is BEHAVIOR_POLICY_V35
    )
    assert len(ResponseRegenerationReason) == 10


@pytest.mark.parametrize("item", SCENARIOS, ids=lambda item: item.name)
@pytest.mark.parametrize("relationship", RELATIONSHIPS)
@pytest.mark.parametrize("load", LOAD_LEVELS)
def test_v37_changes_only_declared_protocol_and_eligible_movement(
    item: ContextUseScenario, relationship: str, load: float
) -> None:
    current_item = replace(item, relationship=relationship)
    affect = mixed_affect(load)
    previous = observe(current_item, BEHAVIOR_POLICY_V35, affect=affect, include_prior_cue=True)
    current = observe(current_item, BEHAVIOR_POLICY_V37, affect=affect, include_prior_cue=True)
    old_agency, new_agency = asdict(previous.agency), asdict(current.agency)
    assert old_agency.pop("schema_version") == 1
    assert new_agency.pop("schema_version") == 2
    assert new_agency == old_agency
    assert current.agency.prior_conversational_cue is None
    assert current.cognition == previous.cognition
    assert current.request.parameters == previous.request.parameters
    assert current.request.trace_id == previous.request.trace_id
    assert current.request.schema_version == previous.request.schema_version
    assert current.request.context_schema_version == previous.request.context_schema_version
    old_manifest, new_manifest = asdict(previous.manifest), asdict(current.manifest)
    assert {key for key in old_manifest if old_manifest[key] != new_manifest[key]} == VERSION_FIELDS
    assert previous.manifest.schema_version == 17
    assert current.manifest.schema_version == 18
    assert normalized_wire(current, previous) == tuple(
        (message.role.value, message.content) for message in previous.request.messages
    )
    current_peer = peer(current.request, PEER_DELIVERY_MARKER_V37)
    assert current_peer.count(_V37_OWNED_RESPONSE) == int(eligible(current))
    assert current_peer.count("Ход:") == 1
    assert PEER_DELIVERY_MARKER_V35 not in current_peer
    assert PEER_DELIVERY_MARKER_V36 not in current_peer
    assert CharacterAgencyReason.PRIOR_CONVERSATIONAL_CUE not in current.agency.reason_codes
    for principle in BEHAVIOR_POLICY_V37.principles:
        if principle.code in {"independent_character", "natural_brevity"}:
            assert current_peer.count(principle.instruction) == 1
    manifest = current.manifest
    assert manifest.cognition_intent_registry_version is not None
    assert manifest.cognition_preserve_uncertainty is not None
    assert manifest.cognition_response_verbosity is not None
    cognition_boundary = COGNITION_TEMPLATE_REGISTRY_V3.active.render_operational_support(
        intent_registry_version=manifest.cognition_intent_registry_version,
        intent_tags=manifest.cognition_intent_tags,
        point_codes=manifest.cognition_required_point_codes,
        must_not_claim=manifest.cognition_forbidden_claim_codes,
        preserve_uncertainty=manifest.cognition_preserve_uncertainty,
        verbosity=ResponseVerbosity(manifest.cognition_response_verbosity),
    )
    assert current_peer.count(cognition_boundary) == 1


@pytest.mark.parametrize(
    "name",
    [
        "news",
        "care",
        "listen",
        "stop",
        "repair",
        "correction",
        "safety",
        "news-and-task",
        "care-and-task",
    ],
)
def test_protected_and_existing_news_care_routes_keep_their_movement(name: str) -> None:
    previous = observe(BY_NAME[name], BEHAVIOR_POLICY_V35, affect=mixed_affect(0.40))
    current = observe(BY_NAME[name], BEHAVIOR_POLICY_V37, affect=mixed_affect(0.40))
    assert not eligible(previous)
    assert not eligible(current)
    assert _V37_OWNED_RESPONSE not in peer(current.request, PEER_DELIVERY_MARKER_V37)
    assert normalized_wire(current, previous) == tuple(
        (message.role.value, message.content) for message in previous.request.messages
    )


@pytest.mark.parametrize("item", SCENARIOS, ids=lambda item: item.name)
def test_analyzer_and_evidence_grammar_are_unchanged(item: ContextUseScenario) -> None:
    recent = recent_for(item)
    assert analyze_character_request_evidence(
        item.user_text, recent, policy_schema_version=37
    ) == analyze_character_request_evidence(item.user_text, recent, policy_schema_version=35)
    assert analyze_dialogue_coherence(
        item.user_text, recent, policy_schema_version=37
    ) == analyze_dialogue_coherence(item.user_text, recent, policy_schema_version=35)


def test_explicit_summary_grammar_miss_is_not_disguised_as_a_new_task_detector() -> None:
    recognized = observe(
        BY_NAME["summary-recognized"], BEHAVIOR_POLICY_V37, affect=mixed_affect(0.0)
    )
    unrecognized = observe(
        BY_NAME["summary-unrecognized"], BEHAVIOR_POLICY_V37, affect=mixed_affect(0.0)
    )
    assert recognized.agency.act is CharacterAgencyAct.HELP
    assert recognized.manifest.character_delivery_goal == "answer_precisely"
    assert not eligible(recognized)
    assert eligible(unrecognized)
    assert _V37_OWNED_RESPONSE not in peer(recognized.request, PEER_DELIVERY_MARKER_V37)
    assert _V37_OWNED_RESPONSE in peer(unrecognized.request, PEER_DELIVERY_MARKER_V37)
    # Both inputs and their exact prior exchange remain data for the foreground generator.
    for item, observation in (
        (BY_NAME["summary-recognized"], recognized),
        (BY_NAME["summary-unrecognized"], unrecognized),
    ):
        contents = [message.content for message in observation.request.messages]
        assert item.user_text == contents[-1]
        assert item.previous_user_texts[0] in contents
        assert item.previous_assistant_texts[0] in contents


def test_v36_cue_is_not_an_unintended_second_v37_intervention() -> None:
    item = prior_question_scenario("v37-prior-question")
    previous = observe(item, BEHAVIOR_POLICY_V35, affect=mixed_affect(0.0), include_prior_cue=True)
    historical = observe(
        item, BEHAVIOR_POLICY_V36, affect=mixed_affect(0.0), include_prior_cue=True
    )
    current = observe(item, BEHAVIOR_POLICY_V37, affect=mixed_affect(0.0), include_prior_cue=True)
    assert historical.agency.prior_conversational_cue is not None
    assert historical.agency.lead == "fused"
    assert current.agency.prior_conversational_cue is None
    assert current.agency.lead == previous.agency.lead == "obligation_first"
    assert normalized_wire(current, previous) == tuple(
        (message.role.value, message.content) for message in previous.request.messages
    )
    recent = recent_for(item)
    dialogue = analyze_dialogue_coherence(item.user_text, recent, policy_schema_version=37)
    evidence = analyze_character_request_evidence(item.user_text, recent, policy_schema_version=37)
    with pytest.raises(ValueError, match="cannot include the V36 conversational cue"):
        ConversationRequestBuilder(BEHAVIOR_POLICY_V37, 16000, 0.3, 768).build(
            _runtime_context(retrieval_available=False),
            user_text=item.user_text,
            trace_id="v37-forged-second-intervention",
            emotional_context=mixed_affect(0.0),
            relationship_context=relationship_fixture(item.relationship),
            recent_context=recent,
            dialogue_context=dialogue,
            cognition_trace=current.cognition,
            character_evidence=evidence,
            character_agency=historical.agency,
            position_context=_positions(None),
            inclination_context=_inclinations(None),
        )


@pytest.mark.parametrize("item", HISTORICAL_SCENARIOS, ids=lambda item: item.name)
def test_frozen_v35_public_request_bytes_and_native_parameters(item: PeerScenario) -> None:
    frozen = json.loads(
        (
            Path(__file__).parent / "fixtures/checkpoint143_v35_before_v36_request_hashes.json"
        ).read_text()
    )
    row = next(
        row for row in frozen["groups"]["historical_public"] if row["scenario_name"] == item.name
    )
    request, _ = historical_observe(item, BEHAVIOR_POLICY_V35)
    assert request_hash(request) == row["request_sha256"]
    assert asdict(request.parameters) == row["native_parameters"]


@pytest.mark.parametrize("item", V36_SCENARIOS, ids=lambda item: item.name)
def test_frozen_v36_cue_corpus_request_bytes_and_native_parameters(item: PeerScenario) -> None:
    frozen = json.loads(
        (
            Path(__file__).parent / "fixtures/checkpoint143_v36_before_v37_request_hashes.json"
        ).read_text()
    )
    observation = observe(
        item, BEHAVIOR_POLICY_V36, affect=affect_context("neutral"), include_prior_cue=True
    )
    serialized = json.dumps(
        asdict(observation.request), ensure_ascii=False, sort_keys=True, default=str
    ).encode()
    assert hashlib.sha256(serialized).hexdigest() == frozen["sha256"][item.name]
    assert asdict(observation.request.parameters) == frozen["native_parameters"][item.name]


def managed_services(
    database: Database, directory: Path, provider: MechanicalConversationProvider
) -> tuple[ConversationServices, FakeAffectiveAppraisalProvider]:
    """Enroll a reviewed synthetic installation; do not bypass production source admission."""

    enroll_installation(database, directory, reviewed=True, new_installation=True)
    appraisal = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: appraisal_response(request, neutral=True)
    )
    services = build_conversation_services(
        database,
        build_initial_self_services(database),
        provider,
        skip_episode_provider(),
        conversation_settings(source_policy_directory=directory),
        clock=FrozenClock(NOW),
        id_generator=SequenceIdGenerator(*(f"v37-{index:04d}" for index in range(100))),
        appraisal_provider=appraisal,
        behavior_policy=BEHAVIOR_POLICY_V37,
        source_policy_required=True,
    )
    services.talk.monotonic = lambda: 0.0
    services.talk.cognition_pipeline = SafeCognitionPipeline(
        planner=DeterministicCognitionPlanner(intent_registry_version=2, monotonic=lambda: 0.0),
        fallback=DeterministicCognitionPlanner(intent_registry_version=2, monotonic=lambda: 0.0),
    )
    return services, appraisal


@pytest.mark.parametrize(
    ("current", "provider_text"),
    [
        ("После перестановки на столе свободнее.", "После перестановки на столе свободнее."),
        ("Сделай краткий пересказ нашего разговора.", "Ты переставил рабочий стол к стене."),
        ("Подведи итог этого разговора.", "Ты переставил рабочий стол к стене."),
        ("Посчитай сумму 12 и 18.", "12 + 18 = 30."),
        ("Повтори точно: «Красная коробка слева».", "«Красная коробка слева»."),
    ],
)
def test_real_talk_commits_useful_or_reflexive_repetition_verbatim_without_new_gate(
    migrated_database: Database, tmp_path: Path, current: str, provider_text: str
) -> None:
    initial = activate(migrated_database)
    provider = MechanicalConversationProvider(
        scripted_texts=("Куда ты поставил лампу?", provider_text)
    )
    services, appraisal = managed_services(migrated_database, tmp_path / "installation", provider)
    session = services.start_session.execute().session_id
    first = _talk(
        services,
        user_text="Я переставил рабочий стол к стене.",
        request_id="first",
        session_id=session,
    )
    reply = _talk(services, user_text=current, request_id="second", session_id=session)
    assert reply.text == provider_text
    assert len(provider.requests) == len(appraisal.requests) == 2
    assert not reply.context_manifest.regeneration_attempted
    assert reply.context_manifest.policy_schema_version == 37
    assert reply.context_manifest.character_agency_decision_schema_version == 2
    assert reply.context_manifest.character_prior_conversational_cue is None
    _assert_cognition_preserved(reply.context_manifest, reply.cognition_trace)
    assert provider.requests[1].messages[-1].content == current
    assert any(message.content == first.text for message in provider.requests[1].messages)
    assert services.talk.get_self.execute() == initial
    for table in (
        "affective_transitions",
        "relationship_transitions",
        "satori_positions",
        "satori_inclinations",
        "personality_revisions",
    ):
        assert scalar(migrated_database, f"SELECT count(*) FROM {table}") == 0
    with migrated_database.session_factory() as unit:
        receipt = unit.get_one(SourcePayloadRow, ("assistant_reply", reply.interaction_id, 1))
        assert receipt.eligible
        assert receipt.proof_status == "complete"
        assert not receipt.legacy
        assert len(receipt.root_message_ids) == 2
        assert receipt.payload_refs == [
            {"kind": "assistant_reply", "payload_id": first.interaction_id, "version": 1}
        ]
    replay = _talk(services, user_text=current, request_id="second", session_id=session)
    assert replay.replayed
    assert replay.text == provider_text
    assert len(provider.requests) == len(appraisal.requests) == 2


def test_same_interaction_retry_reuses_v37_movement_and_authoritative_state(
    migrated_database: Database, tmp_path: Path
) -> None:
    activate(migrated_database)
    provider = MechanicalConversationProvider(
        scripted_texts=(
            "Куда ты поставил лампу?",
            "Я готов, как человек, обсудить это.",
            "WIRE-RETRY",
        )
    )
    services, appraisal = managed_services(migrated_database, tmp_path / "installation", provider)
    session = services.start_session.execute().session_id
    _talk(services, user_text="Я переставил стол к стене.", request_id="before", session_id=session)
    reply = _talk(
        services,
        user_text="У дверцы шкафа теперь снова работает магнит.",
        request_id="retry",
        session_id=session,
    )
    assert reply.text == "WIRE-RETRY"
    assert len(provider.requests) == 3
    assert len(appraisal.requests) == 2
    assert reply.context_manifest.regeneration_attempted
    assert reply.context_manifest.response_regenerated
    assert reply.context_manifest.regeneration_reason == "masculine_self_reference"
    original, retried = provider.requests[1:]
    assert original.parameters == retried.parameters
    assert original.messages[:-2] == retried.messages[:-2]
    assert original.messages[-1] == retried.messages[-1]
    assert peer(original, PEER_DELIVERY_MARKER_V37) == peer(retried, PEER_DELIVERY_MARKER_V37)
    assert _V37_OWNED_RESPONSE in peer(retried, PEER_DELIVERY_MARKER_V37)
    assert scalar(migrated_database, "SELECT count(*) FROM affective_transitions") == 0
    assert scalar(migrated_database, "SELECT count(*) FROM relationship_transitions") == 0


def test_failed_cognition_completion_preserves_conservative_fallback_and_source_admission(
    migrated_database: Database, tmp_path: Path
) -> None:
    activate(migrated_database)
    provider = MechanicalConversationProvider()
    services, appraisal = managed_services(migrated_database, tmp_path / "installation", provider)
    services.talk.cognition_pipeline = SafeCognitionPipeline(
        planner=InvalidCompletionPlanner(),  # type: ignore[arg-type]
        fallback=DeterministicCognitionPlanner(intent_registry_version=2, monotonic=lambda: 0.0),
    )
    reply = _talk(services, user_text=BY_NAME["ordinary-light"].user_text, request_id="fallback")
    assert len(provider.requests) == len(appraisal.requests) == 1
    assert reply.context_manifest.character_agency_status == "fallback"
    assert reply.context_manifest.character_agency_decision_schema_version == 2
    assert reply.context_manifest.character_prior_conversational_cue is None
    assert _V37_OWNED_RESPONSE not in peer(provider.requests[0], PEER_DELIVERY_MARKER_V37)
    _assert_cognition_preserved(reply.context_manifest, reply.cognition_trace)
    with migrated_database.session_factory() as unit:
        receipt = unit.get_one(SourcePayloadRow, ("assistant_reply", reply.interaction_id, 1))
        assert receipt.eligible
        assert receipt.proof_status == "complete"
        assert not receipt.legacy
