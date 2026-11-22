"""Managed source admission for previous-exchange appraisal; synthetic providers only.

The explicit receipt DAG is a controlled fixture, never historical backfill or Character
quality evidence. Installation ledger/barrier behavior remains in its existing tests.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from satori.application.affect.contracts import PreparedAffectiveContext
from satori.application.affect.use_cases import (
    FinalizeAffectiveInteraction,
    PrepareAffectiveContext,
)
from satori.application.conversation.contracts import TalkInput
from satori.application.source_policy.dispatch import admit_provider_dispatch
from satori.core.affect import (
    AffectiveAppraisalProviderError,
    AffectiveAppraisalProviderResponse,
    AffectiveAppraisalRequest,
    AppraisalPreviousExchange,
)
from satori.domain.conversation_history import (
    ConversationInteraction,
    InteractionProviderMetadata,
)
from satori.domain.initial_self import InitialSelfSnapshot
from satori.domain.source_policy import PayloadRef, SourcePolicyError
from satori.infrastructure.persistence.affect_uow import (
    SQLAlchemyAffectiveConversationUnitOfWork,
    SQLAlchemyAffectiveStateUnitOfWork,
)
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.models.affect import (
    AffectiveStateRow,
    AffectiveTransitionRow,
)
from satori.infrastructure.persistence.models.conversation import ConversationMessageRow
from satori.infrastructure.persistence.models.source_policy import SourceInputRow, SourcePayloadRow
from satori.infrastructure.persistence.source_policy import SessionSourceAdmission
from tests.fakes import FakeAffectiveAppraisalProvider, FrozenClock
from tests.test_source_policy_memory import (
    FakeSourceRuntime,
    _receipt,
    _snapshot,
    controlled_session_factory,
)
from tests.test_source_policy_owners import _install
from tests.test_stage4_conversation_memory import INTERACTION_TIME, activate, id_sequence
from tests.test_stage7_affect_integration import (
    _provider_metadata,
    build,
    proposal_for,
)


@dataclass(frozen=True)
class SourceCase:
    database: Database
    self_snapshot: InitialSelfSnapshot
    current: ConversationInteraction
    previous: AppraisalPreviousExchange
    ancestor_root: str
    runtime: FakeSourceRuntime
    prepare: PrepareAffectiveContext
    finalize: FinalizeAffectiveInteraction
    appraisal: FakeAffectiveAppraisalProvider


def _provider(outcome: str) -> FakeAffectiveAppraisalProvider:
    if outcome == "failure":
        return FakeAffectiveAppraisalProvider(
            error=AffectiveAppraisalProviderError("fake-appraisal", "fixture", "offline_fixture")
        )

    def respond(request: AffectiveAppraisalRequest) -> AffectiveAppraisalProviderResponse:
        response = proposal_for(request.interaction_id)
        if outcome == "skipped":
            return replace(response, proposal=replace(response.proposal, salience=0.0))
        if outcome == "rejected":
            return replace(response, proposal=replace(response.proposal, appraisal_confidence=0.1))
        assert outcome == "applied"
        return response

    return FakeAffectiveAppraisalProvider(response_factory=respond)


def _case(
    database: Database, outcome: str = "applied", *, unknown_previous: bool = False
) -> SourceCase:
    self_snapshot = activate(database)
    clock = FrozenClock(INTERACTION_TIME + timedelta(days=2))
    appraisal = _provider(outcome)
    services, _, _ = build(database, appraisal, clock=clock)
    session = services.start_session.execute()
    metadata = InteractionProviderMetadata(
        provider="fake-conversation",
        model="fixture",
        finish_status="stop",
        context_schema_version=1,
        context_manifest_schema_version=1,
        policy_id="satori.conversation.behavior.v1",
        policy_schema_version=1,
    )

    def completed(prefix: str, user: str, assistant: str) -> ConversationInteraction:
        interaction = services.talk.interaction_log.begin(
            TalkInput(user, prefix + "-trace", prefix + "-request", session_id=session.session_id),
            identity_id=self_snapshot.identity.identity_id,
        )
        return services.talk.interaction_log.complete(
            interaction, assistant_text=assistant, provider_metadata=metadata
        )

    ancestor = completed("appraisal-source-ancestor", "An older eligible input", "An older reply")
    prior = completed("appraisal-source-prior", "The schedule changed", "That may be inconvenient")
    assert prior.assistant_message is not None
    previous = AppraisalPreviousExchange(
        1,
        prior.interaction_id,
        prior.user_message.message_id,
        prior.user_message.content,
        prior.assistant_message.message_id,
        prior.assistant_message.content,
    )
    current = services.talk.interaction_log.begin(
        TalkInput(
            "You drew that conclusion too soon",
            "appraisal-current-trace",
            "appraisal-current",
            session_id=session.session_id,
        ),
        identity_id=self_snapshot.identity.identity_id,
    )
    snapshot = _snapshot(self_snapshot.identity.identity_id)
    with database.session_factory() as unit:
        unit.info["source_snapshot"] = snapshot
        _receipt(
            unit,
            snapshot,
            PayloadRef("assistant_reply", ancestor.interaction_id),
            roots=(ancestor.user_message.message_id,),
        )
        _receipt(
            unit,
            snapshot,
            PayloadRef("assistant_reply", prior.interaction_id),
            roots=(prior.user_message.message_id,),
            refs=(PayloadRef("assistant_reply", ancestor.interaction_id),),
            unknown=unknown_previous,
        )
        unit.commit()
    _install(database, snapshot)
    runtime = FakeSourceRuntime(snapshot)
    factory = controlled_session_factory(database, runtime)
    prepare = services.talk.prepare_affect
    assert prepare is not None
    prepare.ensure_state.unit_of_work_factory = lambda: SQLAlchemyAffectiveStateUnitOfWork(factory)
    prepare.ensure_state.execute(self_snapshot.identity.identity_id, initialized_at=clock.now())
    finalize = FinalizeAffectiveInteraction(
        lambda: SQLAlchemyAffectiveConversationUnitOfWork(factory),
        clock,
        id_sequence("appraisal-managed-finalize"),
    )
    return SourceCase(
        database,
        self_snapshot,
        current,
        previous,
        ancestor.user_message.message_id,
        runtime,
        prepare,
        finalize,
        appraisal,
    )


def _prepare(case: SourceCase) -> PreparedAffectiveContext:
    return asyncio.run(
        case.prepare.execute(
            case.self_snapshot,
            case.current,
            user_text=case.current.user_message.content,
            trace_id=case.current.trace_id,
            memory_context=None,
            semantic_context=None,
            previous_exchange=case.previous,
        )
    )


def _exclude(case: SourceCase, target: str) -> None:
    root = case.previous.user_message_id if target == "prior_user" else case.ancestor_root
    assert target in ("prior_user", "assistant_ancestor")
    excluded = _snapshot(case.self_snapshot.identity.identity_id, frozenset({root}))
    _install(case.database, excluded)
    case.runtime.snapshot = excluded


def _foreground_input(case: SourceCase, prepared: PreparedAffectiveContext | None = None) -> str:
    factory = controlled_session_factory(case.database, case.runtime)
    with factory() as unit:
        unit.info["source_snapshot"] = case.runtime.snapshot
        roots: tuple[str, ...] = (case.current.user_message.message_id,)
        refs: tuple[PayloadRef, ...] = ()
        if prepared is not None:
            roots = tuple(dict.fromkeys((*roots, *prepared.actual_input_root_message_ids)))
            refs = prepared.actual_input_payload_refs
        input_id = SessionSourceAdmission(unit).prepare(root_message_ids=roots, payload_refs=refs)
        unit.commit()
    return input_id


def _assert_no_current_commit(case: SourceCase) -> None:
    with case.database.session_factory() as unit:
        state = unit.get_one(AffectiveStateRow, case.self_snapshot.identity.identity_id)
        assert state.state_version == state.mood_version == 1
        assert unit.scalar(select(func.count()).select_from(AffectiveTransitionRow)) == 0
        assert (
            unit.scalar(
                select(func.count())
                .select_from(ConversationMessageRow)
                .where(
                    ConversationMessageRow.interaction_id == case.current.interaction_id,
                    ConversationMessageRow.role == "assistant",
                )
            )
            == 0
        )


def _finalize(
    case: SourceCase,
    prepared: PreparedAffectiveContext,
    foreground_input: str,
) -> ConversationInteraction:
    return case.finalize.execute(
        case.current,
        assistant_text="A synthetic reply",
        provider_metadata=_provider_metadata(prepared),
        prepared=prepared,
        source_input_id=foreground_input,
    )


@pytest.mark.parametrize("outcome", ["applied", "skipped", "rejected", "failure"])
def test_uncited_previous_exchange_dependencies_survive_every_appraisal_outcome(
    migrated_database: Database,
    outcome: str,
) -> None:
    case = _case(migrated_database, outcome)
    prepared = _prepare(case)
    assert prepared.appraisal_status.value == ("unavailable" if outcome == "failure" else outcome)
    assert len(case.appraisal.requests) == 1
    request = case.appraisal.requests[0]
    assert request.schema_version == 2
    assert request.previous_exchange == case.previous
    if prepared.transition is not None:
        assert prepared.transition.proposal.schema_version == 1
        assert prepared.transition.proposal.source_refs == (case.current.interaction_id,)
    expected_roots = {case.current.user_message.message_id, case.previous.user_message_id}
    expected_ref = PayloadRef("assistant_reply", case.previous.interaction_id)
    assert set(prepared.actual_input_root_message_ids) == expected_roots
    assert prepared.actual_input_payload_refs == (expected_ref,)
    assert prepared.source_input_id is not None
    with migrated_database.session_factory() as unit:
        row = unit.get_one(SourceInputRow, prepared.source_input_id)
        assert set(row.root_message_ids) == expected_roots
        assert row.payload_refs == [
            {"kind": expected_ref.kind, "payload_id": expected_ref.payload_id, "version": 1}
        ]
    _assert_no_current_commit(case)


@pytest.mark.parametrize("target", ["prior_user", "assistant_ancestor"])
def test_excluded_uncited_context_blocks_appraisal_before_provider(
    migrated_database: Database,
    target: str,
) -> None:
    case = _case(migrated_database)
    _exclude(case, target)
    with pytest.raises(SourcePolicyError):
        _prepare(case)
    assert case.appraisal.requests == []
    _assert_no_current_commit(case)


@pytest.mark.parametrize("outcome", ["applied", "skipped", "rejected", "failure"])
@pytest.mark.parametrize("target", ["prior_user", "assistant_ancestor"])
def test_exclusion_after_appraisal_blocks_atomic_finalize_even_without_transition(
    migrated_database: Database,
    outcome: str,
    target: str,
) -> None:
    case = _case(migrated_database, outcome)
    prepared = _prepare(case)
    # Deliberately clean foreground override: prepared admission must be checked separately.
    foreground_input = _foreground_input(case)
    _exclude(case, target)
    with pytest.raises(SourcePolicyError):
        _finalize(case, prepared, foreground_input)
    assert len(case.appraisal.requests) == 1
    _assert_no_current_commit(case)


@pytest.mark.parametrize("outcome", ["applied", "skipped", "failure"])
def test_reply_inherits_context_dependencies_and_finalize_replay_does_not_mutate(
    migrated_database: Database,
    outcome: str,
) -> None:
    case = _case(migrated_database, outcome)
    prepared = _prepare(case)
    foreground_input = _foreground_input(case, prepared)
    completed = _finalize(case, prepared, foreground_input)
    replay = _finalize(case, prepared, foreground_input)
    assert replay == completed
    assert len(case.appraisal.requests) == 1
    with migrated_database.session_factory() as unit:
        receipt = unit.get_one(
            SourcePayloadRow, ("assistant_reply", case.current.interaction_id, 1)
        )
        assert set(receipt.root_message_ids) == {
            case.current.user_message.message_id,
            case.previous.user_message_id,
        }
        assert receipt.payload_refs == [
            {"kind": "assistant_reply", "payload_id": case.previous.interaction_id, "version": 1}
        ]
        state = unit.get_one(AffectiveStateRow, case.self_snapshot.identity.identity_id)
        expected_transitions = int(outcome == "applied")
        assert state.state_version == state.mood_version == 1 + expected_transitions
        transitions = tuple(unit.scalars(select(AffectiveTransitionRow)))
        assert len(transitions) == expected_transitions
        for transition in transitions:
            assert transition.source_message_id == case.current.user_message.message_id
    # Retained numerical experience is historical; excluded reply dependencies prevent replay.
    _exclude(case, "assistant_ancestor")
    with pytest.raises(SourcePolicyError):
        _finalize(case, prepared, foreground_input)
    assert len(case.appraisal.requests) == 1


class InvalidatedAtTransportAppraisal(FakeAffectiveAppraisalProvider):
    """Invalidate between application admission and fake transport re-admission."""

    def __init__(self, invalidate: Callable[[], None]) -> None:
        super().__init__(response_factory=lambda request: proposal_for(request.interaction_id))
        self.invalidate = invalidate

    async def generate_structured(
        self,
        request: AffectiveAppraisalRequest,
        /,
    ) -> AffectiveAppraisalProviderResponse:
        self.invalidate()
        admit_provider_dispatch()
        return await super().generate_structured(request)


def test_actual_dispatch_source_failure_is_not_an_appraisal_outage_fallback(
    migrated_database: Database,
) -> None:
    case = _case(migrated_database)
    provider = InvalidatedAtTransportAppraisal(lambda: _exclude(case, "assistant_ancestor"))
    case.prepare.provider = provider
    with pytest.raises(SourcePolicyError):
        _prepare(case)
    assert provider.requests == []
    _assert_no_current_commit(case)


def test_legacy_context_and_reply_keep_unknown_provenance_without_backfill(
    migrated_database: Database,
) -> None:
    case = _case(migrated_database, unknown_previous=True)
    prepared = _prepare(case)
    assert prepared.source_input_id is not None
    foreground_input = _foreground_input(case, prepared)
    # ADR-0070 requires complete prospective provenance for a new transition.
    # Legacy context remains UNKNOWN; it cannot authorize fresh affect/reply writes.
    with pytest.raises(SourcePolicyError, match="appraisal_body_provenance_unproven"):
        _finalize(case, prepared, foreground_input)
    _assert_no_current_commit(case)
    with migrated_database.session_factory() as unit:
        appraisal_input = unit.get_one(SourceInputRow, prepared.source_input_id)
        assert appraisal_input.proof_status == "unknown"
        assert appraisal_input.legacy is True
        receipt = unit.get_one(
            SourcePayloadRow, ("assistant_reply", case.previous.interaction_id, 1)
        )
        assert receipt.proof_status == "unknown"
        assert receipt.legacy is True
        assert (
            unit.get(SourcePayloadRow, ("assistant_reply", case.current.interaction_id, 1)) is None
        )
    # A controlled old-backup fixture: an excluded lineage root is absent from this main DB.
    excluded = _snapshot(
        case.self_snapshot.identity.identity_id, frozenset({"unrelated-old-backup-root"})
    )
    _install(migrated_database, excluded)
    case.runtime.snapshot = excluded
    with pytest.raises(SourcePolicyError):
        _finalize(case, prepared, foreground_input)
    assert len(case.appraisal.requests) == 1
