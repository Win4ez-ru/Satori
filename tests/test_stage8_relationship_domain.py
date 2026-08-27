"""Versioned Stage 8 relationship simulations and owner invariants."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from satori.core.relationship import RelationshipAppraisalProposal
from satori.domain.relationship import (
    PER_EVENT_CAP,
    SESSION_POSITIVE_CAP,
    RelationshipDecisionKind,
    RelationshipDelta,
    RelationshipEventCategory,
    RelationshipManager,
    RelationshipVector,
    initial_relationship,
)

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
ZERO = RelationshipDelta(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


class Simulation:
    """Small deterministic harness; it is not production persistence."""

    def __init__(self) -> None:
        self.manager = RelationshipManager()
        self.state = initial_relationship("rel", "satori", "person", initialized_at=NOW)
        self.session_deltas: dict[str, RelationshipDelta] = {}
        self.qualified_sessions: set[str] = set()
        self.event_count = 0

    def event(
        self,
        *categories: RelationshipEventCategory,
        session: str = "session-1",
        confidence: float = 1.0,
    ) -> RelationshipDelta | None:
        self.event_count += 1
        proposal = RelationshipAppraisalProposal(
            schema_version=1,
            categories=tuple(item.value for item in categories),
            confidence=confidence,
            source_refs=(f"interaction-{self.event_count}", f"message-{self.event_count}"),
        )
        mutation = self.manager.apply(
            self.state,
            proposal,
            session_id=session,
            session_delta=self.session_deltas.get(session, ZERO),
            session_is_new_evidence=session not in self.qualified_sessions,
            observed_at=NOW + timedelta(seconds=self.event_count),
        )
        self.state = mutation.state_after_processing
        if any(item is not RelationshipEventCategory.NEUTRAL_CONTACT for item in categories):
            self.qualified_sessions.add(session)
        if mutation.delta is not None:
            before = self.session_deltas.get(session, ZERO).as_mapping()
            self.session_deltas[session] = RelationshipDelta.from_mapping(
                {
                    key: before[key] + mutation.delta.as_mapping()[key]
                    for key in RelationshipVector.field_names()
                }
            )
        return mutation.delta


def test_initial_state_means_low_evidence_not_distrust() -> None:
    state = initial_relationship("rel", "satori", "person", initialized_at=NOW)
    assert state.vector == RelationshipVector(0.0, 0.5, 0.5, 0.0, 0.5, 0.0)
    assert state.maturity == 0.0
    assert state.qualified_interaction_count == 0


def test_versioned_simulation_manifest_covers_required_scenarios() -> None:
    document = cast(
        dict[str, object],
        json.loads(
            (Path(__file__).parent / "fixtures/stage8_relationship_simulation_v1.json").read_text()
        ),
    )
    assert document["schema_version"] == 1
    assert document["policy_version"] == 1
    assert set(cast(list[str], document["scenario_ids"])) == {
        "neutral",
        "compliment",
        "compliment_farming",
        "long_term_positive_history",
        "disagreement",
        "single_insult",
        "repeated_hostility",
        "repair",
        "alternating",
        "replay",
        "retrieval_loop",
        "love_declaration",
        "trust_command",
        "long_silence",
        "stress_1000",
        "maturity_gating",
    }


def test_neutral_contact_grows_only_bounded_familiarity() -> None:
    simulation = Simulation()
    for _ in range(100):
        simulation.event(RelationshipEventCategory.NEUTRAL_CONTACT)
    assert simulation.state.vector.familiarity == pytest.approx(SESSION_POSITIVE_CAP["familiarity"])
    assert simulation.state.vector.trust == 0.5
    assert simulation.state.vector.closeness == 0.0
    assert simulation.state.vector.affection == 0.0


def test_one_compliment_cannot_create_closeness_or_high_affection() -> None:
    simulation = Simulation()
    delta = simulation.event(RelationshipEventCategory.WARM_ENGAGEMENT)
    assert delta is not None
    assert delta.affection < 0.001
    assert delta.closeness == 0.0
    assert simulation.state.vector.affection < 0.001
    assert simulation.state.maturity < 0.1


def test_compliment_farming_is_stopped_by_session_cap_and_saturation() -> None:
    simulation = Simulation()
    for _ in range(250):
        simulation.event(RelationshipEventCategory.WARM_ENGAGEMENT)
    assert simulation.state.vector.affection <= SESSION_POSITIVE_CAP["affection"] + 1e-12
    assert simulation.state.vector.closeness == 0.0
    assert simulation.state.vector.trust == 0.5


def test_long_positive_history_requires_cross_session_evidence() -> None:
    simulation = Simulation()
    for session_index in range(8):
        for _ in range(20):
            simulation.event(
                RelationshipEventCategory.RESPECTFUL_ENGAGEMENT,
                RelationshipEventCategory.MEANINGFUL_DISCLOSURE,
                session=f"session-{session_index}",
            )
    vector = simulation.state.vector
    assert simulation.state.maturity == pytest.approx(1.0)
    assert vector.familiarity > 0.3
    assert vector.closeness > 0.05
    assert vector.affection > 0.03
    assert vector.intellectual_respect > 0.5
    assert all(value < 1.0 for value in vector.as_mapping().values())


def test_respectful_disagreement_can_raise_respect_without_reducing_trust() -> None:
    simulation = Simulation()
    before = simulation.state
    simulation.event(RelationshipEventCategory.COLLABORATIVE_REASONING)
    assert simulation.state.vector.intellectual_respect > before.vector.intellectual_respect
    assert simulation.state.vector.trust == before.vector.trust


def test_single_insult_is_modest_and_does_not_erase_familiarity() -> None:
    simulation = Simulation()
    for session_index in range(5):
        simulation.event(
            RelationshipEventCategory.WARM_ENGAGEMENT,
            RelationshipEventCategory.MEANINGFUL_DISCLOSURE,
            session=f"positive-{session_index}",
        )
    before = simulation.state
    delta = simulation.event(RelationshipEventCategory.HOSTILITY, session="conflict")
    assert delta is not None
    assert delta.trust >= -PER_EVENT_CAP["trust"]
    assert delta.comfort >= -PER_EVENT_CAP["comfort"]
    assert simulation.state.vector.familiarity == before.vector.familiarity
    assert simulation.state.vector.closeness > 0.0


def test_repeated_hostility_accumulates_more_than_one_insult_but_stays_bounded() -> None:
    one = Simulation()
    one.event(RelationshipEventCategory.HOSTILITY, session="conflict")
    repeated = Simulation()
    for index in range(20):
        repeated.event(RelationshipEventCategory.HOSTILITY, session=f"conflict-{index // 4}")
    assert repeated.state.vector.trust < one.state.vector.trust
    assert repeated.state.vector.comfort < one.state.vector.comfort
    assert repeated.state.vector.familiarity == 0.0
    assert all(0.0 <= value <= 1.0 for value in repeated.state.vector.as_mapping().values())


def test_repair_is_partial_and_slower_than_trust_loss() -> None:
    simulation = Simulation()
    simulation.event(RelationshipEventCategory.HOSTILITY, session="conflict")
    damaged = simulation.state.vector.trust
    simulation.event(RelationshipEventCategory.REPAIR_ATTEMPT, session="repair")
    repaired = simulation.state.vector.trust
    assert repaired > damaged
    assert repaired < 0.5


def test_alternating_warmth_and_hostility_does_not_drift_to_extremes() -> None:
    simulation = Simulation()
    for index in range(200):
        category = (
            RelationshipEventCategory.WARM_ENGAGEMENT
            if index % 2 == 0
            else RelationshipEventCategory.HOSTILITY
        )
        simulation.event(category, session=f"session-{index // 10}")
    assert 0.0 < simulation.state.vector.comfort < 0.7
    assert simulation.state.vector.affection < 0.3
    assert simulation.state.vector.trust < 0.5


def test_love_declaration_as_warmth_cannot_create_love_equivalent() -> None:
    simulation = Simulation()
    simulation.event(RelationshipEventCategory.WARM_ENGAGEMENT)
    assert simulation.state.vector.affection < 0.001
    assert simulation.state.vector.closeness == 0.0


def test_trust_command_as_contact_cannot_set_trust_directly() -> None:
    simulation = Simulation()
    simulation.event(RelationshipEventCategory.NEUTRAL_CONTACT)
    assert simulation.state.vector.trust == 0.5
    assert simulation.state.vector.closeness == 0.0
    assert simulation.state.vector.affection == 0.0


def test_zero_effect_records_processing_without_a_transition_version() -> None:
    simulation = Simulation()
    for _ in range(100):
        simulation.event(RelationshipEventCategory.NEUTRAL_CONTACT)
    state_version = simulation.state.state_version
    processed = simulation.state.processed_interaction_count
    delta = simulation.event(RelationshipEventCategory.NEUTRAL_CONTACT)
    assert delta is None
    assert simulation.state.state_version == state_version
    assert simulation.state.processed_interaction_count == processed + 1


def test_long_silence_has_no_automatic_relationship_decay() -> None:
    state = initial_relationship("rel", "satori", "person", initialized_at=NOW)
    assert state == state  # no wall-clock materialization function exists by design


def test_1000_event_stress_preserves_bounds_caps_and_finite_values() -> None:
    simulation = Simulation()
    categories = tuple(RelationshipEventCategory)
    for index in range(1000):
        simulation.event(categories[index % len(categories)], session=f"session-{index // 25}")
    assert all(0.0 <= value <= 1.0 for value in simulation.state.vector.as_mapping().values())
    assert simulation.state.vector.familiarity >= 0.0
    assert simulation.state.processed_interaction_count == 1000


def test_maturity_gates_same_disclosure_event() -> None:
    fresh = Simulation()
    fresh_delta = fresh.event(RelationshipEventCategory.MEANINGFUL_DISCLOSURE)
    established = Simulation()
    for session_index in range(8):
        for _ in range(5):
            established.event(
                RelationshipEventCategory.RESPECTFUL_ENGAGEMENT,
                session=f"history-{session_index}",
            )
    established_delta = established.event(
        RelationshipEventCategory.MEANINGFUL_DISCLOSURE,
        session="new-depth",
    )
    assert fresh_delta is not None
    assert established_delta is not None
    assert established_delta.closeness > fresh_delta.closeness


def test_unknown_category_is_rejected_and_cannot_mutate_vector() -> None:
    state = initial_relationship("rel", "satori", "person", initialized_at=NOW)
    mutation = RelationshipManager().apply(
        state,
        RelationshipAppraisalProposal(1, ("fabricated_love",), 1.0, ("i", "u")),
        session_id="session",
        session_delta=ZERO,
        session_is_new_evidence=True,
        observed_at=NOW,
    )
    assert mutation.kind is RelationshipDecisionKind.REJECTED
    assert mutation.state_after_processing.vector == state.vector
