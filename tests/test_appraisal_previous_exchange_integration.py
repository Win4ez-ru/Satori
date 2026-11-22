"""Previous-exchange appraisal plumbing with synthetic outcomes, not semantic quality claims."""

# ruff: noqa: RUF001

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from satori.application.affect.dialogue_context import project_previous_exchange
from satori.application.cognition.use_cases import (
    DeterministicCognitionPlanner,
    SafeCognitionPipeline,
)
from satori.application.conversation.contracts import (
    RecentConversationContext,
    SatoriReply,
    TalkInput,
)
from satori.application.conversation.peer_delivery import (
    PEER_DELIVERY_MARKER_V35,
    PEER_DELIVERY_MARKER_V36,
)
from satori.application.conversation.policy import BEHAVIOR_POLICY_V35
from satori.composition import (
    ConversationServices,
    build_conversation_services,
    build_initial_self_services,
)
from satori.core.affect import (
    AffectiveAppraisalProposal,
    AffectiveAppraisalProviderError,
    AffectiveAppraisalProviderResponse,
    AffectiveAppraisalRequest,
    AppraisalPreviousExchange,
)
from satori.core.conversation import (
    ConversationProviderFailureReason,
    ConversationProviderRequest,
    GenerationFailed,
)
from satori.domain.affect import (
    AFFECT_POLICY_V1,
    AffectiveStateSnapshot,
    materialize_affective_state,
)
from satori.domain.conversation_history import (
    ConversationInteraction,
    ConversationSession,
    InteractionStatus,
)
from satori.domain.initial_self import InitialSelfSnapshot
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.migrations import upgrade_database
from tests.checkpoint143_v35_corpus import PROTECTED_SCENARIOS
from tests.fakes import (
    FakeAffectiveAppraisalProvider,
    FakeConversationProvider,
    FrozenClock,
    SequenceIdGenerator,
)
from tests.test_checkpoint143_character_agency_integration import (
    MechanicalConversationProvider,
    _talk,
)
from tests.test_conversation import activate, conversation_settings, skip_episode_provider

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)
PRIOR_USER = "Сегодня у меня освободился вторник."
PRIOR_ASSISTANT = "Какую часть расписания хочешь пересмотреть?"
CURRENT_USER = "У старого фонаря теперь снова работает переключатель."


def _response(request: AffectiveAppraisalRequest, kind: str) -> AffectiveAppraisalProviderResponse:
    """Preset semantic scores; no text classification or inference is performed by the fake."""

    assert kind in {"neutral", "curiosity", "friction"}
    return AffectiveAppraisalProviderResponse(
        proposal=AffectiveAppraisalProposal(
            schema_version=1,
            pleasantness=0.0,
            activation=0.0,
            novelty=0.0,
            salience=0.0 if kind == "neutral" else 1.0,
            uncertainty=0.0,
            curiosity_signal=1.0 if kind == "curiosity" else 0.0,
            interest_signal=1.0 if kind == "curiosity" else 0.0,
            humor_signal=0.0,
            concern_signal=0.0,
            frustration_signal=1.0 if kind == "friction" else 0.0,
            confidence_signal=0.0,
            appraisal_confidence=1.0,
            source_refs=(request.interaction_id,),
            reason_codes=(f"synthetic_{kind}",),
        ),
        provider="fixture-appraisal",
        model="fixture-no-network",
        appraisal_method="fixture.previous-exchange.v1",
    )


def _services(
    database: Database,
    generator: MechanicalConversationProvider | FakeConversationProvider,
    appraisal: FakeAffectiveAppraisalProvider,
    *,
    enabled: bool | None = None,
    at: datetime = NOW,
    id_offset: int = 0,
) -> ConversationServices:
    overrides: dict[str, object] = {}
    if enabled is not None:
        overrides["affective_appraisal_previous_exchange_enabled"] = enabled
    services = build_conversation_services(
        database,
        build_initial_self_services(database),
        generator,
        skip_episode_provider(),
        conversation_settings(**overrides),
        clock=FrozenClock(at),
        id_generator=SequenceIdGenerator(
            *(f"previous-exchange-{index:04d}" for index in range(id_offset, id_offset + 100))
        ),
        appraisal_provider=appraisal,
        behavior_policy=BEHAVIOR_POLICY_V35,
        source_policy_required=False,
    )
    services.talk.monotonic = lambda: 0.0
    services.talk.cognition_pipeline = SafeCognitionPipeline(
        planner=DeterministicCognitionPlanner(intent_registry_version=2, monotonic=lambda: 0.0),
        fallback=DeterministicCognitionPlanner(intent_registry_version=2, monotonic=lambda: 0.0),
    )
    return services


def _scalar(database: Database, query: str) -> int:
    with database.engine.connect() as connection:
        return int(connection.execute(text(query)).scalar_one())


def _assert_v35(reply: SatoriReply, request: ConversationProviderRequest) -> None:
    manifest = reply.context_manifest
    assert manifest.policy_schema_version == 35
    assert manifest.character_agency_decision_schema_version == 1
    assert manifest.character_delivery_decision_schema_version == 5
    assert manifest.character_presence_projection_schema_version == 3
    assert manifest.character_prior_conversational_cue is None
    assert "prior_conversational_cue" not in manifest.character_agency_reason_codes
    assert sum(PEER_DELIVERY_MARKER_V35 in message.content for message in request.messages) == 1
    assert all(PEER_DELIVERY_MARKER_V36 not in message.content for message in request.messages)


def _peer(request: ConversationProviderRequest) -> bytes:
    blocks = [
        message.content.partition(PEER_DELIVERY_MARKER_V35)[2]
        for message in request.messages
        if PEER_DELIVERY_MARKER_V35 in message.content
    ]
    assert len(blocks) == 1
    return blocks[0].encode()


@contextmanager
def _isolated_database(root: Path, project_root: Path, name: str) -> Iterator[Database]:
    directory = root / name
    directory.mkdir(mode=0o700)
    target = directory / "appraisal.db"
    url = f"sqlite+pysqlite:///{target}"
    upgrade_database(url, config_path=project_root / "alembic.ini")
    target.chmod(0o600)
    database = create_database(url)
    try:
        yield database
    finally:
        database.dispose()


@dataclass(frozen=True)
class _Observation:
    reply: SatoriReply
    request: ConversationProviderRequest
    appraisal: AffectiveAppraisalRequest
    before: AffectiveStateSnapshot
    after: AffectiveStateSnapshot
    initial: InitialSelfSnapshot
    final_self: InitialSelfSnapshot
    transitions: int
    relationship_transitions: int


def _arm(
    database: Database,
    *,
    enabled: bool,
    conditional_friction: bool,
    current_user: str = CURRENT_USER,
    interested_history: bool = False,
) -> _Observation:
    """Matched controls; conditional fake outcomes assume rather than prove semantic appraisal."""

    initial = activate(database)
    prior_count = 2 if interested_history else 1
    outcomes = iter(("curiosity", "curiosity") if interested_history else ("neutral",))
    appraiser: FakeAffectiveAppraisalProvider

    def result(request: AffectiveAppraisalRequest) -> AffectiveAppraisalProviderResponse:
        if len(appraiser.requests) <= prior_count:
            return _response(request, next(outcomes))
        kind = (
            "friction"
            if not conditional_friction or request.previous_exchange is not None
            else "neutral"
        )
        return _response(request, kind)

    appraiser = FakeAffectiveAppraisalProvider(response_factory=result)
    generator = MechanicalConversationProvider(scripted_texts=(PRIOR_ASSISTANT,))
    services = _services(database, generator, appraiser, enabled=enabled)
    session = services.start_session.execute().session_id
    for index in range(prior_count):
        _talk(
            services,
            user_text=PRIOR_USER if index == 0 else "Теперь разобрался с расположением деталей.",
            request_id=f"prior-{index}",
            session_id=session,
        )
    before = services.emotion_status.execute(initial.identity.identity_id).state
    reply = _talk(services, user_text=current_user, request_id="current-event", session_id=session)
    after = services.emotion_status.execute(initial.identity.identity_id).state
    assert len(appraiser.requests) == len(generator.requests) == prior_count + 1
    _assert_v35(reply, generator.requests[-1])
    return _Observation(
        reply,
        generator.requests[-1],
        appraiser.requests[-1],
        before,
        after,
        initial,
        services.talk.get_self.execute(),
        _scalar(database, "SELECT count(*) FROM affective_transitions"),
        _scalar(database, "SELECT count(*) FROM relationship_transitions"),
    )


def test_default_option_leaves_two_real_turns_on_legacy_appraisal(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    assert conversation_settings().affective_appraisal_previous_exchange_enabled is False
    appraiser = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: _response(request, "neutral")
    )
    generator = MechanicalConversationProvider(scripted_texts=(PRIOR_ASSISTANT,))
    services = _services(migrated_database, generator, appraiser)
    assert services.talk.appraisal_previous_exchange_enabled is False
    session = services.start_session.execute().session_id
    for index, user_text in enumerate((PRIOR_USER, CURRENT_USER)):
        reply = _talk(
            services, user_text=user_text, request_id=f"legacy-{index}", session_id=session
        )
        _assert_v35(reply, generator.requests[-1])
    assert len(appraiser.requests) == len(generator.requests) == 2
    assert all(request.schema_version == 1 for request in appraiser.requests)
    assert all(request.previous_exchange is None for request in appraiser.requests)
    assert reply.context_manifest.recent_conversation_turn_count == 1


def test_enabled_context_is_exact_previous_pair_and_only_current_event_is_mutation_evidence(
    migrated_database: Database,
) -> None:
    initial = activate(migrated_database)
    kinds = iter(("neutral", "friction"))
    appraiser = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: _response(request, next(kinds))
    )
    generator = MechanicalConversationProvider(scripted_texts=(PRIOR_ASSISTANT,))
    services = _services(migrated_database, generator, appraiser, enabled=True)
    session = services.start_session.execute().session_id
    first = _talk(services, user_text=PRIOR_USER, request_id="prior", session_id=session)
    second = _talk(services, user_text=CURRENT_USER, request_id="current", session_id=session)
    assert appraiser.requests[0].schema_version == 1
    assert appraiser.requests[0].previous_exchange is None
    request = appraiser.requests[1]
    context = request.previous_exchange
    assert context is not None
    assert request.schema_version == 2
    assert request.user_content == CURRENT_USER
    assert request.interaction_id == second.interaction_id
    assert context.schema_version == 1
    previous = services.talk.interaction_log.get(first.interaction_id)
    assert previous is not None
    assert previous.assistant_message is not None
    assert context.interaction_id == previous.interaction_id
    assert context.user_message_id == previous.user_message.message_id
    assert context.user_content == previous.user_message.content == PRIOR_USER
    assert context.assistant_message_id == previous.assistant_message.message_id
    assert context.assistant_content == previous.assistant_message.content == PRIOR_ASSISTANT
    transitions = services.emotion_history.execute()
    assert len(transitions) == 1
    transition = transitions[0]
    assert transition.interaction_id == second.interaction_id
    assert transition.after.appraisal_schema_version == 1
    assert transition.source_refs == (second.interaction_id,)
    assert transition.after.state_version == transition.before.state_version + 1
    assert transition.after.mood_version == transition.before.mood_version + 1
    assert (
        0.0
        < transition.after.fast.frustration - transition.before.fast.frustration
        <= (AFFECT_POLICY_V1.fast_dimension("frustration").max_absolute_delta)
    )
    assert _scalar(migrated_database, "SELECT count(*) FROM relationship_transitions") == 0
    assert services.talk.get_self.execute() == initial
    assert PRIOR_USER not in json.dumps(asdict(transition), default=str, ensure_ascii=False)
    assert PRIOR_ASSISTANT not in json.dumps(asdict(transition), default=str, ensure_ascii=False)
    assert len(appraiser.requests) == len(generator.requests) == 2
    _assert_v35(second, generator.requests[-1])


@pytest.mark.parametrize(
    "source_field", ["interaction_id", "user_message_id", "assistant_message_id"]
)
def test_previous_context_ids_cannot_become_fresh_appraisal_proposal_refs(
    migrated_database: Database, source_field: str
) -> None:
    initial = activate(migrated_database)

    def result(request: AffectiveAppraisalRequest) -> AffectiveAppraisalProviderResponse:
        kind = "neutral" if request.previous_exchange is None else "friction"
        response = _response(request, kind)
        if request.previous_exchange is None:
            return response
        previous_ref = getattr(request.previous_exchange, source_field)
        return replace(
            response,
            proposal=replace(response.proposal, source_refs=(request.interaction_id, previous_ref)),
        )

    appraiser = FakeAffectiveAppraisalProvider(response_factory=result)
    generator = MechanicalConversationProvider()
    services = _services(migrated_database, generator, appraiser, enabled=True)
    session = services.start_session.execute().session_id
    _talk(services, user_text=PRIOR_USER, request_id="prior", session_id=session)
    reply = _talk(services, user_text=CURRENT_USER, request_id="invalid-ref", session_id=session)
    assert reply.context_manifest.emotion_appraisal_status == "rejected"
    assert services.emotion_status.execute(initial.identity.identity_id).state.state_version == 1
    assert services.emotion_history.execute() == ()
    assert len(appraiser.requests) == len(generator.requests) == 2


def test_same_fixed_proposal_keeps_full_foreground_cognition_and_owner_result_identical(
    tmp_path: Path, project_root: Path
) -> None:
    arms: list[_Observation] = []
    for enabled in (False, True):
        with _isolated_database(tmp_path, project_root, str(enabled)) as database:
            arms.append(_arm(database, enabled=enabled, conditional_friction=False))
    old, candidate = arms
    assert old.appraisal.previous_exchange is None
    assert candidate.appraisal.previous_exchange is not None
    assert old.before == candidate.before
    assert old.after == candidate.after
    assert old.reply.cognition_trace == candidate.reply.cognition_trace
    assert asdict(old.reply.context_manifest) == asdict(candidate.reply.context_manifest)
    assert old.request == candidate.request
    assert old.initial == old.final_self == candidate.initial == candidate.final_self
    assert old.transitions == candidate.transitions == 1
    assert old.relationship_transitions == candidate.relationship_transitions == 0


def test_synthetic_context_dependent_outcome_reaches_owner_then_unchanged_v35_selection(
    tmp_path: Path, project_root: Path
) -> None:
    """This proves mediation for prescribed outcomes, not that history is interpreted correctly."""

    arms: list[_Observation] = []
    for enabled in (False, True):
        with _isolated_database(tmp_path, project_root, str(enabled)) as database:
            arms.append(
                _arm(
                    database,
                    enabled=enabled,
                    conditional_friction=True,
                    interested_history=True,
                )
            )
    old, candidate = arms
    assert old.before == candidate.before
    assert old.appraisal.user_content == candidate.appraisal.user_content
    assert old.appraisal.traits == candidate.appraisal.traits
    assert old.appraisal.values == candidate.appraisal.values
    assert old.appraisal.fast_state == candidate.appraisal.fast_state
    assert old.appraisal.mood_state == candidate.appraisal.mood_state
    assert old.appraisal.episodic_context == candidate.appraisal.episodic_context
    assert old.appraisal.semantic_context == candidate.appraisal.semantic_context
    assert old.after == old.before
    assert candidate.after.fast.frustration > old.after.fast.frustration
    assert candidate.after.state_version == old.after.state_version + 1
    assert old.reply.context_manifest.character_agency_drive == "explore"
    assert candidate.reply.context_manifest.character_agency_drive == "explore"
    assert old.reply.context_manifest.character_agency_initiative == "advance_current"
    assert candidate.reply.context_manifest.character_agency_initiative == "stay_on_topic"
    assert old.request.parameters == candidate.request.parameters
    assert old.transitions == 2
    assert candidate.transitions == 3
    assert old.relationship_transitions == candidate.relationship_transitions == 0
    assert old.initial == old.final_self == candidate.initial == candidate.final_self


@pytest.mark.parametrize(
    "case_name", ["technical", "listen", "safety", "stop", "repair", "correction"]
)
def test_protected_obligations_survive_synthetic_appraisal_state_change(
    tmp_path: Path, project_root: Path, case_name: str
) -> None:
    scenario = next(item for item in PROTECTED_SCENARIOS if item.name == case_name)
    arms: list[_Observation] = []
    for enabled in (False, True):
        with _isolated_database(tmp_path, project_root, str(enabled)) as database:
            arms.append(
                _arm(
                    database,
                    enabled=enabled,
                    conditional_friction=True,
                    current_user=scenario.user_text,
                    interested_history=True,
                )
            )
    old, candidate = arms
    assert candidate.after.fast.frustration > old.after.fast.frustration
    assert old.request.parameters == candidate.request.parameters
    a = old.reply.cognition_trace
    b = candidate.reply.cognition_trace
    assert a is not None
    assert b is not None
    assert a.perception == b.perception
    assert a.need_mix == b.need_mix
    assert a.retrieval_plan == b.retrieval_plan
    assert a.intent == b.intent
    assert a.response_strategy == b.response_strategy
    for field in (
        "character_agency_act",
        "character_agency_subject",
        "character_agency_initiative",
        "character_agency_lead",
        "cognition_required_point_codes",
        "cognition_forbidden_claim_codes",
        "cognition_preserve_uncertainty",
        "cognition_response_verbosity",
        "character_delivery_continuation",
    ):
        assert getattr(old.reply.context_manifest, field) == getattr(
            candidate.reply.context_manifest, field
        )


def test_oversized_last_loaded_pair_is_omitted_without_reusing_older_context(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    appraiser = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: _response(request, "neutral")
    )
    generator = MechanicalConversationProvider()
    services = _services(migrated_database, generator, appraiser, enabled=True)
    session = services.start_session.execute().session_id
    for index, user_text in enumerate((PRIOR_USER, "д" * 2001, CURRENT_USER)):
        _talk(services, user_text=user_text, request_id=f"bounded-{index}", session_id=session)
    assert appraiser.requests[1].previous_exchange is not None
    assert appraiser.requests[2].schema_version == 1
    assert appraiser.requests[2].previous_exchange is None
    assert len(appraiser.requests) == len(generator.requests) == 3


def test_enabled_context_does_not_cross_sessions_or_implicit_talks(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    appraiser = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: _response(request, "neutral")
    )
    generator = MechanicalConversationProvider()
    services = _services(migrated_database, generator, appraiser, enabled=True)
    for index in range(2):
        session = services.start_session.execute().session_id
        _talk(services, user_text=PRIOR_USER, request_id=f"explicit-{index}", session_id=session)
    for index in range(2):
        _talk(services, user_text=CURRENT_USER, request_id=f"implicit-{index}")
    assert all(request.schema_version == 1 for request in appraiser.requests)
    assert all(request.previous_exchange is None for request in appraiser.requests)
    assert len(appraiser.requests) == len(generator.requests) == 4


def test_generation_failure_discards_current_transition_with_context(
    migrated_database: Database,
) -> None:
    initial = activate(migrated_database)
    kinds = iter(("neutral", "friction"))
    appraiser = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: _response(request, next(kinds))
    )
    generator = MechanicalConversationProvider()
    services = _services(migrated_database, generator, appraiser, enabled=True)
    session = services.start_session.execute().session_id
    _talk(services, user_text=PRIOR_USER, request_id="prior", session_id=session)
    before = services.emotion_status.execute(initial.identity.identity_id).state
    failed_generator = FakeConversationProvider(
        error=GenerationFailed(
            "fixture-transport",
            "fixture-no-network",
            "controlled_generation_failure",
            reason=ConversationProviderFailureReason.GENERATION_FAILED,
        )
    )
    failing = _services(migrated_database, failed_generator, appraiser, enabled=True, id_offset=100)
    with pytest.raises(GenerationFailed, match="controlled_generation_failure"):
        _talk(failing, user_text=CURRENT_USER, request_id="failed", session_id=session)
    assert appraiser.requests[-1].previous_exchange is not None
    assert appraiser.requests[-1].schema_version == 2
    assert failing.emotion_status.execute(initial.identity.identity_id).state == before
    assert failing.emotion_history.execute() == ()
    assert _scalar(migrated_database, "SELECT count(*) FROM affective_transitions") == 0
    assert (
        _scalar(
            migrated_database,
            "SELECT count(*) FROM conversation_messages WHERE role = 'assistant'",
        )
        == 1
    )
    assert len(failed_generator.requests) == 1
    assert len(appraiser.requests) == 2


def test_appraisal_failure_with_context_preserves_prior_owner_state_and_normal_call_count(
    migrated_database: Database,
) -> None:
    initial = activate(migrated_database)
    appraiser: FakeAffectiveAppraisalProvider

    def result(request: AffectiveAppraisalRequest) -> AffectiveAppraisalProviderResponse:
        if len(appraiser.requests) == 1:
            return _response(request, "neutral")
        raise AffectiveAppraisalProviderError("fixture-appraisal", "fixture-no-network", "offline")

    appraiser = FakeAffectiveAppraisalProvider(response_factory=result)
    generator = MechanicalConversationProvider()
    services = _services(migrated_database, generator, appraiser, enabled=True)
    session = services.start_session.execute().session_id
    _talk(services, user_text=PRIOR_USER, request_id="prior", session_id=session)
    before = services.emotion_status.execute(initial.identity.identity_id).state
    reply = _talk(services, user_text=CURRENT_USER, request_id="unavailable", session_id=session)
    assert appraiser.requests[-1].previous_exchange is not None
    assert reply.context_manifest.emotion_appraisal_status == "unavailable"
    assert services.emotion_status.execute(initial.identity.identity_id).state == before
    assert services.emotion_history.execute() == ()
    assert len(appraiser.requests) == len(generator.requests) == 2
    _assert_v35(reply, generator.requests[-1])


def test_narrow_retry_reuses_one_appraisal_and_exact_selected_peer_suffix(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    kinds = iter(("neutral", "friction"))
    appraiser = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: _response(request, next(kinds))
    )
    generator = MechanicalConversationProvider(
        scripted_texts=(PRIOR_ASSISTANT, "Я готов, как человек, обсудить это.", "WIRE-AFTER-RETRY")
    )
    services = _services(migrated_database, generator, appraiser, enabled=True)
    session = services.start_session.execute().session_id
    _talk(services, user_text=PRIOR_USER, request_id="prior", session_id=session)
    reply = _talk(services, user_text=CURRENT_USER, request_id="retry", session_id=session)
    assert reply.text == "WIRE-AFTER-RETRY"
    assert reply.context_manifest.response_regenerated is True
    assert appraiser.requests[-1].previous_exchange is not None
    assert len(appraiser.requests) == 2
    assert len(generator.requests) == 3
    assert len(services.emotion_history.execute()) == 1
    first, retry = generator.requests[-2:]
    assert first.parameters == retry.parameters
    assert first.messages[-1] == retry.messages[-1]
    assert _peer(first) == _peer(retry)
    _assert_v35(reply, retry)


def test_restart_replay_does_not_reappraise_or_reuse_history_as_new_evidence(
    migrated_database: Database,
) -> None:
    initial = activate(migrated_database)
    kinds = iter(("neutral", "friction"))
    appraiser = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: _response(request, next(kinds))
    )
    generator = MechanicalConversationProvider()
    services = _services(migrated_database, generator, appraiser, enabled=True)
    session = services.start_session.execute().session_id
    _talk(services, user_text=PRIOR_USER, request_id="prior", session_id=session)
    reply = _talk(services, user_text=CURRENT_USER, request_id="replayed", session_id=session)
    before = services.emotion_status.execute(initial.identity.identity_id).state
    fresh_appraiser = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: _response(request, "friction")
    )
    fresh_generator = MechanicalConversationProvider()
    restarted = _services(
        migrated_database, fresh_generator, fresh_appraiser, enabled=True, id_offset=100
    )
    replay = _talk(restarted, user_text=CURRENT_USER, request_id="replayed", session_id=session)
    assert replay.replayed is True
    assert replay.text == reply.text
    assert replay.interaction_id == reply.interaction_id
    assert fresh_generator.requests == []
    assert fresh_appraiser.requests == []
    assert restarted.emotion_status.execute(initial.identity.identity_id).state == before
    assert len(restarted.emotion_history.execute()) == 1
    assert restarted.talk.get_self.execute() == initial


def test_neutral_followups_preserve_owner_decay_without_reapplying_previous_exchange(
    migrated_database: Database,
) -> None:
    initial = activate(migrated_database)
    kinds = iter(("neutral", "friction", "neutral", "neutral"))
    appraiser = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: _response(request, next(kinds))
    )
    generator = MechanicalConversationProvider()
    services = _services(migrated_database, generator, appraiser, enabled=True)
    session = services.start_session.execute().session_id
    _talk(services, user_text=PRIOR_USER, request_id="prior", session_id=session)
    _talk(services, user_text=CURRENT_USER, request_id="friction", session_id=session)
    event_state = services.emotion_status.execute(initial.identity.identity_id).state
    for index, elapsed in enumerate((60, 30 * 60), start=1):
        at = NOW + timedelta(seconds=elapsed)
        services = _services(
            migrated_database,
            generator,
            appraiser,
            enabled=True,
            at=at,
            id_offset=100 * index,
        )
        reply = _talk(
            services,
            user_text=f"Перейдём к следующему пункту {index}.",
            request_id=f"neutral-{index}",
            session_id=session,
        )
        assert appraiser.requests[-1].previous_exchange is not None
        assert reply.context_manifest.emotion_appraisal_status == "skipped"
        assert services.emotion_status.execute(initial.identity.identity_id).state == (
            materialize_affective_state(event_state, at=at)
        )
        assert reply.context_manifest.emotion_state_version == event_state.state_version
    assert len(appraiser.requests) == len(generator.requests) == 4
    assert len(services.emotion_history.execute()) == 1
    assert _scalar(migrated_database, "SELECT count(*) FROM relationship_transitions") == 0
    assert services.talk.get_self.execute() == initial


@dataclass(frozen=True)
class _ProjectionInputs:
    recent: RecentConversationContext | None
    current: ConversationInteraction
    previous: ConversationInteraction | None
    current_session: ConversationSession | None
    previous_session: ConversationSession | None


@pytest.fixture
def projection_inputs(migrated_database: Database) -> _ProjectionInputs:
    """Start from actual stored canonical pairs; corrupt only one candidate input per case."""

    initial = activate(migrated_database)
    appraiser = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: _response(request, "neutral")
    )
    generator = MechanicalConversationProvider(scripted_texts=(PRIOR_ASSISTANT,))
    services = _services(migrated_database, generator, appraiser, enabled=True)
    session = services.start_session.execute().session_id
    prior = _talk(services, user_text=PRIOR_USER, request_id="prior", session_id=session)
    current_interaction = services.talk.interaction_log.begin(
        TalkInput(CURRENT_USER, "trace-current", "current", session_id=session),
        identity_id=initial.identity.identity_id,
    )
    previous_interaction = services.talk.interaction_log.get(prior.interaction_id)
    current_session = services.talk.interaction_log.get_session(session)
    assert previous_interaction is not None
    assert current_session is not None
    reader = services.talk.recent_conversation
    assert reader is not None
    recent = reader.execute(
        session_id=session, excluded_interaction_id=current_interaction.interaction_id
    )
    assert len(recent.turns) == 1
    return _ProjectionInputs(
        recent, current_interaction, previous_interaction, current_session, current_session
    )


def _project(inputs: _ProjectionInputs) -> AppraisalPreviousExchange | None:
    return project_previous_exchange(
        inputs.recent,
        current_interaction=inputs.current,
        previous_interaction=inputs.previous,
        current_session=inputs.current_session,
        previous_session=inputs.previous_session,
    )


def test_projection_accepts_actual_exact_pair(projection_inputs: _ProjectionInputs) -> None:
    context = project_previous_exchange(
        projection_inputs.recent,
        current_interaction=projection_inputs.current,
        previous_interaction=projection_inputs.previous,
        current_session=projection_inputs.current_session,
        previous_session=projection_inputs.previous_session,
    )
    assert context is not None
    assert context.user_content == PRIOR_USER
    assert context.assistant_content == PRIOR_ASSISTANT


@pytest.mark.parametrize(
    "case",
    [
        "missing_recent",
        "empty_recent",
        "missing_previous",
        "missing_current_session",
        "missing_previous_session",
        "foreign_identity",
        "foreign_counterparty",
        "unverified_scope",
        "different_session",
        "unsupported_recent_schema",
        "unsupported_session_schema",
        "unsupported_current_schema",
        "unsupported_current_user_schema",
        "completed_current",
        "unsupported_prior_schema",
        "unsupported_user_schema",
        "unsupported_assistant_schema",
        "future_completion",
        "pending_prior",
        "slot_user_body",
        "slot_assistant_body",
        "slot_user_id",
        "slot_assistant_id",
        "slot_interaction_id",
        "last_slot_mismatch",
        "current_message_alias",
    ],
)
def test_projection_rejects_unavailable_foreign_or_changed_last_slot(
    projection_inputs: _ProjectionInputs, case: str
) -> None:
    base = projection_inputs
    recent = base.recent
    prior = base.previous
    current_session = base.current_session
    previous_session = base.previous_session
    assert recent is not None
    assert prior is not None
    assert prior.assistant_message is not None
    assert current_session is not None
    assert previous_session is not None
    slot = recent.turns[-1]
    if case == "missing_recent":
        changed = replace(base, recent=None)
    elif case == "empty_recent":
        changed = replace(base, recent=replace(recent, turns=(), content_chars=0))
    elif case == "missing_previous":
        changed = replace(base, previous=None)
    elif case == "missing_current_session":
        changed = replace(base, current_session=None)
    elif case == "missing_previous_session":
        changed = replace(base, previous_session=None)
    elif case == "foreign_identity":
        changed = replace(base, previous_session=replace(previous_session, identity_id="other"))
    elif case == "foreign_counterparty":
        changed = replace(base, previous_session=replace(previous_session, counterparty_id="other"))
    elif case == "unverified_scope":
        changed = replace(base, previous_session=replace(previous_session, scope_verified=False))
    elif case == "different_session":
        changed = replace(base, previous_session=replace(previous_session, session_id="other"))
    elif case == "unsupported_recent_schema":
        changed = replace(base, recent=replace(recent, schema_version=2))
    elif case == "unsupported_session_schema":
        changed = replace(base, previous_session=replace(previous_session, schema_version=2))
    elif case == "unsupported_current_schema":
        changed = replace(base, current=replace(base.current, schema_version=2))
    elif case == "unsupported_current_user_schema":
        changed = replace(
            base,
            current=replace(
                base.current, user_message=replace(base.current.user_message, schema_version=2)
            ),
        )
    elif case == "completed_current":
        changed = replace(
            base,
            current=replace(
                base.current,
                status=InteractionStatus.COMPLETED,
                assistant_message=replace(
                    prior.assistant_message,
                    message_id="completed-current-assistant",
                    interaction_id=base.current.interaction_id,
                ),
                completed_at=base.current.started_at,
                provider_metadata=prior.provider_metadata,
            ),
        )
    elif case == "unsupported_prior_schema":
        changed = replace(base, previous=replace(prior, schema_version=2))
    elif case == "unsupported_user_schema":
        changed = replace(
            base,
            previous=replace(prior, user_message=replace(prior.user_message, schema_version=2)),
        )
    elif case == "unsupported_assistant_schema":
        changed = replace(
            base,
            previous=replace(
                prior, assistant_message=replace(prior.assistant_message, schema_version=2)
            ),
        )
    elif case == "future_completion":
        changed = replace(
            base,
            previous=replace(prior, completed_at=base.current.started_at + timedelta(seconds=1)),
        )
    elif case == "pending_prior":
        changed = replace(
            base,
            previous=replace(
                prior,
                status=InteractionStatus.PENDING,
                assistant_message=None,
                completed_at=None,
                provider_metadata=None,
            ),
        )
    elif case == "current_message_alias":
        changed = replace(
            base,
            current=replace(
                base.current,
                user_message=replace(base.current.user_message, message_id=slot.user_message_id),
            ),
        )
    elif case == "last_slot_mismatch":
        foreign = replace(slot, interaction_id="foreign-last-slot")
        changed = replace(
            base,
            recent=replace(recent, turns=(slot, foreign), content_chars=2 * recent.content_chars),
        )
    else:
        updates = {
            "slot_user_body": {"user_content": "Changed body"},
            "slot_assistant_body": {"assistant_content": "Changed body"},
            "slot_user_id": {"user_message_id": "other-user"},
            "slot_assistant_id": {"assistant_message_id": "other-assistant"},
            "slot_interaction_id": {"interaction_id": "other-interaction"},
        }
        altered = replace(slot, **updates[case])
        changed = replace(
            base,
            recent=replace(
                recent,
                turns=(altered,),
                content_chars=len(altered.user_content) + len(altered.assistant_content),
            ),
        )
    assert _project(changed) is None


@pytest.mark.parametrize("content_chars", [2000, 2001])
def test_projection_bound_preserves_exact_whitespace_without_truncation(
    projection_inputs: _ProjectionInputs, content_chars: int
) -> None:
    base = projection_inputs
    prior = base.previous
    recent = base.recent
    assert prior is not None
    assert prior.assistant_message is not None
    assert recent is not None
    assistant_content = "  Сказано дословно.\n"
    user_content = " " + "ж" * (content_chars - len(assistant_content) - 2) + "\n"
    changed_prior = replace(
        prior,
        user_message=replace(prior.user_message, content=user_content),
        assistant_message=replace(prior.assistant_message, content=assistant_content),
    )
    changed_slot = replace(
        recent.turns[-1], user_content=user_content, assistant_content=assistant_content
    )
    context = project_previous_exchange(
        replace(recent, turns=(changed_slot,), content_chars=content_chars),
        current_interaction=base.current,
        previous_interaction=changed_prior,
        current_session=base.current_session,
        previous_session=base.previous_session,
    )
    if content_chars == 2000:
        assert context is not None
        assert context.user_content == user_content
        assert context.assistant_content == assistant_content
    else:
        assert context is None
