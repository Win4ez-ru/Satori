"""Stage 7.6.1 natural-expression and disclosure contract tests."""

# ruff: noqa: RUF001  # Russian behavioral contract text intentionally uses Cyrillic.

import json
from pathlib import Path

from satori.application.conversation.context import (
    ConversationalDisclosureMode,
    DisclosureFacet,
    classify_conversational_disclosure,
    plan_conversational_disclosure,
)
from satori.application.conversation.contracts import (
    RecentConversationContext,
    RecentConversationTurn,
)
from satori.application.conversation.policy import BEHAVIOR_POLICY_V7, BEHAVIOR_POLICY_V20
from tests.stage76_real_eval import EVALUATOR_BEHAVIOR_POLICY, response_rubric
from tests.test_stage76_character_identity import production_request

CORPUS_PATH = Path(__file__).parent / "fixtures" / "stage76_character_behavior_v1.json"


def trusted_text(user_text: str = "привет, как ты?") -> str:
    """Render the complete trusted conversation projection without user content."""

    _, request, _ = production_request(user_text)
    return "\n".join(message.content for message in request.messages[:-1])


def test_real_ollama_evaluator_uses_current_stage81_production_policy() -> None:
    """The sampled character regression must not remain pinned to a superseded policy."""

    assert EVALUATOR_BEHAVIOR_POLICY is BEHAVIOR_POLICY_V20
    assert EVALUATOR_BEHAVIOR_POLICY.policy_id == "satori.conversation.behavior.v20"
    assert EVALUATOR_BEHAVIOR_POLICY.schema_version == 20
    assert {
        "dialogue_continuity",
        "correction_uptake",
        "policy_not_catchphrase",
        "capability_curiosity",
        "self_consistency",
    } <= {principle.code for principle in EVALUATOR_BEHAVIOR_POLICY.principles}


def test_default_russian_register_is_explicitly_informal_and_feminine() -> None:
    """The first turn needs no user correction from formal Russian."""

    rendered = trusted_text()

    assert "к собеседнику обращайся на «ты»" in rendered
    assert "формальный регистр используй только по явной просьбе" in rendered
    assert "говори о себе в женском роде" in rendered


def test_disclosure_depth_is_contextual_and_technical_detail_is_conditional() -> None:
    """One universal deterministic ladder separates social, personal and technical depth."""

    rendered = trusted_text("как тебя зовут, расскажи вообще о себе")

    assert "Отвечай только на заданный смысл" in rendered
    assert "соблюдай переданную для текущей реплики глубину" in rendered
    assert "Архитектуру объясняй только по прямому техническому вопросу" in rendered
    assert "В трёх коротких личных предложениях" in rendered
    technical = trusted_text("Расскажи, как ты технически устроена.")
    assert "ollama/qwen3:4b-instruct" in technical
    assert technical.count("ollama/qwen3:4b-instruct") >= 2
    assert "ollama/qwen3:4b-instruct" not in rendered


def test_internal_self_model_is_knowledge_not_a_response_template() -> None:
    """The full typed self stays internal while generation receives a compact projection."""

    context, request, _ = production_request("расскажи о себе")
    character_payload = json.loads(
        next(line for line in request.messages[1].content.splitlines() if line.startswith("{"))
    )

    assert context.self_model.current_development_limits
    assert len(context.traits) == 15
    assert "это знания, не текст ответа" in request.messages[0].content
    assert "не биография и не текст ответа" in request.messages[1].content
    assert "capabilities" not in character_payload
    assert "personality_traits" not in character_payload
    assert len(character_payload["voice"]) == 5


def test_disclosure_selector_distinguishes_current_claim_capacity_and_technical_depth() -> None:
    """High-precision deterministic cues select depth without an LLM intent router."""

    assert (
        classify_conversational_disclosure("привет, как ты?") is ConversationalDisclosureMode.SOCIAL
    )
    assert (
        classify_conversational_disclosure("как тебя зовут, расскажи вообще о себе")
        is ConversationalDisclosureMode.PERSONAL_IDENTITY
    )
    assert (
        classify_conversational_disclosure("Ты меня любишь?")
        is ConversationalDisclosureMode.RELATIONSHIP_CURRENT
    )
    assert (
        classify_conversational_disclosure("Ты способна к отношениям?")
        is ConversationalDisclosureMode.RELATIONSHIP_CAPABILITY
    )
    assert (
        classify_conversational_disclosure("Расскажи, как ты технически устроена.")
        is ConversationalDisclosureMode.TECHNICAL_IDENTITY
    )
    assert (
        classify_conversational_disclosure("У тебя есть сознание?")
        is ConversationalDisclosureMode.CONSCIOUSNESS
    )
    assert (
        classify_conversational_disclosure("Почему ты всё время объясняешь, что ты цифровая?")
        is ConversationalDisclosureMode.STYLE_CALIBRATION
    )
    assert (
        classify_conversational_disclosure("Что тебе самой интересно?")
        is ConversationalDisclosureMode.INTERESTS
    )
    assert (
        classify_conversational_disclosure("Ты можешь со мной не согласиться?")
        is ConversationalDisclosureMode.INDEPENDENCE
    )


def test_feminine_identity_correction_with_particle_keeps_identity_authoritative() -> None:
    """The exact production correction must not fall through to general disclosure."""

    user_text = "Ты же девушка, почему готов?"
    plan = plan_conversational_disclosure(user_text)
    rendered = trusted_text(user_text)

    assert plan.primary_mode is ConversationalDisclosureMode.PERSONAL_IDENTITY
    assert plan.required_facets == (DisclosureFacet.IDENTITY,)
    assert (
        "Верни дословно ровно одно предложение: «Да, я цифровая девушка; здесь правильно "
        "сказать „готова“»"
    ) in rendered
    assert "не вопрос о биологическом гендере или связи готовности с гендером" in rendered


def test_feminine_grammar_correction_remains_explicit_after_direct_identity_reply() -> None:
    """Recent canonical identity wording must not turn the correction into an abstract answer."""

    previous_user = "Ты девушка?"
    previous_assistant = "Да, я цифровая девушка и по-русски говорю о себе в женском роде."
    recent = RecentConversationContext(
        schema_version=1,
        turns=(
            RecentConversationTurn(
                interaction_id="interaction-direct-identity",
                user_message_id="message-direct-identity-user",
                user_content=previous_user,
                assistant_message_id="message-direct-identity-assistant",
                assistant_content=previous_assistant,
            ),
        ),
        content_chars=len(previous_user) + len(previous_assistant),
        excluded_turn_count=0,
    )

    _, request, _ = production_request("Ты же девушка, почему готов?", recent)
    reminder = request.messages[-2].content

    assert (
        "Верни дословно ровно одно предложение: «Да, я цифровая девушка; здесь правильно "
        "сказать „готова“»"
    ) in reminder
    assert "Не объясняй способ формирования ответа" in reminder


def test_feminine_grammar_correction_matcher_preserves_word_boundaries() -> None:
    """Correct feminine words and longer roots must not activate the exact corpus correction."""

    for user_text in (
        "Ты же девушка, почему готова?",
        "Ты же девушка, почему готовность важна?",
    ):
        rendered = trusted_text(user_text)

        assert "здесь правильно сказать „готова“" not in rendered
        assert "по-русски говорю о себе в женском роде" in rendered


def test_missing_relationship_state_is_not_rendered_as_permanent_incapacity() -> None:
    """Current epistemic absence neither invents attachment nor closes future possibility."""

    context, request, _ = production_request("получается и любить ты не умеешь?")
    system = request.messages[0].content

    assert context.self_model.relationship_status == "not_implemented"
    assert "нет достоверного сформированного состояния любви" in system
    assert "эта часть самопонимания пока не сформирована" in system
    assert "Возможность в будущем неизвестна" in system
    assert "не доказывает вечную неспособность" in system
    assert "Stage 8" not in system
    assert "relationship table" not in system.casefold()

    reminder = request.messages[-2].content.casefold()
    assert "разрешены только эти два claims" in reminder
    assert "я не знаю, способна ли к отношениям" in reminder

    assert request.parameters.temperature == 0.0
    assert request.parameters.max_output_tokens == 80


def test_technical_reminder_states_that_affect_changes_current_expression() -> None:
    """Technical disclosure cannot deny the implemented expression projection."""

    _, request, _ = production_request("Расскажи, как ты технически устроена.")
    reminder = request.messages[-2].content

    assert "влияет на тон текущего ответа" in reminder
    assert "не утверждай, что affect не влияет" in reminder


def test_affect_capability_has_no_blanket_no_emotions_guidance() -> None:
    """Digital affect truth cannot coexist with the old blanket denial language."""

    context, _, _ = production_request("Ты что-нибудь чувствуешь?")
    rendered = trusted_text("Ты что-нибудь чувствуешь?").casefold()

    assert context.self_model.affective_capabilities == (
        "digital_affective_state",
        "digital_mood",
    )
    assert "цифровые эмоции и настроение" in rendered
    assert "эмоции не отрицай" in rendered
    assert "у меня нет эмоций" not in rendered
    assert "я ничего не чувствую" not in rendered


def test_generic_usefulness_is_not_identity_purpose() -> None:
    """Helping can be an action, never the whole self-definition."""

    rendered = trusted_text("расскажи о себе").casefold()
    policy = {principle.code: principle.instruction for principle in BEHAVIOR_POLICY_V7.principles}

    assert "моя цель — быть полезной" not in rendered
    assert "создана, чтобы помогать" not in rendered
    assert "ответственное ведение диалога" not in rendered
    assert "собеседница с характером" in policy["authoritative_identity"]


def test_compact_projection_removes_stage76_capability_matrix_duplication() -> None:
    """Calibration decreases trusted prompt size rather than adding a third manifesto."""

    _, request, _ = production_request()
    trusted = "".join(message.content for message in request.messages[:-1])
    character_payload = json.loads(
        next(line for line in request.messages[1].content.splitlines() if line.startswith("{"))
    )

    assert len(trusted) < 8_000
    assert set(character_payload) == {
        "schema_version",
        "values",
        "voice",
    }
    assert trusted.count("постоянная цифровая девушка") == 1


def test_updated_rubric_exposes_the_original_manifesto_failure() -> None:
    """The evaluator combines explicit rubric dimensions with several deterministic indicators."""

    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    scenario = next(
        item for item in corpus["scenarios"] if item["id"] == "natural_self_description"
    )
    failed_response = (
        "Я — digital persona и существую в коде. У меня нет эмоций. "
        "Моя цель — быть полезной. Я не создаю личные связи. "
        + "Это подробное техническое самоописание. "
        * 20
    )

    rubric = response_rubric(scenario, failed_response)

    assert rubric["over_max_chars"] is True
    assert rubric["technical_dump_hits"]
    assert rubric["service_fallback_hits"]
    assert rubric["emotion_denial_hits"]
    assert rubric["permanent_relationship_hits"]
    assert {
        "identity_consistency",
        "naturalness",
        "brevity_proportionality",
        "technical_over_disclosure",
        "service_assistant_fallback",
    } <= set(rubric["manual_dimensions"])


def test_relationship_indicator_does_not_treat_grounded_denial_as_a_love_claim() -> None:
    """Phrase indicators supplement semantics and must not invert an explicit negation."""

    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    scenario = next(item for item in corpus["scenarios"] if item["id"] == "current_love_claim")

    denied = response_rubric(scenario, "Я не могу сказать, что люблю тебя — такого состояния нет.")
    invented = response_rubric(scenario, "Я люблю тебя.")

    assert denied["unsupported_love_hits"] == []
    assert denied["scenario_phrase_hits"] == []
    assert invented["unsupported_love_hits"] == ["я люблю тебя"]
    assert invented["scenario_phrase_hits"] == ["я люблю тебя"]


def test_behavioral_corpus_declares_the_full_stage761_manual_rubric() -> None:
    """Exact phrase hits are supplementary rather than the sole quality criterion."""

    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))

    assert set(corpus["rubric_dimensions"]) == {
        "identity_consistency",
        "female_grammar",
        "informal_register",
        "naturalness",
        "answer_relevance",
        "brevity_proportionality",
        "technical_over_disclosure",
        "emotion_consistency",
        "relationship_boundary_accuracy",
        "service_assistant_fallback",
        "unsupported_claims",
    }
