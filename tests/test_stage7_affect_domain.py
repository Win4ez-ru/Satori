"""Stage 7 policy, decay, bounds, personality modulation, and simulations."""

import math
from datetime import UTC, datetime, timedelta

import pytest

from satori.core.affect import AffectiveAppraisalProposal
from satori.domain.affect import (
    AFFECT_POLICY_V1,
    AppraisalDecisionKind,
    EmotionManager,
    FastAffectiveState,
    MoodState,
    initial_affective_state,
    materialize_affective_state,
)
from satori.domain.initial_self import activate_from_seed
from satori.domain.personality import Personality, PersonalityTrait
from satori.infrastructure.seeds.loader import JsonSeedLoader
from tests.affect_simulation import AffectSimulation

ORIGIN = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)


def personality() -> Personality:
    snapshot = activate_from_seed(
        JsonSeedLoader().load_canonical(),
        identity_id="affect-domain",
        activation_time=ORIGIN,
    )
    return snapshot.personality


def appraisal(
    interaction_id: str,
    *,
    pleasantness: float = 0.0,
    activation: float = 0.0,
    novelty: float = 0.0,
    salience: float = 0.0,
    uncertainty: float = 0.0,
    curiosity: float = 0.0,
    interest: float = 0.0,
    humor: float = 0.0,
    concern: float = 0.0,
    frustration: float = 0.0,
    confidence_signal: float = 0.0,
    confidence: float = 0.9,
    source_refs: tuple[str, ...] | None = None,
) -> AffectiveAppraisalProposal:
    return AffectiveAppraisalProposal(
        schema_version=1,
        pleasantness=pleasantness,
        activation=activation,
        novelty=novelty,
        salience=salience,
        uncertainty=uncertainty,
        curiosity_signal=curiosity,
        interest_signal=interest,
        humor_signal=humor,
        concern_signal=concern,
        frustration_signal=frustration,
        confidence_signal=confidence_signal,
        appraisal_confidence=confidence,
        source_refs=source_refs or (interaction_id,),
        reason_codes=("controlled_fixture",),
    )


def test_v1_dimensions_ranges_baselines_caps_and_timescales_are_explicit() -> None:
    """The policy contains exactly the Stage 7 continuous state and no relationship state."""

    assert FastAffectiveState.field_names() == (
        "valence",
        "arousal",
        "tension",
        "curiosity",
        "interest",
        "amusement",
        "concern",
        "frustration",
        "situational_confidence",
    )
    assert MoodState.field_names() == ("valence", "energy", "tension")
    assert not (
        {"affection", "attachment", "trust", "closeness"} & set(dict(AFFECT_POLICY_V1.fast))
    )
    state = initial_affective_state("identity", initialized_at=ORIGIN)
    assert state.fast.curiosity == 0.18
    assert state.fast.curiosity != personality().trait("curiosity").value
    assert AFFECT_POLICY_V1.fast_dimension("amusement").half_life_seconds == 5 * 60
    assert AFFECT_POLICY_V1.fast_dimension("concern").half_life_seconds == 2 * 3600
    assert min(item.half_life_seconds for _, item in AFFECT_POLICY_V1.mood) > max(
        item.half_life_seconds for _, item in AFFECT_POLICY_V1.fast
    )


def test_decay_is_monotone_semigroup_signed_and_independent_of_read_frequency() -> None:
    """Lazy materialization depends only on elapsed time and never increments state versions."""

    simulation = AffectSimulation(personality(), origin=ORIGIN)
    decision = simulation.apply(
        appraisal(
            "positive",
            pleasantness=1.0,
            activation=0.8,
            salience=1.0,
            interest=0.8,
            confidence=1.0,
        ),
        seconds=0,
        interaction_id="positive",
    )
    assert decision.transition is not None
    event_state = simulation.state
    direct = materialize_affective_state(event_state, at=ORIGIN + timedelta(hours=1))
    frequent = event_state
    for minute in range(1, 61):
        frequent = materialize_affective_state(
            frequent,
            at=ORIGIN + timedelta(minutes=minute),
        )

    assert frequent.fast.as_mapping() == pytest.approx(direct.fast.as_mapping(), abs=1e-12)
    assert frequent.mood.as_mapping() == pytest.approx(direct.mood.as_mapping(), abs=1e-12)
    assert direct.state_version == event_state.state_version
    assert direct.mood_version == event_state.mood_version
    assert 0.0 < direct.fast.valence < event_state.fast.valence
    repeated_reads = [
        materialize_affective_state(event_state, at=ORIGIN + timedelta(hours=1)) for _ in range(100)
    ]
    assert all(item == repeated_reads[0] for item in repeated_reads)


def test_neutral_hundreds_of_events_have_zero_state_and_mood_drift() -> None:
    """Neutral appraisal is a true no-op rather than a hidden resting-state impulse."""

    simulation = AffectSimulation(personality(), origin=ORIGIN)
    initial = simulation.state
    for index in range(500):
        interaction_id = f"neutral-{index}"
        decision = simulation.apply(
            appraisal(interaction_id),
            seconds=float(index),
            interaction_id=interaction_id,
        )
        assert decision.kind is AppraisalDecisionKind.SKIPPED
    final = simulation.read(seconds=500.0)

    assert final.fast == initial.fast
    assert final.mood == initial.mood
    assert final.state_version == 1
    assert simulation.metrics(final_seconds=500.0).final_drift == pytest.approx(
        {key: 0.0 for key in FastAffectiveState.field_names()}, abs=1e-15
    )


@pytest.mark.parametrize(
    ("event", "expected_direction"),
    [
        (
            appraisal(
                "positive",
                pleasantness=0.95,
                activation=0.6,
                novelty=0.7,
                salience=0.9,
                curiosity=0.7,
                interest=0.9,
                confidence=0.9,
            ),
            1,
        ),
        (
            appraisal(
                "negative",
                pleasantness=-0.8,
                activation=0.7,
                salience=0.9,
                concern=0.8,
                frustration=0.7,
                confidence=0.9,
            ),
            -1,
        ),
    ],
)
def test_single_events_are_bounded_and_recover_without_permanent_extremes(
    event: AffectiveAppraisalProposal,
    expected_direction: int,
) -> None:
    simulation = AffectSimulation(personality(), origin=ORIGIN)
    interaction_id = event.source_refs[0]
    decision = simulation.apply(event, seconds=0, interaction_id=interaction_id)
    assert decision.transition is not None
    delta = decision.transition.applied_delta

    assert math.copysign(1.0, delta.valence) == expected_direction
    for key, value in delta.as_mapping().items():
        assert abs(value) <= AFFECT_POLICY_V1.fast_dimension(key).max_absolute_delta + 1e-12
    assert abs(decision.transition.mood_delta.valence) < abs(delta.valence)
    recovered = simulation.read(seconds=7 * 24 * 3600)
    for key, policy in AFFECT_POLICY_V1.fast:
        assert getattr(recovered.fast, key) == pytest.approx(policy.baseline, abs=1e-9)
    for key, policy in AFFECT_POLICY_V1.mood:
        assert getattr(recovered.mood, key) == pytest.approx(policy.baseline, abs=2e-6)


def test_repeated_frustration_positive_alternating_and_high_frequency_stay_stable() -> None:
    """Simulation stress cases accumulate gradually, remain finite/bounded, and recover."""

    frustrating = AffectSimulation(personality(), origin=ORIGIN)
    for index in range(20):
        interaction_id = f"frustration-{index}"
        frustrating.apply(
            appraisal(
                interaction_id,
                pleasantness=-0.25,
                activation=0.25,
                salience=0.55,
                uncertainty=0.3,
                frustration=0.45,
                confidence=0.8,
            ),
            seconds=index * 60.0,
            interaction_id=interaction_id,
        )
    assert 0.04 < frustrating.state.fast.frustration < 1.0
    assert 0.08 < frustrating.state.fast.tension < 1.0
    frustration_metrics = frustrating.metrics(final_seconds=7 * 24 * 3600)
    assert max(frustration_metrics.maximum.values()) <= 1.0
    assert max(frustration_metrics.final_drift.values()) < 2e-6

    positive = AffectSimulation(personality(), origin=ORIGIN)
    for index in range(24):
        interaction_id = f"positive-{index}"
        positive.apply(
            appraisal(
                interaction_id,
                pleasantness=0.45,
                activation=0.25,
                salience=0.55,
                interest=0.55,
                confidence=0.85,
            ),
            seconds=index * 30.0 * 60.0,
            interaction_id=interaction_id,
        )
    assert 0.0 < positive.state.mood.valence < 1.0
    assert positive.read(seconds=8 * 24 * 3600).mood.valence < positive.state.mood.valence / 100

    alternating = AffectSimulation(personality(), origin=ORIGIN)
    for index in range(100):
        interaction_id = f"alternating-{index}"
        alternating.apply(
            appraisal(
                interaction_id,
                pleasantness=0.5 if index % 2 == 0 else -0.5,
                salience=0.6,
                confidence=0.9,
            ),
            seconds=0.0,
            interaction_id=interaction_id,
        )
    assert alternating.state.fast.valence == pytest.approx(0.0, abs=1e-12)
    assert alternating.state.mood.valence == pytest.approx(0.0, abs=1e-12)

    extreme = AffectSimulation(personality(), origin=ORIGIN)
    for index in range(100):
        interaction_id = f"extreme-{index}"
        extreme.apply(
            appraisal(
                interaction_id,
                pleasantness=-1.0,
                activation=1.0,
                novelty=1.0,
                salience=1.0,
                uncertainty=1.0,
                curiosity=1.0,
                interest=1.0,
                humor=1.0,
                concern=1.0,
                frustration=1.0,
                confidence_signal=-1.0,
                confidence=1.0,
            ),
            seconds=index * 0.001,
            interaction_id=interaction_id,
        )
    values = (*extreme.state.fast.as_mapping().values(), *extreme.state.mood.as_mapping().values())
    assert all(math.isfinite(value) for value in values)
    assert -1.0 <= extreme.state.fast.valence <= 1.0
    assert all(
        0.0 <= getattr(extreme.state.fast, key) <= 1.0
        for key in FastAffectiveState.field_names()
        if key != "valence"
    )


def test_user_distress_is_concern_not_one_to_one_valence_mirroring() -> None:
    """A controlled distress appraisal raises concern more than it copies negative valence."""

    simulation = AffectSimulation(personality(), origin=ORIGIN)
    decision = simulation.apply(
        appraisal(
            "distress",
            pleasantness=-0.35,
            activation=0.35,
            salience=0.9,
            interest=0.8,
            concern=0.95,
            confidence=0.9,
        ),
        seconds=0.0,
        interaction_id="distress",
    )
    assert decision.transition is not None
    assert decision.transition.applied_delta.concern > abs(
        decision.transition.applied_delta.valence
    )
    assert decision.transition.after.fast.valence > -0.2


def test_low_confidence_unknown_refs_and_personality_modulation_are_explicit() -> None:
    """Low authority cannot mutate; patience is the only tested frustration sensitivity."""

    manager = EmotionManager()
    state = initial_affective_state("identity", initialized_at=ORIGIN)
    low_confidence = manager.evaluate(
        appraisal("low", salience=1.0, frustration=1.0, confidence=0.2),
        state,
        personality(),
        interaction_id="low",
        allowed_source_refs=("low",),
        event_time=ORIGIN,
    )
    unknown = manager.evaluate(
        appraisal(
            "known",
            salience=1.0,
            frustration=1.0,
            source_refs=("known", "hallucinated-memory"),
        ),
        state,
        personality(),
        interaction_id="known",
        allowed_source_refs=("known",),
        event_time=ORIGIN,
    )
    assert low_confidence.kind is AppraisalDecisionKind.REJECTED
    assert unknown.kind is AppraisalDecisionKind.REJECTED

    original = personality()

    def with_patience(value: float) -> Personality:
        return Personality(
            schema_version=original.schema_version,
            aggregate_version=original.aggregate_version,
            traits=tuple(
                PersonalityTrait(item.key, value, item.baseline_value)
                if item.key == "patience"
                else item
                for item in original.traits
            ),
        )

    controlled = appraisal("patience", salience=0.8, frustration=0.7, confidence=0.9)
    impatient = manager.evaluate(
        controlled,
        state,
        with_patience(0.1),
        interaction_id="patience",
        allowed_source_refs=("patience",),
        event_time=ORIGIN,
    )
    patient = manager.evaluate(
        controlled,
        state,
        with_patience(0.9),
        interaction_id="patience",
        allowed_source_refs=("patience",),
        event_time=ORIGIN,
    )
    assert impatient.transition is not None
    assert patient.transition is not None
    assert (
        impatient.transition.applied_delta.frustration
        > patient.transition.applied_delta.frustration
    )
    assert original == personality()


def test_mood_decays_substantially_slower_than_fast_emotion() -> None:
    simulation = AffectSimulation(personality(), origin=ORIGIN)
    decision = simulation.apply(
        appraisal("timescale", pleasantness=1.0, salience=1.0, confidence=1.0),
        seconds=0.0,
        interaction_id="timescale",
    )
    assert decision.transition is not None
    peak_fast = decision.transition.after.fast.valence
    peak_mood = decision.transition.after.mood.valence
    later = simulation.read(seconds=4 * 3600)

    assert later.fast.valence / peak_fast < 0.05
    assert later.mood.valence / peak_mood > 0.75
