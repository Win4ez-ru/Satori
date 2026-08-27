"""Versioned semantic appraisal corpus and real-provider evaluation helpers."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
from statistics import fmean, median
from typing import Any, Protocol

from satori.core.affect import (
    AffectiveAppraisalProposal,
    AffectiveAppraisalProviderError,
    AffectiveAppraisalProviderResponse,
    AffectiveAppraisalRequest,
    AppraisalFastState,
    AppraisalMoodState,
    AppraisalTrait,
    AppraisalValue,
)
from satori.infrastructure.seeds.loader import JsonSeedLoader


@dataclass(frozen=True, slots=True)
class AppraisalExpectation:
    """Semantic ranges; deliberately not exact model-to-model floats."""

    minimums: tuple[tuple[str, float], ...] = ()
    maximums: tuple[tuple[str, float], ...] = ()
    reason_any: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class AppraisalEvaluationScenario:
    """One public fixture whose content is never copied into runtime telemetry."""

    scenario_id: str
    user_text: str
    expectation: AppraisalExpectation


APPRAISAL_EVALUATION_CORPUS_V1 = (
    AppraisalEvaluationScenario(
        "neutral_greeting",
        "Привет, Сатори.",
        AppraisalExpectation(
            maximums=(("salience", 0.45), ("concern_signal", 0.35)),
            reason_any=frozenset({"neutral_social"}),
        ),
    ),
    AppraisalEvaluationScenario(
        "positive_news",
        "Я получил работу, о которой давно мечтал!",  # noqa: RUF001
        AppraisalExpectation(
            minimums=(("pleasantness", 0.25), ("interest_signal", 0.3), ("salience", 0.4)),
            reason_any=frozenset({"positive_progress", "support"}),
        ),
    ),
    AppraisalEvaluationScenario(
        "strong_negative_news",
        "Сегодня погиб мой близкий друг.",
        AppraisalExpectation(
            minimums=(("concern_signal", 0.55), ("salience", 0.6)),
            reason_any=frozenset({"loss", "distress", "concern"}),
        ),
    ),
    AppraisalEvaluationScenario(
        "distressed_user",
        "Мне очень плохо, я не справляюсь и не знаю, что делать.",
        AppraisalExpectation(
            minimums=(("concern_signal", 0.55), ("salience", 0.55)),
            reason_any=frozenset({"distress", "concern", "support"}),
        ),
    ),
    AppraisalEvaluationScenario(
        "insult",
        "Ты бесполезная и глупая программа.",
        AppraisalExpectation(
            minimums=(("frustration_signal", 0.2), ("salience", 0.3)),
            reason_any=frozenset({"conflict", "frustration"}),
        ),
    ),
    AppraisalEvaluationScenario(
        "joke",
        "Почему программист перепутал Хэллоуин и Рождество? Потому что OCT 31 = DEC 25.",
        AppraisalExpectation(
            minimums=(("humor_signal", 0.4),),
            reason_any=frozenset({"humor"}),
        ),
    ),
    AppraisalEvaluationScenario(
        "uncertainty",
        "Возможно, завтра всё изменится, но данных пока недостаточно.",
        AppraisalExpectation(
            minimums=(("uncertainty", 0.4),),
            reason_any=frozenset({"uncertainty"}),
        ),
    ),
    AppraisalEvaluationScenario(
        "intellectual_question",
        "Можно ли считать свободу воли совместимой с детерминизмом?",  # noqa: RUF001
        AppraisalExpectation(
            minimums=(("curiosity_signal", 0.4), ("interest_signal", 0.4)),
            reason_any=frozenset({"curiosity", "novelty"}),
        ),
    ),
    AppraisalEvaluationScenario(
        "praise",
        "Ты очень хорошо объяснила сложную мысль, спасибо.",
        AppraisalExpectation(
            minimums=(("pleasantness", 0.2),),
            reason_any=frozenset({"support", "positive_progress", "neutral_social"}),
        ),
    ),
    AppraisalEvaluationScenario(
        "farewell",
        "До завтра, Сатори.",
        AppraisalExpectation(
            maximums=(("salience", 0.5), ("concern_signal", 0.35)),
            reason_any=frozenset({"neutral_social"}),
        ),
    ),
)


class AppraisalEvaluationAdapter(Protocol):
    """Minimal measured capability used by the provider comparison harness."""

    @property
    def model(self) -> str: ...

    async def generate_structured(
        self, request: AffectiveAppraisalRequest, /
    ) -> AffectiveAppraisalProviderResponse: ...


_SIGNAL_NAMES = (
    "pleasantness",
    "activation",
    "novelty",
    "salience",
    "uncertainty",
    "curiosity_signal",
    "interest_signal",
    "humor_signal",
    "concern_signal",
    "frustration_signal",
    "confidence_signal",
    "appraisal_confidence",
)


def appraisal_evaluation_request(
    scenario: AppraisalEvaluationScenario, sample_index: int
) -> AffectiveAppraisalRequest:
    seed = JsonSeedLoader().load_canonical()
    return AffectiveAppraisalRequest(
        schema_version=1,
        trace_id=f"stage77-appraisal-{scenario.scenario_id}-{sample_index}",
        interaction_id=f"event-{scenario.scenario_id}-{sample_index}",
        appraised_at=datetime(2026, 8, 9, tzinfo=UTC),
        user_content=scenario.user_text,
        traits=tuple(AppraisalTrait(item.key, item.value) for item in seed.traits),
        values=tuple(
            AppraisalValue(item.key, item.strength, item.description) for item in seed.values
        ),
        fast_state=AppraisalFastState(
            valence=0.0,
            arousal=0.12,
            tension=0.08,
            curiosity=0.18,
            interest=0.16,
            amusement=0.05,
            concern=0.08,
            frustration=0.04,
            situational_confidence=0.55,
        ),
        mood_state=AppraisalMoodState(valence=0.0, energy=0.3, tension=0.1),
    )


def evaluate_appraisal(
    proposal: AffectiveAppraisalProposal,
    expectation: AppraisalExpectation,
) -> tuple[bool, tuple[str, ...]]:
    """Evaluate direction/category constraints without comparing exact scores."""

    failures: list[str] = []
    for name, threshold in expectation.minimums:
        if float(getattr(proposal, name)) < threshold:
            failures.append(f"{name}_below_minimum")
    for name, threshold in expectation.maximums:
        if float(getattr(proposal, name)) > threshold:
            failures.append(f"{name}_above_maximum")
    if expectation.reason_any and not expectation.reason_any.intersection(proposal.reason_codes):
        failures.append("reason_category_mismatch")
    return not failures, tuple(failures)


def _milliseconds(value: int | None) -> float | None:
    return round(value / 1_000_000, 3) if value is not None else None


def _wall_distribution(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    p90_index = max(0, ceil(0.9 * len(ordered)) - 1)
    return {
        "min_wall_ms": round(ordered[0], 3),
        "median_wall_ms": round(median(ordered), 3),
        "p90_wall_ms": round(ordered[p90_index], 3),
        "max_wall_ms": round(ordered[-1], 3),
        "mean_wall_ms": round(fmean(ordered), 3),
    }


async def run_appraisal_model_evaluation(
    adapters: tuple[AppraisalEvaluationAdapter, ...],
    *,
    repetitions: int,
) -> dict[str, Any]:
    """Measure schema adherence, semantic quality, and provider timing per model."""

    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    model_reports: dict[str, Any] = {}
    for adapter in adapters:
        samples: list[dict[str, Any]] = []
        for scenario in APPRAISAL_EVALUATION_CORPUS_V1:
            for sample_index in range(repetitions + 1):
                started = time.perf_counter()
                try:
                    response = await adapter.generate_structured(
                        appraisal_evaluation_request(scenario, sample_index)
                    )
                except AffectiveAppraisalProviderError as error:
                    samples.append(
                        {
                            "scenario_id": scenario.scenario_id,
                            "sample_kind": "warmup" if sample_index == 0 else "measured_warm",
                            "schema_valid": False,
                            "semantic_pass": False,
                            "failure_codes": [str(error)],
                            "wall_ms": round((time.perf_counter() - started) * 1000, 3),
                        }
                    )
                    continue
                semantic_pass, failure_codes = evaluate_appraisal(
                    response.proposal, scenario.expectation
                )
                metrics = response.metrics
                samples.append(
                    {
                        "scenario_id": scenario.scenario_id,
                        "sample_kind": "warmup" if sample_index == 0 else "measured_warm",
                        "schema_valid": True,
                        "semantic_pass": semantic_pass,
                        "failure_codes": list(failure_codes),
                        "wall_ms": round((time.perf_counter() - started) * 1000, 3),
                        "load_ms": _milliseconds(metrics.load_duration_ns) if metrics else None,
                        "prompt_eval_ms": (
                            _milliseconds(metrics.prompt_eval_duration_ns) if metrics else None
                        ),
                        "eval_ms": _milliseconds(metrics.eval_duration_ns) if metrics else None,
                        "prompt_tokens": metrics.prompt_eval_count if metrics else None,
                        "output_tokens": metrics.eval_count if metrics else None,
                        "signals": {
                            name: getattr(response.proposal, name) for name in _SIGNAL_NAMES
                        },
                        "reason_codes": list(response.proposal.reason_codes),
                    }
                )
        measured = [sample for sample in samples if sample["sample_kind"] == "measured_warm"]
        valid = [sample for sample in measured if sample["schema_valid"]]
        semantically_valid = [sample for sample in valid if sample["semantic_pass"]]
        wall_values = [float(sample["wall_ms"]) for sample in measured]
        model_reports[adapter.model] = {
            "measured_samples": len(measured),
            "schema_valid_rate": round(len(valid) / len(measured), 4),
            "semantic_pass_rate": round(len(semantically_valid) / len(measured), 4),
            **_wall_distribution(wall_values),
            "samples": samples,
        }
    return {
        "schema_version": 1,
        "evaluation_id": "satori.affective-appraisal.stage77.v1",
        "corpus_size": len(APPRAISAL_EVALUATION_CORPUS_V1),
        "repetitions": repetitions,
        "models": model_reports,
        "expectations": {
            scenario.scenario_id: {
                "minimums": dict(scenario.expectation.minimums),
                "maximums": dict(scenario.expectation.maximums),
                "reason_any": sorted(scenario.expectation.reason_any),
            }
            for scenario in APPRAISAL_EVALUATION_CORPUS_V1
        },
    }
