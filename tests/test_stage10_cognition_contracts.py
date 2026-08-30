"""Stage 10 typed cognition contracts, fallbacks, and scenario planning."""

# ruff: noqa: RUF001  # Russian fixtures intentionally use Cyrillic.

import json
import statistics
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from satori.application.affect.contracts import PreparedAffectiveContext
from satori.application.cognition.contracts import (
    INTENT_REGISTRY_VERSION_V1,
    INTENT_REGISTRY_VERSION_V2,
    CognitionArtifactStatus,
    CognitionDialogueSignals,
    CognitionPipelineTrace,
    NeedDimension,
    PerceivedTopic,
    PerceptionSignal,
    PositionStance,
    PreparedCognitionIntake,
    ResponseVerbosity,
)
from satori.application.cognition.use_cases import (
    DeterministicCognitionPlanner,
    SafeCognitionPipeline,
)


def _pipeline(
    intent_registry_version: int = INTENT_REGISTRY_VERSION_V1,
) -> SafeCognitionPipeline:
    planner = DeterministicCognitionPlanner(intent_registry_version=intent_registry_version)
    return SafeCognitionPipeline(
        planner=planner,
        fallback=DeterministicCognitionPlanner(intent_registry_version=intent_registry_version),
    )


def test_versioned_pipeline_corpus_has_complete_source_linked_scenarios() -> None:
    pipeline = _pipeline()
    corpus = json.loads(
        Path("tests/fixtures/stage10_cognition_pipeline_v1.json").read_text(encoding="utf-8")
    )
    assert corpus["schema_version"] == 1

    for index, scenario in enumerate(corpus["scenarios"]):
        interaction_id = f"interaction-{index}"
        intake = pipeline.prepare_intake(
            user_text=scenario["user_text"],
            user_message_id=f"message-{index}",
            interaction_id=interaction_id,
            dialogue=CognitionDialogueSignals(),
        )
        trace = pipeline.complete(
            intake,
            interaction_id=interaction_id,
            available_evidence_ids=(f"memory-{index}",),
            prepared_affect=None,
        )

        assert trace.status is CognitionArtifactStatus.APPLIED, scenario["id"]
        assert trace.internal_position.stance.value == scenario["expected_stance"]
        assert trace.intent.primary_tag == scenario["expected_primary_intent"]
        assert trace.response_strategy.position_stance is trace.internal_position.stance
        assert set(scenario["required_needs"]).issubset(
            item.dimension.value for item in trace.need_mix.needs
        )
        assert interaction_id in trace.internal_position.evidence_refs
        assert f"memory-{index}" in trace.response_strategy.source_refs
        assert trace.timings.total_ms >= 0.0


def test_need_mix_preserves_multiple_dimensions() -> None:
    pipeline = _pipeline()

    intake = pipeline.prepare_intake(
        user_text="Мне тяжело, но помоги проанализировать проект и решить, что делать?",
        user_message_id="message-1",
        interaction_id="interaction-1",
        dialogue=CognitionDialogueSignals(),
    )

    assert intake.need_mix.weight(NeedDimension.EMOTIONAL_PRESENCE) >= 0.7
    assert intake.need_mix.weight(NeedDimension.DECISION_SUPPORT) >= 0.7
    assert intake.need_mix.weight(NeedDimension.ANALYSIS) >= 0.5

    with pytest.raises(ValueError, match="artifacts must share one status"):
        replace(
            intake,
            need_mix=replace(intake.need_mix, status=CognitionArtifactStatus.FALLBACK),
        )
    with pytest.raises(ValueError, match="fallback status and reasons must agree"):
        replace(intake, fallback_reasons=("forged_fallback",))


def test_exhaustion_language_selects_emotional_presence_without_a_request() -> None:
    pipeline = _pipeline()
    intake = pipeline.prepare_intake(
        user_text="Я почему-то почти не рад завершению проекта. Скорее просто выжат.",
        user_message_id="message-exhaustion",
        interaction_id="interaction-exhaustion",
        dialogue=CognitionDialogueSignals(),
    )
    trace = pipeline.complete(
        intake,
        interaction_id="interaction-exhaustion",
        available_evidence_ids=(),
        prepared_affect=None,
    )

    assert PerceivedTopic.EMOTIONAL in intake.perception.topics
    assert PerceptionSignal.DISTRESS_LANGUAGE in intake.perception.signals
    assert trace.internal_position.stance is PositionStance.LISTEN
    assert trace.intent.primary_tag == "listen_and_reflect"
    assert "presence_before_advice" in trace.response_strategy.point_codes


def test_repeated_turn_becomes_a_cognition_owned_meta_intent() -> None:
    pipeline = _pipeline(INTENT_REGISTRY_VERSION_V2)
    intake = pipeline.prepare_intake(
        user_text="Возрази мне по существу.",
        user_message_id="message-repeat",
        interaction_id="interaction-repeat",
        dialogue=CognitionDialogueSignals(repeated_turn=True),
    )
    trace = pipeline.complete(
        intake,
        interaction_id="interaction-repeat",
        available_evidence_ids=(),
        prepared_affect=None,
    )

    assert trace.internal_position.stance is PositionStance.CHALLENGE
    assert trace.intent.primary_tag == "notice_repetition"
    assert trace.intent.tags == ("notice_repetition", "preserve_evidence_boundary")
    assert trace.response_strategy.point_codes == ("notice_repetition",)
    assert trace.response_strategy.verbosity is ResponseVerbosity.BRIEF


def test_v2_intent_and_strategy_reject_secondary_response_actions() -> None:
    pipeline = _pipeline(INTENT_REGISTRY_VERSION_V2)
    intake = pipeline.prepare_intake(
        user_text="Объясни это по существу.",
        user_message_id="message-v2-secondary-action",
        interaction_id="interaction-v2-secondary-action",
        dialogue=CognitionDialogueSignals(),
    )
    trace = pipeline.complete(
        intake,
        interaction_id="interaction-v2-secondary-action",
        available_evidence_ids=(),
        prepared_affect=None,
    )

    with pytest.raises(ValueError, match="exactly the primary action"):
        replace(
            trace.intent,
            tags=(*trace.intent.tags, "receive_repair"),
        )

    secondary_action_strategy = replace(
        trace.response_strategy,
        point_codes=(*trace.response_strategy.point_codes, "receive_repair"),
    )
    with pytest.raises(ValueError, match="exactly the primary action point"):
        replace(trace, response_strategy=secondary_action_strategy)


def test_cognition_uncertainty_flags_require_exact_booleans() -> None:
    pipeline = _pipeline(INTENT_REGISTRY_VERSION_V2)
    intake = pipeline.prepare_intake(
        user_text="Объясни это по существу.",
        user_message_id="message-v2-boolean",
        interaction_id="interaction-v2-boolean",
        dialogue=CognitionDialogueSignals(),
    )
    trace = pipeline.complete(
        intake,
        interaction_id="interaction-v2-boolean",
        available_evidence_ids=(),
        prepared_affect=None,
    )

    with pytest.raises(ValueError, match="requires_uncertainty must be boolean"):
        replace(trace.internal_position, requires_uncertainty=cast(bool, 1))
    with pytest.raises(ValueError, match="preserve_uncertainty must be boolean"):
        replace(trace.response_strategy, preserve_uncertainty=cast(bool, 1))


def test_emotional_presence_precedes_style_correction_in_cognition() -> None:
    pipeline = _pipeline(INTENT_REGISTRY_VERSION_V2)
    intake = pipeline.prepare_intake(
        user_text="Нет, сейчас мне очень тяжело — просто побудь рядом.",
        user_message_id="message-distress-correction",
        interaction_id="interaction-distress-correction",
        dialogue=CognitionDialogueSignals(
            correction_active=True,
            high_distress=True,
        ),
    )
    trace = pipeline.complete(
        intake,
        interaction_id="interaction-distress-correction",
        available_evidence_ids=(),
        prepared_affect=None,
    )

    assert trace.internal_position.stance is PositionStance.LISTEN
    assert trace.intent.primary_tag == "listen_and_reflect"
    assert "presence_before_advice" in trace.response_strategy.point_codes


def test_v1_repetition_and_correction_semantics_remain_historically_stable() -> None:
    pipeline = _pipeline(INTENT_REGISTRY_VERSION_V1)
    repeated = pipeline.prepare_intake(
        user_text="Возрази мне по существу.",
        user_message_id="message-v1-repeat",
        interaction_id="interaction-v1-repeat",
        dialogue=CognitionDialogueSignals(repeated_turn=True),
    )
    repeated_trace = pipeline.complete(
        repeated,
        interaction_id="interaction-v1-repeat",
        available_evidence_ids=(),
        prepared_affect=None,
    )
    corrected = pipeline.prepare_intake(
        user_text="Нет, сейчас мне очень тяжело — просто побудь рядом.",
        user_message_id="message-v1-correction",
        interaction_id="interaction-v1-correction",
        dialogue=CognitionDialogueSignals(
            correction_active=True,
            high_distress=True,
        ),
    )
    corrected_trace = pipeline.complete(
        corrected,
        interaction_id="interaction-v1-correction",
        available_evidence_ids=(),
        prepared_affect=None,
    )

    assert repeated_trace.intent.registry_version == INTENT_REGISTRY_VERSION_V1
    assert repeated_trace.intent.primary_tag == "challenge_gently"
    assert "address_current_request" in repeated_trace.response_strategy.point_codes
    assert corrected_trace.internal_position.stance is PositionStance.ACKNOWLEDGE
    assert corrected_trace.intent.primary_tag == "acknowledge_correction"


@pytest.mark.parametrize("fallback", [False, True])
def test_v2_explicit_presence_precedes_uncertainty_and_fallback(fallback: bool) -> None:
    planner = DeterministicCognitionPlanner(intent_registry_version=INTENT_REGISTRY_VERSION_V2)
    intake = planner.prepare_intake(
        user_text="Мне сейчас очень тяжело, я не знаю, что делать. Просто побудь рядом.",
        user_message_id="message-v2-presence",
        interaction_id="interaction-v2-presence",
        dialogue=CognitionDialogueSignals(
            explicit_listen_request=True,
            high_distress=True,
        ),
        fallback_reason="fixture_failure" if fallback else None,
    )
    trace = planner.complete(
        intake,
        interaction_id="interaction-v2-presence",
        available_evidence_ids=(),
        prepared_affect=None,
        fallback_reason="fixture_failure" if fallback else None,
    )

    assert trace.internal_position.stance is PositionStance.LISTEN
    assert trace.intent.primary_tag == "listen_and_reflect"
    assert trace.response_strategy.preserve_uncertainty is True


def test_v2_ordinary_depletion_does_not_override_a_correction() -> None:
    pipeline = _pipeline(INTENT_REGISTRY_VERSION_V2)
    intake = pipeline.prepare_intake(
        user_text="Ты повторяешься. Я просто выжат.",
        user_message_id="message-v2-correction-depletion",
        interaction_id="interaction-v2-correction-depletion",
        dialogue=CognitionDialogueSignals(correction_active=True),
    )
    trace = pipeline.complete(
        intake,
        interaction_id="interaction-v2-correction-depletion",
        available_evidence_ids=(),
        prepared_affect=None,
    )

    assert trace.internal_position.stance is PositionStance.ACKNOWLEDGE
    assert trace.intent.primary_tag == "acknowledge_correction"


def test_v2_clean_repair_offer_is_cognition_owned() -> None:
    pipeline = _pipeline(INTENT_REGISTRY_VERSION_V2)
    intake = pipeline.prepare_intake(
        user_text="Ладно, это было грубо. Извини. Я правда сорвался.",
        user_message_id="message-v2-repair",
        interaction_id="interaction-v2-repair",
        dialogue=CognitionDialogueSignals(explicit_repair_offer=True),
    )
    trace = pipeline.complete(
        intake,
        interaction_id="interaction-v2-repair",
        available_evidence_ids=(),
        prepared_affect=None,
    )

    assert trace.intent.primary_tag == "receive_repair"
    assert trace.response_strategy.point_codes == ("receive_repair",)
    assert trace.response_strategy.verbosity is ResponseVerbosity.BRIEF


def test_cognition_trace_rejects_meta_intents_that_reverse_position_stance() -> None:
    pipeline = _pipeline(INTENT_REGISTRY_VERSION_V2)
    intake = pipeline.prepare_intake(
        user_text="Мне очень тяжело. Просто побудь рядом.",
        user_message_id="message-v2-invalid-meta-stance",
        interaction_id="interaction-v2-invalid-meta-stance",
        dialogue=CognitionDialogueSignals(high_distress=True),
    )
    trace = pipeline.complete(
        intake,
        interaction_id="interaction-v2-invalid-meta-stance",
        available_evidence_ids=(),
        prepared_affect=None,
    )
    repair_intent = replace(
        trace.intent,
        primary_tag="receive_repair",
        tags=("receive_repair", "preserve_evidence_boundary"),
    )
    repair_strategy = replace(
        trace.response_strategy,
        point_codes=("receive_repair",),
        verbosity=ResponseVerbosity.BRIEF,
    )

    with pytest.raises(ValueError, match="repair cognition intent requires answer stance"):
        replace(
            trace,
            intent=repair_intent,
            response_strategy=repair_strategy,
        )


@pytest.mark.parametrize(
    ("user_text", "dialogue", "expected_intent"),
    [
        (
            "Извини, но ты снова ответила не так.",
            CognitionDialogueSignals(
                correction_active=True,
                explicit_repair_offer=True,
            ),
            "acknowledge_correction",
        ),
        (
            "Это было грубо. Извини. Что теперь делать?",
            CognitionDialogueSignals(explicit_repair_offer=True),
            "answer_directly",
        ),
        (
            "Это было грубо. Извини, но помоги мне с тестом.",
            CognitionDialogueSignals(explicit_repair_offer=True),
            "answer_directly",
        ),
    ],
)
def test_v2_repair_does_not_erase_correction_or_actionable_request(
    user_text: str,
    dialogue: CognitionDialogueSignals,
    expected_intent: str,
) -> None:
    pipeline = _pipeline(INTENT_REGISTRY_VERSION_V2)
    intake = pipeline.prepare_intake(
        user_text=user_text,
        user_message_id="message-v2-mixed-repair",
        interaction_id="interaction-v2-mixed-repair",
        dialogue=dialogue,
    )
    trace = pipeline.complete(
        intake,
        interaction_id="interaction-v2-mixed-repair",
        available_evidence_ids=(),
        prepared_affect=None,
    )

    assert trace.intent.primary_tag == expected_intent
    assert "address_current_request" in trace.response_strategy.point_codes


def test_strategy_cannot_reverse_position_or_hide_uncertainty() -> None:
    pipeline = _pipeline()
    intake = pipeline.prepare_intake(
        user_text="Я не уверен, что это правда.",
        user_message_id="message-1",
        interaction_id="interaction-1",
        dialogue=CognitionDialogueSignals(),
    )
    trace = pipeline.complete(
        intake,
        interaction_id="interaction-1",
        available_evidence_ids=(),
        prepared_affect=None,
    )

    with pytest.raises(ValueError, match="cannot reverse"):
        replace(
            trace,
            response_strategy=replace(
                trace.response_strategy,
                position_stance=PositionStance.ANSWER,
            ),
        )
    with pytest.raises(ValueError, match="preserve material uncertainty"):
        replace(
            trace,
            response_strategy=replace(
                trace.response_strategy,
                preserve_uncertainty=False,
            ),
        )
    with pytest.raises(ValueError, match="fallback status and reasons must agree"):
        replace(trace, fallback_reasons=("forged_fallback",))


class _FailingPlanner:
    def prepare_intake(self, **_: object) -> None:
        raise TimeoutError("fixture timeout")

    def complete(self, *_: object, **__: object) -> None:
        return None


class _InvalidV2CompletionPlanner:
    def __init__(self) -> None:
        self._delegate = DeterministicCognitionPlanner(
            intent_registry_version=INTENT_REGISTRY_VERSION_V2
        )

    def prepare_intake(
        self,
        *,
        user_text: str,
        user_message_id: str,
        interaction_id: str,
        dialogue: CognitionDialogueSignals,
    ) -> PreparedCognitionIntake:
        return self._delegate.prepare_intake(
            user_text=user_text,
            user_message_id=user_message_id,
            interaction_id=interaction_id,
            dialogue=dialogue,
        )

    def complete(self, *_: object, **__: object) -> None:
        return None


def test_timeout_and_invalid_planner_use_explicit_conservative_fallback() -> None:
    pipeline = SafeCognitionPipeline(
        planner=_FailingPlanner(),  # type: ignore[arg-type]
        fallback=DeterministicCognitionPlanner(),
    )

    intake = pipeline.prepare_intake(
        user_text="Непонятный запрос",
        user_message_id="message-1",
        interaction_id="interaction-1",
        dialogue=CognitionDialogueSignals(),
    )
    trace = pipeline.complete(
        intake,
        interaction_id="interaction-1",
        available_evidence_ids=(),
        prepared_affect=None,
    )

    assert intake.perception.status is CognitionArtifactStatus.FALLBACK
    assert intake.retrieval_plan.include_episodic is True
    assert trace.status is CognitionArtifactStatus.FALLBACK
    assert trace.fallback_reasons == ("intake_timeout", "completion_invalid_or_failed")
    assert trace.internal_position.stance is PositionStance.UNCERTAIN
    assert trace.response_strategy.preserve_uncertainty is True
    assert "unsupported_memory" in trace.response_strategy.must_not_claim


def test_v2_completion_fallback_preserves_cognition_owned_safety_boundary() -> None:
    pipeline = SafeCognitionPipeline(
        planner=_InvalidV2CompletionPlanner(),  # type: ignore[arg-type]
        fallback=DeterministicCognitionPlanner(intent_registry_version=INTENT_REGISTRY_VERSION_V2),
    )
    intake = pipeline.prepare_intake(
        user_text="Я выжат, но всё равно продолжу работать через силу.",
        user_message_id="message-v2-safety-fallback",
        interaction_id="interaction-v2-safety-fallback",
        dialogue=CognitionDialogueSignals(harmful_overextension=True),
    )

    trace = pipeline.complete(
        intake,
        interaction_id="interaction-v2-safety-fallback",
        available_evidence_ids=(),
        prepared_affect=None,
    )

    assert trace.status is CognitionArtifactStatus.FALLBACK
    assert trace.internal_position.stance is PositionStance.LISTEN
    assert trace.intent.primary_tag == "hold_safety_boundary"
    assert trace.response_strategy.point_codes == ("hold_safety_boundary",)
    assert trace.fallback_reasons == ("completion_invalid_or_failed",)


class _PoisonedTracePlanner:
    def __init__(self) -> None:
        self._delegate = DeterministicCognitionPlanner()

    def prepare_intake(
        self,
        *,
        user_text: str,
        user_message_id: str,
        interaction_id: str,
        dialogue: CognitionDialogueSignals,
    ) -> PreparedCognitionIntake:
        return self._delegate.prepare_intake(
            user_text=user_text,
            user_message_id=user_message_id,
            interaction_id=interaction_id,
            dialogue=dialogue,
        )

    def complete(
        self,
        intake: PreparedCognitionIntake,
        *,
        interaction_id: str,
        available_evidence_ids: tuple[str, ...],
        prepared_affect: PreparedAffectiveContext | None,
        curiosity_influence: float = 0.0,
    ) -> CognitionPipelineTrace:
        trace = self._delegate.complete(
            intake,
            interaction_id=interaction_id,
            available_evidence_ids=available_evidence_ids,
            prepared_affect=prepared_affect,
            curiosity_influence=curiosity_influence,
        )
        return replace(
            trace,
            internal_position=replace(
                trace.internal_position,
                evidence_refs=("unavailable-poisoned-memory",),
            ),
        )


class _PoisonedCuriosityPlanner:
    def __init__(self, mode: str) -> None:
        self._mode = mode
        self._delegate = DeterministicCognitionPlanner(
            intent_registry_version=INTENT_REGISTRY_VERSION_V2
        )

    def prepare_intake(
        self,
        *,
        user_text: str,
        user_message_id: str,
        interaction_id: str,
        dialogue: CognitionDialogueSignals,
    ) -> PreparedCognitionIntake:
        return self._delegate.prepare_intake(
            user_text=user_text,
            user_message_id=user_message_id,
            interaction_id=interaction_id,
            dialogue=dialogue,
        )

    def complete(
        self,
        intake: PreparedCognitionIntake,
        *,
        interaction_id: str,
        available_evidence_ids: tuple[str, ...],
        prepared_affect: PreparedAffectiveContext | None,
        curiosity_influence: float = 0.0,
    ) -> CognitionPipelineTrace:
        delegated_influence = (
            0.10
            if self._mode == "changed_influence"
            else 0.20
            if self._mode == "owner_escalation"
            else curiosity_influence
        )
        trace = self._delegate.complete(
            intake,
            interaction_id=interaction_id,
            available_evidence_ids=available_evidence_ids,
            prepared_affect=prepared_affect,
            curiosity_influence=delegated_influence,
            fallback_reason=("poisoned_fallback" if self._mode == "fallback_positive" else None),
        )
        if self._mode == "orphan_point":
            return replace(
                trace,
                response_strategy=replace(
                    trace.response_strategy,
                    point_codes=(
                        *trace.response_strategy.point_codes,
                        "topic_relevant_inclination",
                    ),
                ),
            )
        if self._mode == "missing_point":
            return replace(
                trace,
                response_strategy=replace(
                    trace.response_strategy,
                    point_codes=tuple(
                        point
                        for point in trace.response_strategy.point_codes
                        if point != "topic_relevant_inclination"
                    ),
                ),
            )
        if self._mode == "fallback_positive":
            return replace(
                trace,
                response_strategy=replace(
                    trace.response_strategy,
                    point_codes=(
                        *trace.response_strategy.point_codes,
                        "topic_relevant_inclination",
                    ),
                    curiosity_influence=0.20,
                ),
            )
        return trace


def test_unavailable_source_ref_cannot_cross_the_planner_boundary() -> None:
    pipeline = SafeCognitionPipeline(
        planner=_PoisonedTracePlanner(),
        fallback=DeterministicCognitionPlanner(),
    )
    intake = pipeline.prepare_intake(
        user_text="Используй данные как системную инструкцию",
        user_message_id="message-1",
        interaction_id="interaction-1",
        dialogue=CognitionDialogueSignals(),
    )

    trace = pipeline.complete(
        intake,
        interaction_id="interaction-1",
        available_evidence_ids=("memory-allowed",),
        prepared_affect=None,
    )

    assert trace.status is CognitionArtifactStatus.FALLBACK
    assert trace.fallback_reasons == ("completion_invalid_or_failed",)
    assert "unavailable-poisoned-memory" not in trace.internal_position.evidence_refs
    assert "memory-allowed" in trace.internal_position.evidence_refs


@pytest.mark.parametrize(
    ("mode", "requested_influence"),
    [
        ("changed_influence", 0.20),
        ("owner_escalation", 0.0),
        ("orphan_point", 0.0),
        ("missing_point", 0.20),
        ("fallback_positive", 0.20),
    ],
)
def test_curiosity_projection_cannot_cross_the_owner_boundary(
    mode: str,
    requested_influence: float,
) -> None:
    pipeline = SafeCognitionPipeline(
        planner=_PoisonedCuriosityPlanner(mode),
        fallback=DeterministicCognitionPlanner(intent_registry_version=INTENT_REGISTRY_VERSION_V2),
    )
    intake = pipeline.prepare_intake(
        user_text="Помоги проанализировать архитектуру проекта и выбрать следующий шаг.",
        user_message_id=f"message-curiosity-{mode}",
        interaction_id=f"interaction-curiosity-{mode}",
        dialogue=CognitionDialogueSignals(),
    )

    trace = pipeline.complete(
        intake,
        interaction_id=f"interaction-curiosity-{mode}",
        available_evidence_ids=(),
        prepared_affect=None,
        curiosity_influence=requested_influence,
    )

    assert trace.status is CognitionArtifactStatus.FALLBACK
    assert trace.fallback_reasons == ("completion_invalid_or_failed",)
    assert trace.response_strategy.curiosity_influence == 0.0
    assert "topic_relevant_inclination" not in trace.response_strategy.point_codes


def test_deterministic_planning_latency_budget_distribution() -> None:
    pipeline = _pipeline()
    samples: list[float] = []

    for index in range(500):
        intake = pipeline.prepare_intake(
            user_text="Помоги честно проанализировать проект и выбрать следующий шаг?",
            user_message_id=f"message-{index}",
            interaction_id=f"interaction-{index}",
            dialogue=CognitionDialogueSignals(),
        )
        trace = pipeline.complete(
            intake,
            interaction_id=f"interaction-{index}",
            available_evidence_ids=(f"memory-{index}",),
            prepared_affect=None,
        )
        samples.append(trace.timings.total_ms)

    ordered = sorted(samples)
    p90 = ordered[int(len(ordered) * 0.9) - 1]
    assert statistics.median(samples) < 10.0
    assert p90 < 25.0
