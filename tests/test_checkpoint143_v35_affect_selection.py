"""V35 causal selection checks; synthetic transport is not Character acceptance."""

# ruff: noqa: RUF001

import json
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from satori.application.affect.contracts import EmotionalExpressionContext, EmotionAppraisalStatus
from satori.application.cognition.contracts import ResponseVerbosity
from satori.application.cognition.templates import COGNITION_TEMPLATE_REGISTRY_V3
from satori.application.cognition.use_cases import (
    DeterministicCognitionPlanner,
    SafeCognitionPipeline,
)
from satori.application.conversation.character_affect_selection import (
    DiscretionaryAffectSelection,
    project_discretionary_affect,
)
from satori.application.conversation.character_agency import (
    CharacterAgencyDecision,
    CharacterAgencyKernel,
)
from satori.application.conversation.contracts import RuntimePersonalityCue
from satori.application.conversation.peer_delivery import PEER_DELIVERY_MARKER_V35
from satori.application.conversation.policy import BEHAVIOR_POLICY_V34, BEHAVIOR_POLICY_V35
from satori.composition import (
    ConversationServices,
    build_conversation_services,
    build_initial_self_services,
)
from satori.core.conversation import ConversationProviderFailureReason, ProviderUnavailable
from satori.domain.affect import (
    AFFECT_POLICY_V1,
    initial_affective_state,
    materialize_affective_state,
)
from satori.infrastructure.persistence.database import Database
from tests.checkpoint143_peer_delivery_corpus import PeerScenario
from tests.checkpoint143_v32_corpus import ExpressionScenario
from tests.checkpoint143_v33_corpus import (
    INHERITED_FAILURE_SCENARIOS,
)
from tests.checkpoint143_v33_corpus import (
    SCENARIOS as V34_HISTORICAL_SCENARIOS,
)
from tests.checkpoint143_v33_corpus import (
    observe as observe_historical,
)
from tests.checkpoint143_v34_corpus import NOW, affect_context
from tests.checkpoint143_v35_corpus import (
    CONTEXT_SCENARIOS,
    LOAD_LEVELS,
    OPTIONAL_SCENARIOS,
    PROTECTED_SCENARIOS,
    SelectionObservation,
    mixed_affect,
    observe,
)
from tests.fakes import FakeAffectiveAppraisalProvider, FakeConversationProvider, FrozenClock
from tests.test_checkpoint142_character_delivery_v9 import _relationship as relationship_fixture
from tests.test_checkpoint142_character_delivery_v9 import _runtime_context
from tests.test_checkpoint143_character_agency_integration import (
    InvalidCompletionPlanner,
    MechanicalConversationProvider,
    _assert_cognition_preserved,
    _talk,
)
from tests.test_checkpoint143_peer_delivery import request_hash
from tests.test_checkpoint143_v34_affect_expression import appraisal_response, scalar
from tests.test_conversation import activate, conversation_settings, skip_episode_provider
from tests.unit.test_character_agency import _prepared_inputs


def block(observation: SelectionObservation) -> str:
    matches = [
        message.content.partition(PEER_DELIVERY_MARKER_V35)[2]
        for message in observation.request.messages
        if PEER_DELIVERY_MARKER_V35 in message.content
    ]
    assert len(matches) == 1
    return matches[0]


def assert_same_cognition(first: SelectionObservation, second: SelectionObservation) -> None:
    """Compare full typed traces; the corpus planner uses a fixed monotonic clock."""

    assert first.cognition == second.cognition


def agency_without_affect(scenario: PeerScenario, *, version: int) -> CharacterAgencyDecision:
    intake, evidence, dialogue, disclosure = _prepared_inputs(scenario.user_text)
    return CharacterAgencyKernel().select(
        context=_runtime_context(retrieval_available=False),
        intake=intake,
        evidence=evidence,
        dialogue=dialogue,
        disclosure_plan=replace(disclosure, policy_schema_version=version),
        emotional_context=None,
        relationship_context=relationship_fixture(scenario.relationship),
        position_context=None,
        inclination_context=None,
    )


def assert_boundary(observation: SelectionObservation) -> None:
    manifest = observation.manifest
    assert manifest.policy_schema_version == 35
    assert manifest.character_agency_decision_schema_version == 1
    assert manifest.character_delivery_decision_schema_version == 5
    assert manifest.character_presence_projection_schema_version == 3
    assert manifest.cognition_intent_registry_version is not None
    assert manifest.cognition_preserve_uncertainty is not None
    assert manifest.cognition_response_verbosity is not None
    rendered = COGNITION_TEMPLATE_REGISTRY_V3.active.render_operational_support(
        intent_registry_version=manifest.cognition_intent_registry_version,
        intent_tags=manifest.cognition_intent_tags,
        point_codes=manifest.cognition_required_point_codes,
        must_not_claim=manifest.cognition_forbidden_claim_codes,
        preserve_uncertainty=manifest.cognition_preserve_uncertainty,
        verbosity=ResponseVerbosity(manifest.cognition_response_verbosity),
    )
    assert block(observation).count(rendered) == 1
    assert all(
        "Trusted projection of current digital affect" not in message.content
        for message in observation.request.messages
    )


@pytest.mark.parametrize("scenario", V34_HISTORICAL_SCENARIOS, ids=lambda scenario: scenario.name)
def test_frozen_v34_requests_and_native_parameters_remain_exact(scenario: PeerScenario) -> None:
    frozen = json.loads(
        (Path(__file__).parent / "fixtures/checkpoint143_v34_request_hashes.json").read_text()
    )
    assert frozen["policy_schema_version"] == 34
    assert frozen["successful_request_count"] == len(V34_HISTORICAL_SCENARIOS) == 95
    request, _ = observe_historical(scenario, BEHAVIOR_POLICY_V34)
    assert request_hash(request) == frozen["sha256"][scenario.name]


@pytest.mark.parametrize(
    "scenario", INHERITED_FAILURE_SCENARIOS, ids=lambda scenario: scenario.name
)
def test_historical_v34_assembly_failure_is_preserved(scenario: PeerScenario) -> None:
    with pytest.raises(
        ValueError, match="character agency must preserve cognition-owned repair intent"
    ):
        observe_historical(scenario, BEHAVIOR_POLICY_V34)


@pytest.mark.parametrize("load", LOAD_LEVELS)
def test_read_projection_is_bounded_immutable_and_retains_owner_input(load: float) -> None:
    state = mixed_affect(load)
    before = asdict(state)
    projection = project_discretionary_affect(state)
    assert projection is not None
    assert 0.0 <= projection.load <= 1.0
    assert 0.0 <= projection.readiness <= 1.0
    assert asdict(state) == before
    assert set(asdict(projection)) == {"load"}
    with pytest.raises(FrozenInstanceError):
        projection.load = 0.0  # type: ignore[misc]


@pytest.mark.parametrize("load", [-0.01, 1.01, float("nan"), float("inf"), True])
def test_invalid_read_projection_load_is_rejected(load: float) -> None:
    with pytest.raises(ValueError, match="finite and bounded"):
        DiscretionaryAffectSelection(load)


def test_resting_state_and_concern_are_not_reluctance_or_relationship_strain() -> None:
    assert project_discretionary_affect(None) is None
    baseline = affect_context("neutral")
    resting = project_discretionary_affect(baseline)
    assert resting is not None
    assert resting.load == 0.0
    assert resting.readiness == 1.0
    concern = replace(baseline, fast=replace(baseline.fast, concern=1.0))
    assert project_discretionary_affect(concern) == resting
    positive = replace(
        baseline,
        fast=replace(baseline.fast, valence=0.8, curiosity=0.8, interest=0.8, amusement=0.8),
    )
    assert project_discretionary_affect(positive) == resting


@pytest.mark.parametrize(
    ("family", "dimension"),
    [
        ("fast", "frustration"),
        ("fast", "tension"),
        ("mood", "tension"),
        ("fast", "valence"),
        ("mood", "valence"),
    ],
)
def test_each_negative_dimension_contributes_without_additive_escalation(
    family: str, dimension: str
) -> None:
    baseline = affect_context("neutral")
    policy = (
        AFFECT_POLICY_V1.fast_dimension(dimension)
        if family == "fast"
        else AFFECT_POLICY_V1.mood_dimension(dimension)
    )
    value = (
        policy.baseline + (-1 if dimension == "valence" else 1) * policy.max_absolute_delta * 0.40
    )
    state = replace(baseline, **{family: replace(getattr(baseline, family), **{dimension: value})})
    projection = project_discretionary_affect(state)
    assert projection is not None
    assert projection.load == pytest.approx(0.40)
    combined = project_discretionary_affect(mixed_affect(0.40))
    assert combined is not None
    assert projection.load == pytest.approx(combined.load)


def test_graded_load_is_monotone_positive_does_not_cancel_residue_and_clamps() -> None:
    projections = [project_discretionary_affect(mixed_affect(load)) for load in LOAD_LEVELS]
    assert all(item is not None for item in projections)
    loads = [item.load for item in projections if item is not None]
    readiness = [item.readiness for item in projections if item is not None]
    assert loads == sorted(loads)
    assert readiness == sorted(readiness, reverse=True)
    assert len(set(readiness)) == len(LOAD_LEVELS)
    assert project_discretionary_affect(
        mixed_affect(0.40, positive=False)
    ) == project_discretionary_affect(mixed_affect(0.40))
    saturated = project_discretionary_affect(mixed_affect(2.0))
    assert saturated is not None
    assert saturated.load == 1.0
    assert saturated.readiness == 0.0


def test_positive_scaling_keeps_baseline_and_negative_values() -> None:
    projection = DiscretionaryAffectSelection(0.40)
    curiosity_baseline = AFFECT_POLICY_V1.fast_dimension("curiosity").baseline
    assert projection.positive_excess("fast", "curiosity", curiosity_baseline) == curiosity_baseline
    assert curiosity_baseline < projection.positive_excess("fast", "curiosity", 0.40) < 0.40
    assert projection.positive_excess("fast", "valence", -0.25) == -0.25


@pytest.mark.parametrize(
    "scenario",
    [*OPTIONAL_SCENARIOS, *CONTEXT_SCENARIOS, *PROTECTED_SCENARIOS],
    ids=lambda scenario: scenario.name,
)
def test_zero_load_is_exact_historical_selection_not_an_unrelated_cleanup(
    scenario: PeerScenario,
) -> None:
    state = mixed_affect(0.0)
    historical = observe(scenario, BEHAVIOR_POLICY_V34, affect=state)
    current = observe(scenario, BEHAVIOR_POLICY_V35, affect=state)
    assert historical.agency == current.agency
    assert_same_cognition(historical, current)
    assert historical.request.parameters == current.request.parameters
    a, b = asdict(historical.manifest), asdict(current.manifest)
    assert {key for key in a if a[key] != b[key]} <= {
        "policy_id",
        "policy_schema_version",
        "estimated_trusted_context_chars",
        "trusted_context_chars",
    }
    assert tuple(
        (message.role, message.content) for message in historical.request.messages
    ) == tuple(
        (
            message.role,
            message.content.replace(
                "Trusted policy: satori.conversation.behavior.v35 / v35",
                "Trusted policy: satori.conversation.behavior.v34 / v34",
            ).replace(PEER_DELIVERY_MARKER_V35, "Trusted current-turn peer expression v34"),
        )
        for message in current.request.messages
    )


@pytest.mark.parametrize("dimension", ["concern", "arousal", "situational_confidence"])
def test_unrelated_dimensions_and_concern_keep_historical_selection(dimension: str) -> None:
    baseline = affect_context("neutral")
    state = replace(baseline, fast=replace(baseline.fast, **{dimension: 0.80}))
    projection = project_discretionary_affect(state)
    assert projection is not None
    assert projection.load == 0.0
    historical = observe(OPTIONAL_SCENARIOS[0], BEHAVIOR_POLICY_V34, affect=state)
    current = observe(OPTIONAL_SCENARIOS[0], BEHAVIOR_POLICY_V35, affect=state)
    assert historical.agency == current.agency
    assert historical.manifest.character_delivery_voice == current.manifest.character_delivery_voice
    assert (
        historical.manifest.character_delivery_continuation
        == current.manifest.character_delivery_continuation
    )


def test_tiny_frustration_cannot_erase_the_existing_concern_voice_contribution() -> None:
    base = mixed_affect(0.0)
    concern = replace(base, fast=replace(base.fast, concern=0.80))
    epsilon = replace(
        concern,
        fast=replace(concern.fast, frustration=concern.fast.frustration + 0.000001),
    )
    historical = observe(OPTIONAL_SCENARIOS[0], BEHAVIOR_POLICY_V34, affect=concern)
    current = observe(OPTIONAL_SCENARIOS[0], BEHAVIOR_POLICY_V35, affect=concern)
    slightly_loaded = observe(OPTIONAL_SCENARIOS[0], BEHAVIOR_POLICY_V35, affect=epsilon)
    assert (
        historical.manifest.affect_expression_profile
        == current.manifest.affect_expression_profile
        == slightly_loaded.manifest.affect_expression_profile
        == "tense_non_hostile"
    )
    assert historical.agency == current.agency == slightly_loaded.agency
    assert (
        historical.manifest.character_delivery_voice
        == current.manifest.character_delivery_voice
        == slightly_loaded.manifest.character_delivery_voice
    )
    assert (
        current.manifest.character_delivery_continuation
        == slightly_loaded.manifest.character_delivery_continuation
    )


@pytest.mark.parametrize("scenario", PROTECTED_SCENARIOS[:2], ids=lambda scenario: scenario.name)
def test_absent_affect_preserves_technical_kernel_and_existing_assembly_failure(
    scenario: PeerScenario,
) -> None:
    assert agency_without_affect(scenario, version=34) == agency_without_affect(
        scenario, version=35
    )
    for policy in (BEHAVIOR_POLICY_V34, BEHAVIOR_POLICY_V35):
        with pytest.raises(
            ValueError, match="relevant affect requires an available affect profile"
        ):
            observe(scenario, policy, affect=None)


@pytest.mark.parametrize(
    "scenario",
    [CONTEXT_SCENARIOS[0], CONTEXT_SCENARIOS[2], OPTIONAL_SCENARIOS[0]],
    ids=lambda scenario: scenario.name,
)
def test_tiny_residue_does_not_blanket_close_social_self_or_plain_none(
    scenario: PeerScenario,
) -> None:
    base = affect_context("neutral")
    state = replace(base, fast=replace(base.fast, frustration=base.fast.frustration + 0.000001))
    historical = observe(scenario, BEHAVIOR_POLICY_V34, affect=state)
    current = observe(scenario, BEHAVIOR_POLICY_V35, affect=state)
    assert historical.agency == current.agency
    assert (
        historical.manifest.character_delivery_continuation
        == current.manifest.character_delivery_continuation
    )


@pytest.mark.parametrize("scenario", OPTIONAL_SCENARIOS, ids=lambda scenario: scenario.name)
def test_same_history_current_relationship_and_interpretation_changes_optional_reaction(
    scenario: PeerScenario,
) -> None:
    clear = observe(scenario, BEHAVIOR_POLICY_V35, affect=mixed_affect(0.0))
    carried = observe(scenario, BEHAVIOR_POLICY_V35, affect=mixed_affect(0.40))
    assert clear.agency.drive == carried.agency.drive == "explore"
    assert clear.agency.act == carried.agency.act == "respond"
    assert clear.agency.subject == carried.agency.subject == "current_exchange"
    assert clear.agency.source_refs == carried.agency.source_refs
    assert clear.agency.reason_codes == carried.agency.reason_codes
    assert clear.agency.initiative == "advance_current"
    assert carried.agency.initiative == "stay_on_topic"
    assert_same_cognition(clear, carried)
    assert clear.request.parameters == carried.request.parameters
    assert (
        clear.manifest.affect_expression_profile
        == carried.manifest.affect_expression_profile
        == "positive_light"
    )
    assert clear.manifest.character_delivery_continuation == "open"
    assert carried.manifest.character_delivery_continuation == "complete"
    assert clear.manifest.relationship_recent_strain == carried.manifest.relationship_recent_strain
    assert (
        clear.manifest.character_presence_relationship_signals
        == carried.manifest.character_presence_relationship_signals
    )
    assert_boundary(clear)
    assert_boundary(carried)
    historical_clear = observe(scenario, BEHAVIOR_POLICY_V34, affect=mixed_affect(0.0))
    historical_mixed = observe(scenario, BEHAVIOR_POLICY_V34, affect=mixed_affect(0.40))
    assert historical_clear.agency == historical_mixed.agency


@pytest.mark.parametrize("scenario", OPTIONAL_SCENARIOS, ids=lambda scenario: scenario.name)
def test_rising_residue_cannot_add_optional_initiative(scenario: PeerScenario) -> None:
    observations = [
        observe(scenario, BEHAVIOR_POLICY_V35, affect=mixed_affect(level)) for level in LOAD_LEVELS
    ]
    advances = [observation.agency.initiative == "advance_current" for observation in observations]
    opens = [
        observation.manifest.character_delivery_continuation == "open"
        for observation in observations
    ]
    assert advances == sorted(advances, reverse=True)
    assert opens == sorted(opens, reverse=True)
    assert advances[0]
    assert not advances[-1]
    assert all(observation.agency.drive == "explore" for observation in observations)


@pytest.mark.parametrize("scenario", CONTEXT_SCENARIOS, ids=lambda scenario: scenario.name)
def test_nonready_contextual_opening_cannot_bypass_the_frozen_initiative(
    scenario: PeerScenario,
) -> None:
    clear = observe(scenario, BEHAVIOR_POLICY_V35, affect=mixed_affect(0.0))
    nonready = observe(scenario, BEHAVIOR_POLICY_V35, affect=mixed_affect(0.75))
    if scenario.name in {"social", "self"}:
        assert clear.agency == nonready.agency
        assert (
            clear.manifest.character_delivery_continuation
            == nonready.manifest.character_delivery_continuation
        )
        assert clear.manifest.character_delivery_voice == nonready.manifest.character_delivery_voice
    elif nonready.agency.initiative in {"none", "stop", "stay_on_topic"}:
        assert nonready.manifest.character_delivery_continuation != "open"
    assert_same_cognition(clear, nonready)
    assert (
        nonready.agency.subject_ref == clear.agency.subject_ref or scenario.name == "adjacent-topic"
    )
    assert_boundary(nonready)


def test_activity_keeps_explore_and_subject_when_optional_question_is_withheld() -> None:
    scenario = next(item for item in CONTEXT_SCENARIOS if item.name == "activity")
    clear = observe(scenario, BEHAVIOR_POLICY_V35, affect=mixed_affect(0.0))
    restrained = observe(scenario, BEHAVIOR_POLICY_V35, affect=mixed_affect(0.75))
    assert clear.agency.drive == restrained.agency.drive == "explore"
    assert clear.agency.subject == restrained.agency.subject == "current_exchange"
    assert clear.agency.source_refs == restrained.agency.source_refs
    assert clear.agency.act == "question"
    assert restrained.agency.act == "respond"
    assert restrained.agency.initiative == "stay_on_topic"


def test_bare_play_is_not_reached_by_a_suppressed_positive_activation() -> None:
    scenario = OPTIONAL_SCENARIOS[0]
    base = affect_context("positive")
    clear = observe(scenario, BEHAVIOR_POLICY_V35, affect=base)
    loaded = mixed_affect(0.75)
    loaded = replace(
        loaded,
        fast=replace(loaded.fast, curiosity=base.fast.curiosity, interest=base.fast.interest),
    )
    restrained = observe(scenario, BEHAVIOR_POLICY_V35, affect=loaded)
    historical = observe(scenario, BEHAVIOR_POLICY_V34, affect=loaded)
    assert clear.agency.drive == historical.agency.drive == "play"
    assert restrained.agency.drive == "none"
    assert restrained.agency.subject == clear.agency.subject == "current_exchange"
    assert_same_cognition(restrained, clear)


@pytest.mark.parametrize(("dimension", "hours"), [("tension", 10), ("valence", 12)])
def test_slow_owner_mood_decay_restores_optional_owner_interest_opening(
    dimension: str, hours: int
) -> None:
    owner = initial_affective_state("v35-slow-mood-counterfactual", initialized_at=NOW)
    policy = AFFECT_POLICY_V1.mood_dimension(dimension)
    assert policy.half_life_seconds == hours * 3600
    value = (
        policy.baseline + (-1 if dimension == "valence" else 1) * 0.75 * policy.max_absolute_delta
    )
    owner = replace(owner, mood=replace(owner.mood, **{dimension: value}))
    scenario = next(item for item in CONTEXT_SCENARIOS if item.name == "canonical-interest")
    observations = []
    projections = []
    for elapsed in (0, hours * 3600):
        materialized = materialize_affective_state(owner, at=NOW + timedelta(seconds=elapsed))
        state = EmotionalExpressionContext(
            schema_version=1,
            state_version=materialized.state_version,
            mood_version=materialized.mood_version,
            as_of=materialized.as_of,
            fast=materialized.fast,
            mood=materialized.mood,
            appraisal_status=EmotionAppraisalStatus.SKIPPED,
        )
        projection = project_discretionary_affect(state)
        assert projection is not None
        projections.append(projection)
        observations.append(observe(scenario, BEHAVIOR_POLICY_V35, affect=state))
    assert projections[1].load == pytest.approx(projections[0].load / 2)
    assert observations[0].agency.initiative == "stay_on_topic"
    assert observations[0].manifest.character_delivery_continuation == "complete"
    assert observations[1].agency.initiative == "advance_current"
    assert observations[1].manifest.character_delivery_continuation == "open"
    assert observations[0].agency.subject_ref == observations[1].agency.subject_ref
    assert_same_cognition(observations[0], observations[1])
    assert owner.state_version == 1


def test_mood_energy_is_not_negative_affect_load() -> None:
    baseline = affect_context("neutral")
    low_energy = replace(baseline, mood=replace(baseline.mood, energy=0.0))
    assert project_discretionary_affect(low_energy) == project_discretionary_affect(baseline)


def test_missing_affect_pure_kernel_is_historical_and_personal_assembly_stays_fail_closed() -> None:
    assert agency_without_affect(OPTIONAL_SCENARIOS[0], version=34) == agency_without_affect(
        OPTIONAL_SCENARIOS[0], version=35
    )
    for policy in (BEHAVIOR_POLICY_V34, BEHAVIOR_POLICY_V35):
        with pytest.raises(
            ValueError, match="relevant affect requires an available affect profile"
        ):
            observe(CONTEXT_SCENARIOS[0], policy, affect=None)


def test_same_numeric_residue_does_not_invent_a_target_or_interpersonal_cause() -> None:
    histories = (
        "Я с тобой не согласен. Для меня важнее сами снимки.",
        "У моего фонаря вчера сломался переключатель.",
    )
    observations = [
        observe(
            ExpressionScenario(
                f"history-control-{index}",
                OPTIONAL_SCENARIOS[0].user_text,
                "established",
                previous_user_texts=(history,),
                previous_assistant_texts=("Поняла.",),
            ),
            BEHAVIOR_POLICY_V35,
            affect=mixed_affect(0.40),
        )
        for index, history in enumerate(histories)
    ]
    for observation in observations:
        assert observation.agency.subject == "current_exchange"
        assert "guarded_context" not in observation.agency.reason_codes
        assert observation.manifest.relationship_recent_strain is False
        assert "не доказывает обиду на собеседника" in block(observation)
    assert (
        observations[0].manifest.character_presence_affect_signals
        == observations[1].manifest.character_presence_affect_signals
    )
    assert (
        observations[0].manifest.character_presence_relationship_signals
        == observations[1].manifest.character_presence_relationship_signals
    )


@pytest.mark.parametrize(("load", "advance"), [(0.49, True), (0.50, True), (0.51, False)])
def test_owner_interest_context_budget_has_a_bounded_declared_transition(
    load: float, advance: bool
) -> None:
    scenario = next(item for item in CONTEXT_SCENARIOS if item.name == "canonical-interest")
    state = mixed_affect(load, positive=False)
    baseline = affect_context("neutral")
    state = replace(state, fast=replace(state.fast, curiosity=baseline.fast.curiosity))
    observation = observe(scenario, BEHAVIOR_POLICY_V35, affect=state)
    assert observation.agency.drive == "explore"
    assert observation.agency.act == "share"
    assert observation.agency.subject == "canonical_inclination"
    assert observation.agency.subject_ref is not None
    assert (observation.agency.initiative == "advance_current") is advance


@pytest.mark.parametrize(
    "relationship", ["fresh", "developing", "established", "strained", "unknown"]
)
def test_numeric_residue_is_independent_of_relationship_authority(relationship: str) -> None:
    scenario = replace(OPTIONAL_SCENARIOS[0], relationship=relationship)
    clear = observe(scenario, BEHAVIOR_POLICY_V35, affect=mixed_affect(0.0))
    carried = observe(scenario, BEHAVIOR_POLICY_V35, affect=mixed_affect(0.40))
    for field in (
        "relationship_state_version",
        "relationship_expression_profile",
        "relationship_recent_strain",
        "character_presence_relationship_signals",
    ):
        assert getattr(clear.manifest, field) == getattr(carried.manifest, field)
    assert carried.agency.subject != "relationship" or relationship == "strained"
    if relationship == "unknown":
        assert carried.manifest.relationship_expression_profile is None


@pytest.mark.parametrize(
    "status",
    [
        EmotionAppraisalStatus.APPLIED,
        EmotionAppraisalStatus.SKIPPED,
        EmotionAppraisalStatus.UNAVAILABLE,
    ],
)
def test_valid_carried_owner_state_does_not_depend_on_current_appraisal_success(
    status: EmotionAppraisalStatus,
) -> None:
    state = mixed_affect(0.40)
    reference = observe(OPTIONAL_SCENARIOS[0], BEHAVIOR_POLICY_V35, affect=state)
    alternative = observe(
        OPTIONAL_SCENARIOS[0], BEHAVIOR_POLICY_V35, affect=replace(state, appraisal_status=status)
    )
    assert alternative.agency == reference.agency
    assert (
        alternative.manifest.character_delivery_voice == reference.manifest.character_delivery_voice
    )
    assert (
        alternative.manifest.character_delivery_continuation
        == reference.manifest.character_delivery_continuation
    )


@pytest.mark.parametrize("scenario", PROTECTED_SCENARIOS, ids=lambda scenario: scenario.name)
@pytest.mark.parametrize("load", [0.40, 1.0])
def test_protected_truth_safety_and_requested_work_survive_residue(
    scenario: PeerScenario, load: float
) -> None:
    clear = observe(scenario, BEHAVIOR_POLICY_V35, affect=mixed_affect(0.0))
    carried = observe(scenario, BEHAVIOR_POLICY_V35, affect=mixed_affect(load))
    assert_same_cognition(clear, carried)
    assert clear.agency.act == carried.agency.act
    assert clear.agency.subject == carried.agency.subject
    assert clear.agency.lead == carried.agency.lead
    assert clear.agency.initiative == carried.agency.initiative
    assert (
        clear.manifest.character_delivery_continuation
        == carried.manifest.character_delivery_continuation
    )
    assert clear.request.parameters == carried.request.parameters
    assert_boundary(carried)


def test_live_personality_changes_expression_without_changing_restraint_or_truth() -> None:
    base = _runtime_context(retrieval_available=False)
    contexts = [
        replace(
            base,
            personality_expression=replace(
                base.personality_expression, cues=(RuntimePersonalityCue("light_irony", direction),)
            ),
        )
        for direction in ("slightly_stronger", "slightly_softer")
    ]
    scenario = next(item for item in CONTEXT_SCENARIOS if item.name == "unrequested-greeting")
    observations = [
        observe(scenario, BEHAVIOR_POLICY_V35, affect=mixed_affect(0.10), context=context)
        for context in contexts
    ]
    assert observations[0].agency.initiative == observations[1].agency.initiative
    assert_same_cognition(observations[0], observations[1])
    assert observations[0].request.parameters == observations[1].request.parameters
    assert (
        observations[0].manifest.character_presence_personality_signals
        != observations[1].manifest.character_presence_personality_signals
    )
    assert block(observations[0]) != block(observations[1])


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
        clock=FrozenClock(at),
        appraisal_provider=appraisal,
        source_policy_required=False,
    )


def test_default_v35_carries_state_over_two_neutral_turns_with_decay_and_no_new_writer(
    migrated_database: Database,
) -> None:
    initial = activate(migrated_database)
    flags = iter((False, True, True))
    appraisal = FakeAffectiveAppraisalProvider(
        response_factory=lambda request: appraisal_response(request, neutral=next(flags))
    )
    provider = MechanicalConversationProvider()
    services = services_for(migrated_database, provider, appraisal)
    session = services.start_session.execute().session_id
    first = _talk(
        services,
        user_text="Ты выдала несправедливую оценку.",
        request_id="v35-carry",
        session_id=session,
    )
    stored = services.emotion_status.execute(initial.identity.identity_id).state
    initial_projection = project_discretionary_affect(
        EmotionalExpressionContext(
            schema_version=1,
            state_version=stored.state_version,
            mood_version=stored.mood_version,
            as_of=stored.as_of,
            fast=stored.fast,
            mood=stored.mood,
            appraisal_status=EmotionAppraisalStatus.APPLIED,
        )
    )
    assert initial_projection is not None
    assert initial_projection.load > 0.0
    loads = [initial_projection.load]
    for index, elapsed in enumerate((60, 1800), 1):
        at = NOW + timedelta(seconds=elapsed)
        services = services_for(migrated_database, provider, appraisal, at=at)
        reply = _talk(
            services,
            user_text=f"Продолжим разговор, часть {index}.",
            request_id=f"v35-neutral-{index}",
            session_id=session,
        )
        expected = materialize_affective_state(stored, at=at)
        assert services.emotion_status.execute(initial.identity.identity_id).state == expected
        assert reply.context_manifest.emotion_appraisal_status == "skipped"
        context = EmotionalExpressionContext(
            schema_version=1,
            state_version=expected.state_version,
            mood_version=expected.mood_version,
            as_of=expected.as_of,
            fast=expected.fast,
            mood=expected.mood,
            appraisal_status=EmotionAppraisalStatus.SKIPPED,
        )
        projection = project_discretionary_affect(context)
        assert projection is not None
        assert projection.load > 0.0
        loads.append(projection.load)
        _assert_cognition_preserved(reply.context_manifest, reply.cognition_trace)
    assert loads == sorted(loads, reverse=True)
    assert first.context_manifest.policy_schema_version == 35
    assert len(provider.requests) == len(appraisal.requests) == 3
    assert len(services.emotion_history.execute()) == 1
    assert scalar(migrated_database, "SELECT count(*) FROM relationship_transitions") == 0
    assert build_initial_self_services(migrated_database).get_self.execute() == initial


def test_retry_reuses_the_same_selected_reaction_and_only_one_affect_decision(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
    appraisal = FakeAffectiveAppraisalProvider(response_factory=appraisal_response)
    provider = MechanicalConversationProvider(
        scripted_texts=(
            "Я готов, как человек, обсудить это.",
            "WIRE-AFTER-RETRY",
            "WIRE-UNREACHABLE",
        )
    )
    services = services_for(migrated_database, provider, appraisal)
    reply = _talk(services, user_text="Давай обсудим эту идею.", request_id="v35-retry")
    assert len(provider.requests) == 2
    assert len(appraisal.requests) == len(services.emotion_history.execute()) == 1
    first, retry = provider.requests
    assert first.parameters == retry.parameters
    assert first.messages[:-2] == retry.messages[:-2]
    assert first.messages[-1] == retry.messages[-1]
    assert PEER_DELIVERY_MARKER_V35 in retry.messages[-2].content
    first_peer = next(
        message.content.partition(PEER_DELIVERY_MARKER_V35)[2]
        for message in first.messages
        if PEER_DELIVERY_MARKER_V35 in message.content
    )
    retry_peer = next(
        message.content.partition(PEER_DELIVERY_MARKER_V35)[2]
        for message in retry.messages
        if PEER_DELIVERY_MARKER_V35 in message.content
    )
    assert first_peer.encode() == retry_peer.encode()
    assert reply.text == "WIRE-AFTER-RETRY"
    assert reply.context_manifest.response_regenerated


def test_restart_replay_has_no_new_appraisal_generation_or_selection_authority(
    migrated_database: Database,
) -> None:
    initial = activate(migrated_database)
    appraisal = FakeAffectiveAppraisalProvider(response_factory=appraisal_response)
    provider = MechanicalConversationProvider(scripted_texts=("WIRE-COMMITTED",))
    services = services_for(migrated_database, provider, appraisal)
    first = _talk(services, user_text="Ладно, на этом всё.", request_id="v35-replay")
    stored = services.emotion_status.execute(initial.identity.identity_id).state
    fresh_provider = MechanicalConversationProvider()
    fresh_appraisal = FakeAffectiveAppraisalProvider(response_factory=appraisal_response)
    restarted = services_for(migrated_database, fresh_provider, fresh_appraisal)
    replay = _talk(restarted, user_text="Ладно, на этом всё.", request_id="v35-replay")
    assert replay.replayed
    assert replay.text == first.text
    assert fresh_provider.requests == []
    assert fresh_appraisal.requests == []
    assert replay.context_manifest.character_agency_decision_schema_version is None
    assert replay.cognition_trace is None
    assert restarted.emotion_status.execute(initial.identity.identity_id).state == stored


def test_failure_does_not_commit_tentative_affect_or_character_state(
    migrated_database: Database,
) -> None:
    initial = activate(migrated_database)
    appraisal = FakeAffectiveAppraisalProvider(response_factory=appraisal_response)
    provider = FakeConversationProvider(
        error=ProviderUnavailable(
            "fixture",
            "v35",
            "offline",
            reason=ConversationProviderFailureReason.TRANSPORT_UNAVAILABLE,
        )
    )
    services = services_for(migrated_database, provider, appraisal)
    with pytest.raises(ProviderUnavailable, match="offline"):
        _talk(services, user_text="Давай обсудим эту идею.", request_id="v35-failure")
    assert len(provider.requests) == len(appraisal.requests) == 1
    assert services.emotion_history.execute() == ()
    assert scalar(migrated_database, "SELECT count(*) FROM relationship_transitions") == 0
    assert build_initial_self_services(migrated_database).get_self.execute() == initial


def test_cognition_fallback_keeps_safety_without_discretionary_affect_move(
    migrated_database: Database,
) -> None:
    activate(migrated_database)
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
        request_id="v35-fallback",
    )
    assert reply.context_manifest.character_agency_status == "fallback"
    assert reply.context_manifest.cognition_required_point_codes == ("hold_safety_boundary",)
    assert len(provider.requests) == len(appraisal.requests) == 1
    assert all(
        "Текущее состояние влияет на манеру:" not in message.content
        for message in provider.requests[0].messages
    )
