"""Owner-routed source retirement and provider/commit admission without Character tuning."""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import event, func, select

from satori.application.affect.use_cases import FinalizeAffectiveInteraction
from satori.application.conversation.contracts import TalkInput
from satori.application.models.use_cases import FormCurrentModels
from satori.application.personality.use_cases import ApplyPersonalityReflection
from satori.application.semantic.contracts import RetrievedSemanticClaim, RetrievedSemanticContext
from satori.core.inclinations import InclinationKind, InclinationProposal
from satori.core.models import ModelFormationProviderResponse, ModelFormationRequest
from satori.domain.inclinations import INCLINATION_POLICY_VERSION, inclination_key
from satori.domain.models import OwnerFormationPlan, UserModelManager, WorldModelManager
from satori.domain.personality_evolution import PersonalityManager
from satori.domain.positions import PositionFormationPlan, PositionManager
from satori.domain.reflection import ReflectionOutcome, ReflectionOutcomeDecision
from satori.domain.source_policy import PayloadRef, SourceEligibilitySnapshot, SourcePolicyError
from satori.infrastructure.persistence.affect_uow import (
    SQLAlchemyAffectiveConversationUnitOfWork,
    SQLAlchemyAffectiveStateUnitOfWork,
)
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.models.conversation import ConversationMessageRow
from satori.infrastructure.persistence.models.models import (
    ModelFormationDecisionRow,
    UserModelClaimRow,
    WorldModelClaimRow,
)
from satori.infrastructure.persistence.models.positions import (
    SatoriInclinationRow,
    SatoriPositionRow,
)
from satori.infrastructure.persistence.models.reflection import ReflectionSourceRow
from satori.infrastructure.persistence.models.semantic import SemanticClaimRow
from satori.infrastructure.persistence.models.source_policy import (
    OwnerSourceWithdrawalRow,
    SourcePayloadRow,
)
from satori.infrastructure.persistence.models_uow import SQLAlchemyCurrentModelsUnitOfWork
from satori.infrastructure.persistence.personality_uow import SQLAlchemyPersonalityUnitOfWork
from satori.infrastructure.persistence.positions_uow import SQLAlchemyPositionsUnitOfWork
from satori.infrastructure.persistence.repositories.models import SQLAlchemyCurrentModelsRepository
from satori.infrastructure.persistence.repositories.personality import (
    SQLAlchemyPersonalityRepository,
)
from satori.infrastructure.persistence.repositories.positions import SQLAlchemyPositionsRepository
from satori.infrastructure.persistence.repositories.reflection import SQLAlchemyReflectionRepository
from satori.infrastructure.persistence.repositories.relationship import (
    SQLAlchemyRelationshipRepository,
)
from satori.infrastructure.persistence.source_policy import (
    SessionSourceAdmission,
    mark_owner_reconciled,
    materialize_mirror,
)
from tests.fakes import FakeAffectiveAppraisalProvider, FakeModelFormationProvider, FrozenClock
from tests.test_source_policy_memory import (
    FakeSourceRuntime,
    _receipt,
    _snapshot,
    controlled_session_factory,
)
from tests.test_stage4_conversation_memory import INTERACTION_TIME, activate, id_sequence
from tests.test_stage7_affect_integration import (
    _provider_metadata,
    proposal_for,
)
from tests.test_stage7_affect_integration import (
    build as build_affect,
)
from tests.test_stage9_models_integration import (
    build_former,
    create_interaction,
    proposal_for_request,
)
from tests.test_stage13_inclinations_persistence import (
    _applied_inclination,
    _canonical_position_source,
    _create_reflection_source,
)
from tests.test_stage14_personality_persistence import (
    NOW,
    PrefixIds,
    _seed_personality_run,
)


def _install(database: Database, snapshot: SourceEligibilitySnapshot) -> None:
    with database.session_factory() as session:
        session.info["source_snapshot"] = snapshot
        materialize_mirror(session, snapshot)
        for owner in ("current_models", "positions"):
            mark_owner_reconciled(session, owner)
        session.commit()


def _form_models(database: Database) -> tuple[str, str, str, str]:
    identity = activate(database).identity.identity_id
    interaction_id = create_interaction(
        database,
        counterparty_id="alice",
        content="Проект Аврора запланирован",
        prefix="source-owner-model",
        day=1,
    )
    decision = asyncio.run(
        build_former(
            database,
            FakeModelFormationProvider(response_factory=proposal_for_request),
            prefix="source-owner-formation",
            day=1,
        ).execute(interaction_id, trace_id="source-owner-formation")
    )
    return identity, interaction_id, decision.user_claim_ids[0], decision.world_claim_ids[0]


def test_models_withdraw_unknown_legacy_even_when_excluded_root_is_absent(
    migrated_database: Database,
) -> None:
    identity, _, user_id, world_id = _form_models(migrated_database)
    snapshot = _snapshot(identity, frozenset({"root-absent-from-old-backup"}))
    with migrated_database.session_factory() as session:
        session.info["source_snapshot"] = snapshot
        before_user = SQLAlchemyCurrentModelsRepository(session)._map_user_claim(
            session.get_one(UserModelClaimRow, user_id)
        )
        before_world = SQLAlchemyCurrentModelsRepository(session)._map_world_claim(
            session.get_one(WorldModelClaimRow, world_id)
        )
        before_user_revisions = SQLAlchemyCurrentModelsRepository(session).list_user_revisions(
            user_id
        )
        before_world_revisions = SQLAlchemyCurrentModelsRepository(session).list_world_revisions(
            world_id
        )
        _receipt(session, snapshot, PayloadRef("user_model_claim", user_id), unknown=True)
        _receipt(session, snapshot, PayloadRef("world_model_claim", world_id), unknown=True)
        session.flush()
        materialize_mirror(session, snapshot)
        repository = SQLAlchemyCurrentModelsRepository(session)
        repository.withdraw_source_support(
            excluded_message_ids=snapshot.excluded_message_ids,
            request_id="exclude-old",
            policy_generation=1,
            now=INTERACTION_TIME + timedelta(days=3),
        )
        session.commit()
    with migrated_database.session_factory() as session:
        repository = SQLAlchemyCurrentModelsRepository(session)
        user = repository.get_user_claim(user_id)
        world = repository.get_world_claim(world_id)
        assert user is not None
        assert world is not None
        for before, after in ((before_user, user), (before_world, world)):
            assert after.status.value == "retracted"
            assert after.aggregate_version == before.aggregate_version + 1
            assert (after.value, after.confidence, after.evidence) == (
                before.value,
                before.confidence,
                before.evidence,
            )
        assert repository.list_user_revisions(user_id) == before_user_revisions
        assert repository.list_world_revisions(world_id) == before_world_revisions
        assert session.scalar(select(func.count()).select_from(OwnerSourceWithdrawalRow)) == 2


def test_initial_enrollment_preserves_proven_legacy_payload_support(
    migrated_database: Database,
) -> None:
    identity, _, user_id, world_id = _form_models(migrated_database)
    snapshot = _snapshot(identity)
    with migrated_database.session_factory() as session:
        session.info["source_snapshot"] = snapshot
        repository = SQLAlchemyCurrentModelsRepository(session)
        user = repository._map_user_claim(session.get_one(UserModelClaimRow, user_id))
        world = repository._map_world_claim(session.get_one(WorldModelClaimRow, world_id))
        _receipt(
            session,
            snapshot,
            PayloadRef("user_model_claim", user_id),
            unknown=True,
            roots=tuple(edge.source_message_id for edge in user.evidence),
        )
        _receipt(
            session,
            snapshot,
            PayloadRef("world_model_claim", world_id),
            unknown=True,
            roots=tuple(edge.source_message_id for edge in world.evidence),
        )
        session.flush()
        materialize_mirror(session, snapshot)
        SQLAlchemyCurrentModelsRepository(session).withdraw_source_support(
            excluded_message_ids=frozenset(),
            request_id="enroll",
            policy_generation=0,
            now=INTERACTION_TIME + timedelta(days=3),
        )
        session.commit()
        assert session.get_one(UserModelClaimRow, user_id).status == "current"
        assert session.get_one(WorldModelClaimRow, world_id).status == "current"
        assert session.scalar(select(func.count()).select_from(OwnerSourceWithdrawalRow)) == 0


def test_uncited_input_dependency_withholds_whole_model_aggregate(
    migrated_database: Database,
) -> None:
    identity, _, user_id, world_id = _form_models(migrated_database)
    other = create_interaction(
        migrated_database,
        counterparty_id="alice",
        content="Дополнительный контекст решения",
        prefix="uncited-model-input",
        day=2,
    )
    with migrated_database.session_factory() as session:
        root = session.scalar(
            select(ConversationMessageRow.message_id).where(
                ConversationMessageRow.interaction_id == other,
                ConversationMessageRow.role == "user",
            )
        )
        assert root is not None
        snapshot = _snapshot(identity)
        _receipt(session, snapshot, PayloadRef("user_model_claim", user_id), roots=(root,))
        _receipt(session, snapshot, PayloadRef("world_model_claim", world_id), roots=(root,))
        session.commit()
    excluded = _snapshot(identity, frozenset({root}))
    _install(migrated_database, excluded)
    with migrated_database.session_factory() as session:
        session.info["source_snapshot"] = excluded
        repository = SQLAlchemyCurrentModelsRepository(session)
        assert repository.list_user_claims(identity_id=identity, counterparty_id="alice") == ()
        assert repository.list_world_claims(identity_id=identity, counterparty_id="alice") == ()
        assert repository.get_user_claim(user_id) is None
        repository.withdraw_source_support(
            excluded_message_ids=excluded.excluded_message_ids,
            request_id="exclude-uncited",
            policy_generation=1,
            now=INTERACTION_TIME + timedelta(days=3),
        )
        session.commit()
        assert session.get_one(UserModelClaimRow, user_id).status == "retracted"
        assert session.get_one(WorldModelClaimRow, world_id).status == "retracted"


@pytest.mark.parametrize("exclude_before_provider", [True, False])
def test_model_provider_and_commit_recheck_actual_window(
    migrated_database: Database,
    exclude_before_provider: bool,
) -> None:
    identity = activate(migrated_database).identity.identity_id
    interaction_id = create_interaction(
        migrated_database,
        counterparty_id="alice",
        content="Проект Аврора запланирован",
        prefix="late-model-input",
        day=1,
    )
    with migrated_database.session_factory() as session:
        root = session.scalar(
            select(ConversationMessageRow.message_id).where(
                ConversationMessageRow.interaction_id == interaction_id,
                ConversationMessageRow.role == "user",
            )
        )
        assert root is not None
    initial = _snapshot(identity)
    excluded = _snapshot(identity, frozenset({root}))
    _install(migrated_database, excluded if exclude_before_provider else initial)
    runtime = FakeSourceRuntime(excluded if exclude_before_provider else initial)
    factory = controlled_session_factory(migrated_database, runtime)

    def late_response(request: ModelFormationRequest) -> ModelFormationProviderResponse:
        _install(migrated_database, excluded)
        runtime.snapshot = excluded
        return proposal_for_request(request)

    provider = FakeModelFormationProvider(response_factory=late_response)
    former = FormCurrentModels(
        unit_of_work_factory=lambda: SQLAlchemyCurrentModelsUnitOfWork(factory),
        provider=provider,
        user_manager=UserModelManager(),
        world_manager=WorldModelManager(),
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=2)),
        id_generator=id_sequence("late-model-owner"),
    )
    with pytest.raises(
        (ValueError, SourcePolicyError), match=r"model_source_ineligible|source_root_unavailable"
    ):
        asyncio.run(former.execute(interaction_id, trace_id="late-model"))
    assert len(provider.requests) == (0 if exclude_before_provider else 1)
    with migrated_database.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ModelFormationDecisionRow)) == 0
        assert session.scalar(select(func.count()).select_from(UserModelClaimRow)) == 0
        assert session.scalar(select(func.count()).select_from(WorldModelClaimRow)) == 0


def test_position_and_inclination_retirement_preserve_numeric_history(
    migrated_database: Database,
) -> None:
    identity = activate(migrated_database).identity.identity_id
    _, _, session_id, _ = _canonical_position_source(migrated_database, identity_id=identity)
    _, source, proposal_id = _create_reflection_source(migrated_database, identity_id=identity)
    inclination, evaluation = _applied_inclination(
        identity_id=identity,
        source=source,
        source_session_id=session_id,
        outcome_id="owner-hold-outcome",
    )
    outcome = ReflectionOutcome(
        outcome_id="owner-hold-outcome",
        proposal_id=proposal_id,
        target_policy_version=INCLINATION_POLICY_VERSION,
        decision=ReflectionOutcomeDecision.ACCEPTED,
        reason_code=evaluation.reason_code,
        target_aggregate_type="satori_inclinations",
        target_aggregate_id=inclination.inclination_id,
        decided_at=inclination.updated_at,
    )
    with SQLAlchemyPositionsUnitOfWork(migrated_database.session_factory) as unit:
        assert unit.positions.record_inclination_reflection_decision(
            outcome,
            evaluation,
            identity_id=identity,
            trace_id="hold-fixture",
            audit_event_id="hold-fixture",
        )
        unit.commit()
    snapshot = _snapshot(identity, frozenset({"root-missing-from-backup"}))
    with migrated_database.session_factory() as session:
        session.info["source_snapshot"] = snapshot
        _receipt(
            session, snapshot, PayloadRef("position", "inclination-source-position"), unknown=True
        )
        _receipt(
            session, snapshot, PayloadRef("inclination", inclination.inclination_id), unknown=True
        )
        session.flush()
        materialize_mirror(session, snapshot)
        repository = SQLAlchemyPositionsRepository(session)
        repository.withdraw_source_support(
            excluded_message_ids=snapshot.excluded_message_ids,
            request_id="hold-source",
            policy_generation=1,
            now=INTERACTION_TIME + timedelta(days=9),
        )
        session.commit()
        assert (
            session.get_one(SatoriPositionRow, "inclination-source-position").status == "retracted"
        )
        assert repository.list_inclinations(identity_id=identity) == ()
        assert repository.list_inclination_references(identity_id=identity) == ()
        assert repository.list_held_inclination_keys(identity_id=identity) == frozenset(
            {inclination.inclination_key}
        )
    with migrated_database.session_factory() as session:
        assert (
            SQLAlchemyPositionsRepository(session)._map_inclination(
                session.get_one(SatoriInclinationRow, inclination.inclination_id)
            )
            == inclination
        )
    with migrated_database.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(SatoriInclinationRow)) == 1
        assert session.scalar(select(func.count()).select_from(OwnerSourceWithdrawalRow)) == 2


def test_pending_reflection_and_relationship_backlogs_become_terminal_metadata(
    migrated_database: Database,
) -> None:
    identity = activate(migrated_database).identity.identity_id
    interaction_id, root, _, _ = _canonical_position_source(migrated_database, identity_id=identity)
    run, _, proposal_id = _create_reflection_source(migrated_database, identity_id=identity)
    snapshot = _snapshot(identity, frozenset({root}))
    _install(migrated_database, snapshot)
    with migrated_database.session_factory() as session:
        session.info["source_snapshot"] = snapshot
        source_before = tuple(session.scalars(select(ReflectionSourceRow.source_id)))
        reflection = SQLAlchemyReflectionRepository(session)
        reflection.withdraw_source_support(
            excluded_message_ids=snapshot.excluded_message_ids,
            request_id="exclude-backlogs",
            policy_generation=1,
            now=INTERACTION_TIME + timedelta(days=3),
        )
        relationship = SQLAlchemyRelationshipRepository(session)
        relationship.withdraw_source_support(
            excluded_message_ids=snapshot.excluded_message_ids,
            request_id="exclude-backlogs",
            policy_generation=1,
            now=INTERACTION_TIME + timedelta(days=3),
        )
        session.commit()
        terminal = reflection.get_run(run.run_id)
        assert terminal is not None
        assert terminal.status.value == "exhausted"
        assert reflection.list_proposals(run.run_id) == ()
        outcomes = reflection.list_outcomes(run.run_id)
        assert len(outcomes) == 1
        assert outcomes[0].proposal_id == proposal_id
        assert outcomes[0].reason_code == "canonical_source_excluded"
        assert relationship.get_source(interaction_id) is None
        assert relationship.list_unprocessed_source_ids(identity, "alice", limit=20) == ()
        assert relationship.get_decision(interaction_id) is None
        assert tuple(session.scalars(select(ReflectionSourceRow.source_id))) == source_before
        assert {receipt.owner for receipt in session.scalars(select(OwnerSourceWithdrawalRow))} == {
            "reflection_run",
            "relationship_backlog",
        }


def test_personality_rejects_excluded_fixed_root_without_numeric_rollback(
    migrated_database: Database,
) -> None:
    fixture = _seed_personality_run(migrated_database)
    with migrated_database.session_factory() as session:
        source_rows = tuple(
            session.scalars(
                select(ReflectionSourceRow)
                .where(ReflectionSourceRow.run_id == fixture.run_id)
                .order_by(ReflectionSourceRow.ordinal)
            )
        )
        excluded_root = source_rows[-1].root_message_id
        before = SQLAlchemyPersonalityRepository(session).get_current(fixture.identity_id)
        snapshot = _snapshot(fixture.identity_id)
        _receipt(
            session,
            snapshot,
            PayloadRef("reflection_proposal", fixture.proposal_id),
            roots=tuple(row.root_message_id for row in source_rows),
        )
        session.commit()
    excluded = _snapshot(fixture.identity_id, frozenset({excluded_root}))
    _install(migrated_database, excluded)
    factory = controlled_session_factory(migrated_database, FakeSourceRuntime(excluded))
    owner = ApplyPersonalityReflection(
        unit_of_work_factory=lambda: SQLAlchemyPersonalityUnitOfWork(factory),
        manager=PersonalityManager(),
        clock=FrozenClock(NOW),
        id_generator=PrefixIds("excluded-personality"),
    )
    with pytest.raises(ValueError, match="personality_source_ineligible"):
        owner.apply(
            fixture.identity_id,
            reflection_run_id=fixture.run_id,
            reflection_proposal_id=fixture.proposal_id,
            proposal=fixture.proposal,
            trace_id="excluded-personality",
        )
    with migrated_database.session_factory() as session:
        assert SQLAlchemyPersonalityRepository(session).get_current(fixture.identity_id) == before


@pytest.mark.parametrize(("topic", "alternative"), [("Alpha", "Beta"), ("Beta", "Alpha")])
def test_held_inclination_key_is_typed_rejection_for_both_preference_orders(
    topic: str,
    alternative: str,
) -> None:
    candidate = InclinationProposal(
        InclinationKind.PREFERENCE, topic, alternative, 0.99, ("clean-source",)
    )
    evaluation = PositionManager().evaluate_inclination(
        candidate,
        identity_id="satori",
        sources=(),
        existing_inclinations=(),
        reflection_outcome_id="clean-outcome",
        now=INTERACTION_TIME,
        new_id=lambda: "forbidden-new-identity",
        held_keys=frozenset({inclination_key(InclinationKind.PREFERENCE, "alpha", "beta")}),
    )
    assert evaluation.kind.value == "rejected"
    assert evaluation.reason_code == "inclination_disclosure_held"
    assert evaluation.inclination is None
    assert evaluation.new_evidence == ()


def test_affect_finalize_checks_prepared_input_separately_from_foreground_override(
    migrated_database: Database,
) -> None:
    self_snapshot = activate(migrated_database)
    old_interaction = create_interaction(
        migrated_database,
        counterparty_id="alice",
        content="Prior source for appraisal context",
        prefix="affect-old-root",
        day=1,
    )
    appraisal = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: proposal_for(request.interaction_id)
    )
    services, _, _ = build_affect(
        migrated_database, appraisal, clock=FrozenClock(INTERACTION_TIME + timedelta(days=2))
    )
    command = TalkInput("Current clean source", "affect-current-trace", "affect-current-request")
    current = services.talk.interaction_log.begin(
        command, identity_id=self_snapshot.identity.identity_id
    )
    snapshot = _snapshot(self_snapshot.identity.identity_id)
    with migrated_database.session_factory() as session:
        old_root = session.scalar(
            select(ConversationMessageRow.message_id).where(
                ConversationMessageRow.interaction_id == old_interaction,
                ConversationMessageRow.role == "user",
            )
        )
        assert old_root is not None
        session.add(
            SemanticClaimRow(
                claim_id="old-context-claim",
                claim_key="a" * 64,
                schema_version=1,
                aggregate_version=1,
                subject="user",
                predicate="context",
                value_kind="text",
                value="Prior source for appraisal context",
                normalized_value="prior source for appraisal context",
                polarity=True,
                claim_kind="explicit_fact",
                confidence=0.8,
                status="active",
                valid_from=INTERACTION_TIME + timedelta(days=1),
                valid_until=None,
                superseded_by_claim_id=None,
                created_at=INTERACTION_TIME + timedelta(days=1),
                updated_at=INTERACTION_TIME + timedelta(days=1),
                formation_method="synthetic.affect-context.v1",
                formation_version=1,
                normalization_version=1,
            )
        )
        _receipt(
            session, snapshot, PayloadRef("semantic_claim", "old-context-claim"), roots=(old_root,)
        )
        session.commit()
    _install(migrated_database, snapshot)
    runtime = FakeSourceRuntime(snapshot)
    factory = controlled_session_factory(migrated_database, runtime)
    prepare = services.talk.prepare_affect
    assert prepare is not None
    prepare.ensure_state.unit_of_work_factory = lambda: SQLAlchemyAffectiveStateUnitOfWork(factory)
    semantic = RetrievedSemanticContext(
        "available",
        (
            RetrievedSemanticClaim(
                "old-context-claim",
                "user",
                "context",
                "text",
                "Prior source for appraisal context",
                True,
                "explicit_fact",
                0.8,
                (),
                (old_root,),
            ),
        ),
        (PayloadRef("semantic_claim", "old-context-claim"),),
    )
    prepared = asyncio.run(
        prepare.execute(
            self_snapshot,
            current,
            user_text=command.user_text,
            trace_id=command.trace_id,
            memory_context=None,
            semantic_context=semantic,
        )
    )
    assert prepared.transition is not None
    assert prepared.source_input_id is not None
    with factory() as session:
        session.info["source_snapshot"] = snapshot
        foreground_input = SessionSourceAdmission(session).prepare(
            root_message_ids=(current.user_message.message_id,),
            payload_refs=(),
        )
        session.commit()
    excluded = _snapshot(snapshot.identity_id, frozenset({old_root}))
    _install(migrated_database, excluded)
    runtime.snapshot = excluded
    finalize = FinalizeAffectiveInteraction(
        lambda: SQLAlchemyAffectiveConversationUnitOfWork(factory),
        FrozenClock(INTERACTION_TIME + timedelta(days=2)),
        id_sequence("blocked-affect-finalize"),
    )
    with pytest.raises(SourcePolicyError, match="source_payload_unavailable"):
        finalize.execute(
            current,
            assistant_text="Provider reply",
            provider_metadata=_provider_metadata(prepared),
            prepared=prepared,
            source_input_id=foreground_input,
        )
    with factory() as session:
        from satori.infrastructure.persistence.repositories.affect import (
            SQLAlchemyAffectiveStateRepository,
        )

        repository = SQLAlchemyAffectiveStateRepository(session)
        state = repository.get_state(snapshot.identity_id)
        assert state is not None
        assert state.state_version == 1
        assert tuple(repository.list_transitions()) == ()
        assert (
            session.scalar(
                select(func.count())
                .select_from(ConversationMessageRow)
                .where(
                    ConversationMessageRow.interaction_id == current.interaction_id,
                    ConversationMessageRow.role == "assistant",
                )
            )
            == 0
        )


@pytest.mark.parametrize(
    ("owner", "held_prior_payload"),
    [("models", False), ("positions", False), ("models", True), ("positions", True)],
)
def test_direct_owner_plan_denied_before_first_sql_for_unprepared_roots_or_prior_text(
    migrated_database: Database,
    owner: str,
    held_prior_payload: bool,
) -> None:
    if owner == "models":
        identity, _, aggregate_id, _ = _form_models(migrated_database)
        with migrated_database.session_factory() as session:
            repository = SQLAlchemyCurrentModelsRepository(session)
            original_model = repository._map_user_claim(
                session.get_one(UserModelClaimRow, aggregate_id)
            )
            prior_key = session.scalar(select(ModelFormationDecisionRow.idempotency_key))
            assert prior_key is not None
            prior_decision = repository.get_decision(prior_key)
            assert prior_decision is not None
    else:
        identity = activate(migrated_database).identity.identity_id
        _canonical_position_source(migrated_database, identity_id=identity)
        _, _, proposal_id = _create_reflection_source(migrated_database, identity_id=identity)
        aggregate_id = "inclination-source-position"
        with migrated_database.session_factory() as session:
            original_position = SQLAlchemyPositionsRepository(session)._map_position(
                session.get_one(SatoriPositionRow, aggregate_id)
            )
    clean_interaction = create_interaction(
        migrated_database,
        counterparty_id="alice",
        content="Separate canonical completed window",
        prefix=f"clean-plan-window-{owner}",
        day=2,
    )
    snapshot = _snapshot(
        identity, frozenset({"absent-old-root"}) if held_prior_payload else frozenset()
    )
    with migrated_database.session_factory() as session:
        session.info["source_snapshot"] = snapshot
        if held_prior_payload:
            _receipt(
                session,
                snapshot,
                PayloadRef("user_model_claim" if owner == "models" else "position", aggregate_id),
                unknown=True,
            )
            session.commit()
    _install(migrated_database, snapshot)
    with migrated_database.session_factory() as session:
        session.info["source_snapshot"] = snapshot
        clean_root = session.scalar(
            select(ConversationMessageRow.message_id).where(
                ConversationMessageRow.interaction_id == clean_interaction,
                ConversationMessageRow.role == "user",
            )
        )
        assert clean_root is not None
        input_id = SessionSourceAdmission(session).prepare(
            root_message_ids=(clean_root,), payload_refs=()
        )
        session.commit()
        mutation_operations: list[str] = []

        def observe_first_write(
            connection: object,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            executemany: bool,
        ) -> None:
            operation = statement.lstrip().split(maxsplit=1)[0].upper()
            if operation in {"INSERT", "UPDATE", "DELETE"}:
                mutation_operations.append(operation)

        event.listen(migrated_database.engine, "before_cursor_execute", observe_first_write)

        def attempt_writer() -> None:
            if owner == "models":
                assert prior_decision is not None
                model_id = aggregate_id if held_prior_payload else "unprepared-model-identity"
                model = replace(
                    original_model,
                    claim_id=model_id,
                    aggregate_version=2 if held_prior_payload else 1,
                    evidence=original_model.evidence
                    if held_prior_payload
                    else tuple(
                        replace(edge, evidence_id="unprepared-model-edge", claim_id=model_id)
                        for edge in original_model.evidence
                    ),
                )
                decision = replace(
                    prior_decision,
                    decision_id="unprepared-model-decision",
                    idempotency_key="unprepared-model-key",
                    user_claim_ids=(model_id,),
                    world_claim_ids=(),
                    world_created_count=0,
                )
                SQLAlchemyCurrentModelsRepository(session).record_decision(
                    decision,
                    OwnerFormationPlan(claims=(model,), revisions=()),
                    OwnerFormationPlan(claims=(), revisions=()),
                    user_audit_event_id="unprepared-model-audit",
                    world_audit_event_id="unprepared-world-audit",
                    source_input_id=input_id,
                )
            else:
                position_id = aggregate_id if held_prior_payload else "unprepared-position-identity"
                position = replace(
                    original_position,
                    position_id=position_id,
                    aggregate_version=2 if held_prior_payload else 1,
                    evidence=original_position.evidence
                    if held_prior_payload
                    else tuple(
                        replace(
                            edge,
                            evidence_id="unprepared-position-edge",
                            position_id=position_id,
                        )
                        for edge in original_position.evidence
                    ),
                )
                outcome = ReflectionOutcome(
                    "unprepared-position-outcome",
                    proposal_id,
                    1,
                    ReflectionOutcomeDecision.ACCEPTED,
                    "owner_changes_applied",
                    "satori_positions",
                    position_id,
                    INTERACTION_TIME + timedelta(days=3),
                )
                SQLAlchemyPositionsRepository(session).record_reflection_decision(
                    outcome,
                    PositionFormationPlan(positions=(position,), revisions=()),
                    identity_id=identity,
                    trace_id="unprepared-position",
                    audit_event_id="unprepared-position-audit",
                    source_input_id=input_id,
                )

        try:
            with pytest.raises(
                SourcePolicyError,
                match="source_payload_unavailable"
                if held_prior_payload
                else "source_evidence_unavailable",
            ):
                attempt_writer()
            assert mutation_operations == []
        finally:
            event.remove(migrated_database.engine, "before_cursor_execute", observe_first_write)
        session.rollback()


def test_initial_enrollment_retires_unproven_unknown_keys_without_faking_exclusion(
    migrated_database: Database,
) -> None:
    identity, _, user_id, world_id = _form_models(migrated_database)
    snapshot = _snapshot(identity)
    with migrated_database.session_factory() as session:
        session.info["source_snapshot"] = snapshot
        _receipt(session, snapshot, PayloadRef("user_model_claim", user_id), unknown=True)
        _receipt(session, snapshot, PayloadRef("world_model_claim", world_id), unknown=True)
        session.flush()
        for row in session.scalars(select(SourcePayloadRow)):
            row.legacy = False
        session.flush()
        materialize_mirror(session, snapshot)
        SQLAlchemyCurrentModelsRepository(session).withdraw_source_support(
            excluded_message_ids=frozenset(),
            request_id="enroll-unproven",
            policy_generation=0,
            now=INTERACTION_TIME + timedelta(days=3),
        )
        session.commit()
        assert session.get_one(UserModelClaimRow, user_id).status == "retracted"
        assert session.get_one(WorldModelClaimRow, world_id).status == "retracted"
        receipts = tuple(session.scalars(select(OwnerSourceWithdrawalRow)))
        assert len(receipts) == 2
        assert all(
            receipt.policy_generation == 0 and receipt.root_message_ids == []
            for receipt in receipts
        )


def test_unproven_pending_reflection_closes_at_generation_zero(
    migrated_database: Database,
) -> None:
    identity = activate(migrated_database).identity.identity_id
    _canonical_position_source(migrated_database, identity_id=identity)
    run, source, proposal_id = _create_reflection_source(migrated_database, identity_id=identity)
    snapshot = _snapshot(identity)
    with migrated_database.session_factory() as session:
        session.info["source_snapshot"] = snapshot
        _receipt(
            session,
            snapshot,
            PayloadRef("reflection_proposal", proposal_id),
            roots=(source.root_message_id,),
            unknown=True,
        )
        session.flush()
        session.get_one(SourcePayloadRow, ("reflection_proposal", proposal_id, 1)).legacy = False
        session.flush()
        materialize_mirror(session, snapshot)
        repository = SQLAlchemyReflectionRepository(session)
        repository.withdraw_source_support(
            excluded_message_ids=frozenset(),
            request_id="enroll-reflection-unproven",
            policy_generation=0,
            now=INTERACTION_TIME + timedelta(days=3),
        )
        session.commit()
        terminal = repository.get_run(run.run_id)
        assert terminal is not None
        assert terminal.status.value == "exhausted"
        assert repository.list_proposals(run.run_id) == ()
        assert repository.list_outcomes(run.run_id)[0].reason_code == "source_eligibility_withheld"
        receipt = session.scalar(select(OwnerSourceWithdrawalRow))
        assert receipt is not None
        assert receipt.policy_generation == 0
        assert receipt.root_message_ids == []
