"""Scripted represented events retain their dimensions without admitting live repair.

Real application services, canonical history and source admission are exercised.
Classifier outputs, foreground replies and affect appraisals are authored fixtures;
these checks do not measure model understanding, personality or emotional continuity.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from satori.application.conversation.contracts import (
    RecentConversationContext,
    RecentConversationTurn,
)
from satori.application.conversation.repair_interpretation import PrepareRepairInterpretation
from satori.application.conversation.repair_interpretation_contracts import (
    PreparedRepairInterpretation,
)
from satori.core.conversation import (
    ConversationGenerationParameters,
    ConversationProviderRequest,
)
from satori.domain.source_policy import PayloadRef
from tests.repair_frame_diagnostic import RepairFrameDiagnosticCase
from tests.represented_repair_diagnostic import (
    ALL_CASES,
    AUTHORED_CASES,
    AUTHORED_LABELS_ONLY,
    INHERITED_INPUT_CASES,
    validate_corpus,
)
from tests.test_checkpoint143_character_agency_integration import _talk
from tests.test_repair_agreement_integration import (
    assert_classification,
    assert_exact_chronological_request,
    baseline_cue,
    configure,
    owner_snapshot,
    recent,
)
from tests.test_repair_interpretation_integration import (
    OLDER_PAIR,
    RepairCase,
    RepairCaseFactory,
    assert_domain_neutral,
    assert_full_receipt,
    current,
    is_repair,
    seed,
    trace_sources,
)


@pytest.fixture
def represented_cases(tmp_path: Path, project_root: Path) -> Iterator[RepairCaseFactory]:
    factory = RepairCaseFactory(tmp_path, project_root)
    try:
        yield factory
    finally:
        factory.dispose()


@dataclass(slots=True)
class CapturedRepairOperation:
    """Observe the actual prepared result without changing its admission or contents."""

    delegate: PrepareRepairInterpretation
    prepared: list[PreparedRepairInterpretation] = field(default_factory=list)

    async def execute(
        self,
        *,
        current_user_message_id: str,
        current_user_text: str,
        interlocutor_name: str,
        parameters: ConversationGenerationParameters,
        trace_id: str,
        max_context_chars: int,
        prior_turn: RecentConversationTurn | None = None,
    ) -> PreparedRepairInterpretation:
        outcome = await self.delegate.execute(
            current_user_message_id=current_user_message_id,
            current_user_text=current_user_text,
            interlocutor_name=interlocutor_name,
            parameters=parameters,
            trace_id=trace_id,
            max_context_chars=max_context_chars,
            prior_turn=prior_turn,
        )
        self.prepared.append(outcome)
        return outcome


def capture_operation(case: RepairCase) -> CapturedRepairOperation:
    delegate = case.services.talk.prepare_repair_interpretation
    assert delegate is not None
    captured = CapturedRepairOperation(delegate)
    case.services.talk.prepare_repair_interpretation = captured  # type: ignore[assignment]
    return captured


def assert_bound_interpretation(
    outcome: PreparedRepairInterpretation,
    case: RepairCase,
    history: RecentConversationContext,
    scenario: RepairFrameDiagnosticCase,
) -> None:
    """Check the genuine prepared object's dimensions, canonical IDs and exact spans."""

    interpretation = outcome.interpretation
    assert interpretation is not None
    assert interpretation.schema_version == 2
    assert interpretation.speech_act.value == scenario.authored_speech_act
    assert interpretation.frame.value == scenario.authored_frame
    assert interpretation.addressee.value == scenario.authored_addressee
    assert interpretation.basis.value == scenario.authored_basis
    assert interpretation.uncertainty.value == scenario.authored_uncertainty
    assert interpretation.current_user_message_id == current(case)[1]
    quote = scenario.current_focal_quote
    assert scenario.current_text.count(quote) == 1
    start = scenario.current_text.index(quote)
    current_span = interpretation.current_user_span
    assert current_span is not None
    assert current_span == (start, start + len(quote))
    assert scenario.current_text[slice(*current_span)] == quote
    latest = history.turns[-1] if history.turns else None
    assert interpretation.prior_interaction_id == (
        latest.interaction_id if latest is not None else None
    )
    assert interpretation.prior_user_message_id == (
        latest.user_message_id if latest is not None else None
    )
    assert interpretation.prior_assistant_message_id == (
        latest.assistant_message_id if latest is not None else None
    )
    if scenario.authored_basis == "prior_pair":
        assert latest is not None
        for text, evidence, span in (
            (latest.user_content, scenario.prior_user_quote, interpretation.prior_user_span),
            (
                latest.assistant_content,
                scenario.prior_assistant_quote,
                interpretation.prior_assistant_span,
            ),
        ):
            assert text.count(evidence) == 1
            assert span is not None
            start = text.index(evidence)
            assert span == (start, start + len(evidence))
            assert text[slice(*span)] == evidence
    else:
        assert interpretation.prior_user_span is None
        assert interpretation.prior_assistant_span is None
    assert outcome.repair_offer_override is scenario.expected_effective


@pytest.mark.parametrize("scenario", ALL_CASES, ids=lambda row: row.case_id)
def test_represented_corpus_retains_inner_event_and_canonical_sources_without_owner_writes(
    represented_cases: RepairCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    scenario: RepairFrameDiagnosticCase,
) -> None:
    validate_corpus()
    assert AUTHORED_LABELS_ONLY
    assert len(AUTHORED_CASES) == 10
    assert len(INHERITED_INPUT_CASES) == 2
    old = represented_cases.create("represented-off", enabled=False)
    new = represented_cases.create("represented-on", enabled=True)
    old_session = seed(old, scenario.prior_history)
    new_session = seed(new, scenario.prior_history)
    history = recent(new, new_session)
    assert tuple((turn.user_content, turn.assistant_content) for turn in history.turns) == (
        scenario.prior_history
    )
    roots = tuple(turn.user_message_id for turn in history.turns)
    refs = tuple(PayloadRef("assistant_reply", turn.interaction_id) for turn in history.turns)
    owners = owner_snapshot(new, new_session)
    configure(new.provider, scenario)
    captured = capture_operation(new)
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
    assert len(captured.prepared) == 1
    assert_bound_interpretation(captured.prepared[0], new, history, scenario)
    input_id = assert_full_receipt(new, sources, roots, refs, committed=True)
    assert sources.transported == [input_id, input_id]
    assert_classification(reply, scenario)
    assert reply.repair_interpretation_trace is not None
    assert reply.repair_interpretation_trace.baseline_repair_offer is baseline_cue(baseline)
    assert baseline.repair_interpretation_trace is None
    assert old.provider.for_repair() == []
    assert requests[-1].parameters == old.provider.for_foreground()[-1].parameters
    assert requests[-1].messages[-1] == old.provider.for_foreground()[-1].messages[-1]
    if reply.repair_interpretation_trace.effective_repair_offer is baseline_cue(baseline):
        assert requests[-1] == old.provider.for_foreground()[-1]
    assert owner_snapshot(new, new_session) == owners
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


def test_identical_words_keep_received_and_live_context_distinct(
    represented_cases: RepairCaseFactory,
) -> None:
    """The scripted contrast proves context transport and admission, not semantics."""

    matched = tuple(
        case
        for case in AUTHORED_CASES
        if case.matched_group == "represented_same_current_different_frame"
    )
    assert len(matched) == 2
    assert matched[0].current_text == matched[1].current_text
    requests: list[ConversationProviderRequest] = []
    effective: list[bool] = []
    for index, scenario in enumerate(matched):
        case = represented_cases.create(f"represented-matched-{index}", enabled=True)
        session_id = seed(case, scenario.prior_history)
        history = recent(case, session_id)
        configure(case.provider, scenario)
        captured = capture_operation(case)
        reply = _talk(
            case.services,
            user_text=scenario.current_text,
            request_id="current",
            session_id=session_id,
        )
        assert_classification(reply, scenario)
        assert len(captured.prepared) == 1
        assert_bound_interpretation(captured.prepared[0], case, history, scenario)
        requests.append(case.provider.for_repair()[-1])
        assert reply.repair_interpretation_trace is not None
        effective.append(reply.repair_interpretation_trace.effective_repair_offer)
    assert effective == [False, True]
    assert requests[0].messages[:-1] == requests[1].messages[:-1]
    assert requests[0].parameters == requests[1].parameters
    assert requests[0].messages[-1] != requests[1].messages[-1]


def test_uncited_older_history_retains_full_receipt_while_latest_pair_binds_event(
    represented_cases: RepairCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = next(
        case for case in AUTHORED_CASES if case.case_id == "same_words_received_fragment"
    )
    scenario = replace(original, prior_history=(OLDER_PAIR, *original.prior_history))
    case = represented_cases.create("represented-full-footprint", enabled=True)
    session_id = seed(case, scenario.prior_history)
    history = recent(case, session_id)
    assert len(history.turns) == 2
    configure(case.provider, scenario)
    captured = capture_operation(case)
    sources = trace_sources(monkeypatch, case.provider)
    before = len(case.provider.requests)
    reply = _talk(
        case.services,
        user_text=scenario.current_text,
        request_id="current",
        session_id=session_id,
    )
    requests = case.provider.requests[before:]
    assert [is_repair(request) for request in requests] == [True, False]
    assert_exact_chronological_request(requests[0], case, history, scenario.current_text)
    assert all(text not in requests[0].messages[-1].content for text in OLDER_PAIR)
    assert len(captured.prepared) == 1
    assert_bound_interpretation(captured.prepared[0], case, history, scenario)
    roots = tuple(turn.user_message_id for turn in history.turns)
    refs = tuple(PayloadRef("assistant_reply", turn.interaction_id) for turn in history.turns)
    input_id = assert_full_receipt(case, sources, roots, refs, committed=True)
    assert sources.transported == [input_id, input_id]
    assert_classification(reply, scenario)
    assert_domain_neutral(case)


def test_disabled_operation_has_no_classification_and_abstain_preserves_exact_foreground(
    represented_cases: RepairCaseFactory,
) -> None:
    scenario = next(
        case for case in AUTHORED_CASES if case.case_id == "same_words_received_fragment"
    )
    disabled = represented_cases.create("represented-disabled", enabled=False)
    abstaining = represented_cases.create("represented-abstaining", enabled=True)
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
