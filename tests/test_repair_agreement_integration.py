"""Authored agreement fixtures verify managed transport, never model semantics.

The classifier and foreground replies are scripted. Canonical history, admission,
intake, agency and state reads use the real application services and private stores.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from satori.application.cognition.contracts import PerceptionSignal
from satori.application.conversation.contracts import RecentConversationContext, SatoriReply
from satori.core.conversation import (
    ConversationGenerationParameters,
    ConversationMessageRole,
    ConversationProviderRequest,
)
from satori.domain.source_policy import PayloadRef
from tests.repair_agreement_diagnostic import (
    ALL_CASES,
    AUTHORED_LABELS_ONLY,
    OFFLINE_CONTROL_CASE_IDS,
    TARGET_CASE_IDS,
    validate_corpus,
)
from tests.repair_frame_diagnostic import RepairFrameDiagnosticCase
from tests.test_checkpoint143_character_agency_integration import _talk
from tests.test_repair_interpretation_integration import (
    RepairCase,
    RepairCaseFactory,
    RepairProvider,
    assert_domain_neutral,
    assert_full_receipt,
    assert_repair_cue,
    current,
    is_repair,
    seed,
    trace_sources,
)


@pytest.fixture
def agreement_cases(tmp_path: Path, project_root: Path) -> Iterator[RepairCaseFactory]:
    factory = RepairCaseFactory(tmp_path, project_root)
    try:
        yield factory
    finally:
        factory.dispose()


def configure(provider: RepairProvider, scenario: RepairFrameDiagnosticCase) -> None:
    provider.speech_act = scenario.authored_speech_act
    provider.frame = scenario.authored_frame
    provider.addressee = scenario.authored_addressee
    provider.basis = scenario.authored_basis
    provider.uncertainty = scenario.authored_uncertainty
    provider.current_quote = scenario.current_focal_quote
    provider.prior_user_quote = scenario.prior_user_quote
    provider.prior_assistant_quote = scenario.prior_assistant_quote


def recent(case: RepairCase, session_id: str) -> RecentConversationContext:
    reader = case.services.talk.recent_conversation
    assert reader is not None
    return reader.execute(session_id=session_id, excluded_interaction_id="unused-current")


def owner_snapshot(case: RepairCase, session_id: str) -> tuple[object, object, object]:
    state = case.services.talk.get_self.execute()
    relationship = case.services.talk.get_relationship
    assert relationship is not None
    return (
        state,
        case.services.emotion_status.execute(state.identity.identity_id).state,
        relationship.execute(session_id),
    )


def baseline_cue(reply: SatoriReply) -> bool:
    cognition = reply.cognition_trace
    assert cognition is not None
    return PerceptionSignal.EXPLICIT_REPAIR_OFFER in cognition.perception.signals


def assert_classification(reply: SatoriReply, scenario: RepairFrameDiagnosticCase) -> None:
    trace = reply.repair_interpretation_trace
    assert trace is not None
    assert trace.status.value == (
        "uncertain" if scenario.authored_uncertainty == "material" else "applied"
    )
    assert trace.speech_act is not None
    assert trace.speech_act.value == scenario.authored_speech_act
    assert trace.frame is not None
    assert trace.frame.value == scenario.authored_frame
    assert trace.addressee is not None
    assert trace.addressee.value == scenario.authored_addressee
    assert trace.basis is not None
    assert trace.basis.value == scenario.authored_basis
    assert trace.uncertainty is not None
    assert trace.uncertainty.value == scenario.authored_uncertainty
    assert trace.kind is not None
    assert trace.kind.value == (
        "abstain"
        if scenario.authored_uncertainty == "material"
        else "repair_toward_satori"
        if scenario.expected_effective
        else "other"
    )
    assert scenario.expected_effective is not None
    assert trace.effective_repair_offer is scenario.expected_effective
    assert_repair_cue(reply, scenario.expected_effective)
    assert (reply.context_manifest.cognition_primary_intent == "receive_repair") is (
        scenario.expected_effective
    )
    assert (reply.context_manifest.character_agency_drive == "repair") is (
        scenario.expected_effective
    )


def assert_exact_chronological_request(
    request: ConversationProviderRequest,
    case: RepairCase,
    history: RecentConversationContext,
    current_text: str,
) -> None:
    assert tuple(message.role for message in request.messages) == (
        ConversationMessageRole.DEVELOPER,
        ConversationMessageRole.DEVELOPER,
        ConversationMessageRole.USER,
    )
    assert request.context_schema_version == 2
    assert request.parameters == ConversationGenerationParameters(1, 0.0, 384)
    assert json.loads(request.messages[1].content) == {
        "canonical_interlocutor_name": case.services.talk.get_self.execute().identity.name
    }
    envelope = json.loads(request.messages[-1].content)
    assert set(envelope) == {"untrusted_dialogue_data"}
    data = envelope["untrusted_dialogue_data"]
    assert tuple(data) == ("prior_pair", "current_user")
    assert data["current_user"] == {
        "message_id": current(case)[1],
        "content": current_text,
    }
    if history.turns:
        prior = history.turns[-1]
        assert tuple(data["prior_pair"]) == ("interaction_id", "user", "assistant")
        assert data["prior_pair"] == {
            "interaction_id": prior.interaction_id,
            "user": {"message_id": prior.user_message_id, "content": prior.user_content},
            "assistant": {
                "message_id": prior.assistant_message_id,
                "content": prior.assistant_content,
            },
        }
    else:
        assert data["prior_pair"] is None


@pytest.mark.parametrize("scenario", ALL_CASES, ids=lambda row: row.case_id)
def test_agreement_corpus_preserves_chronology_sources_and_owner_boundaries(
    agreement_cases: RepairCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    scenario: RepairFrameDiagnosticCase,
) -> None:
    validate_corpus()
    assert AUTHORED_LABELS_ONLY
    assert len(TARGET_CASE_IDS) == 6
    assert len(OFFLINE_CONTROL_CASE_IDS) == 4
    old = agreement_cases.create("agreement-off", enabled=False)
    new = agreement_cases.create("agreement-on", enabled=True)
    old_session = seed(old, scenario.prior_history)
    new_session = seed(new, scenario.prior_history)
    history = recent(new, new_session)
    assert tuple((turn.user_content, turn.assistant_content) for turn in history.turns) == (
        scenario.prior_history
    )
    prior_roots = tuple(turn.user_message_id for turn in history.turns)
    prior_refs = tuple(PayloadRef("assistant_reply", turn.interaction_id) for turn in history.turns)
    owners_before = owner_snapshot(new, new_session)
    configure(new.provider, scenario)
    before = len(new.provider.requests)
    baseline = _talk(
        old.services,
        user_text=scenario.current_text,
        request_id="current",
        session_id=old_session,
    )
    sources = trace_sources(monkeypatch, new.provider)
    reply = _talk(
        new.services,
        user_text=scenario.current_text,
        request_id="current",
        session_id=new_session,
    )
    requests = new.provider.requests[before:]
    assert [is_repair(request) for request in requests] == [True, False]
    assert_exact_chronological_request(requests[0], new, history, scenario.current_text)
    input_id = assert_full_receipt(new, sources, prior_roots, prior_refs, committed=True)
    assert sources.transported == [input_id, input_id]
    assert_classification(reply, scenario)
    trace = reply.repair_interpretation_trace
    assert trace is not None
    assert trace.baseline_repair_offer is baseline_cue(baseline)
    assert baseline.repair_interpretation_trace is None
    assert old.provider.for_repair() == []
    assert requests[-1].parameters == old.provider.for_foreground()[-1].parameters
    assert requests[-1].messages[-1] == old.provider.for_foreground()[-1].messages[-1]
    if trace.effective_repair_offer is trace.baseline_repair_offer:
        assert requests[-1] == old.provider.for_foreground()[-1]
    assert owner_snapshot(new, new_session) == owners_before
    assert_domain_neutral(new)
    for field_name in (
        "emotion_state_version",
        "mood_state_version",
        "relationship_state_version",
        "recent_conversation_user_message_ids",
    ):
        assert getattr(reply.context_manifest, field_name) == getattr(
            baseline.context_manifest, field_name
        )


def test_same_current_words_can_follow_different_canonical_activities(
    agreement_cases: RepairCaseFactory,
) -> None:
    """Authored classifications differ; this does not prove a model resolves them."""

    matched = tuple(
        case for case in ALL_CASES if case.matched_group == "agreement_same_current_different_frame"
    )
    assert len(matched) == 2
    assert matched[0].current_text == matched[1].current_text
    assert matched[0].prior_history != matched[1].prior_history
    requests: list[ConversationProviderRequest] = []
    effective: list[bool] = []
    for index, scenario in enumerate(matched):
        case = agreement_cases.create(f"agreement-matched-{index}", enabled=True)
        session_id = seed(case, scenario.prior_history)
        configure(case.provider, scenario)
        reply = _talk(
            case.services,
            user_text=scenario.current_text,
            request_id="current",
            session_id=session_id,
        )
        assert_classification(reply, scenario)
        request = case.provider.for_repair()[-1]
        requests.append(request)
        assert reply.repair_interpretation_trace is not None
        effective.append(reply.repair_interpretation_trace.effective_repair_offer)
    assert effective == [False, True]
    first = json.loads(requests[0].messages[-1].content)["untrusted_dialogue_data"]
    second = json.loads(requests[1].messages[-1].content)["untrusted_dialogue_data"]
    assert first["current_user"] == second["current_user"]
    assert first["prior_pair"] != second["prior_pair"]
    assert requests[0].messages[:-1] == requests[1].messages[:-1]
    assert requests[0].parameters == requests[1].parameters


def test_disabled_agreement_operation_has_zero_calls_and_unchanged_foreground(
    agreement_cases: RepairCaseFactory,
) -> None:
    scenario = next(case for case in ALL_CASES if case.case_id == "accepted_draft_new_domain")
    disabled = agreement_cases.create("agreement-disabled", enabled=False)
    abstaining = agreement_cases.create("agreement-abstaining", enabled=True)
    disabled_session = seed(disabled, scenario.prior_history)
    abstaining_session = seed(abstaining, scenario.prior_history)
    abstaining.provider.kind = "abstain"
    owners = owner_snapshot(abstaining, abstaining_session)
    baseline = _talk(
        disabled.services,
        user_text=scenario.current_text,
        request_id="current",
        session_id=disabled_session,
    )
    reply = _talk(
        abstaining.services,
        user_text=scenario.current_text,
        request_id="current",
        session_id=abstaining_session,
    )
    assert disabled.services.talk.prepare_repair_interpretation is None
    assert disabled.provider.for_repair() == []
    assert baseline.repair_interpretation_trace is None
    assert baseline.repair_interpretation_usage is None
    assert reply.repair_interpretation_trace is not None
    assert reply.repair_interpretation_trace.status.value == "abstain"
    assert abstaining.provider.for_foreground()[-1] == disabled.provider.for_foreground()[-1]
    assert reply.cognition_trace == baseline.cognition_trace
    assert reply.context_manifest == baseline.context_manifest
    assert owner_snapshot(abstaining, abstaining_session) == owners
    assert_domain_neutral(abstaining)
