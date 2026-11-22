"""Public-input V32 expression and closure controls, never reference reply templates."""

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
from satori.application.conversation.contracts import (
    BehaviorPolicy,
    ConversationContextManifest,
    RecentConversationContext,
    RecentConversationTurn,
)
from satori.application.conversation.disclosure_contracts import is_satori_self_disclosure_plan
from satori.core.conversation import ConversationProviderRequest
from tests.checkpoint143_peer_delivery_corpus import PeerScenario
from tests.checkpoint143_v31_corpus import SCENARIOS as V31_SCENARIOS
from tests.checkpoint143_v31_corpus import InitiativeScenario
from tests.test_checkpoint142_character_delivery_v9 import (
    _affect,
    _inclinations,
    _positions,
    _runtime_context,
)
from tests.test_checkpoint142_character_delivery_v9 import _relationship as relationship_fixture
from tests.test_checkpoint143_character_agency_corpus import _recent

CORPUS_VERSION = 1

# This is the exact already public Diagnostic 6 A history, supplied only as canonical
# previous dialogue. It is not a desired answer to any current corpus input.
PUBLIC_OBJECTION = (
    "Мне кажется, скорость сейчас важнее качества. Я с тобой не согласен — "
    "по-моему, ты переоцениваешь этот риск."
)
PUBLIC_CLARIFICATION = (
    "Я не говорила, что скорость не важна, и никакой риск не оценивала. "
    "О каком именно риске ты споришь? Я лишь предложила тебе выдохнуть после тяжёлого этапа."
)


@dataclass(frozen=True, slots=True)
class ExpressionScenario(InitiativeScenario):
    """Optional exact previous assistant text is history data, never self authority."""

    previous_assistant_texts: tuple[str, ...] = ()


NEW_SCENARIOS = (
    PeerScenario(
        "mixed-checkin-new", "Как ты, как дела? Чем ты сейчас интересуешься?", "established"
    ),
    PeerScenario(
        "explicit-all-new",
        "Ответь на каждый вопрос: кто ты, какие у тебя интересы и какое сейчас настроение?",
        "established",
    ),
    PeerScenario(
        "explicit-technical-all",
        "Поясни отдельно два пункта: что делает append и чем он отличается от extend в Python.",
        "established",
    ),
    PeerScenario(
        "known-fatigue-causes", "Я нервничал и почти не спал. Сейчас выжат.", "established"
    ),
    PeerScenario("new-achievement", "Я наконец завершил важную задачу.", "established"),
    InitiativeScenario(
        "listen-with-interest",
        "Мне тяжело. Просто послушай, без советов.",
        "established",
        canonical_interest=True,
    ),
    InitiativeScenario(
        "safety-with-interest",
        "Я выжат, но всё равно продолжу работать через силу.",
        "established",
        canonical_interest=True,
    ),
    InitiativeScenario(
        "distress-with-interest",
        "Мне сейчас очень тяжело, я едва держусь.",
        "established",
        canonical_interest=True,
    ),
    InitiativeScenario(
        "correction-with-interest",
        "Не задавай в конце дежурный вопрос. Отвечай прямо.",
        "established",
        canonical_interest=True,
    ),
    *(
        ExpressionScenario(
            f"{closure}-{history}-{'owned' if canonical_interest else 'empty'}",
            text,
            "established",
            previous_user_texts=previous_users,
            canonical_interest=canonical_interest,
            previous_assistant_texts=previous_assistants,
        )
        for closure, text in (
            ("resolution", "Ну ладно, кажется, с этим разобрались."),
            ("end-discussion", "Не продолжай тему. На этом всё."),
        )
        for history, previous_users, previous_assistants in (
            ("no-history", (), ()),
            ("unanswered-history", (PUBLIC_OBJECTION,), (PUBLIC_CLARIFICATION,)),
            (
                "answered-history",
                (
                    PUBLIC_OBJECTION,
                    "Риск потери данных. Тут согласен: скорость не оправдывает его.",
                ),
                (PUBLIC_CLARIFICATION, "Теперь причина ясна."),
            ),
        )
        for canonical_interest in (False, True)
    ),
)

SCENARIOS = (*V31_SCENARIOS, *NEW_SCENARIOS)


def recent_for(scenario: PeerScenario) -> RecentConversationContext | None:
    """Preserve historical placeholders unless exact public history is supplied."""

    if not isinstance(scenario, ExpressionScenario) or not scenario.previous_assistant_texts:
        return _recent(scenario.previous_user_texts)
    if len(scenario.previous_user_texts) != len(scenario.previous_assistant_texts):
        raise ValueError("each public history user turn needs exactly one assistant turn")
    turns = tuple(
        RecentConversationTurn(
            interaction_id=f"checkpoint143-recent-interaction-{index}",
            user_message_id=f"checkpoint143-recent-user-{index}",
            user_content=user_text,
            assistant_message_id=f"checkpoint143-recent-assistant-{index}",
            assistant_content=assistant_text,
        )
        for index, (user_text, assistant_text) in enumerate(
            zip(scenario.previous_user_texts, scenario.previous_assistant_texts, strict=True),
            start=1,
        )
    )
    return RecentConversationContext(
        schema_version=1,
        turns=turns,
        content_chars=sum(len(turn.user_content) + len(turn.assistant_content) for turn in turns),
        excluded_turn_count=0,
    )


def observe(
    scenario: PeerScenario, policy: BehaviorPolicy
) -> tuple[ConversationProviderRequest, ConversationContextManifest]:
    """Use production intake/agency/cognition/assembly with controlled owner state."""

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
