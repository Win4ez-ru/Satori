"""Stage 13 bounded inclination context and cognition influence tests."""

# ruff: noqa: RUF001  # Russian behavior fixtures intentionally use Cyrillic.

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Self, cast

import pytest

from satori.application.cognition.contracts import (
    CognitionArtifactStatus,
    CognitionDialogueSignals,
    CognitionPipelineTrace,
    PositionStance,
)
from satori.application.cognition.use_cases import DeterministicCognitionPlanner
from satori.application.positions.contracts import (
    SatoriInclinationsContext,
    inclinations_context_json,
)
from satori.application.positions.ports import PositionsUnitOfWork
from satori.application.positions.use_cases import GetSatoriPositions
from satori.core.inclinations import InclinationKind, InclinationStateReference

AS_OF = datetime(2026, 8, 22, 12, tzinfo=UTC)
IDENTITY_ID = "satori"


class _ReferenceUnitOfWork:
    """Read-only unit fixture exposing only the repository method under test."""

    def __init__(self, references: tuple[InclinationStateReference, ...]) -> None:
        self.references = references

    @property
    def positions(self) -> Self:
        return self

    def list_inclination_references(
        self, *, identity_id: str
    ) -> tuple[InclinationStateReference, ...]:
        assert identity_id == IDENTITY_ID
        return self.references

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def commit(self) -> None:
        raise AssertionError("inclination context reads must not commit")

    def rollback(self) -> None:
        raise AssertionError("inclination context reads must not roll back explicitly")


def reference(
    inclination_id: str,
    *,
    topic: str,
    kind: InclinationKind = InclinationKind.INTEREST,
    alternative: str | None = None,
    score: float = 0.20,
    confidence: float = 0.70,
    stability: float = 0.0,
    state_as_of: datetime = AS_OF,
) -> InclinationStateReference:
    return InclinationStateReference(
        inclination_id=inclination_id,
        aggregate_version=1,
        kind=kind,
        topic=topic,
        alternative_topic=alternative,
        score=score,
        confidence=confidence,
        stability=stability,
        state_as_of=state_as_of,
    )


def reader(
    references: tuple[InclinationStateReference, ...],
    *,
    top_k: int = 3,
    max_chars: int = 720,
) -> GetSatoriPositions:
    factory = cast(
        Callable[[], PositionsUnitOfWork],
        lambda: _ReferenceUnitOfWork(references),
    )
    return GetSatoriPositions(
        unit_of_work_factory=factory,
        inclination_top_k=top_k,
        max_inclination_context_chars=max_chars,
    )


def project(
    references: tuple[InclinationStateReference, ...],
    user_text: str,
    *,
    as_of: datetime = AS_OF,
) -> SatoriInclinationsContext:
    return reader(references).project_inclination_context(
        identity_id=IDENTITY_ID,
        user_text=user_text,
        as_of=as_of,
    )


def test_exact_topic_phrase_is_relevant_but_inflection_and_unrelated_turn_are_not() -> None:
    references = (
        reference("inclination-jazz", topic="джаз", score=0.24),
        reference("inclination-space", topic="космос", score=0.22),
    )

    exact = project(references, "Давай обсудим джаз сегодня")
    inflected = project(references, "Что думаешь про джазовый фестиваль?")
    unrelated = project(references, "Как устроены океанские течения?")

    assert exact.inclination_ids == ("inclination-jazz",)
    assert exact.status == "available"
    assert inflected.inclinations == ()
    assert unrelated.status == "empty"


def test_owned_topic_read_is_explicit_bounded_and_interest_only() -> None:
    references = (
        reference("interest-space", topic="космос", score=0.42),
        reference("interest-history", topic="история", score=0.31),
        reference(
            "preference-tea",
            kind=InclinationKind.PREFERENCE,
            topic="чай",
            alternative="кофе",
            score=0.70,
        ),
    )
    projection = reader(references).project_inclination_context(
        identity_id=IDENTITY_ID,
        user_text="Ладно, с этим разобрались.",
        as_of=AS_OF,
        include_owned_topic=True,
    )
    historical_default = project(references, "Ладно, с этим разобрались.")

    assert projection.inclination_ids == ("interest-space",)
    assert projection.inclinations[0].kind == InclinationKind.INTEREST.value
    assert historical_default.status == "empty"


@pytest.mark.parametrize(
    "explicit_query",
    [
        "Какие у тебя интересы?",
        "Что ты предпочитаешь?",
        "What are your interests?",
        "What do you prefer?",
    ],
)
def test_explicit_russian_and_english_queries_select_interests_and_preferences(
    explicit_query: str,
) -> None:
    references = (
        reference("interest-jazz", topic="джаз", score=0.30),
        reference(
            "preference-tea",
            kind=InclinationKind.PREFERENCE,
            topic="чай",
            alternative="кофе",
            score=0.25,
        ),
    )

    context = project(references, explicit_query)

    assert context.status == "available"
    assert set(context.inclination_ids) == {"interest-jazz", "preference-tea"}


def test_confidence_and_effective_magnitude_exact_boundaries_are_inclusive() -> None:
    references = (
        reference("exact-boundary", topic="джаз", score=0.05, confidence=0.55),
        reference("low-confidence", topic="джаз", score=0.50, confidence=0.549999),
        reference("low-score", topic="джаз", score=0.049999, confidence=1.0),
    )

    context = project(references, "джаз")

    assert context.inclination_ids == ("exact-boundary",)
    assert context.inclinations[0].effective_score == pytest.approx(0.05)
    assert context.curiosity_influence == pytest.approx(0.05)


def test_explicit_as_of_decay_is_pure_and_crosses_context_threshold_after_half_life() -> None:
    anchored = reference(
        "decaying-interest",
        topic="джаз",
        score=0.10,
        confidence=0.70,
        stability=0.0,
    )
    original = replace(anchored)
    half_life = AS_OF + timedelta(days=30)

    exact = project((anchored,), "джаз", as_of=half_life)
    after = project(
        (anchored,),
        "джаз",
        as_of=half_life + timedelta(microseconds=1),
    )

    assert exact.inclination_ids == (anchored.inclination_id,)
    assert exact.inclinations[0].effective_score == pytest.approx(0.05)
    assert after.status == "empty"
    assert anchored == original


def test_selection_is_deterministic_top_three_and_respects_720_character_budget() -> None:
    short = tuple(
        reference(
            f"short-{index}",
            topic=f"topic-{index}",
            score=score,
            confidence=0.80,
        )
        for index, score in enumerate((0.70, 0.50, 0.90, 0.60, 0.80), start=1)
    )
    short_context = project(short, "What are your interests?")

    assert short_context.inclination_ids == ("short-3", "short-5", "short-1")
    assert len(short_context.inclinations) == 3
    assert len(inclinations_context_json(short_context)) <= 720

    long_preferences = tuple(
        reference(
            f"long-{index}",
            kind=InclinationKind.PREFERENCE,
            topic=f"topic-{index}-" + "x" * 78,
            alternative=f"alternative-{index}-" + "y" * 72,
            score=score,
            confidence=0.80,
        )
        for index, score in enumerate((0.90, 0.80, 0.70), start=1)
    )
    long_context = project(long_preferences, "What are your preferences?")

    assert 0 < len(long_context.inclinations) < 3
    assert len(inclinations_context_json(long_context)) <= 720
    assert long_context.inclination_ids == tuple(
        item.inclination_id for item in long_preferences[: len(long_context.inclinations)]
    )


def test_preference_direction_is_explicit_and_preferences_never_add_curiosity() -> None:
    preferences = (
        reference(
            "positive-preference",
            kind=InclinationKind.PREFERENCE,
            topic="чай",
            alternative="кофе",
            score=0.30,
        ),
        reference(
            "negative-preference",
            kind=InclinationKind.PREFERENCE,
            topic="кошки",
            alternative="собаки",
            score=-0.25,
        ),
    )

    context = project(preferences, "Какие у тебя предпочтения?")
    by_id = {item.inclination_id: item for item in context.inclinations}

    assert by_id["positive-preference"].preferred_topic == "чай"
    assert by_id["negative-preference"].preferred_topic == "собаки"
    assert context.curiosity_influence == 0.0


def test_curiosity_comes_only_from_interests_and_is_capped_at_point_two() -> None:
    mixed = (
        reference(
            "strong-preference",
            kind=InclinationKind.PREFERENCE,
            topic="чай",
            alternative="кофе",
            score=0.99,
        ),
        reference("moderate-interest", topic="джаз", score=0.18),
    )
    capped = (
        *mixed,
        reference("strong-interest", topic="архитектура", score=0.90),
    )

    mixed_context = project(mixed, "Какие у тебя интересы?")
    capped_context = project(capped, "Какие у тебя интересы?")

    assert mixed_context.curiosity_influence == pytest.approx(0.18)
    assert capped_context.curiosity_influence == pytest.approx(0.20)


def cognition_trace(
    user_text: str,
    *,
    curiosity_influence: float,
    fallback_reason: str | None = None,
) -> CognitionPipelineTrace:
    planner = DeterministicCognitionPlanner()
    intake = planner.prepare_intake(
        user_text=user_text,
        user_message_id="message-1",
        interaction_id="interaction-1",
        dialogue=CognitionDialogueSignals(),
        fallback_reason=fallback_reason,
    )
    return planner.complete(
        intake,
        interaction_id="interaction-1",
        available_evidence_ids=(),
        prepared_affect=None,
        curiosity_influence=curiosity_influence,
    )


def test_inclination_strategy_point_does_not_create_a_question_or_new_intent() -> None:
    baseline = cognition_trace("Расскажи про джаз", curiosity_influence=0.0)
    influenced = cognition_trace("Расскажи про джаз", curiosity_influence=0.20)

    assert influenced.status is CognitionArtifactStatus.APPLIED
    assert influenced.intent.primary_tag == baseline.intent.primary_tag
    assert influenced.intent.tags == baseline.intent.tags
    assert "ask_specific_follow_up" not in influenced.intent.tags
    assert "ask_specific_follow_up" not in influenced.response_strategy.point_codes
    assert "topic_relevant_inclination" in influenced.response_strategy.point_codes
    assert influenced.response_strategy.curiosity_influence == pytest.approx(0.20)


@pytest.mark.parametrize(
    ("user_text", "fallback_reason", "expected_stance"),
    [
        ("Расскажи про джаз", "controlled-fallback", PositionStance.UNCERTAIN),
        ("Мне очень тяжело, побудь со мной", None, PositionStance.LISTEN),
        ("Я не уверен, возможно ли это", None, PositionStance.UNCERTAIN),
    ],
)
def test_fallback_distress_and_uncertainty_suppress_inclination_strategy_influence(
    user_text: str,
    fallback_reason: str | None,
    expected_stance: PositionStance,
) -> None:
    trace = cognition_trace(
        user_text,
        curiosity_influence=0.20,
        fallback_reason=fallback_reason,
    )

    assert trace.internal_position.stance is expected_stance
    assert "topic_relevant_inclination" not in trace.response_strategy.point_codes
    assert trace.response_strategy.curiosity_influence == 0.0
    assert "ask_specific_follow_up" not in trace.intent.tags


def test_cognition_rejects_curiosity_above_the_stage_thirteen_cap() -> None:
    with pytest.raises(ValueError, match=r"\[0, 0\.20\]"):
        cognition_trace("Расскажи про джаз", curiosity_influence=0.200001)
