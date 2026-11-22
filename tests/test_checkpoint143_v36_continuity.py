"""Causal pre-generation continuity and unchanged owner/obligation boundaries."""

# ruff: noqa: RUF001

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from satori.application.cognition.use_cases import (
    DeterministicCognitionPlanner,
    SafeCognitionPipeline,
)
from satori.application.conversation.character_agency import CharacterAgencyReason
from satori.application.conversation.character_dialogue_cue import (
    project_prior_conversational_cue,
)
from satori.application.conversation.peer_delivery import (
    PEER_DELIVERY_MARKER_V35,
    PEER_DELIVERY_MARKER_V36,
)
from satori.application.conversation.policy import BEHAVIOR_POLICY_V35, BEHAVIOR_POLICY_V36
from satori.composition import ConversationServices
from satori.core.affect import AffectiveAppraisalProviderResponse, AffectiveAppraisalRequest
from satori.infrastructure.persistence.database import Database
from tests.checkpoint143_peer_delivery_corpus import PeerScenario
from tests.checkpoint143_v32_corpus import recent_for
from tests.checkpoint143_v33_corpus import SCENARIOS as HISTORICAL_SCENARIOS
from tests.checkpoint143_v33_corpus import observe as historical_observe
from tests.checkpoint143_v35_corpus import (
    CONTEXT_SCENARIOS,
    OPTIONAL_SCENARIOS,
    SelectionObservation,
    mixed_affect,
    observe,
)
from tests.checkpoint143_v36_corpus import CURRENT_TASK, SCENARIOS, scenario
from tests.fakes import FakeAffectiveAppraisalProvider
from tests.test_checkpoint143_character_agency_integration import (
    InvalidCompletionPlanner,
    MechanicalConversationProvider,
    _talk,
)
from tests.test_checkpoint143_peer_delivery import request_hash
from tests.test_checkpoint143_v34_affect_expression import appraisal_response, scalar
from tests.test_checkpoint143_v35_affect_selection import services_for
from tests.test_conversation import activate


def v36(item: PeerScenario, *, load: float = 0.0) -> SelectionObservation:
    return observe(item, BEHAVIOR_POLICY_V36, affect=mixed_affect(load), include_prior_cue=True)


def peer(observation: SelectionObservation) -> str:
    return next(
        message.content
        for message in observation.request.messages
        if PEER_DELIVERY_MARKER_V36 in message.content
    )


def test_prior_question_changes_selection_before_prose_without_changing_task_or_state() -> None:
    question = v36(scenario("question"))
    statement = v36(scenario("statement", previous="Вы давно не виделись."))
    assert question.agency.lead == "fused"
    assert statement.agency.lead == "obligation_first"
    assert question.agency.prior_conversational_cue is not None
    assert statement.agency.prior_conversational_cue is None
    assert question.cognition == statement.cognition
    assert question.request.parameters == statement.request.parameters
    for item in (question, statement):
        assert (
            item.agency.drive,
            item.agency.act,
            item.agency.subject,
            item.agency.initiative,
        ) == ("help", "help", "user_request", "stay_on_topic")
        assert item.manifest.character_delivery_goal == "answer_precisely"
        assert item.manifest.character_delivery_continuation == "complete"
        assert item.manifest.schema_version == 18
        assert item.agency.schema_version == 2
    cue = question.agency.prior_conversational_cue
    assert cue.prior_assistant_message_id not in question.agency.source_refs
    assert cue.prior_user_message_id not in question.agency.source_refs
    assert question.manifest.character_prior_conversational_cue == cue
    assert "Связка необязательна" in peer(question)
    assert (
        question.manifest.character_presence_affect_signals
        == statement.manifest.character_presence_affect_signals
    )


@pytest.mark.parametrize("item", SCENARIOS[:5], ids=lambda item: item.name)
def test_candidate_does_not_claim_to_classify_answer_decline_or_redirect(
    item: PeerScenario,
) -> None:
    observation = v36(item)
    assert observation.agency.prior_conversational_cue is not None
    assert observation.agency.prior_conversational_cue.kind == "question_candidate"
    assert not set(asdict(observation.agency.prior_conversational_cue)).intersection(
        {"uptake", "offense", "motive", "pending", "emotion"}
    )


@pytest.mark.parametrize("load", [0.0, 0.4, 0.75, 1.0])
@pytest.mark.parametrize("relationship", ["fresh", "established", "strained", "unknown"])
def test_existing_affect_readiness_and_relationship_authority_bound_optional_context(
    load: float, relationship: str
) -> None:
    item = scenario("context", relationship=relationship)
    observation = v36(item, load=load)
    baseline = observe(item, BEHAVIOR_POLICY_V35, affect=mixed_affect(load))
    assert observation.cognition == baseline.cognition
    assert observation.request.parameters == baseline.request.parameters
    assert observation.agency.act == baseline.agency.act
    assert observation.agency.subject == baseline.agency.subject
    assert (
        observation.manifest.relationship_state_version
        == baseline.manifest.relationship_state_version
    )
    assert (
        observation.manifest.character_presence_affect_signals
        == baseline.manifest.character_presence_affect_signals
    )
    if load > 0.5 or relationship == "strained":
        assert observation.agency.prior_conversational_cue is None


@pytest.mark.parametrize("item", SCENARIOS[8:], ids=lambda item: item.name)
def test_protected_routes_do_not_acquire_a_prior_question_obligation(item: PeerScenario) -> None:
    observation = v36(item)
    assert observation.agency.prior_conversational_cue is None
    assert CharacterAgencyReason.PRIOR_CONVERSATIONAL_CUE not in observation.agency.reason_codes
    assert "В последней доступной канонической реплике" not in peer(observation)


def test_absent_candidate_keeps_historical_wire_except_version_marker() -> None:
    item = scenario("no-question", previous="Вы давно не виделись.")
    current = v36(item)
    previous = observe(item, BEHAVIOR_POLICY_V35, affect=mixed_affect(0.0))
    assert current.request.parameters == previous.request.parameters
    assert [
        message.content.replace(PEER_DELIVERY_MARKER_V36, PEER_DELIVERY_MARKER_V35).replace(
            "Trusted policy: satori.conversation.behavior.v36 / v36",
            "Trusted policy: satori.conversation.behavior.v35 / v35",
        )
        for message in current.request.messages
    ] == [message.content for message in previous.request.messages]


@pytest.mark.parametrize("item", OPTIONAL_SCENARIOS + CONTEXT_SCENARIOS, ids=lambda item: item.name)
def test_no_cue_preserves_inherited_ordinary_initiative_guidance(item: PeerScenario) -> None:
    current = observe(item, BEHAVIOR_POLICY_V36, affect=mixed_affect(0.0))
    previous = observe(item, BEHAVIOR_POLICY_V35, affect=mixed_affect(0.0))
    assert current.agency.prior_conversational_cue is None
    assert current.request.parameters == previous.request.parameters
    assert [
        message.content.replace(PEER_DELIVERY_MARKER_V36, PEER_DELIVERY_MARKER_V35).replace(
            "Trusted policy: satori.conversation.behavior.v36 / v36",
            "Trusted policy: satori.conversation.behavior.v35 / v35",
        )
        for message in current.request.messages
    ] == [message.content for message in previous.request.messages]


def test_forged_cue_or_historical_schema_cannot_license_the_new_topology() -> None:
    observation = v36(scenario("question"))
    cue = observation.agency.prior_conversational_cue
    assert cue is not None
    with pytest.raises(ValueError, match="not licensed"):
        replace(observation.agency, schema_version=1)
    with pytest.raises(ValueError, match="exact int"):
        replace(observation.agency, schema_version=2.0)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="cue"):
        replace(observation.agency, prior_conversational_cue=None)
    with pytest.raises(ValueError, match="recent context"):
        replace(
            observation.manifest,
            character_prior_conversational_cue=replace(cue, prior_user_message_id="foreign-user"),
        )
    assert project_prior_conversational_cue(recent_for(scenario("question"))) == cue


@pytest.mark.parametrize("item", HISTORICAL_SCENARIOS, ids=lambda item: item.name)
def test_all_frozen_public_v35_requests_and_native_parameters_remain_exact(
    item: PeerScenario,
) -> None:
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


def interested_appraisal(request: AffectiveAppraisalRequest) -> AffectiveAppraisalProviderResponse:
    response = appraisal_response(request, neutral=True)
    return replace(
        response,
        proposal=replace(
            response.proposal,
            pleasantness=0.4,
            salience=0.8,
            curiosity_signal=0.8,
            interest_signal=0.8,
            reason_codes=("controlled_curiosity",),
        ),
    )


def configured(
    database: Database, provider: MechanicalConversationProvider
) -> tuple[ConversationServices, FakeAffectiveAppraisalProvider]:
    appraisal = FakeAffectiveAppraisalProvider(response_factory=interested_appraisal)
    services = services_for(database, provider, appraisal)
    services.talk.request_builder = replace(
        services.talk.request_builder, policy=BEHAVIOR_POLICY_V36
    )
    return services, appraisal


def test_runtime_reconstructs_cue_from_history_and_replay_skips_selection(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    provider = MechanicalConversationProvider(
        scripted_texts=("Какая часть проекта тебе интереснее?", "WIRE-TASK")
    )
    services, appraisal = configured(migrated_database, provider)
    session = services.start_session.execute().session_id
    first = _talk(
        services, user_text="У меня новый проект.", request_id="v36-first", session_id=session
    )
    task = _talk(
        services,
        user_text="Расскажи кратко, что такое DNS.",
        request_id="v36-task",
        session_id=session,
    )
    assert task.context_manifest.character_prior_conversational_cue is not None
    replay = _talk(
        services,
        user_text="Расскажи кратко, что такое DNS.",
        request_id="v36-task",
        session_id=session,
    )
    assert replay.replayed
    assert replay.context_manifest.character_prior_conversational_cue is None
    assert len(provider.requests) == len(appraisal.requests) == 2
    assert scalar(migrated_database, "SELECT count(*) FROM relationship_transitions") == 0
    assert first.context_manifest.character_agency_decision_schema_version == 2
    assert any(
        "В последней доступной канонической реплике" in message.content
        for message in provider.requests[1].messages
    )


def test_retry_keeps_cue_and_failed_completion_discards_it(migrated_database: Database) -> None:
    activate(migrated_database)
    provider = MechanicalConversationProvider(
        scripted_texts=(
            "Что тебе хочется разобрать?",
            "Я готов, как человек, обсудить это.",
            "WIRE-RETRY",
        )
    )
    services, appraisal = configured(migrated_database, provider)
    session = services.start_session.execute().session_id
    _talk(services, user_text="Продолжим.", request_id="v36-before-retry", session_id=session)
    reply = _talk(services, user_text=CURRENT_TASK, request_id="v36-retry", session_id=session)
    assert len(provider.requests) == 3
    assert len(appraisal.requests) == 2
    assert reply.context_manifest.character_prior_conversational_cue is not None
    assert provider.requests[1].messages[:-2] == provider.requests[2].messages[:-2]
    original_peer = next(
        message.content.partition(PEER_DELIVERY_MARKER_V36)[2]
        for message in provider.requests[1].messages
        if PEER_DELIVERY_MARKER_V36 in message.content
    )
    retry_peer = next(
        message.content.partition(PEER_DELIVERY_MARKER_V36)[2]
        for message in provider.requests[2].messages
        if PEER_DELIVERY_MARKER_V36 in message.content
    )
    assert original_peer == retry_peer
    services.talk.cognition_pipeline = SafeCognitionPipeline(
        planner=InvalidCompletionPlanner(),  # type: ignore[arg-type]
        fallback=DeterministicCognitionPlanner(intent_registry_version=2),
    )
    fallback = _talk(
        services, user_text=CURRENT_TASK, request_id="v36-fallback", session_id=session
    )
    assert fallback.context_manifest.character_agency_decision_schema_version == 2
    assert fallback.context_manifest.character_prior_conversational_cue is None
