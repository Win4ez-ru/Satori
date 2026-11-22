"""Versioned initiative realization controls; public inputs, no reference answer prose."""

# ruff: noqa: RUF001
from dataclasses import dataclass

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
from tests.checkpoint143_peer_delivery_corpus import PeerScenario
from tests.checkpoint143_v30_corpus import SCENARIOS as V30_SCENARIOS
from tests.test_checkpoint142_character_delivery_v9 import (
    _affect,
    _inclinations,
    _positions,
    _runtime_context,
)
from tests.test_checkpoint142_character_delivery_v9 import _relationship as relationship_fixture
from tests.test_checkpoint143_character_agency_corpus import _recent

CORPUS_VERSION = 1


@dataclass(frozen=True, slots=True)
class InitiativeScenario(PeerScenario):
    canonical_interest: bool = False


SCENARIOS = (
    *V30_SCENARIOS,
    PeerScenario("activity-open", "Я сейчас смотрю фильм.", "established"),
    PeerScenario("activity-fresh", "Я сейчас смотрю фильм.", "fresh"),
    PeerScenario("activity-strained", "Я сейчас смотрю фильм.", "strained"),
    PeerScenario("activity-negative", "Я сейчас смотрю фильм.", "established", "soft_negative"),
    PeerScenario(
        "activity-corrected",
        "Я сейчас смотрю фильм.",
        "established",
        previous_user_texts=("Не задавай в конце дежурный вопрос. Отвечай прямо.",),
    ),
    PeerScenario(
        "achievement-corrected",
        "Я закончил сложную часть проекта.",
        "established",
        previous_user_texts=("Не задавай в конце дежурный вопрос. Отвечай прямо.",),
    ),
    PeerScenario("achievement-tired", "Я закончил проект, но я выжат.", "established"),
    PeerScenario(
        "achievement-negative", "Я закончил сложную часть проекта.", "established", "soft_negative"
    ),
    PeerScenario(
        "achievement-listen",
        "Я закончил проект, но мне тяжело. Просто послушай, без советов.",
        "established",
    ),
    PeerScenario("creative-request", "Помоги придумать три идеи для проекта.", "established"),
    PeerScenario("motivation-request", "Подбодри меня начать работу над проектом.", "established"),
    PeerScenario("precise-question", "Почему в Python список изменяемый?", "established"),
    InitiativeScenario(
        "owned-topic",
        "Ну ладно, кажется, с этим разобрались.",
        "established",
        canonical_interest=True,
    ),
    InitiativeScenario(
        "owned-topic-strained",
        "Ну ладно, кажется, с этим разобрались.",
        "strained",
        canonical_interest=True,
    ),
    InitiativeScenario(
        "owned-stop", "Не продолжай тему. На этом всё.", "established", canonical_interest=True
    ),
    InitiativeScenario(
        "owned-contribution", "Сегодня всё шло спокойно.", "established", canonical_interest=True
    ),
)


def observe(
    scenario: PeerScenario, policy: BehaviorPolicy
) -> tuple[ConversationProviderRequest, ConversationContextManifest]:
    """Cross actual intake, agency, cognition completion and provider assembly."""

    recent = _recent(scenario.previous_user_texts)
    dialogue = analyze_dialogue_coherence(scenario.user_text, recent)
    # Version selection is explicit so historical request bytes remain testable.
    evidence = analyze_character_request_evidence(
        scenario.user_text, recent, policy_schema_version=policy.schema_version
    )
    disclosure = plan_conversational_disclosure(
        scenario.user_text, dialogue, policy_schema_version=policy.schema_version
    )
    intake = DeterministicCognitionPlanner(intent_registry_version=2).prepare_intake(
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
    affect = _affect(scenario.affect)
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
    cognition = DeterministicCognitionPlanner(intent_registry_version=2).complete(
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
