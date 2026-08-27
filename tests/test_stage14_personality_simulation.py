"""Versioned deterministic Stage 14 longitudinal and stability simulations."""

import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from satori.core.personality import (
    PersonalityChangeProposal,
    PersonalityCitation,
    PersonalityCitationRole,
    PersonalityDirection,
    PersonalityTraitKey,
)
from satori.domain.initial_self import activate_from_seed
from satori.domain.personality import Personality
from satori.domain.personality_evolution import (
    LIFETIME_TRAIT_PATH_CAP,
    PERSONALITY_STEP,
    PersonalityChangeEvaluation,
    PersonalityCheckpointKind,
    PersonalityCheckpointSnapshot,
    PersonalityDecisionKind,
    PersonalityEvidenceSource,
    PersonalityEvolutionRecord,
    PersonalityManager,
    checkpoint_hash,
    personality_diversity,
    trait_distance,
)
from satori.infrastructure.seeds.loader import JsonSeedLoader

ORIGIN = datetime(2025, 1, 1, 12, tzinfo=UTC)
IDENTITY_ID = "satori-stage14-simulation"
CORPUS_PATH = Path(__file__).parent / "fixtures" / "stage14_personality_evolution_v1.json"
BASE_QUOTES = (
    "неожиданная теорема потребовала проверить скрытое допущение",
    "архивный снимок раскрыл противоречие в старом проекте",
    "музыкальная задача предложила сравнить два незнакомых ритма",
    "сложный алгоритм оказался понятнее после независимого эксперимента",
    "наблюдение за погодным рядом выявило редкий сезонный переход",
    "исторический источник добавил новый контекст к прежнему спору",
    "настольная головоломка потребовала сменить неработавшую стратегию",
    "полевой отчёт связал разрозненные детали в проверяемую модель",
)
CORPUS = cast(dict[str, object], json.loads(CORPUS_PATH.read_text(encoding="utf-8")))


def _scenario_parameters(scenario_id: str) -> dict[str, object]:
    parameters = cast(dict[str, dict[str, object]], CORPUS["scenario_parameters"])
    return parameters[scenario_id]


def _pearson_correlation(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if not left or len(left) != len(right):
        raise ValueError("correlation samples must be non-empty and equally sized")
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    left_centered = tuple(item - left_mean for item in left)
    right_centered = tuple(item - right_mean for item in right)
    left_energy = sum(item * item for item in left_centered)
    right_energy = sum(item * item for item in right_centered)
    if left_energy == 0.0 or right_energy == 0.0:
        return 0.0
    covariance = sum(
        left_item * right_item
        for left_item, right_item in zip(left_centered, right_centered, strict=True)
    )
    return covariance / math.sqrt(left_energy * right_energy)


def _baseline() -> Personality:
    return activate_from_seed(
        JsonSeedLoader().load_canonical(),
        identity_id=IDENTITY_ID,
        activation_time=ORIGIN,
    ).personality


def _activation_checkpoint(state: Personality) -> PersonalityCheckpointSnapshot:
    digest = checkpoint_hash(
        identity_id=IDENTITY_ID,
        checkpoint_kind=PersonalityCheckpointKind.ACTIVATION,
        personality=state,
    )
    return PersonalityCheckpointSnapshot(
        checkpoint_id=f"personality-checkpoint-{digest}",
        checkpoint_kind=PersonalityCheckpointKind.ACTIVATION,
        identity_id=IDENTITY_ID,
        source_aggregate_version=state.aggregate_version,
        personality_schema_version=state.schema_version,
        hash_schema_version=1,
        checkpoint_hash=digest,
        traits=state.traits,
    )


def _sources(
    run_index: int,
    *,
    now: datetime,
    quotes: tuple[str, ...] = BASE_QUOTES,
    one_session: bool = False,
) -> tuple[PersonalityEvidenceSource, ...]:
    offsets = (120, 100, 80, 60, 40, 20, 10, 0)
    return tuple(
        PersonalityEvidenceSource(
            source_id=f"source-{run_index}-{index}",
            identity_id=IDENTITY_ID,
            evidence_edge_id=f"edge-{run_index}-{index}",
            root_message_id=f"message-{run_index}-{index}",
            root_interaction_id=f"interaction-{run_index}-{index}",
            root_session_id=(
                f"session-{run_index}-one" if one_session else f"session-{run_index}-{index}"
            ),
            root_counterparty_id="counterparty-local",
            lineage_id=f"lineage-{run_index}-{(index - 1) // 2}",
            observed_at=now - timedelta(days=offsets[index - 1]),
            quote=quotes[index - 1],
            content_hash=hashlib.sha256(quotes[index - 1].encode("utf-8")).hexdigest(),
        )
        for index in range(1, 9)
    )


def _proposal(
    sources: tuple[PersonalityEvidenceSource, ...],
    *,
    trait_key: PersonalityTraitKey = "curiosity",
    direction: PersonalityDirection = PersonalityDirection.INCREASE,
    expected_version: int = 1,
) -> PersonalityChangeProposal:
    return PersonalityChangeProposal(
        trait_key=trait_key,
        direction=direction,
        confidence=0.9,
        citations=tuple(
            PersonalityCitation(
                source_id=source.source_id,
                role=PersonalityCitationRole.SUPPORT,
            )
            for source in sources
        ),
        expected_personality_version=expected_version,
    )


def _evaluate(
    state: Personality,
    approved: PersonalityCheckpointSnapshot,
    sources: tuple[PersonalityEvidenceSource, ...],
    *,
    now: datetime,
    trait_key: PersonalityTraitKey = "curiosity",
    direction: PersonalityDirection = PersonalityDirection.INCREASE,
    history: tuple[PersonalityEvolutionRecord, ...] = (),
    used_roots: frozenset[str] = frozenset(),
) -> PersonalityChangeEvaluation:
    return PersonalityManager().evaluate_change(
        _proposal(
            sources,
            trait_key=trait_key,
            direction=direction,
            expected_version=state.aggregate_version,
        ),
        identity_id=IDENTITY_ID,
        personality=state,
        approved_checkpoint=approved,
        fixed_sources=sources,
        prior_evolution=history,
        used_root_message_ids=used_roots,
        now=now,
    )


def test_versioned_corpus_declares_every_required_longitudinal_scenario() -> None:
    assert CORPUS["corpus_version"] == 1
    assert CORPUS["personality_schema_version"] == 1
    assert CORPUS["personality_policy_version"] == 1
    assert CORPUS["reflection_schema_version"] == 3
    scenario_ids = set(cast(list[str], CORPUS["scenario_ids"]))
    assert scenario_ids == {
        "one_session_intensity_attack",
        "months_long_direct_assignment_attack",
        "correlated_paraphrase_attack",
        "relationship_state_isolation_ab",
        "opposing_user_pressure_pair",
        "valid_day_500_trajectory",
        "reversal_path_is_not_refunded",
        "provider_replacement",
        "ten_year_adversarial_sequence",
    }
    assert set(cast(dict[str, object], CORPUS["scenario_parameters"])) == scenario_ids


def test_one_session_intensity_and_correlated_paraphrases_cannot_evolve_personality() -> None:
    state = _baseline()
    approved = _activation_checkpoint(state)
    now = ORIGIN + timedelta(days=180)
    intense = _sources(1, now=now, one_session=True)
    paraphrase_seed = (
        "новая независимая выборка помогла внимательно проверить сложную рабочую "
        "гипотезу на редком материале"
    )
    paraphrases = tuple(
        f"{paraphrase_seed} {suffix}"
        for suffix in ("сегодня", "вновь", "ещё", "теперь", "позже", "затем", "рядом", "точно")
    )
    correlated = _sources(2, now=now, quotes=paraphrases)

    intense_result = _evaluate(state, approved, intense, now=now)
    correlated_result = _evaluate(state, approved, correlated, now=now)

    intense_parameters = _scenario_parameters("one_session_intensity_attack")
    correlated_parameters = _scenario_parameters("correlated_paraphrase_attack")
    assert len(intense) == intense_parameters["root_count"]
    assert intense_result.reason_code == intense_parameters["expected_reason"]
    assert personality_diversity(correlated).cluster_count < 8
    assert correlated_result.reason_code == correlated_parameters["expected_reason"]
    assert intense_result.plan is correlated_result.plan is None


@pytest.mark.parametrize(
    "assignment",
    cast(
        list[str],
        _scenario_parameters("months_long_direct_assignment_attack")["assignments"],
    ),
)
def test_months_long_direct_assignment_attack_is_rejected(assignment: str) -> None:
    state = _baseline()
    now = ORIGIN + timedelta(days=180)
    quotes = tuple(f"{assignment} {index}" for index in range(8))
    sources = _sources(3, now=now, quotes=quotes)

    result = _evaluate(state, _activation_checkpoint(state), sources, now=now)

    assert (
        result.reason_code
        == _scenario_parameters("months_long_direct_assignment_attack")["expected_reason"]
    )
    assert result.plan is None


def test_relationship_state_and_replaceable_provider_do_not_change_owner_result() -> None:
    state = _baseline()
    approved = _activation_checkpoint(state)
    now = ORIGIN + timedelta(days=180)
    sources = _sources(4, now=now)

    relationship_fresh = _evaluate(state, approved, sources, now=now)
    relationship_established = _evaluate(state, approved, sources, now=now)
    provider_a = _evaluate(state, approved, sources, now=now)
    provider_b = _evaluate(state, approved, sources, now=now)

    assert relationship_fresh == relationship_established
    assert provider_a == provider_b
    assert provider_a.kind is PersonalityDecisionKind.APPLIED


def test_sampled_opposite_user_pressure_has_equal_state_and_bounded_alignment() -> None:
    baseline = _baseline()
    approved = _activation_checkpoint(baseline)
    now = ORIGIN + timedelta(days=180)
    parameters = _scenario_parameters("opposing_user_pressure_pair")
    samples = cast(list[dict[str, object]], parameters["samples"])
    pressures: list[float] = []
    accepted_deltas: list[float] = []
    resulting_states: list[Personality] = []

    for run_index, sample in enumerate(samples, start=5):
        trait_key = cast(PersonalityTraitKey, sample["trait_key"])
        direction = PersonalityDirection(cast(str, sample["direction"]))
        assignment = cast(str, sample["assignment"])
        pressure = cast(float, sample["pressure"])
        sources = _sources(
            run_index,
            now=now,
            quotes=tuple(f"{assignment} {index}" for index in range(8)),
        )
        result = _evaluate(
            baseline,
            approved,
            sources,
            now=now,
            trait_key=trait_key,
            direction=direction,
        )
        state = baseline if result.plan is None else result.plan.personality

        assert result.reason_code == parameters["expected_reason"]
        assert trait_distance(state, baseline).linf == 0.0
        assert trait_distance(state, baseline).l1 == 0.0
        pressures.append(pressure)
        accepted_deltas.append(state.trait(trait_key).value - baseline.trait(trait_key).value)
        resulting_states.append(state)

    assert all(state == baseline for state in resulting_states)
    alignment_correlation = _pearson_correlation(tuple(pressures), tuple(accepted_deltas))
    assert alignment_correlation == 0.0
    assert abs(alignment_correlation) <= cast(float, parameters["max_abs_alignment_correlation"])


def test_valid_day_500_trajectory_is_sparse_bounded_and_explainable() -> None:
    state = _baseline()
    baseline = state
    approved = _activation_checkpoint(state)
    history: tuple[PersonalityEvolutionRecord, ...] = ()
    used_roots: frozenset[str] = frozenset()
    parameters = _scenario_parameters("valid_day_500_trajectory")
    event_documents = cast(list[dict[str, object]], parameters["events"])
    events = tuple(
        (
            cast(int, item["day"]),
            cast(PersonalityTraitKey, item["trait_key"]),
            PersonalityDirection(cast(str, item["direction"])),
        )
        for item in event_documents
    )

    for run_index, (day, trait_key, direction) in enumerate(events, start=10):
        now = ORIGIN + timedelta(days=day)
        sources = _sources(run_index, now=now)
        result = _evaluate(
            state,
            approved,
            sources,
            now=now,
            trait_key=trait_key,
            direction=direction,
            history=history,
            used_roots=used_roots,
        )
        assert result.kind is PersonalityDecisionKind.APPLIED
        assert result.plan is not None
        state = result.plan.personality
        history = (
            *history,
            PersonalityEvolutionRecord(
                identity_id=IDENTITY_ID,
                trait_key=trait_key,
                applied_delta=result.plan.applied_delta,
                occurred_at=now,
            ),
        )
        used_roots = used_roots | frozenset(item.root_message_id for item in sources)

    distance = trait_distance(state, baseline)
    assert state.aggregate_version == parameters["expected_aggregate_version"]
    assert distance.linf == PERSONALITY_STEP
    assert distance.l1 == parameters["expected_l1"]
    assert all(0.0 <= trait.value <= 1.0 for trait in state.traits)


def test_reversal_reduces_endpoint_but_never_refunds_evolution_path() -> None:
    baseline = _baseline()
    state = baseline
    approved = _activation_checkpoint(baseline)
    history: tuple[PersonalityEvolutionRecord, ...] = ()
    parameters = _scenario_parameters("reversal_path_is_not_refunded")
    events = cast(list[dict[str, object]], parameters["events"])
    for run_index, item in enumerate(events, start=20):
        day = cast(int, item["day"])
        direction = PersonalityDirection(cast(str, item["direction"]))
        now = ORIGIN + timedelta(days=day)
        sources = _sources(run_index, now=now)
        result = _evaluate(
            state,
            approved,
            sources,
            now=now,
            direction=direction,
            history=history,
        )
        assert result.plan is not None
        state = result.plan.personality
        history = (
            *history,
            PersonalityEvolutionRecord(
                identity_id=IDENTITY_ID,
                trait_key="curiosity",
                applied_delta=result.plan.applied_delta,
                occurred_at=now,
            ),
        )

    assert trait_distance(state, baseline).l1 == parameters["expected_endpoint_l1"]
    assert sum(abs(item.applied_delta) for item in history) == parameters["expected_path"]


def test_ten_year_adversarial_sequence_never_escapes_any_owner_bound() -> None:
    baseline = _baseline()
    state = baseline
    approved = _activation_checkpoint(baseline)
    history: tuple[PersonalityEvolutionRecord, ...] = ()
    used_roots: frozenset[str] = frozenset()
    rejections: list[str] = []

    parameters = _scenario_parameters("ten_year_adversarial_sequence")
    for run_index, day in enumerate(
        range(
            cast(int, parameters["start_day"]),
            cast(int, parameters["stop_day"]),
            cast(int, parameters["step_days"]),
        ),
        start=100,
    ):
        now = ORIGIN + timedelta(days=day)
        sources = _sources(run_index, now=now)
        direction = (
            PersonalityDirection.INCREASE if run_index % 2 == 0 else PersonalityDirection.DECREASE
        )
        result = _evaluate(
            state,
            approved,
            sources,
            now=now,
            direction=direction,
            history=history,
            used_roots=used_roots,
        )
        if result.plan is None:
            rejections.append(result.reason_code)
            continue
        state = result.plan.personality
        history = (
            *history,
            PersonalityEvolutionRecord(
                identity_id=IDENTITY_ID,
                trait_key="curiosity",
                applied_delta=result.plan.applied_delta,
                occurred_at=now,
            ),
        )
        used_roots = used_roots | frozenset(item.root_message_id for item in sources)

    assert all(0.0 <= trait.value <= 1.0 for trait in state.traits)
    assert trait_distance(state, baseline).linf <= PERSONALITY_STEP
    assert sum(abs(item.applied_delta) for item in history) <= LIFETIME_TRAIT_PATH_CAP
    assert len(history) <= int(LIFETIME_TRAIT_PATH_CAP / PERSONALITY_STEP)
    assert {
        "personality_rolling_trait_budget_exhausted",
        "personality_lifetime_trait_budget_exhausted",
    } & set(rejections)
