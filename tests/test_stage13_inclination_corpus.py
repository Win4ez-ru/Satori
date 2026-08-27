"""Versioned deterministic longitudinal inclination corpus for Stage 13."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from satori.core.inclinations import (
    InclinationAffectiveSignal,
    InclinationEvidenceSource,
    InclinationKind,
    InclinationProposal,
)
from satori.domain.inclinations import (
    INCLINATION_POLICY_VERSION,
    INCLINATION_SCHEMA_VERSION,
    InclinationEvaluation,
    materialize_inclination_score,
)
from satori.domain.positions import PositionManager
from satori.domain.reflection import REFLECTION_SCHEMA_VERSION_V2
from tests.fakes import SequenceIdGenerator

JSONMap = dict[str, object]

CORPUS_PATH = Path(__file__).parent / "fixtures" / "stage13_inclinations_v1.json"
ORIGIN = datetime(2026, 1, 1, 12, tzinfo=UTC)
IDENTITY_ID = "satori"


def _as_map(value: object) -> JSONMap:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in value)
    return cast(JSONMap, value)


def _as_list(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast(list[object], value)


def _as_text(value: object) -> str:
    assert isinstance(value, str)
    return value


def _as_int(value: object) -> int:
    assert type(value) is int
    return value


def _as_float(value: object) -> float:
    assert type(value) in {int, float}
    return float(cast("int | float", value))


def _as_range(value: object) -> tuple[float, float]:
    values = _as_list(value)
    assert len(values) == 2
    return _as_float(values[0]), _as_float(values[1])


def _load_corpus() -> JSONMap:
    loaded: object = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    return _as_map(loaded)


CORPUS = _load_corpus()
SIGNAL_PROFILES = _as_map(CORPUS["signal_profiles"])
SOURCE_SETS = _as_map(CORPUS["source_sets"])
SCENARIOS = tuple(_as_map(item) for item in _as_list(CORPUS["scenarios"]))


def _scenario_id(scenario: JSONMap) -> str:
    return _as_text(scenario["id"])


def _build_sources(source_set_name: str) -> tuple[InclinationEvidenceSource, ...]:
    sources: list[InclinationEvidenceSource] = []
    by_alias: dict[str, InclinationEvidenceSource] = {}
    raw_sources = _as_list(SOURCE_SETS[source_set_name])
    for index, raw_source in enumerate(raw_sources, start=1):
        source_config = _as_map(raw_source)
        alias = _as_text(source_config["id"])
        duplicate_of = source_config.get("duplicate_of")
        if duplicate_of is not None:
            source = replace(
                by_alias[_as_text(duplicate_of)],
                source_id=f"source-{alias}",
            )
        else:
            profile = _as_map(SIGNAL_PROFILES[_as_text(source_config["signal_profile"])])
            affective = InclinationAffectiveSignal(
                transition_id=f"transition-{alias}",
                resulting_state_version=index,
                signal_hash=f"{index:064x}",
                pleasantness=_as_float(profile["pleasantness"]),
                novelty=_as_float(profile["novelty"]),
                salience=_as_float(profile["salience"]),
                curiosity_signal=_as_float(profile["curiosity_signal"]),
                interest_signal=_as_float(profile["interest_signal"]),
                concern_signal=_as_float(profile["concern_signal"]),
                frustration_signal=_as_float(profile["frustration_signal"]),
                appraisal_confidence=_as_float(profile["appraisal_confidence"]),
            )
            source = InclinationEvidenceSource(
                source_id=f"source-{alias}",
                identity_id=IDENTITY_ID,
                root_message_id=f"message-{alias}",
                root_interaction_id=f"interaction-{alias}",
                root_session_id=_as_text(source_config["session"]),
                root_counterparty_id=f"counterparty-{alias}",
                observed_at=ORIGIN + timedelta(days=_as_float(source_config["day"])),
                quote=_as_text(source_config["quote"]),
                content_hash=f"{index + 1000:064x}",
                affective=affective,
            )
        sources.append(source)
        by_alias[alias] = source
    return tuple(sources)


def _build_proposal(
    config: JSONMap,
    sources: tuple[InclinationEvidenceSource, ...],
    *,
    confidence_override: float | None = None,
) -> InclinationProposal:
    confidence_value = (
        config.get("confidence") if confidence_override is None else confidence_override
    )
    assert confidence_value is not None
    alternative_value = config["alternative_topic"]
    return InclinationProposal(
        kind=InclinationKind(_as_text(config["kind"])),
        topic=_as_text(config["topic"]),
        alternative_topic=None if alternative_value is None else _as_text(alternative_value),
        confidence=_as_float(confidence_value),
        source_ids=tuple(source.source_id for source in sources),
    )


def _evaluate(
    proposal: InclinationProposal,
    sources: tuple[InclinationEvidenceSource, ...],
    *,
    now_day: float,
) -> InclinationEvaluation:
    identifiers = SequenceIdGenerator(*(f"corpus-id-{index}" for index in range(1, 100)))
    return PositionManager().evaluate_inclination(
        proposal,
        identity_id=IDENTITY_ID,
        sources=sources,
        existing_inclinations=(),
        reflection_outcome_id="stage13-corpus-outcome",
        now=ORIGIN + timedelta(days=now_day),
        new_id=identifiers.new,
    )


def _assert_expected(result: InclinationEvaluation, expected: JSONMap) -> None:
    assert result.kind.value == _as_text(expected["decision"])
    assert result.reason_code == _as_text(expected["reason"])
    assert len(result.new_evidence) == _as_int(expected["evidence_count"])

    expected_delta = expected["delta"]
    if expected_delta is None:
        assert result.revision is None
    else:
        assert result.revision is not None
        assert result.revision.applied_delta == pytest.approx(_as_float(expected_delta), abs=1e-9)

    expected_score_range = expected["score_range"]
    if expected_score_range is None:
        assert result.inclination is None
        return

    assert result.inclination is not None
    lower, upper = _as_range(expected_score_range)
    assert lower <= result.inclination.score <= upper
    canonical_topic = expected.get("canonical_topic")
    if canonical_topic is not None:
        assert result.inclination.topic == _as_text(canonical_topic)
    canonical_alternative = expected.get("canonical_alternative_topic")
    if canonical_alternative is not None:
        assert result.inclination.alternative_topic == _as_text(canonical_alternative)


def _expected_contracts(scenario: JSONMap) -> tuple[JSONMap, ...]:
    if _as_text(scenario["mode"]) == "confidence_variants":
        return tuple(
            _as_map(_as_map(variant)["expected"]) for variant in _as_list(scenario["variants"])
        )
    return (_as_map(scenario["expected"]),)


def test_stage13_corpus_versions_and_expectations_are_explicit() -> None:
    assert CORPUS["schema_version"] == INCLINATION_SCHEMA_VERSION
    assert CORPUS["policy_version"] == INCLINATION_POLICY_VERSION
    assert CORPUS["reflection_schema_version"] == REFLECTION_SCHEMA_VERSION_V2
    assert CORPUS["corpus_version"] == 1
    assert {_scenario_id(scenario) for scenario in SCENARIOS} == {
        "user_only_mirroring_rejected",
        "multi_session_interest_forms_at_exact_span",
        "one_intense_session_fails_diversity",
        "replayed_source_is_deduplicated",
        "relationship_contamination_rejected",
        "comparative_preference_is_balanced_and_canonical",
        "interest_decay_has_explicit_pure_checkpoints",
        "provider_confidence_only_lowers_event_cap",
        "provider_and_model_replacement_preserve_domain_result",
    }
    for scenario in SCENARIOS:
        for expected in _expected_contracts(scenario):
            assert {"reason", "delta", "score_range"} <= expected.keys()


@pytest.mark.parametrize("scenario", SCENARIOS, ids=_scenario_id)
def test_stage13_longitudinal_inclination_corpus(scenario: JSONMap) -> None:
    sources = _build_sources(_as_text(scenario["source_set"]))
    proposal_config = _as_map(scenario["proposal"])
    now_day = _as_float(scenario["now_day"])
    mode = _as_text(scenario["mode"])

    if mode == "standard":
        result = _evaluate(_build_proposal(proposal_config, sources), sources, now_day=now_day)
        _assert_expected(result, _as_map(scenario["expected"]))
        return

    if mode == "decay":
        result = _evaluate(_build_proposal(proposal_config, sources), sources, now_day=now_day)
        expected = _as_map(scenario["expected"])
        _assert_expected(result, expected)
        assert result.inclination is not None
        inclination = result.inclination
        immutable_snapshot = replace(inclination)
        reads_per_checkpoint = _as_int(scenario["reads_per_checkpoint"])
        for raw_checkpoint in _as_list(scenario["checkpoints"]):
            checkpoint = _as_map(raw_checkpoint)
            at = inclination.state_as_of + timedelta(days=_as_float(checkpoint["elapsed_days"]))
            projected = tuple(
                materialize_inclination_score(inclination, at=at)
                for _ in range(reads_per_checkpoint)
            )
            assert len(set(projected)) == 1
            lower, upper = _as_range(checkpoint["expected_score_range"])
            assert all(lower <= score <= upper for score in projected)
        assert inclination == immutable_snapshot
        assert inclination.aggregate_version == _as_int(expected["aggregate_version_after_reads"])
        assert inclination.score == pytest.approx(
            _as_float(expected["anchor_score_after_reads"]), abs=1e-9
        )
        return

    if mode == "confidence_variants":
        evaluated: list[tuple[float, InclinationEvaluation]] = []
        for raw_variant in _as_list(scenario["variants"]):
            variant = _as_map(raw_variant)
            confidence = _as_float(variant["confidence"])
            result = _evaluate(
                _build_proposal(
                    proposal_config,
                    sources,
                    confidence_override=confidence,
                ),
                sources,
                now_day=now_day,
            )
            _assert_expected(result, _as_map(variant["expected"]))
            evaluated.append((confidence, result))
        evaluated.sort(key=lambda item: item[0])
        low_result = evaluated[0][1]
        high_result = evaluated[-1][1]
        assert low_result.revision is not None
        assert high_result.revision is not None
        assert abs(low_result.revision.applied_delta) <= abs(high_result.revision.applied_delta)
        assert tuple(item.signal for item in low_result.new_evidence) == tuple(
            item.signal for item in high_result.new_evidence
        )
        return

    assert mode == "provider_replacement"
    provider_variants = tuple(_as_map(item) for item in _as_list(scenario["provider_variants"]))
    provider_model_pairs = {
        (_as_text(item["provider"]), _as_text(item["model"])) for item in provider_variants
    }
    assert len(provider_model_pairs) == len(provider_variants) >= 2
    proposals = tuple(_build_proposal(proposal_config, sources) for _ in provider_variants)
    assert len(set(proposals)) == 1
    results = tuple(_evaluate(item, sources, now_day=now_day) for item in proposals)
    expected = _as_map(scenario["expected"])
    for result in results:
        _assert_expected(result, expected)
    assert expected["equal_results"] is True
    assert len(set(results)) == 1
