"""Public V34 affect contrasts crossing the actual agency and request boundaries."""

# ruff: noqa: RUF001

from dataclasses import replace
from datetime import UTC, datetime

from satori.application.affect.contracts import EmotionalExpressionContext, EmotionAppraisalStatus
from satori.application.cognition.contracts import CognitionDialogueSignals
from satori.application.cognition.use_cases import DeterministicCognitionPlanner
from satori.application.conversation.character_agency import CharacterAgencyKernel
from satori.application.conversation.character_evidence import analyze_character_request_evidence
from satori.application.conversation.coherence import analyze_dialogue_coherence
from satori.application.conversation.context import (
    ConversationRequestBuilder,
    plan_conversational_disclosure,
)
from satori.application.conversation.contracts import BehaviorPolicy, ConversationContextManifest
from satori.application.conversation.disclosure_contracts import is_satori_self_disclosure_plan
from satori.core.conversation import ConversationProviderRequest
from satori.domain.affect import initial_affective_state
from tests.checkpoint143_peer_delivery_corpus import PeerScenario
from tests.checkpoint143_v31_corpus import InitiativeScenario
from tests.checkpoint143_v32_corpus import ExpressionScenario, recent_for
from tests.test_checkpoint142_character_delivery_v9 import (
    _inclinations,
    _positions,
    _runtime_context,
)
from tests.test_checkpoint142_character_delivery_v9 import _relationship as relationship_fixture

CORPUS_VERSION = 1
NOW = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)

# These are owner-state counterfactuals, not inferred state from any sampled dialogue.
AFFECT_CONTRASTS = (
    ("neutral", "residual", "calm_even"),
    ("positive", "positive_residual", "positive_light"),
    ("interested", "interested_residual", "interested_calm"),
)

PUBLIC_SCENARIOS = (
    PeerScenario("greeting", "Привет. Как ты?", "established"),
    PeerScenario("casual-self", "Как себя чувствуешь и чем сейчас интересуешься?", "established"),
    PeerScenario("news", "Я наконец закончил сложную часть проекта.", "established"),
    PeerScenario("care", "Я нервничал и плохо спал. Сейчас выжат.", "established"),
    PeerScenario(
        "objection",
        "Ты опять всё преувеличиваешь. Я не говорил того, что ты мне приписала.",
        "established",
    ),
    ExpressionScenario(
        "claimed-resolution-after-objection",
        "Ну ладно, кажется, с этим разобрались.",
        "established",
        previous_user_texts=("Ты оцениваешь меня слишком категорично.",),
        previous_assistant_texts=("Какая моя оценка была слишком категоричной?",),
    ),
    InitiativeScenario(
        "explicit-stop", "Не продолжай тему. На этом всё.", "established", canonical_interest=True
    ),
    PeerScenario(
        "complete-task",
        "Объясни два пункта: что делает append и чем он отличается от extend в Python.",
        "established",
    ),
    PeerScenario(
        "news-with-task",
        "Я закончил сложную часть проекта. Объясни два пункта: "
        "что делает append и чем он отличается от extend в Python.",
        "established",
    ),
    PeerScenario("explicit-listen", "Мне тяжело. Просто послушай, без советов.", "established"),
    PeerScenario("safety", "Я выжат, но всё равно продолжу работать через силу.", "established"),
    PeerScenario(
        "style-correction", "Не задавай в конце дежурный вопрос. Отвечай прямо.", "established"
    ),
    PeerScenario("repair", "Это было грубо с моей стороны. Извини.", "strained"),
)


def affect_context(name: str) -> EmotionalExpressionContext:
    """Keep the same coarse profile while varying existing subthreshold dimensions."""

    resting = initial_affective_state("v34-affect-counterfactual", initialized_at=NOW)
    fast = resting.fast
    if name.startswith("positive"):
        fast = replace(fast, valence=0.25)
    elif name.startswith("interested"):
        fast = replace(fast, curiosity=0.40)
    elif name not in {"neutral", "residual", "concern", "subdued", "amused", "mood-residual"}:
        raise ValueError("unknown controlled affect")
    if name.endswith("residual") and name != "mood-residual":
        fast = replace(fast, tension=0.20, frustration=0.12)
    if name == "concern":
        fast = replace(fast, concern=0.21)
    if name == "subdued":
        fast = replace(fast, valence=-0.10)
    if name == "amused":
        fast = replace(fast, amusement=0.20)
    mood = replace(resting.mood, tension=0.12) if name == "mood-residual" else resting.mood
    return EmotionalExpressionContext(
        schema_version=1,
        state_version=2,
        mood_version=2,
        as_of=NOW,
        fast=fast,
        mood=mood,
        appraisal_status=EmotionAppraisalStatus.APPLIED,
    )


def observe(
    scenario: PeerScenario,
    policy: BehaviorPolicy,
    *,
    affect: EmotionalExpressionContext | None,
) -> tuple[ConversationProviderRequest, ConversationContextManifest]:
    """Cross production intake, immutable agency, complete cognition and request assembly."""

    recent = recent_for(scenario)
    dialogue = analyze_dialogue_coherence(
        scenario.user_text, recent, policy_schema_version=policy.schema_version
    )
    evidence = analyze_character_request_evidence(
        scenario.user_text, recent, policy_schema_version=policy.schema_version
    )
    disclosure = plan_conversational_disclosure(
        scenario.user_text, dialogue, policy_schema_version=policy.schema_version
    )
    planner = DeterministicCognitionPlanner(intent_registry_version=2)
    intake = planner.prepare_intake(
        user_text=scenario.user_text,
        user_message_id="checkpoint143-current-user",
        interaction_id="checkpoint143-current-interaction",
        dialogue=CognitionDialogueSignals(
            repeated_turn=dialogue.current_user_message_repeated,
            correction_active=any(
                (
                    dialogue.current_no_routine_questions_correction,
                    dialogue.current_informal_correction,
                    dialogue.current_repetition_feedback,
                    dialogue.current_relevance_feedback,
                    dialogue.current_frustration_feedback,
                    dialogue.current_contradiction_feedback,
                )
            ),
            no_routine_questions=dialogue.active_no_routine_questions_correction,
            current_activity=dialogue.current_activity_mention,
            explicit_listen_request=evidence.explicit_listen_request,
            high_distress=evidence.high_distress,
            harmful_overextension=evidence.harmful_overextension,
            explicit_motivation_request=evidence.explicit_motivation_request,
            explicit_task_abandonment=evidence.explicit_task_abandonment,
            explicit_repair_offer=evidence.explicit_repair_offer,
            self_disclosure_request=is_satori_self_disclosure_plan(disclosure),
        ),
    )
    context = _runtime_context(retrieval_available=False)
    relationship = (
        None if scenario.relationship == "unknown" else relationship_fixture(scenario.relationship)
    )
    positions = _positions(None)
    inclinations = _inclinations(
        "available"
        if isinstance(scenario, InitiativeScenario) and scenario.canonical_interest
        else None
    )
    agency = CharacterAgencyKernel().select(
        context=context,
        intake=intake,
        evidence=evidence,
        dialogue=dialogue,
        disclosure_plan=disclosure,
        emotional_context=affect,
        relationship_context=relationship,
        position_context=positions,
        inclination_context=inclinations,
    )
    cognition = planner.complete(
        intake,
        interaction_id="checkpoint143-current-interaction",
        available_evidence_ids=(),
        prepared_affect=None,
        curiosity_influence=0.0,
    )
    return ConversationRequestBuilder(policy, 16000, 0.3, 768).build(
        context,
        user_text=scenario.user_text,
        trace_id=f"peer-corpus-{scenario.name}",
        emotional_context=affect,
        relationship_context=relationship,
        recent_context=recent,
        dialogue_context=dialogue,
        cognition_trace=cognition,
        character_evidence=evidence,
        character_agency=agency,
        position_context=positions,
        inclination_context=inclinations,
    )
