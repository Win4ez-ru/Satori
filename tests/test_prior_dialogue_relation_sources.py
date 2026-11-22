"""Actual managed Talk keeps one full receipt across interpretation, retry and commit.

Authored fake transport data exercise source lifecycle, not semantic quality.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from satori.application.conversation.history import InteractionLog
from satori.application.source_policy.contracts import SourceExclusionCommand
from satori.application.source_policy.use_cases import SourcePolicyControls
from satori.core.conversation import (
    ConversationProviderError,
    ConversationProviderRequest,
    ConversationProviderResponse,
)
from satori.domain.source_policy import PayloadRef, SourcePolicyError
from satori.infrastructure.persistence.models.conversation import (
    ConversationInteractionRow,
    ConversationMessageRow,
)
from satori.infrastructure.persistence.models.source_policy import SourceInputRow, SourcePayloadRow
from satori.infrastructure.persistence.repositories import conversation as conversation_repository
from satori.infrastructure.persistence.source_policy import SessionSourceAdmission, record_payload
from satori.infrastructure.source_policy.installation import canonical_root, repair_installation
from tests.prior_dialogue_relation_diagnostic import (
    EXCHANGE_INPUT,
    EXCHANGE_MOVE,
    RELEVANT_SHIFT,
    PriorRelationScenario,
)
from tests.test_checkpoint143_character_agency_integration import _talk
from tests.test_prior_dialogue_relation_integration import (
    RelationCase,
    RelationCaseFactory,
    RelationProvider,
    assert_domain_neutral,
    is_relation,
    seed_history,
)

Operation = Literal["relation", "foreground"]
Hook = Callable[[Operation, ConversationProviderRequest], None]
OLDER_INPUT = "Объясни два пункта: что делает append и чем он отличается от extend в Python."
OLDER_REPLY = "Append добавляет один элемент; extend добавляет элементы последовательности."
SOURCE_SCENARIO = PriorRelationScenario(
    "uncited-older-history",
    ((OLDER_INPUT, OLDER_REPLY), (EXCHANGE_INPUT, EXCHANGE_MOVE)),
    RELEVANT_SHIFT,
    "shift_from_prior_move",
    expected_shift=True,
)


@dataclass(slots=True)
class HookedRelationProvider(RelationProvider):
    """Queue hooks precede actual admission; response hooks also cover typed outage."""

    before_dispatch: Hook | None = None
    after_response: Hook | None = None
    transport_active: bool = field(default=False, init=False)

    async def generate(
        self, request: ConversationProviderRequest, /
    ) -> ConversationProviderResponse:
        operation: Operation = "relation" if is_relation(request) else "foreground"
        if self.before_dispatch is not None:
            self.before_dispatch(operation, request)
        try:
            self.transport_active = True
            try:
                response = await RelationProvider.generate(self, request)
            finally:
                self.transport_active = False
        except ConversationProviderError:
            if self.after_response is not None:
                self.after_response(operation, request)
            raise
        if self.after_response is not None:
            self.after_response(operation, request)
        return response


@pytest.fixture
def source_cases(tmp_path: Path, project_root: Path) -> Iterator[RelationCaseFactory]:
    factory = RelationCaseFactory(tmp_path, project_root)
    try:
        yield factory
    finally:
        factory.dispose()


def _seed(case: RelationCase) -> tuple[str, tuple[str, ...], tuple[PayloadRef, ...]]:
    session = seed_history(case, SOURCE_SCENARIO)
    reader = case.services.talk.recent_conversation
    assert reader is not None
    recent = reader.execute(session_id=session, excluded_interaction_id="unused-current-slot")
    assert len(recent.turns) == 2
    return (
        session,
        tuple(turn.user_message_id for turn in recent.turns),
        tuple(PayloadRef("assistant_reply", turn.interaction_id) for turn in recent.turns),
    )


def _exclude(case: RelationCase, message_id: str) -> None:
    root = canonical_root(case.database, case.runtime, message_id, counterparty_id="local-default")
    controls = SourcePolicyControls(case.runtime)
    preview = controls.preview(root)
    receipt = controls.apply(
        SourceExclusionCommand(
            message_id=message_id,
            expected_generation=preview.generation,
            request_id=f"exclude-{message_id}",
            preview_digest=preview.preview_digest,
        ),
        root,
        repair=lambda snapshot, item: repair_installation(
            case.database, case.runtime, snapshot, item
        ),
    )
    assert receipt.phase.value == "complete"


@dataclass(slots=True)
class SourceTrace:
    prepared: list[tuple[tuple[str, ...], tuple[PayloadRef, ...]]] = field(default_factory=list)
    input_ids: list[str | None] = field(default_factory=list)
    dispatched: list[str] = field(default_factory=list)
    transport_dispatched: list[str] = field(default_factory=list)
    recorded: list[str | None] = field(default_factory=list)


def _trace(
    monkeypatch: pytest.MonkeyPatch,
    provider: HookedRelationProvider,
    *,
    before_prepare: Callable[[], None] | None = None,
) -> SourceTrace:
    trace = SourceTrace()
    original_prepare = InteractionLog.prepare_inputs
    original_dispatch = SessionSourceAdmission.dispatch
    original_record = record_payload

    def prepare(
        self: InteractionLog,
        *,
        root_message_ids: tuple[str, ...],
        payload_refs: tuple[PayloadRef, ...],
    ) -> str | None:
        trace.prepared.append((root_message_ids, payload_refs))
        if before_prepare is not None:
            before_prepare()
        input_id = original_prepare(
            self, root_message_ids=root_message_ids, payload_refs=payload_refs
        )
        trace.input_ids.append(input_id)
        return input_id

    def dispatch(self: SessionSourceAdmission, input_id: str) -> None:
        trace.dispatched.append(input_id)
        if provider.transport_active:
            trace.transport_dispatched.append(input_id)
        original_dispatch(self, input_id)

    def record(
        session: Session, kind: str, payload_id: str, version: int, input_id: str | None
    ) -> None:
        if kind == "assistant_reply":
            trace.recorded.append(input_id)
        original_record(session, kind, payload_id, version, input_id)

    monkeypatch.setattr(InteractionLog, "prepare_inputs", prepare)
    monkeypatch.setattr(SessionSourceAdmission, "dispatch", dispatch)
    monkeypatch.setattr(conversation_repository, "record_payload", record)
    return trace


def _current(case: RelationCase) -> tuple[str, str, str]:
    with case.database.session_factory() as session:
        row = session.scalar(
            select(ConversationInteractionRow).where(
                ConversationInteractionRow.client_request_id == "current"
            )
        )
        assert row is not None
        message = session.scalar(
            select(ConversationMessageRow).where(
                ConversationMessageRow.interaction_id == row.interaction_id,
                ConversationMessageRow.role == "user",
            )
        )
        assert message is not None
        return row.interaction_id, message.message_id, row.status


def _assert_failed_without_reply(case: RelationCase) -> None:
    interaction_id, _message_id, status = _current(case)
    assert status == "failed"
    with case.database.session_factory() as session:
        assert (
            session.scalar(
                select(ConversationMessageRow.message_id).where(
                    ConversationMessageRow.interaction_id == interaction_id,
                    ConversationMessageRow.role == "assistant",
                )
            )
            is None
        )
        assert session.get(SourcePayloadRow, ("assistant_reply", interaction_id, 1)) is None
    assert_domain_neutral(case)


def _assert_full_receipt(
    case: RelationCase,
    trace: SourceTrace,
    prior_roots: tuple[str, ...],
    prior_refs: tuple[PayloadRef, ...],
    *,
    committed: bool,
) -> str:
    assert len(trace.prepared) == 1
    assert len(trace.input_ids) == 1
    input_id = trace.input_ids[0]
    assert input_id is not None
    interaction_id, current_root, _status = _current(case)
    expected_roots = {current_root, *prior_roots}
    roots, refs = trace.prepared[0]
    assert set(roots) == expected_roots
    assert refs == prior_refs
    with case.database.session_factory() as session:
        row = session.get(SourceInputRow, input_id)
        assert row is not None
        assert set(row.root_message_ids) == expected_roots
        encoded_refs = [
            {"kind": ref.kind, "payload_id": ref.payload_id, "version": ref.version}
            for ref in prior_refs
        ]
        assert row.payload_refs == encoded_refs
        assert row.proof_status == "complete"
        # Appraisal has its own receipt; exactly one input binds the entire generation.
        full_rows = [
            candidate
            for candidate in session.scalars(select(SourceInputRow))
            if set(candidate.root_message_ids) == expected_roots
        ]
        assert [candidate.input_id for candidate in full_rows] == [input_id]
        if committed:
            payload = session.get(SourcePayloadRow, ("assistant_reply", interaction_id, 1))
            assert payload is not None
            assert set(payload.root_message_ids) == expected_roots
            assert payload.payload_refs == encoded_refs
            assert payload.proof_status == "complete"
    if committed:
        assert trace.recorded == [input_id]
    return input_id


@pytest.mark.parametrize("outcome", ["valid", "abstain", "invalid", "material", "unavailable"])
def test_one_full_uncited_footprint_survives_fallback_and_shared_retry(
    source_cases: RelationCaseFactory, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    provider = HookedRelationProvider()
    case = source_cases.create("full-" + outcome, enabled=True, provider=provider)
    session, roots, refs = _seed(case)
    provider.kind = "abstain" if outcome == "abstain" else "shift_from_prior_move"
    provider.outcome = "valid" if outcome == "abstain" else outcome
    provider.foreground_texts = (OLDER_REPLY, EXCHANGE_MOVE, "Я готов, как человек.", "WIRE-RETRY")
    relation_before, foreground_before = (
        len(provider.for_relation()),
        len(provider.for_foreground()),
    )
    trace = _trace(monkeypatch, provider)
    reply = _talk(case.services, user_text=RELEVANT_SHIFT, request_id="current", session_id=session)
    input_id = _assert_full_receipt(case, trace, roots, refs, committed=True)
    assert len(provider.for_relation()) - relation_before == 1
    assert len(provider.for_foreground()) - foreground_before == 2
    assert trace.transport_dispatched == [input_id, input_id, input_id]
    assert reply.context_manifest.regeneration_reason == "masculine_self_reference"
    assert_domain_neutral(case)
    calls = len(provider.requests), len(case.appraisal.requests)
    prepare_count = len(trace.prepared)
    replay = _talk(
        case.services, user_text=RELEVANT_SHIFT, request_id="current", session_id=session
    )
    assert replay.replayed
    assert (len(provider.requests), len(case.appraisal.requests)) == calls
    assert len(trace.prepared) == prepare_count
    assert replay.context_manifest.prior_dialogue_relation_status is None


@pytest.mark.parametrize(
    ("phase", "outcome"),
    [
        ("prepare", "valid"),
        ("semantic_queue", "valid"),
        ("after_semantic", "valid"),
        ("after_semantic", "abstain"),
        ("after_semantic", "invalid"),
        ("after_semantic", "unavailable"),
        ("foreground_queue", "valid"),
        ("retry_queue", "valid"),
        ("commit", "valid"),
        ("commit", "abstain"),
        ("commit", "invalid"),
        ("commit", "unavailable"),
    ],
)
def test_uncited_older_root_withdrawal_fails_closed_at_each_boundary(
    source_cases: RelationCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    outcome: str,
) -> None:
    provider = HookedRelationProvider()
    case = source_cases.create(phase + "-" + outcome, enabled=True, provider=provider)
    session, roots, refs = _seed(case)
    provider.kind = "abstain" if outcome == "abstain" else "shift_from_prior_move"
    provider.outcome = "valid" if outcome == "abstain" else outcome
    provider.foreground_texts = (
        (OLDER_REPLY, EXCHANGE_MOVE, "Я готов, как человек.", "WIRE-RETRY")
        if phase == "retry_queue"
        else (OLDER_REPLY, EXCHANGE_MOVE, "WIRE")
    )
    relation_before, foreground_before = (
        len(provider.for_relation()),
        len(provider.for_foreground()),
    )
    fired = False

    def withdraw() -> None:
        nonlocal fired
        if not fired:
            fired = True
            _exclude(case, roots[0])

    def before(operation: Operation, _request: ConversationProviderRequest) -> None:
        if (
            (phase == "semantic_queue" and operation == "relation")
            or (phase == "foreground_queue" and operation == "foreground")
            or (
                phase == "retry_queue"
                and operation == "foreground"
                and len(provider.for_foreground()) - foreground_before == 1
            )
        ):
            withdraw()

    def after(operation: Operation, _request: ConversationProviderRequest) -> None:
        if (phase == "after_semantic" and operation == "relation") or (
            phase == "commit" and operation == "foreground"
        ):
            withdraw()

    provider.before_dispatch = before
    provider.after_response = after
    trace = _trace(monkeypatch, provider, before_prepare=withdraw if phase == "prepare" else None)
    with pytest.raises(SourcePolicyError, match=r"source|excluded|dependency"):
        _talk(case.services, user_text=RELEVANT_SHIFT, request_id="current", session_id=session)
    assert fired
    _assert_failed_without_reply(case)
    assert len(trace.prepared) == 1
    assert trace.recorded == []
    expected_semantic = 0 if phase in {"prepare", "semantic_queue"} else 1
    expected_foreground = 1 if phase in {"retry_queue", "commit"} else 0
    assert len(provider.for_relation()) - relation_before == expected_semantic
    assert len(provider.for_foreground()) - foreground_before == expected_foreground
    if phase != "prepare":
        _assert_full_receipt(case, trace, roots, refs, committed=False)


def test_completed_replay_of_withdrawn_reply_is_blocked_without_new_operations(
    source_cases: RelationCaseFactory,
) -> None:
    provider = HookedRelationProvider()
    case = source_cases.create("withdrawn-replay", enabled=True, provider=provider)
    session, roots, _refs = _seed(case)
    reply = _talk(case.services, user_text=RELEVANT_SHIFT, request_id="current", session_id=session)
    _exclude(case, roots[0])
    calls = len(provider.requests), len(case.appraisal.requests)
    with pytest.raises(SourcePolicyError, match=r"source|excluded|dependency"):
        _talk(case.services, user_text=RELEVANT_SHIFT, request_id="current", session_id=session)
    assert (len(provider.requests), len(case.appraisal.requests)) == calls
    with case.database.session_factory() as unit:
        row = unit.get(ConversationInteractionRow, reply.interaction_id)
        assert row is not None
        assert row.status == "completed"
    assert_domain_neutral(case)


def test_unexpected_semantic_failure_is_failed_without_foreground_or_reply(
    source_cases: RelationCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = HookedRelationProvider()
    case = source_cases.create("unexpected-failure", enabled=True, provider=provider)
    session, roots, refs = _seed(case)
    provider.outcome = "unexpected"
    relation_before, foreground_before = (
        len(provider.for_relation()),
        len(provider.for_foreground()),
    )
    trace = _trace(monkeypatch, provider)
    with pytest.raises(RuntimeError, match="synthetic unexpected relation failure"):
        _talk(case.services, user_text=RELEVANT_SHIFT, request_id="current", session_id=session)
    assert len(provider.for_relation()) - relation_before == 1
    assert len(provider.for_foreground()) == foreground_before
    _assert_failed_without_reply(case)
    _assert_full_receipt(case, trace, roots, refs, committed=False)
    assert trace.recorded == []
