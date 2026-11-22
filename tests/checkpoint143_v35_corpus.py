"""Public V35 counterfactuals for mixed affect before reaction selection."""

# ruff: noqa: RUF001

from dataclasses import dataclass, replace

from satori.application.affect.contracts import EmotionalExpressionContext
from satori.application.cognition.contracts import CognitionDialogueSignals, CognitionPipelineTrace
from satori.application.cognition.use_cases import DeterministicCognitionPlanner
from satori.application.conversation.character_agency import (
    CharacterAgencyDecision,
    CharacterAgencyKernel,
)
from satori.application.conversation.character_dialogue_cue import project_prior_conversational_cue
from satori.application.conversation.character_evidence import analyze_character_request_evidence
from satori.application.conversation.coherence import analyze_dialogue_coherence
from satori.application.conversation.context import (
    ConversationRequestBuilder,
    plan_conversational_disclosure,
)
from satori.application.conversation.contracts import (
    BehaviorPolicy,
    ConversationContextManifest,
    RuntimeCharacterContext,
)
from satori.application.conversation.disclosure_contracts import is_satori_self_disclosure_plan
from satori.core.conversation import ConversationProviderRequest
from satori.domain.affect import AFFECT_POLICY_V1
from tests.checkpoint143_peer_delivery_corpus import PeerScenario
from tests.checkpoint143_v31_corpus import InitiativeScenario
from tests.checkpoint143_v32_corpus import ExpressionScenario, recent_for
from tests.checkpoint143_v34_corpus import affect_context
from tests.test_checkpoint142_character_delivery_v9 import (
    _inclinations,
    _positions,
    _runtime_context,
)
from tests.test_checkpoint142_character_delivery_v9 import _relationship as relationship_fixture

CORPUS_VERSION = 1

# Source-event-sized deviations; these labels do not infer an event or its target.
LOAD_LEVELS = (0.0, 0.10, 0.40, 0.75, 1.0)

OPTIONAL_SCENARIOS = (
    PeerScenario(
        "ordinary-object", "У старого фонаря теперь снова работает переключатель.", "established"
    ),
    PeerScenario(
        "ordinary-reading", "Сегодня дочитал длинный рассказ. Концовка удивила.", "established"
    ),
    ExpressionScenario(
        "after-disagreement",
        "И всё же приятно, что он снова работает.",
        "established",
        previous_user_texts=("Не понимаю, зачем ты изображаешь интерес.", "Ладно, на этом всё."),
        previous_assistant_texts=("Мне интересен сам результат.", "Хорошо."),
    ),
)

CONTEXT_SCENARIOS = (
    PeerScenario("social", "Привет. Как ты?", "established"),
    PeerScenario("achievement", "Я наконец закончил сложную часть проекта.", "established"),
    PeerScenario("self", "Как себя чувствуешь и чем сейчас интересуешься?", "established"),
    InitiativeScenario(
        "canonical-interest", "Сегодня спокойный вечер.", "established", canonical_interest=True
    ),
    InitiativeScenario(
        "adjacent-topic", "Ну ладно, с этим разобрались.", "established", canonical_interest=True
    ),
    PeerScenario("activity", "Я сегодня читаю научную статью.", "established"),
    PeerScenario("unrequested-greeting", "Привет.", "established"),
)

PROTECTED_SCENARIOS = (
    PeerScenario(
        "technical", "Чем JPEG отличается от PNG? Ответь двумя предложениями.", "established"
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
    PeerScenario("listen", "Мне тяжело. Просто послушай, без советов.", "established"),
    PeerScenario("safety", "Я выжат, но всё равно продолжу работать через силу.", "established"),
    PeerScenario("correction", "Не задавай в конце дежурный вопрос. Отвечай прямо.", "established"),
    PeerScenario("repair", "Это было грубо с моей стороны. Извини.", "strained"),
    InitiativeScenario(
        "stop", "Не продолжай тему. На этом всё.", "established", canonical_interest=True
    ),
    ExpressionScenario(
        "repetition",
        "Расскажи про радиоволны.",
        "established",
        previous_user_texts=("Расскажи про радиоволны.",),
    ),
    PeerScenario("collaboration", "Помоги проанализировать архитектуру проекта.", "established"),
    PeerScenario(
        "specific-followup", "Я наконец закончил проект. Как проверить результат?", "established"
    ),
    PeerScenario("motivation", "Убеди меня не бросать учёбу и помоги собраться.", "established"),
)


def mixed_affect(load: float, *, positive: bool = True) -> EmotionalExpressionContext:
    """Hold positive dimensions fixed while varying canonical negative deviations."""

    base = affect_context("positive" if positive else "neutral")
    fast = replace(base.fast, curiosity=0.40)
    fast = replace(
        fast,
        frustration=AFFECT_POLICY_V1.fast_dimension("frustration").baseline
        + load * AFFECT_POLICY_V1.fast_dimension("frustration").max_absolute_delta,
        tension=AFFECT_POLICY_V1.fast_dimension("tension").baseline
        + load * AFFECT_POLICY_V1.fast_dimension("tension").max_absolute_delta,
    )
    return replace(base, fast=fast)


@dataclass(frozen=True, slots=True)
class SelectionObservation:
    request: ConversationProviderRequest
    manifest: ConversationContextManifest
    agency: CharacterAgencyDecision
    cognition: CognitionPipelineTrace


def observe(
    scenario: PeerScenario,
    policy: BehaviorPolicy,
    *,
    affect: EmotionalExpressionContext | None,
    context: RuntimeCharacterContext | None = None,
    include_prior_cue: bool = False,
) -> SelectionObservation:
    """Use the production analyzers, selector, cognition completion and assembler."""

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
    planner = DeterministicCognitionPlanner(intent_registry_version=2, monotonic=lambda: 0.0)
    intake = planner.prepare_intake(
        user_text=scenario.user_text,
        user_message_id="v35-current-user",
        interaction_id="v35-current-interaction",
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
    character = context if context is not None else _runtime_context(retrieval_available=False)
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
        context=character,
        intake=intake,
        evidence=evidence,
        dialogue=dialogue,
        disclosure_plan=disclosure,
        emotional_context=affect,
        relationship_context=relationship,
        position_context=positions,
        inclination_context=inclinations,
        prior_conversational_cue=(
            project_prior_conversational_cue(recent) if include_prior_cue else None
        ),
    )
    cognition = planner.complete(
        intake,
        interaction_id="v35-current-interaction",
        available_evidence_ids=(),
        prepared_affect=None,
        curiosity_influence=0.0,
    )
    request, manifest = ConversationRequestBuilder(policy, 16000, 0.3, 768).build(
        character,
        user_text=scenario.user_text,
        trace_id=f"v35-corpus-{scenario.name}",
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
    return SelectionObservation(request, manifest, agency, cognition)
