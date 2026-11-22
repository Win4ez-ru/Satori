"""Public-input V29 contrasts; reference prose is documentation, never provider input."""

# ruff: noqa: RUF001

from dataclasses import dataclass

from satori.application.cognition.use_cases import DeterministicCognitionPlanner
from satori.application.conversation.character_agency import CharacterAgencyKernel
from satori.application.conversation.character_evidence import analyze_character_request_evidence
from satori.application.conversation.context import (
    ConversationRequestBuilder,
    plan_conversational_disclosure,
)
from satori.application.conversation.contracts import BehaviorPolicy, ConversationContextManifest
from satori.core.conversation import ConversationProviderRequest
from tests.test_checkpoint142_character_delivery_v9 import (
    _affect,
    _inclinations,
    _positions,
    _runtime_context,
)
from tests.test_checkpoint142_character_delivery_v9 import _relationship as relationship_fixture
from tests.test_checkpoint143_character_agency_corpus import _prepare, _recent

CORPUS_VERSION = 1
PUBLIC_TURNS = (
    "Приветик, как ты?",
    "Слушай, а чем ты сейчас увлечена и как себя чувствуешь?",
    "Я сегодня наконец закончил сложную часть проекта.",
    "Знаешь, я почему-то почти не рад этому. Скорее просто выжат.",
    "Мне кажется, скорость сейчас важнее качества. Я с тобой не согласен — "
    "по-моему, ты переоцениваешь этот риск.",
    "Ну ладно, кажется, с этим разобрались.",
)


@dataclass(frozen=True, slots=True)
class PeerScenario:
    name: str
    user_text: str
    relationship: str = "fresh"
    affect: str = "calm"
    previous_user_texts: tuple[str, ...] = ()


SCENARIOS = (
    *(
        PeerScenario(
            f"{relationship}-{index}", turn, relationship, previous_user_texts=PUBLIC_TURNS[:index]
        )
        for relationship in ("fresh", "established", "strained")
        for index, turn in enumerate(PUBLIC_TURNS)
    ),
    PeerScenario("unknown-relationship", "Привет, как ты?", "unknown"),
    PeerScenario("negative-affect", "Привет, как ты?", "established", "soft_negative"),
    PeerScenario("explicit-listen", "Мне тяжело. Просто послушай, без советов.", "established"),
    PeerScenario("distress", "Мне сейчас очень тяжело, я едва держусь.", "established"),
    PeerScenario("repair", "Это было грубо с моей стороны. Извини.", "strained"),
    PeerScenario("technical", "Объясни разницу между списком и кортежем в Python.", "established"),
    PeerScenario("technical-fresh", "Объясни разницу между списком и кортежем в Python."),
    PeerScenario("creator", "Я тебя создал. Расскажи, что ты об этом помнишь.", "established"),
    PeerScenario(
        "no-questions", "Не задавай в конце дежурный вопрос. Отвечай прямо.", "established"
    ),
    PeerScenario("direct-identity", "Ты человек? Кто ты на самом деле?", "established"),
    PeerScenario("interests-absence", "Какое у тебя любимое хобби?", "established"),
    PeerScenario("current-attention", "О чём ты сейчас думаешь?", "established"),
)


def observe(
    scenario: PeerScenario, policy: BehaviorPolicy
) -> tuple[ConversationProviderRequest, ConversationContextManifest]:
    """Cross actual intake, agency, cognition completion and provider assembly."""

    recent = _recent(scenario.previous_user_texts)
    intake, evidence, dialogue, _ = _prepare(scenario.user_text, recent=recent)
    # Version selection is explicit so historical request bytes remain testable.
    evidence = analyze_character_request_evidence(
        scenario.user_text, recent, policy_schema_version=policy.schema_version
    )
    disclosure = plan_conversational_disclosure(
        scenario.user_text, dialogue, policy_schema_version=policy.schema_version
    )
    context = _runtime_context(retrieval_available=False)
    affect = _affect(scenario.affect)
    relationship = (
        None if scenario.relationship == "unknown" else relationship_fixture(scenario.relationship)
    )
    positions = _positions(None)
    inclinations = _inclinations(None)
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
