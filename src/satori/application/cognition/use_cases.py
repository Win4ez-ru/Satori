"""Deterministic V1 cognition planning and explicit conservative fallback."""

# Ruff's ambiguous-Unicode rule is intentionally disabled for Russian lexical cues.
# ruff: noqa: RUF001

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from satori.application.affect.contracts import EmotionAppraisalStatus, PreparedAffectiveContext
from satori.application.cognition.contracts import (
    APPRAISAL_ARTIFACT_SCHEMA_VERSION,
    COGNITION_PIPELINE_SCHEMA_VERSION,
    INTENT_REGISTRY_VERSION_V1,
    INTENT_REGISTRY_VERSION_V2,
    INTENT_SCHEMA_VERSION,
    INTERNAL_POSITION_SCHEMA_VERSION,
    NEED_MIX_SCHEMA_VERSION,
    PERCEPTION_SCHEMA_VERSION,
    RESPONSE_STRATEGY_SCHEMA_VERSION,
    RETRIEVAL_PLAN_SCHEMA_VERSION,
    AppraisalArtifact,
    CognitionArtifactStatus,
    CognitionDialogueSignals,
    CognitionPipelineTrace,
    CognitionStepTimings,
    IntentSelection,
    InternalPosition,
    NeedDimension,
    NeedMix,
    NeedWeight,
    PerceivedTopic,
    Perception,
    PerceptionSignal,
    PositionStance,
    PreparedCognitionIntake,
    ResponseStrategy,
    ResponseTone,
    ResponseVerbosity,
    RetrievalPlan,
    RetrievalQueryMode,
)
from satori.application.cognition.ports import CognitionPlannerPort

_TOPIC_CUES: dict[PerceivedTopic, tuple[str, ...]] = {
    PerceivedTopic.TECHNICAL: (
        "код",
        "python",
        "архитект",
        "api",
        "ошибк",
        "тест",
        "database",
        "программ",
    ),
    PerceivedTopic.EMOTIONAL: (
        "чувств",
        "груст",
        "страш",
        "тревож",
        "больно",
        "одинок",
        "устал",
        "выжат",
        "вымот",
        "опустош",
        "нет сил",
        "не рад",
        "тяжело",
        "эмоц",
        "что-то случилось",
    ),
    PerceivedTopic.RELATIONSHIP: ("отношен", "между нами", "довер", "близ", "люб"),
    PerceivedTopic.MEMORY: ("помни", "вспомни", "обсуждали", "раньше", "прошл"),
    PerceivedTopic.SELF: ("кто ты", "сатор", "твой характер", "ты умеешь", "о себе"),
    PerceivedTopic.PROJECT: ("проект", "задач", "roadmap", "stage", "план"),
    PerceivedTopic.DECISION: ("решить", "выбрать", "стоит ли", "вариант", "что делать"),
    PerceivedTopic.CREATIVE: ("придум", "идея", "твор", "сюжет", "дизайн", "концепт"),
}
_DISTRESS_CUES = (
    "мне плохо",
    "невыносим",
    "разбит",
    "паник",
    "очень тяжело",
    "больно",
    "выжат",
    "вымот",
    "опустош",
    "нет сил",
)
_UNCERTAINTY_CUES = ("не знаю", "не уверен", "не уверена", "возможно", "может быть", "кажется")
_CHALLENGE_CUES = ("возраз", "оспор", "критику", "скажи честно", "не соглашай", "challenge")
_REQUEST_CUES = ("сделай", "помоги", "давай", "объясни", "расскажи", "покажи", "проверь")


def _contains_any(text: str, cues: tuple[str, ...]) -> bool:
    return any(cue in text for cue in cues)


def _unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


@dataclass(slots=True)
class DeterministicCognitionPlanner:
    """Versioned conservative planner with no model, repository or state-write capability."""

    monotonic: Callable[[], float] = time.perf_counter
    intent_registry_version: int = INTENT_REGISTRY_VERSION_V1

    def __post_init__(self) -> None:
        if self.intent_registry_version not in {
            INTENT_REGISTRY_VERSION_V1,
            INTENT_REGISTRY_VERSION_V2,
        }:
            raise ValueError("cognition intent_registry_version is not supported")

    def prepare_intake(
        self,
        *,
        user_text: str,
        user_message_id: str,
        interaction_id: str,
        dialogue: CognitionDialogueSignals,
        fallback_reason: str | None = None,
    ) -> PreparedCognitionIntake:
        """Build pre-retrieval artifacts from the current canonical message only."""

        normalized = " ".join(user_text.casefold().split())
        source_refs = (interaction_id, user_message_id)
        status = (
            CognitionArtifactStatus.FALLBACK
            if fallback_reason is not None
            else CognitionArtifactStatus.APPLIED
        )

        started = self.monotonic()
        topics = [topic for topic, cues in _TOPIC_CUES.items() if _contains_any(normalized, cues)]
        if not topics:
            topics = [PerceivedTopic.GENERAL]
        signals: list[PerceptionSignal] = []
        if "?" in user_text:
            signals.append(PerceptionSignal.QUESTION)
        if _contains_any(normalized, _REQUEST_CUES):
            signals.append(PerceptionSignal.REQUEST)
        if _contains_any(normalized, _DISTRESS_CUES):
            signals.append(PerceptionSignal.DISTRESS_LANGUAGE)
        if dialogue.correction_active:
            signals.append(PerceptionSignal.CORRECTION)
        if _contains_any(normalized, _UNCERTAINTY_CUES):
            signals.append(PerceptionSignal.UNCERTAINTY_LANGUAGE)
        if _contains_any(normalized, _CHALLENGE_CUES):
            signals.append(PerceptionSignal.CHALLENGE_REQUEST)
        if dialogue.repeated_turn:
            signals.append(PerceptionSignal.REPEATED_TURN)
        if self.intent_registry_version >= INTENT_REGISTRY_VERSION_V2:
            if dialogue.explicit_listen_request:
                signals.append(PerceptionSignal.EXPLICIT_LISTEN_REQUEST)
            if dialogue.high_distress:
                signals.append(PerceptionSignal.HIGH_DISTRESS)
            if dialogue.harmful_overextension:
                signals.append(PerceptionSignal.HARMFUL_OVEREXTENSION)
            if dialogue.explicit_motivation_request:
                signals.append(PerceptionSignal.EXPLICIT_MOTIVATION_REQUEST)
            if dialogue.explicit_task_abandonment:
                signals.append(PerceptionSignal.EXPLICIT_TASK_ABANDONMENT)
            if dialogue.explicit_repair_offer:
                signals.append(PerceptionSignal.EXPLICIT_REPAIR_OFFER)
            if dialogue.self_disclosure_request:
                signals.append(PerceptionSignal.SELF_DISCLOSURE_REQUEST)
        perception = Perception(
            schema_version=PERCEPTION_SCHEMA_VERSION,
            status=status,
            topics=tuple(topics),
            signals=tuple(signals),
            confidence=0.55 if fallback_reason else 0.82,
            source_refs=source_refs,
        )
        perception_ms = (self.monotonic() - started) * 1000

        started = self.monotonic()
        needs = self._need_weights(perception, fallback=fallback_reason is not None)
        uncertainty = 0.65 if fallback_reason else self._uncertainty(perception)
        risk_flags = (
            ("distress_language_present",)
            if PerceptionSignal.DISTRESS_LANGUAGE in perception.signals
            else ()
        )
        need_mix = NeedMix(
            schema_version=NEED_MIX_SCHEMA_VERSION,
            status=status,
            needs=needs,
            uncertainty=uncertainty,
            risk_flags=risk_flags,
            source_refs=source_refs,
        )
        need_mix_ms = (self.monotonic() - started) * 1000

        started = self.monotonic()
        retrieval_plan = RetrievalPlan(
            schema_version=RETRIEVAL_PLAN_SCHEMA_VERSION,
            status=status,
            query_mode=(
                RetrievalQueryMode.CONSERVATIVE_FALLBACK
                if fallback_reason
                else RetrievalQueryMode.CURRENT_INPUT
            ),
            include_episodic=True,
            include_semantic=True,
            include_current_models=True,
            source_refs=source_refs,
        )
        retrieval_plan_ms = (self.monotonic() - started) * 1000
        return PreparedCognitionIntake(
            perception=perception,
            need_mix=need_mix,
            retrieval_plan=retrieval_plan,
            fallback_reasons=(fallback_reason,) if fallback_reason else (),
            perception_ms=perception_ms,
            need_mix_ms=need_mix_ms,
            retrieval_plan_ms=retrieval_plan_ms,
        )

    def complete(
        self,
        intake: PreparedCognitionIntake,
        *,
        interaction_id: str,
        available_evidence_ids: tuple[str, ...],
        prepared_affect: PreparedAffectiveContext | None,
        curiosity_influence: float = 0.0,
        fallback_reason: str | None = None,
    ) -> CognitionPipelineTrace:
        """Build post-appraisal planning artifacts and validate expression invariants."""

        if (
            isinstance(curiosity_influence, bool)
            or not math.isfinite(curiosity_influence)
            or not 0.0 <= curiosity_influence <= 0.20
        ):
            raise ValueError("curiosity_influence must be in [0, 0.20]")

        completion_started = self.monotonic()
        fallback_reasons = _unique(
            [*intake.fallback_reasons, *([fallback_reason] if fallback_reason else [])]
        )
        status = (
            CognitionArtifactStatus.FALLBACK
            if fallback_reasons
            else CognitionArtifactStatus.APPLIED
        )
        if any(
            artifact.status is not status
            for artifact in (
                intake.perception,
                intake.need_mix,
                intake.retrieval_plan,
            )
        ):
            intake = replace(
                intake,
                perception=replace(intake.perception, status=status),
                need_mix=replace(intake.need_mix, status=status),
                retrieval_plan=replace(intake.retrieval_plan, status=status),
                fallback_reasons=fallback_reasons,
            )

        started = self.monotonic()
        appraisal = self._appraisal_artifact(interaction_id, prepared_affect)
        appraisal_ms = (self.monotonic() - started) * 1000

        evidence_refs = _unique(
            [interaction_id, *intake.perception.source_refs, *available_evidence_ids]
        )[:16]
        started = self.monotonic()
        position = self._position(
            intake,
            appraisal,
            evidence_refs=evidence_refs,
            status=status,
        )
        position_ms = (self.monotonic() - started) * 1000

        started = self.monotonic()
        intent = self._intent(intake, position, status=status)
        intent_ms = (self.monotonic() - started) * 1000

        started = self.monotonic()
        strategy = self._strategy(
            intake,
            position,
            intent,
            status=status,
            curiosity_influence=curiosity_influence,
        )
        strategy_ms = (self.monotonic() - started) * 1000
        total_ms = (
            intake.perception_ms
            + intake.need_mix_ms
            + intake.retrieval_plan_ms
            + (self.monotonic() - completion_started) * 1000
        )
        return CognitionPipelineTrace(
            schema_version=COGNITION_PIPELINE_SCHEMA_VERSION,
            status=status,
            perception=intake.perception,
            need_mix=intake.need_mix,
            retrieval_plan=intake.retrieval_plan,
            appraisal=appraisal,
            internal_position=position,
            intent=intent,
            response_strategy=strategy,
            fallback_reasons=fallback_reasons,
            timings=CognitionStepTimings(
                perception_ms=intake.perception_ms,
                need_mix_ms=intake.need_mix_ms,
                retrieval_plan_ms=intake.retrieval_plan_ms,
                appraisal_handoff_ms=appraisal_ms,
                position_ms=position_ms,
                intent_ms=intent_ms,
                strategy_ms=strategy_ms,
                total_ms=total_ms,
            ),
        )

    @staticmethod
    def _need_weights(perception: Perception, *, fallback: bool) -> tuple[NeedWeight, ...]:
        weights: dict[NeedDimension, float] = {NeedDimension.INFORMATION: 0.45}
        topics = set(perception.topics)
        signals = set(perception.signals)
        if PerceivedTopic.TECHNICAL in topics:
            weights.update({NeedDimension.INFORMATION: 0.72, NeedDimension.ANALYSIS: 0.84})
        self_disclosure_request = PerceptionSignal.SELF_DISCLOSURE_REQUEST in signals
        if (
            PerceivedTopic.EMOTIONAL in topics or PerceptionSignal.DISTRESS_LANGUAGE in signals
        ) and not self_disclosure_request:
            weights[NeedDimension.EMOTIONAL_PRESENCE] = 0.86
            weights[NeedDimension.REASSURANCE] = 0.62
            weights[NeedDimension.INFORMATION] = min(weights[NeedDimension.INFORMATION], 0.28)
        if signals.intersection(
            {
                PerceptionSignal.EXPLICIT_LISTEN_REQUEST,
                PerceptionSignal.HIGH_DISTRESS,
            }
        ):
            weights[NeedDimension.EMOTIONAL_PRESENCE] = 0.95
            weights[NeedDimension.INFORMATION] = min(weights[NeedDimension.INFORMATION], 0.20)
        if self_disclosure_request:
            weights[NeedDimension.INFORMATION] = max(weights[NeedDimension.INFORMATION], 0.76)
        if PerceivedTopic.DECISION in topics:
            weights[NeedDimension.DECISION_SUPPORT] = 0.82
            weights[NeedDimension.ANALYSIS] = max(weights.get(NeedDimension.ANALYSIS, 0.0), 0.58)
        if PerceptionSignal.CHALLENGE_REQUEST in signals:
            weights[NeedDimension.CHALLENGE] = 0.84
            weights[NeedDimension.ANALYSIS] = max(weights.get(NeedDimension.ANALYSIS, 0.0), 0.62)
        if PerceptionSignal.CORRECTION in signals:
            weights[NeedDimension.ACCOUNTABILITY] = 0.76
        if PerceivedTopic.CREATIVE in topics:
            weights[NeedDimension.CREATIVE_COLLABORATION] = 0.82
        if fallback:
            weights = {
                NeedDimension.INFORMATION: 0.45,
                NeedDimension.EMOTIONAL_PRESENCE: 0.35,
            }
        return tuple(NeedWeight(dimension=key, weight=value) for key, value in weights.items())

    @staticmethod
    def _uncertainty(perception: Perception) -> float:
        if PerceptionSignal.UNCERTAINTY_LANGUAGE in perception.signals:
            return 0.58
        if perception.topics == (PerceivedTopic.GENERAL,) and not perception.signals:
            return 0.42
        return 0.18

    @staticmethod
    def _appraisal_artifact(
        interaction_id: str,
        prepared_affect: PreparedAffectiveContext | None,
    ) -> AppraisalArtifact:
        if prepared_affect is None:
            return AppraisalArtifact(
                schema_version=APPRAISAL_ARTIFACT_SCHEMA_VERSION,
                status=CognitionArtifactStatus.UNAVAILABLE,
                reason_code="affect_pipeline_not_configured",
                source_refs=(interaction_id,),
                appraisal_confidence=None,
                emotion_state_version=None,
                mood_state_version=None,
            )
        status = {
            EmotionAppraisalStatus.APPLIED: CognitionArtifactStatus.APPLIED,
            EmotionAppraisalStatus.SKIPPED: CognitionArtifactStatus.SKIPPED,
            EmotionAppraisalStatus.REJECTED: CognitionArtifactStatus.REJECTED,
            EmotionAppraisalStatus.UNAVAILABLE: CognitionArtifactStatus.UNAVAILABLE,
        }[prepared_affect.appraisal_status]
        proposal = prepared_affect.transition.proposal if prepared_affect.transition else None
        return AppraisalArtifact(
            schema_version=APPRAISAL_ARTIFACT_SCHEMA_VERSION,
            status=status,
            reason_code=prepared_affect.reason_code,
            source_refs=proposal.source_refs if proposal is not None else (interaction_id,),
            appraisal_confidence=(proposal.appraisal_confidence if proposal is not None else None),
            emotion_state_version=prepared_affect.expression.state_version,
            mood_state_version=prepared_affect.expression.mood_version,
        )

    def _position(
        self,
        intake: PreparedCognitionIntake,
        appraisal: AppraisalArtifact,
        *,
        evidence_refs: tuple[str, ...],
        status: CognitionArtifactStatus,
    ) -> InternalPosition:
        needs = intake.need_mix
        signals = set(intake.perception.signals)
        explicit_presence = bool(
            signals.intersection(
                {
                    PerceptionSignal.EXPLICIT_LISTEN_REQUEST,
                    PerceptionSignal.HIGH_DISTRESS,
                    PerceptionSignal.HARMFUL_OVEREXTENSION,
                }
            )
        )
        if self.intent_registry_version >= INTENT_REGISTRY_VERSION_V2 and explicit_presence:
            stance = PositionStance.LISTEN
            summary = "Prioritize explicitly requested or safety-relevant presence."
        elif (
            self.intent_registry_version >= INTENT_REGISTRY_VERSION_V2
            and PerceptionSignal.SELF_DISCLOSURE_REQUEST in signals
        ):
            stance = PositionStance.ANSWER
            summary = "Answer the explicit request about Satori from trusted self state."
        elif status is CognitionArtifactStatus.FALLBACK or needs.uncertainty >= 0.55:
            stance = PositionStance.UNCERTAIN
            summary = "Respond conservatively and make material uncertainty explicit."
        elif PerceptionSignal.CORRECTION in signals:
            stance = PositionStance.ACKNOWLEDGE
            summary = "Acknowledge the correction before addressing the current request."
        elif (
            self.intent_registry_version >= INTENT_REGISTRY_VERSION_V2
            and PerceptionSignal.EXPLICIT_TASK_ABANDONMENT in signals
        ):
            stance = PositionStance.CHALLENGE
            summary = "Challenge the stated abandonment without attacking the person."
        elif (
            self.intent_registry_version >= INTENT_REGISTRY_VERSION_V2
            and PerceptionSignal.EXPLICIT_MOTIVATION_REQUEST in signals
        ):
            stance = PositionStance.COLLABORATE
            summary = "Provide the explicitly requested bounded motivational push."
        elif needs.weight(NeedDimension.EMOTIONAL_PRESENCE) >= 0.7:
            stance = PositionStance.LISTEN
            summary = "Prioritize attentive presence before analysis or advice."
        elif needs.weight(NeedDimension.CHALLENGE) >= 0.7:
            stance = PositionStance.CHALLENGE
            summary = "Offer an honest bounded challenge instead of automatic agreement."
        elif (
            needs.weight(NeedDimension.DECISION_SUPPORT) >= 0.7
            or needs.weight(NeedDimension.CREATIVE_COLLABORATION) >= 0.7
        ):
            stance = PositionStance.COLLABORATE
            summary = "Work through the request collaboratively while preserving evidence limits."
        else:
            stance = PositionStance.ANSWER
            summary = "Address the current request directly and preserve evidence limits."
        supporting = ["current_canonical_user_request"]
        if len(evidence_refs) > len(set(intake.perception.source_refs)):
            supporting.append("bounded_prior_evidence_available")
        if appraisal.status is CognitionArtifactStatus.APPLIED:
            supporting.append("owner_approved_affect_handoff")
        concerns = ["unsupported_past_claim"]
        if needs.uncertainty >= 0.5:
            concerns.append("material_uncertainty")
        if "distress_language_present" in needs.risk_flags:
            concerns.append("distress_requires_care")
        return InternalPosition(
            schema_version=INTERNAL_POSITION_SCHEMA_VERSION,
            status=status,
            stance=stance,
            summary=summary,
            confidence=max(0.2, 1.0 - needs.uncertainty),
            supporting_point_codes=tuple(supporting),
            concern_codes=tuple(concerns),
            evidence_refs=evidence_refs,
            requires_uncertainty=(stance is PositionStance.UNCERTAIN or needs.uncertainty >= 0.5),
        )

    def _intent(
        self,
        intake: PreparedCognitionIntake,
        position: InternalPosition,
        *,
        status: CognitionArtifactStatus,
    ) -> IntentSelection:
        primary_by_stance = {
            PositionStance.ANSWER: "answer_directly",
            PositionStance.LISTEN: "listen_and_reflect",
            PositionStance.CHALLENGE: "challenge_gently",
            PositionStance.UNCERTAIN: "clarify_uncertainty",
            PositionStance.COLLABORATE: "support_decision",
            PositionStance.ACKNOWLEDGE: "acknowledge_correction",
        }
        safety_boundary = (
            self.intent_registry_version >= INTENT_REGISTRY_VERSION_V2
            and PerceptionSignal.HARMFUL_OVEREXTENSION in intake.perception.signals
        )
        repeated = (
            self.intent_registry_version >= INTENT_REGISTRY_VERSION_V2
            and PerceptionSignal.REPEATED_TURN in intake.perception.signals
        )
        repair_offer = (
            self.intent_registry_version >= INTENT_REGISTRY_VERSION_V2
            and PerceptionSignal.EXPLICIT_REPAIR_OFFER in intake.perception.signals
            and position.stance is PositionStance.ANSWER
            and not {
                PerceptionSignal.QUESTION,
                PerceptionSignal.REQUEST,
                PerceptionSignal.CORRECTION,
                PerceptionSignal.CHALLENGE_REQUEST,
            }.intersection(intake.perception.signals)
        )
        primary = (
            "hold_safety_boundary"
            if safety_boundary
            else "notice_repetition"
            if repeated
            else "receive_repair"
            if repair_offer
            else primary_by_stance[position.stance]
        )
        tags = [primary, "preserve_evidence_boundary"]
        meta_intent = safety_boundary or repeated or repair_offer
        if not meta_intent and intake.need_mix.weight(NeedDimension.ANALYSIS) >= 0.5:
            tags.append("analyze")
        if not meta_intent and intake.need_mix.weight(NeedDimension.CREATIVE_COLLABORATION) >= 0.7:
            tags.append("collaborate_creatively")
        return IntentSelection(
            schema_version=INTENT_SCHEMA_VERSION,
            registry_version=self.intent_registry_version,
            status=status,
            primary_tag=primary,
            tags=_unique(tags),
            priority=max(weight.weight for weight in intake.need_mix.needs),
            source_refs=position.evidence_refs,
        )

    @staticmethod
    def _strategy(
        intake: PreparedCognitionIntake,
        position: InternalPosition,
        intent: IntentSelection,
        *,
        status: CognitionArtifactStatus,
        curiosity_influence: float,
    ) -> ResponseStrategy:
        tone_by_stance = {
            PositionStance.ANSWER: ResponseTone.WARM_DIRECT,
            PositionStance.LISTEN: ResponseTone.WARM_GENTLE,
            PositionStance.CHALLENGE: ResponseTone.WARM_DIRECT,
            PositionStance.UNCERTAIN: ResponseTone.CONCISE_NEUTRAL,
            PositionStance.COLLABORATE: ResponseTone.ANALYTICAL,
            PositionStance.ACKNOWLEDGE: ResponseTone.WARM_DIRECT,
        }
        verbosity = (
            ResponseVerbosity.BRIEF
            if intent.primary_tag in {"notice_repetition", "hold_safety_boundary", "receive_repair"}
            else ResponseVerbosity.DETAILED
            if PerceivedTopic.TECHNICAL in intake.perception.topics
            else ResponseVerbosity.BRIEF
            if position.stance in {PositionStance.LISTEN, PositionStance.ACKNOWLEDGE}
            else ResponseVerbosity.MEDIUM
        )
        meta_intent = intent.primary_tag in {
            "notice_repetition",
            "hold_safety_boundary",
            "receive_repair",
        }
        points = (
            [intent.primary_tag] if meta_intent else [intent.primary_tag, "address_current_request"]
        )
        if not meta_intent and position.requires_uncertainty:
            points.append("state_uncertainty")
        if not meta_intent and "distress_requires_care" in position.concern_codes:
            points.append("presence_before_advice")
        inclination_allowed = (
            status is CognitionArtifactStatus.APPLIED
            and not meta_intent
            and curiosity_influence > 0.0
            and position.stance
            not in {PositionStance.LISTEN, PositionStance.ACKNOWLEDGE, PositionStance.UNCERTAIN}
            and "distress_requires_care" not in position.concern_codes
        )
        if inclination_allowed:
            points.append("topic_relevant_inclination")
        return ResponseStrategy(
            schema_version=RESPONSE_STRATEGY_SCHEMA_VERSION,
            status=status,
            position_stance=position.stance,
            preserve_uncertainty=position.requires_uncertainty,
            tone=tone_by_stance[position.stance],
            verbosity=verbosity,
            humor=0.0
            if position.stance in {PositionStance.LISTEN, PositionStance.UNCERTAIN}
            else 0.12,
            softness=0.78 if position.stance is PositionStance.LISTEN else 0.58,
            point_codes=_unique(points),
            must_not_claim=(
                "unsupported_memory",
                "hidden_user_state",
                "durable_satori_belief",
                "false_certainty",
            ),
            source_refs=position.evidence_refs,
            curiosity_influence=curiosity_influence if inclination_allowed else 0.0,
        )


@dataclass(slots=True)
class SafeCognitionPipeline:
    """Convert planner failures into one visible conservative fallback path."""

    planner: CognitionPlannerPort
    fallback: DeterministicCognitionPlanner

    def prepare_intake(
        self,
        *,
        user_text: str,
        user_message_id: str,
        interaction_id: str,
        dialogue: CognitionDialogueSignals,
    ) -> PreparedCognitionIntake:
        """Use the configured planner or return a typed fallback artifact set."""

        try:
            result = self.planner.prepare_intake(
                user_text=user_text,
                user_message_id=user_message_id,
                interaction_id=interaction_id,
                dialogue=dialogue,
            )
            if not isinstance(result, PreparedCognitionIntake):
                raise ValueError("cognition planner returned an invalid intake contract")
            self._validate_intake_refs(
                result,
                allowed_refs={interaction_id, user_message_id},
            )
            return result
        except Exception as error:
            return self.fallback.prepare_intake(
                user_text=user_text,
                user_message_id=user_message_id,
                interaction_id=interaction_id,
                dialogue=dialogue,
                fallback_reason=self._reason(error, phase="intake"),
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
        """Use the configured planner or complete through the conservative fallback."""

        try:
            result = self.planner.complete(
                intake,
                interaction_id=interaction_id,
                available_evidence_ids=available_evidence_ids,
                prepared_affect=prepared_affect,
                curiosity_influence=curiosity_influence,
            )
            if not isinstance(result, CognitionPipelineTrace):
                raise ValueError("cognition planner returned an invalid trace contract")
            self._validate_trace_refs(
                result,
                allowed_refs={
                    interaction_id,
                    *intake.perception.source_refs,
                    *available_evidence_ids,
                },
            )
            if not set(intake.fallback_reasons).issubset(result.fallback_reasons):
                raise ValueError("cognition trace discarded an intake fallback reason")
            self._validate_curiosity_projection(
                result,
                requested_influence=curiosity_influence,
            )
            return result
        except Exception as error:
            fallback_result = self.fallback.complete(
                intake,
                interaction_id=interaction_id,
                available_evidence_ids=available_evidence_ids,
                prepared_affect=prepared_affect,
                curiosity_influence=curiosity_influence,
                fallback_reason=self._reason(error, phase="completion"),
            )
            self._validate_curiosity_projection(
                fallback_result,
                requested_influence=curiosity_influence,
            )
            return fallback_result

    @staticmethod
    def _reason(error: Exception, *, phase: str) -> str:
        error_kind = "timeout" if isinstance(error, TimeoutError) else "invalid_or_failed"
        return f"{phase}_{error_kind}"

    @staticmethod
    def _validate_intake_refs(
        intake: PreparedCognitionIntake,
        *,
        allowed_refs: set[str],
    ) -> None:
        for artifact_refs in (
            intake.perception.source_refs,
            intake.need_mix.source_refs,
            intake.retrieval_plan.source_refs,
        ):
            if not set(artifact_refs).issubset(allowed_refs):
                raise ValueError("cognition intake contains an unavailable source ref")

    @staticmethod
    def _validate_trace_refs(
        trace: CognitionPipelineTrace,
        *,
        allowed_refs: set[str],
    ) -> None:
        for artifact_refs in (
            trace.perception.source_refs,
            trace.need_mix.source_refs,
            trace.retrieval_plan.source_refs,
            trace.appraisal.source_refs,
            trace.internal_position.evidence_refs,
            trace.intent.source_refs,
            trace.response_strategy.source_refs,
        ):
            if not set(artifact_refs).issubset(allowed_refs):
                raise ValueError("cognition trace contains an unavailable source ref")

    @staticmethod
    def _validate_curiosity_projection(
        trace: CognitionPipelineTrace,
        *,
        requested_influence: float,
    ) -> None:
        """Keep owner-approved curiosity bounded across an untrusted planner boundary."""

        if (
            isinstance(requested_influence, bool)
            or not math.isfinite(requested_influence)
            or not 0.0 <= requested_influence <= 0.20
        ):
            raise ValueError("curiosity_influence must be in [0, 0.20]")
        projected_influence = trace.response_strategy.curiosity_influence
        if projected_influence not in {0.0, requested_influence}:
            raise ValueError("cognition planner changed owner-approved curiosity influence")
        inclination_present = "topic_relevant_inclination" in trace.response_strategy.point_codes
        if (projected_influence > 0.0) is not inclination_present:
            raise ValueError("cognition planner curiosity point and influence disagree")
        if projected_influence > 0.0 and trace.status is not CognitionArtifactStatus.APPLIED:
            raise ValueError("fallback cognition cannot project curiosity influence")
