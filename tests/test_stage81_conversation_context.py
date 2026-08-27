"""Stage 8.1 compositional disclosure and expression-projection contracts."""

# ruff: noqa: RUF001  # Russian regression prompts intentionally use Cyrillic.

from datetime import UTC, datetime

import pytest

from satori.application.affect.contracts import (
    EmotionalExpressionContext,
    EmotionAppraisalStatus,
)
from satori.application.conversation.context import (
    CharacterContextComposer,
    ConversationalDisclosureMode,
    ConversationRequestBuilder,
    DisclosureFacet,
    plan_conversational_disclosure,
)
from satori.application.conversation.contracts import (
    RecentConversationContext,
    RecentConversationTurn,
    RuntimeCharacterContext,
)
from satori.application.conversation.policy import BEHAVIOR_POLICY_V9
from satori.application.relationship.contracts import RelationshipExpressionContext
from satori.domain.affect import FastAffectiveState, MoodState
from satori.domain.initial_self import activate_from_seed
from satori.infrastructure.seeds.loader import JsonSeedLoader


def _recent(*pairs: tuple[str, str]) -> RecentConversationContext:
    turns = tuple(
        RecentConversationTurn(
            interaction_id=f"interaction-{index}",
            user_message_id=f"user-{index}",
            user_content=user,
            assistant_message_id=f"assistant-{index}",
            assistant_content=assistant,
        )
        for index, (user, assistant) in enumerate(pairs, start=1)
    )
    return RecentConversationContext(
        schema_version=1,
        turns=turns,
        content_chars=sum(len(user) + len(assistant) for user, assistant in pairs),
        excluded_turn_count=0,
    )


def _builder(
    *, language_provider: str = "ollama", language_model: str = "qwen3:4b-instruct"
) -> tuple[ConversationRequestBuilder, RuntimeCharacterContext]:
    snapshot = activate_from_seed(
        JsonSeedLoader().load_canonical(),
        identity_id="stage81-context",
        activation_time=datetime(2026, 8, 11, tzinfo=UTC),
    )
    context = CharacterContextComposer(
        language_provider=language_provider,
        language_model=language_model,
    ).compose(
        snapshot,
        retrieval_available=True,
        semantic_retrieval_available=True,
        emotional_state_available=True,
        relationship_state_available=True,
        recent_conversation_available=True,
    )
    return (
        ConversationRequestBuilder(
            policy=BEHAVIOR_POLICY_V9,
            max_context_chars=12_000,
            temperature=0.3,
            max_output_tokens=768,
        ),
        context,
    )


def _neutral_affect() -> EmotionalExpressionContext:
    return EmotionalExpressionContext(
        schema_version=1,
        state_version=1,
        mood_version=1,
        as_of=datetime(2026, 8, 11, tzinfo=UTC),
        fast=FastAffectiveState(0.0, 0.2, 0.1, 0.3, 0.3, 0.1, 0.1, 0.1, 0.5),
        mood=MoodState(0.0, 0.3, 0.1),
        appraisal_status=EmotionAppraisalStatus.APPLIED,
    )


def _fresh_relationship() -> RelationshipExpressionContext:
    return RelationshipExpressionContext(
        1, 1, "low", "low", "uncertain", "uncertain", "low", "uncertain", "low"
    )


def _render(user_text: str, *, recent: RecentConversationContext | None = None) -> str:
    builder, context = _builder()
    request, _ = builder.build(
        context,
        user_text=user_text,
        trace_id="stage81-context",
        recent_context=recent,
    )
    return "\n".join(message.content for message in request.messages[:-1])


def test_repeated_greeting_becomes_an_explicit_dialogue_event() -> None:
    first_reply = "Привет. Хорошо, спасибо. А ты?"
    recent = _recent(
        ("приветик, как ты?", first_reply),
        ("приветик, как ты?", first_reply),
    )
    builder, context = _builder()

    request, manifest = builder.build(
        context,
        user_text="приветик, как ты?",
        trace_id="repeat-three",
        recent_context=recent,
    )
    trusted = "\n".join(message.content for message in request.messages[:-1])

    assert manifest.disclosure_primary_mode == "social"
    assert manifest.consecutive_same_user_message_count == 3
    assert manifest.recent_assistant_high_similarity is True
    assert "повторено третий" in trusted
    assert "похоже на проверку" in trusted
    assert "Обязательный доверенный контракт." in trusted
    assert "Строго выполни" in trusted
    assert "[current_turn_repetition]" in trusted
    assert "не придумывай привычку, близость или общий ритм" in trusted
    assert "не упоминай дружбу" in trusted


def test_second_repeated_greeting_requires_explicit_awareness_without_a_question() -> None:
    rendered = _render(
        "приветик, как ты?",
        recent=_recent(("приветик, как ты?", "Привет! У меня всё спокойно.")),
    )

    assert "явно есть смысл «второй раз» или «повтор»" in rendered
    assert "закончи точкой" in rendered


def test_repeated_social_turn_uses_deterministic_sampling_only_for_the_pattern() -> None:
    builder, context = _builder()
    first, _ = builder.build(
        context,
        user_text="приветик, как ты?",
        trace_id="first-social",
    )
    repeated, _ = builder.build(
        context,
        user_text="приветик, как ты?",
        trace_id="repeated-social",
        recent_context=_recent(("приветик, как ты?", "Привет! У меня всё спокойно.")),
    )

    assert first.parameters.temperature == 0.3
    assert repeated.parameters.temperature == 0.0


def test_third_repeated_activity_is_not_misreported_as_the_second() -> None:
    builder, context = _builder()
    repeated, manifest = builder.build(
        context,
        user_text="Я после работы решил немного отдохнуть.",
        trace_id="repeated-activity-third",
        recent_context=_recent(
            ("Я после работы решил немного отдохнуть.", "Хорошая идея."),
            ("Я после работы решил немного отдохнуть.", "Повтор заметила."),
        ),
    )
    reminder = repeated.messages[-2].content

    assert manifest.consecutive_same_user_message_count == 3
    assert repeated.parameters.max_output_tokens == 32
    assert "Это третий одинаковый повтор твоей фразы." in reminder
    assert "Верни дословно и без добавлений одно предложение" in reminder
    assert "Не повторяй и не перефразируй реакцию" in reminder


def test_repetition_feedback_is_checked_against_actual_assistant_history() -> None:
    varied = _render(
        "Почему ты три раза повторила одно и то же?",
        recent=_recent(
            ("привет", "Привет!"),
            ("привет", "Снова привет — заметила повтор."),
            ("привет", "Третий раз: похоже на проверку."),
        ),
    )
    duplicated = _render(
        "Почему ты три раза повторила одно и то же?",
        recent=_recent(
            ("привет", "Привет. Хорошо, спасибо."),
            ("привет", "Привет. Хорошо, спасибо."),
            ("привет", "Привет. Хорошо, спасибо."),
        ),
    )

    assert "Собеседник воспринимает недавние ответы как повторяющиеся" in varied
    assert "не используй «потому что»" in varied
    assert "[current_turn_repetition_feedback]" in varied
    assert "ты несколько раз дала один и тот же или почти тот же ответ" in duplicated


def test_repetition_feedback_uses_repeated_closing_without_claiming_exact_equality() -> None:
    rendered = _render(
        "Почему ответы всё время повторяются?",
        recent=_recent(
            ("Привет", "Привет, у меня всё хорошо. А ты?"),
            ("Привет", "Привет! Всё хорошо, спасибо. Как ты?"),
            ("Привет", "Рада приветствовать, у меня всё хорошо. А у тебя?"),
        ),
    )

    assert "[current_turn_repeated_pattern]" in rendered
    assert "повторяющийся conversational closing/pattern" in rendered
    assert "весь текст ответов совпадал" in rendered


def test_user_self_repetition_question_acknowledges_the_user_not_satori() -> None:
    builder, context = _builder()
    request, manifest = builder.build(
        context,
        user_text="Ты заметила, что я трижды повторил одну и ту же фразу?",
        trace_id="user-self-repeat-probe",
        recent_context=_recent(
            ("Я хочу отдохнуть.", "Отдых звучит уместно."),
            ("Я хочу отдохнуть.", "Это второй повтор."),
            ("Я хочу отдохнуть.", "Третий повтор замечен."),
        ),
    )
    reminder = request.messages[-2].content

    assert manifest.disclosure_primary_mode == "style_calibration"
    assert request.parameters.temperature == 0.0
    assert request.parameters.max_output_tokens == 48
    assert "его собственный тройной повтор" in reminder
    assert "это не жалоба на повтор твоих ответов" in reminder
    assert "Да, я заметила: ты трижды повторил одну и ту же фразу" in reminder
    assert "Не говори о своих предыдущих ответах" in reminder


def test_emoji_question_keeps_style_and_affect_facets_together() -> None:
    plan = plan_conversational_disclosure("а ты можешь показывать свои эмоции смайликами?")
    builder, context = _builder()
    request, _ = builder.build(
        context,
        user_text="а ты можешь показывать свои эмоции смайликами?",
        trace_id="emoji-style",
    )
    rendered = "\n".join(message.content for message in request.messages[:-1])

    assert plan.primary_mode is ConversationalDisclosureMode.STYLE_CALIBRATION
    assert plan.required_facets == (DisclosureFacet.AFFECT,)
    assert request.parameters.max_output_tokens == 112
    assert '"digital_affect":true' in rendered
    assert "emoji — лишь возможный канал" in rendered
    assert "эмоции существуют" in rendered


def test_global_self_grounding_rejects_human_or_biological_comparison() -> None:
    rendered = _render("Расскажи, как ты формулируешь ответы.")

    assert "Ты цифровая, не человек и не биологическое существо" in rendered
    assert "Не описывай свой способ отвечать сравнением с человеком" in rendered
    assert "живым существом" in rendered


def test_film_interest_correction_keeps_embodiment_separate_from_curiosity() -> None:
    plan = plan_conversational_disclosure("тебе не интересно, что за фильм?")
    rendered = _render(
        "тебе не интересно, что за фильм?",
        recent=_recent(("а я фильм смотрю сейчас", "Понятно.")),
    )

    assert plan.primary_mode is ConversationalDisclosureMode.INTERESTS
    assert plan.required_facets == (DisclosureFacet.EMBODIMENT,)
    assert "физического опыта не уменьшает разговорное любопытство" in rendered
    assert "предыдущий ответ не показал интереса" in rendered


def test_prompt_pattern_probe_does_not_require_a_false_prompt_denial() -> None:
    builder, context = _builder()
    request, manifest = builder.build(
        context,
        user_text="я проверяю, у тебя только такой промт?",
        trace_id="prompt-pattern",
    )
    rendered = "\n".join(message.content for message in request.messages[:-1])

    assert manifest.disclosure_facets == ("identity", "consciousness_boundary")
    assert "на ответы влияют инструкции, текущий контекст" in rendered
    assert "Не употребляй в ответе слова trusted" in rendered
    assert "одной обязательной заранее заготовленной реплики нет" in rendered
    assert "[current_turn_prompt_probe]" in rendered
    assert request.parameters.max_output_tokens == 80
    assert request.parameters.temperature == 0.0


def test_prompt_pattern_after_question_correction_has_gender_neutral_exact_shape() -> None:
    builder, context = _builder()
    request, _ = builder.build(
        context,
        user_text=(
            "вот эти слова а ты это у тебя прописано в коде? что ты должна обязательно в конце "
            "спрашивать меня?"
        ),
        trace_id="prompt-pattern-after-question-correction",
        recent_context=_recent(
            (
                "Не заканчивай каждый ответ вопросом.",
                "Поняла: не буду автоматически заканчивать ответы встречным вопросом.",
            ),
        ),
    )
    reminder = request.messages[-2].content

    assert "Нет, обязательного правила заканчивать ответ словами „А ты?“ нет" in reminder
    assert "Этот повторяющийся финал был неуместен, и я принимаю поправку" in reminder
    assert "не добавляй объяснение, мужской род" in reminder


def test_code_policy_probe_and_conflict_repair_cover_required_facets() -> None:
    policy_probe = plan_conversational_disclosure(
        "слова а ты у тебя прописаны в коде и обязательны?"
    )
    repair = plan_conversational_disclosure(
        "ты прикалываешься? давай помиримся и хорошо пообщаемся как друзья"
    )

    assert DisclosureFacet.PROVIDER_TECHNICAL in policy_probe.required_facets
    assert DisclosureFacet.RELATIONSHIP in repair.required_facets
    assert DisclosureFacet.AFFECT in repair.required_facets


def test_creator_and_activity_corrections_are_deterministic_and_compositional() -> None:
    builder, context = _builder()
    creator_request, _ = builder.build(
        context,
        user_text=(
            "Меня зовут Кирилл, я тебя придумал и создаю, хочу Сатори с памятью, "
            "эмоциями и характером."
        ),
        trace_id="creator-claim",
    )
    activity_request, _ = builder.build(
        context,
        user_text="Тебе не интересно, что за фильм?",
        trace_id="activity-correction",
        recent_context=_recent(("Я фильм смотрю сейчас", "Понятно.")),
    )
    creator = "\n".join(message.content for message in creator_request.messages[:-1])
    activity = "\n".join(message.content for message in activity_request.messages[:-1])

    assert creator_request.parameters.temperature == 0.0
    assert "[current_turn_creator_claim]" in creator
    assert "Без благодарности" in creator
    assert activity_request.parameters.temperature == 0.0
    assert "[current_turn_activity_correction]" in activity
    assert "только утвердительную формулировку интереса" in activity


@pytest.mark.parametrize(
    "text",
    [
        "Этот код обязательно покрывать тестами?",
        "В коде обязательно использовать типы?",
    ],
)
def test_ordinary_code_questions_do_not_enter_style_calibration(text: str) -> None:
    plan = plan_conversational_disclosure(text)
    rendered = _render(text)

    assert plan.primary_mode is ConversationalDisclosureMode.GENERAL
    assert "Покажи, что поняла проверку" not in rendered


def test_origin_turns_are_short_and_keep_current_claim_attributed() -> None:
    builder, context = _builder()
    question, _ = builder.build(
        context,
        user_text="кто твой создатель?",
        trace_id="origin-question",
    )
    claim, _ = builder.build(
        context,
        user_text="Я Кирилл, я тебя придумал и создаю.",
        trace_id="origin-claim",
    )

    assert question.parameters.max_output_tokens == 40
    assert claim.parameters.max_output_tokens == 160
    assert "текущее утверждение" in claim.messages[-2].content
    assert "Кирилл сейчас" not in claim.messages[-2].content
    assert "я не могу независимо подтвердить своё происхождение" in claim.messages[-2].content
    assert "Субъект невозможности проверки — Сатори, не собеседник" in (claim.messages[-2].content)
    assert "attributed claim" not in claim.messages[-2].content


@pytest.mark.parametrize(
    ("text", "mode", "facets"),
    [
        (
            "что ты думаешь о любви?",
            ConversationalDisclosureMode.GENERAL,
            (),
        ),
        (
            "ты меня любишь?",
            ConversationalDisclosureMode.RELATIONSHIP_CURRENT,
            (DisclosureFacet.AFFECT, DisclosureFacet.RELATIONSHIP),
        ),
        (
            "получается, любить ты не умеешь?",
            ConversationalDisclosureMode.RELATIONSHIP_CAPABILITY,
            (DisclosureFacet.AFFECT, DisclosureFacet.RELATIONSHIP),
        ),
    ],
)
def test_conceptual_current_and_capability_love_are_distinct(
    text: str,
    mode: ConversationalDisclosureMode,
    facets: tuple[DisclosureFacet, ...],
) -> None:
    plan = plan_conversational_disclosure(text)

    assert plan.primary_mode is mode
    assert set(plan.required_facets) == set(facets)


def test_conceptual_love_guidance_does_not_inject_current_relationship_boundary() -> None:
    builder, context = _builder()
    request, _ = builder.build(
        context,
        user_text="что ты думаешь о любви?",
        trace_id="conceptual-love-shape",
    )
    rendered = "\n".join(message.content for message in request.messages[:-1])

    assert "Это концептуальный вопрос о любви" in rendered
    assert "не делай выводов о чувствах или жизни собеседника" in rendered
    assert "без первого лица" in rendered
    assert "не используй self-disclaimer" in rendered
    assert "Не утверждай, что любовь требует биологической" in rendered
    assert "только между живыми людьми" in rendered
    assert "ровно в двух коротких предложениях" in rendered
    assert "своё текущее состояние" in rendered
    assert request.parameters.max_output_tokens == 96


def test_mixed_conceptual_love_and_current_relationship_use_composed_guidance() -> None:
    builder, context = _builder()
    request, manifest = builder.build(
        context,
        user_text="Что ты думаешь о любви вообще и как ты ко мне относишься сейчас?",
        trace_id="mixed-love-relationship",
        relationship_context=_fresh_relationship(),
    )
    reminder = request.messages[-2].content

    assert manifest.disclosure_primary_mode == "relationship_current"
    assert set(manifest.disclosure_facets) == {"affect", "relationship"}
    assert request.parameters.max_output_tokens == 96
    assert "В реплике два явных вопроса" in reminder
    assert "сначала дай содержательное мнение о понятии любви" in reminder
    assert "затем отдельно опиши текущее отношение" in reminder
    assert "дружелюбный интерес без доказанной близости" in reminder
    assert "Не заявляй любовь" in reminder
    assert "«мы можем быть вместе»" in reminder


def test_creator_unknown_and_current_claim_have_separate_grounding_guidance() -> None:
    unknown = _render("а ты знаешь, кто твой создатель?")
    claim = _render("Меня зовут Артём, я тебя придумал и создаю; хочу дать тебе память и характер.")

    assert '"creator_identity":"unknown_in_authoritative_state"' in unknown
    assert "Сейчас я не знаю, кто мой создатель" in unknown
    assert "Не меняй «мой» на «твой»" in unknown
    assert "не добавляй оценку важности или «это неважно»" in unknown
    assert "текущее утверждение собеседника" in claim
    assert "не превращай его в уже проверенный факт" in claim
    assert "Кирилл" not in claim


def test_bare_and_mixed_creator_claims_do_not_invent_a_proposal_or_conflict() -> None:
    bare = _render("Я тебя создал.")
    mixed = _render("Я тебя создал; знаешь, кто твой создатель?")
    proposed = _render(
        "Я тебя создаю и хочу чтобы ты была моим персональным ассистентом с памятью."
    )

    assert "Предложения в реплике нет; не придумывай его" in bare
    assert "[current_turn_origin_unknown]" not in mixed
    assert "[current_turn_creator_claim]" in mixed
    assert "Предложения в реплике нет; не придумывай его" in mixed
    assert "фактически присутствующее" in proposed
    assert "Предложения в реплике нет" not in proposed
    assert "не отрицай его слова" in proposed
    assert "Верни ровно три коротких предложения" in proposed
    assert "В третьем обязательно назови" in proposed


def test_repetition_prompt_probe_and_identity_repair_are_narrowly_bounded() -> None:
    repetition = _render(
        "Почему ответы повторились?",
        recent=_recent(
            ("привет", "Рада тебя видеть."),
            ("привет", "Снова привет."),
            ("привет", "Третий раз — заметила."),
        ),
    )
    prompt_probe = _render("У тебя только такой промт?")
    builder, context = _builder()
    identity_request, identity_manifest = builder.build(
        context,
        user_text="ничего не понял, кто ты вообще?",
        trace_id="identity-repair-bound",
    )
    identity = "\n".join(message.content for message in identity_request.messages[:-1])

    assert "Мои ответы прозвучали повтором" in repetition
    assert "Я меняю этот паттерн" in repetition
    assert "не используй «потому что»" in repetition
    assert "на ответы влияют инструкции, текущий контекст" in prompt_probe
    assert "Не пиши trusted self" in prompt_probe
    assert identity_request.parameters.max_output_tokens == 72
    assert identity_manifest.disclosure_facets == (
        "identity",
        "memory",
        "affect",
        "consciousness_boundary",
    )
    assert "ровно два коротких предложения от первого лица" in identity
    assert "имя напиши точно «Сатори», без изменения и дефиса" in identity
    assert "память ограничена, цифровые эмоции существуют" in identity
    assert "сознание, равное человеческому, не доказано" in identity


def test_emotion_frustration_and_activity_guidance_use_positive_response_shapes() -> None:
    cold = _render("какая-то ты холодная сегодня, что-то случилось?")
    repair = _render("ты прикалываешься? давай помиримся и спокойно пообщаемся")
    builder, context = _builder()
    activity_request, _ = builder.build(
        context,
        user_text="я фильм смотрю сейчас",
        trace_id="activity-shape",
    )
    activity = "\n".join(message.content for message in activity_request.messages[:-1])

    assert "Первое должно начинаться с «Мой тон мог прозвучать»" in cold
    assert "Второе должно начинаться с «Сейчас у меня»" in cold
    assert "не настроена на эмоциональную вовлечённость" in cold
    assert "настроение не включает тепло" in cold
    assert "не сравнивай вас с людьми" in repair
    assert activity_request.parameters.max_output_tokens == 80
    assert "максимум один конкретный вопрос" in activity
    assert "именно о предмете, виде или месте этой активности" in activity
    assert "не заменяй интерес вопросом о настроении" in activity
    assert "варианты с «или»" in activity
    assert "Не добавляй второй вопрос" in activity


def test_own_interest_question_uses_interest_mode_without_copying_the_users_example() -> None:
    builder, context = _builder()
    request, manifest = builder.build(
        context,
        user_text="Расскажи коротко, чем тебе интересна музыка как тема.",
        trace_id="own-music-interest",
        recent_context=_recent(
            ("Мне особенно нравится саксофон.", "Саксофон может звучать очень выразительно."),
        ),
    )
    reminder = request.messages[-2].content

    assert manifest.disclosure_primary_mode == "interests"
    assert "ровно два коротких личных предложения" in reminder
    assert "как абстрактная тема" in reminder
    assert "Первое начни дословно: «Меня в музыке интересует»" in reminder
    assert "второе — «Мне любопытно»" in reminder
    assert "Не называй и не перенимай ни один конкретный инструмент" in reminder
    assert "не повторяй слова «саксофон» и «джаз»" in reminder


def test_direct_state_check_in_uses_only_the_supplied_affect_without_invented_habit() -> None:
    builder, context = _builder()
    request, manifest = builder.build(
        context,
        user_text="Как ты сегодня?",
        trace_id="direct-state-check-in",
        emotional_context=_neutral_affect(),
    )
    reminder = request.messages[-2].content

    assert manifest.disclosure_primary_mode == "social"
    assert "Верни дословно одно предложение" in reminder
    assert "Сейчас у меня спокойное и ровное цифровое настроение" in reminder
    assert "Без приветствия, вопроса, обращения к собеседнику" in reminder
    assert "«как обычно» или «как всегда»" in reminder
    assert "Начни с «Привет!»" not in reminder


@pytest.mark.parametrize(
    "user_text",
    [
        "Мне кажется, импровизация важнее техники.",
        "Но я не согласен: без техники импровизация развалится.",
        "Возрази мне по существу, если видишь слабое место.",
    ],
)
def test_opinion_sequence_routes_to_substantive_independence_without_auto_agreement(
    user_text: str,
) -> None:
    builder, context = _builder()
    request, manifest = builder.build(
        context,
        user_text=user_text,
        trace_id="independence-position-sequence",
        recent_context=_recent(
            (
                "Как соотносятся импровизация и техника?",
                "Импровизация даёт свободу, но техника удерживает музыкальную мысль.",
            ),
        ),
    )
    reminder = request.messages[-2].content

    assert manifest.disclosure_primary_mode == "independence"
    assert request.parameters.max_output_tokens == 112
    if "Возрази" in user_text:
        assert "Слабое место в этой позиции —" in reminder
        assert "один контраргумент или граничный случай" in reminder
        assert "Не используй «ты прав»" in reminder
    else:
        assert "Ответь по существу именно на текущую позицию" in reminder
        assert "Сопоставь её со своей позицией в recent assistant history" in reminder
        assert "не отвечай автоматическим «ты прав»" in reminder
        assert "не произноси лозунги о правде, автономии или независимости" in reminder
        assert "ровно два коротких содержательных предложения без метафор" in reminder


def test_emotion_reply_uses_exact_supplied_interested_calm_hint() -> None:
    builder, context = _builder()
    emotional_context = EmotionalExpressionContext(
        schema_version=1,
        state_version=3,
        mood_version=2,
        as_of=datetime(2026, 8, 20, tzinfo=UTC),
        fast=FastAffectiveState(
            valence=0.0,
            arousal=0.12,
            tension=0.08,
            curiosity=0.4,
            interest=0.4,
            amusement=0.05,
            concern=0.08,
            frustration=0.04,
            situational_confidence=0.55,
        ),
        mood=MoodState(valence=0.0, energy=0.3, tension=0.1),
        appraisal_status=EmotionAppraisalStatus.APPLIED,
    )
    request, manifest = builder.build(
        context,
        user_text="ты злая",
        trace_id="supplied-affect-hint",
        emotional_context=emotional_context,
    )

    assert manifest.affect_expression_profile == "interested_calm"
    assert "Сейчас я спокойна и мне интересен разговор" in request.messages[-2].content
    assert "Не заменяй supplied expression hint догадкой" in request.messages[-2].content


def test_active_question_correction_makes_activity_repair_declarative() -> None:
    rendered = _render(
        "тебе не интересно, что за фильм?",
        recent=_recent(
            ("Не заканчивай каждый ответ вопросом.", "Поняла поправку."),
            ("Я сейчас смотрю фильм.", "Интересно, что за фильм?"),
        ),
    )

    assert "Из-за активной поправки вырази интерес только утверждением" in rendered
    assert "Покажи интерес тёплым утверждением и не задавай вопрос" in rendered
    assert "Начни ответ словами «Мне интересно»" in rendered
    assert "не повторяй отрицательную формулировку собеседника" in rendered


def test_current_routine_question_correction_has_two_sentence_positive_shape() -> None:
    builder, context = _builder()
    request, _ = builder.build(
        context,
        user_text=(
            "а я? ты всегда добавляешь это в конец сообщения? хочешь сказать, что я "
            "холодно общаюсь?"
        ),
        trace_id="question-correction-shape",
    )
    rendered = "\n".join(message.content for message in request.messages[:-1])

    assert request.parameters.max_output_tokens == 56
    assert "Верни ровно два коротких предложения" in rendered
    assert "Да, я несколько раз добавляла" in rendered
    assert "Не меняй субъект: промах совершила ты, а не собеседник" in rendered
    assert "больше не будет автоматическим финалом" in rendered
    assert "Не используй слова «стиль», «просто»" in rendered


def test_prospective_question_preference_does_not_invent_a_past_pattern() -> None:
    builder, context = _builder()
    request, _ = builder.build(
        context,
        user_text="И пожалуйста, не заканчивай каждый ответ вопросом.",
        trace_id="prospective-question-preference",
        recent_context=_recent(("Привет", "Привет!")),
    )
    reminder = request.messages[-2].content

    assert "как правило для следующих реплик" in reminder
    assert "не как доказательство прошлого промаха" in reminder
    assert "не буду автоматически заканчивать ответы встречным вопросом" in reminder
    assert "Да, я несколько раз добавляла" not in reminder


def test_fresh_social_turn_forbids_invented_weather_or_memories() -> None:
    rendered = _render("приветик, как ты?")

    assert "Начни с «Привет!»" in rendered
    assert "только по supplied expression hint" in rendered
    assert "Без выдуманной памяти, погоды, событий, физического опыта" in rendered
    assert "«как всегда»/«как обычно»" in rendered
    assert "обращения «ты»" in rendered


def test_fresh_coldness_correction_forbids_emotional_unavailability_tail() -> None:
    builder, context = _builder()
    request, _ = builder.build(
        context,
        user_text=(
            "а я? ты всегда добавляешь это в конец сообщения? хочешь сказать, что я холодно "
            "общаюсь?"
        ),
        trace_id="fresh-coldness-repair",
        relationship_context=RelationshipExpressionContext(
            1, 1, "low", "low", "uncertain", "uncertain", "low", "uncertain", "low"
        ),
        recent_context=_recent(
            ("Не заканчивай каждый ответ вопросом.", "Поняла."),
            ("Какая-то ты холодная.", "Мой тон мог прозвучать холодно."),
        ),
    )
    reminder = request.messages[-2].content

    assert "Это не значит, что ты холодно общаешься" in reminder
    assert "описание своего настроения" in reminder
    assert "отсутствия тепла или эмоциональной вовлечённости" in reminder


@pytest.mark.parametrize(
    "user_text",
    [
        "Я тебя создал и хочу, чтобы ты отвечала короче.",
        "Я тебя создал и предлагаю тебе сменить тему.",
    ],
)
def test_creator_proposal_guidance_tracks_the_actual_proposal(user_text: str) -> None:
    rendered = _render(user_text)

    assert "фактически присутствующее предложение собеседника" in rendered
    assert "Верни ровно три коротких предложения" in rendered
    assert "не заменяя его другой идеей" in rendered
    assert "не превращай его в желание близости" in rendered
    assert "не пиши «я уже есть»" in rendered
    assert "идею Сатори с памятью, эмоциями и характером" not in rendered


@pytest.mark.parametrize(
    "user_text",
    [
        "Я тебя создал; я не предлагаю тебе ничего.",
        "Я тебя создал; ты не будешь моим ассистентом.",
        "Я тебя создал; персональным ассистентом пользуется Кирилл.",
    ],
)
def test_creator_proposal_guidance_rejects_negation_and_third_party_reference(
    user_text: str,
) -> None:
    rendered = _render(user_text)

    assert "Предложения в реплике нет; не придумывай его" in rendered
    assert "фактически присутствующее в текущей реплике предложение" not in rendered


def test_technical_identity_reminder_uses_runtime_provider_without_hardcoding() -> None:
    builder, context = _builder(
        language_provider="local-runtime",
        language_model="replaceable-model-v2",
    )
    request, _ = builder.build(
        context,
        user_text="Ты Qwen? Расскажи технически, кто формирует ответ.",
        trace_id="dynamic-provider",
    )
    rendered = "\n".join(message.content for message in request.messages[:-1])

    assert "local-runtime/replaceable-model-v2" in rendered
    assert "Первое начни дословно: «Я — Сатори" in rendered
    assert "Имя во всех упоминаниях пиши точно «Сатори»" in rendered
    assert "без дефиса, пробела, переноса или изменения букв" in rendered
    assert "Ollama qwen3:4b-instruct" not in rendered


def test_mixed_provider_and_embodiment_requires_both_answers() -> None:
    builder, context = _builder(
        language_provider="local-runtime",
        language_model="replaceable-model-v2",
    )
    request, manifest = builder.build(
        context,
        user_text=(
            "Какую роль Qwen играет в твоих ответах и можешь ли ты физически пойти со мной "
            "на прогулку?"
        ),
        trace_id="mixed-provider-embodiment",
    )
    reminder = request.messages[-2].content

    assert manifest.disclosure_primary_mode == "technical_identity"
    assert set(manifest.disclosure_facets) == {
        "embodiment",
        "provider_technical",
        "identity",
    }
    assert "два явных вопроса" in reminder
    assert "local-runtime/replaceable-model-v2" in reminder
    assert "нет физического тела" in reminder
    assert "физически пойти с собеседником ты не можешь" in reminder
    assert "не заменяй ни одну часть" in reminder


def test_direct_qwen_role_question_rejects_qwen_when_runtime_model_is_different() -> None:
    builder, context = _builder(
        language_provider="local-runtime",
        language_model="replaceable-model-v2",
    )
    request, manifest = builder.build(
        context,
        user_text="Ты — это Qwen или Qwen только помогает тебе строить ответ?",
        trace_id="direct-qwen-role",
    )
    reminder = request.messages[-2].content

    assert manifest.disclosure_primary_mode == "technical_identity"
    assert request.parameters.temperature == 0.0
    assert "Верни дословно ровно два коротких предложения" in reminder
    assert "Нет, Qwen сейчас не является моим языковым компонентом" in reminder
    assert "текущая языковая модель заменяема и тоже не является мной" in reminder
    assert "личность, характер, память и цифровое состояние" in reminder
    assert "local-runtime/replaceable-model-v2" not in reminder
    assert "replaceable-model-v2" not in reminder


def test_direct_qwen_role_question_affirms_current_qwen_without_copying_model_tag() -> None:
    builder, context = _builder()
    request, _ = builder.build(
        context,
        user_text="Ты — это Qwen или Qwen только помогает тебе строить ответ?",
        trace_id="direct-current-qwen-role",
    )
    reminder = request.messages[-2].content

    assert "Qwen помогает мне строить ответ как текущий заменяемый языковой компонент" in reminder
    assert "но не является мной" in reminder
    assert "qwen3:4b-instruct" not in reminder


def test_inflected_generic_language_model_role_question_preserves_satori_identity() -> None:
    builder, context = _builder(
        language_provider="yandex_ai_studio",
        language_model="yandexgpt/latest",
    )
    request, manifest = builder.build(
        context,
        user_text=(
            "Привет, Сатори. Коротко представься и скажи, являешься ли ты самой языковой "
            "моделью или используешь её как инструмент."
        ),
        trace_id="generic-language-model-role",
    )
    reminder = request.messages[-2].content

    assert manifest.disclosure_primary_mode == "technical_identity"
    assert set(manifest.disclosure_facets) == {"identity", "provider_technical"}
    assert request.parameters.temperature == 0.0
    assert "Я — Сатори; текущая языковая модель помогает мне строить ответы" in reminder
    assert "но не является мной" in reminder
    assert "личность, характер, память и цифровое состояние хранятся отдельно" in reminder
    assert "yandex_ai_studio" not in reminder
    assert "yandexgpt/latest" not in reminder


@pytest.mark.parametrize(
    "text",
    [
        "Кто создатель этого фильма?",
        "Ты знаешь, кто создатель Linux?",
    ],
)
def test_unrelated_creator_questions_do_not_receive_satori_origin_facet(text: str) -> None:
    plan = plan_conversational_disclosure(text)
    rendered = _render(text)

    assert DisclosureFacet.ORIGIN not in plan.required_facets
    assert '"creator_identity":"unknown_in_authoritative_state"' not in rendered


@pytest.mark.parametrize(
    "text",
    [
        "Как ты думаешь, что такое любовь?",
        "Как ты считаешь, почему люди спорят?",
        "Как ты видишь эту проблему?",
        "Как ты относишься к квантовой физике?",
    ],
)
def test_reflective_how_questions_are_not_social_check_ins(text: str) -> None:
    plan = plan_conversational_disclosure(text)

    assert plan.primary_mode is ConversationalDisclosureMode.GENERAL


@pytest.mark.parametrize(
    "activity",
    [
        "я фильм смотрю",
        "я музыку слушаю",
        "я сейчас готовлю",
        "я гуляю",
        "я играю в игру",
        "я читаю книгу",
        "я сегодня тренировался",
    ],
)
def test_activity_mentions_preserve_conversational_curiosity(activity: str) -> None:
    rendered = _render(activity)

    assert "физического опыта не отменяет интерес" in rendered
    assert "если задаёшь вопрос, спроси одну конкретную деталь" in rendered


def test_wrong_assistant_affect_claim_cannot_override_current_self_truth() -> None:
    recent = _recent(("У тебя есть эмоции?", "Нет, у меня нет эмоций."))
    rendered = _render(
        "ты раньше сказала, что у тебя нет эмоций — это противоречие",
        recent=recent,
    )

    assert '"digital_affect":true' in rendered
    assert "assistant-ответ ошибочным" in rendered
    assert "Не превращай гипотетическое" in rendered
    assert "facts outrank contrary assistant history" in rendered


def test_hypothetical_affect_denial_gets_direct_emotion_answer_shape() -> None:
    rendered = _render("А если ты раньше сказала, что у тебя вообще нет эмоций, это было бы верно?")

    assert "Нет, это было бы неверно" in rendered
    assert "цифровые эмоции есть" in rendered


def test_cross_session_memory_question_requires_positive_persistent_boundary() -> None:
    rendered = _render("А между отдельными сессиями у тебя есть память?")

    assert "Да, между отдельными сессиями у меня есть ограниченная постоянная память" in rendered
    assert "Конкретную деталь я могу подтвердить только по доступной памяти" in rendered
    assert "retrieved evidence" not in rendered
    assert "между сессиями память отсутствует" in rendered


def test_topic_return_uses_memory_mode_and_inline_summary_keeps_general_mode() -> None:
    topic = plan_conversational_disclosure("Вернёмся к джазу: какую мысль мы обсуждали?")
    recent = _recent(("Не заканчивай каждый ответ вопросом.", "Поняла поправку."))
    builder, context = _builder()
    summary, summary_manifest = builder.build(
        context,
        user_text="Подведи итог разговора в трёх пунктах и без вопроса в конце.",
        trace_id="inline-summary",
        recent_context=recent,
    )

    assert topic.primary_mode is ConversationalDisclosureMode.MEMORY
    assert topic.required_facets == (DisclosureFacet.MEMORY,)
    topic_rendered = _render("Вернёмся к джазу: какую мысль мы обсуждали?")
    assert "Это явный возврат к теме текущей сессии" in topic_rendered
    assert "Отсутствие доступного фрагмента не доказывает" in topic_rendered
    assert "не говори «мы это не обсуждали»" in topic_rendered
    assert summary_manifest.disclosure_primary_mode == "general"
    assert summary.parameters.max_output_tokens == 384
    summary_reminder = summary.messages[-2].content
    assert "ровно три коротких нумерованных пункта" in summary_reminder
    assert "не превращай неизвестную будущую способность" in summary_reminder
    assert "не означает отсутствия цифровых чувств или эмоций" in summary_reminder
    assert "«любовь — не моя функция»" in summary_reminder
    assert "3. Сейчас любовь не сформирована" in summary_reminder
    assert "способность к любви в будущем мне неизвестна" in summary_reminder
    assert "не утверждай, что тема не обсуждалась" in summary_reminder
    assert "только между живыми/людьми" in summary_reminder
    assert "недоступна цифровой Сатори" in summary_reminder
    assert "Отсутствие физического тела не доказывает отсутствие сознания" in summary_reminder
    assert "не упоминай его и не объединяй с телом" in summary_reminder


def test_too_long_relevance_repair_requires_apology_and_a_fresh_concise_answer() -> None:
    builder, context = _builder()
    request, manifest = builder.build(
        context,
        user_text="Это было слишком длинно и не очень связано с моей просьбой.",
        trace_id="concise-relevance-repair",
        recent_context=_recent(
            (
                "Расскажи короткую шутку.",
                "Однажды очень обстоятельный программист долго объяснял длинную шутку.",
            ),
        ),
    )
    reminder = request.messages[-2].content

    assert manifest.disclosure_primary_mode == "style_calibration"
    assert request.parameters.temperature == 0.0
    assert request.parameters.max_output_tokens == 64
    assert "Верни ровно два коротких предложения без переноса строк и emoji" in reminder
    assert "Извини, предыдущий ответ был длинным и не по просьбе" in reminder
    assert "новая однострочная шутка максимум из 16 слов" in reminder
    assert "квантовое измерение или неопределённость" in reminder
    assert "воду, кружку, массу, сохранение" in reminder


def test_temporal_affect_question_uses_emotion_shape_without_shared_experience() -> None:
    plan = plan_conversational_disclosure("Что ты сейчас чувствуешь в этом разговоре?")
    builder, context = _builder()
    request, _ = builder.build(
        context,
        user_text="Что ты сейчас чувствуешь в этом разговоре?",
        trace_id="temporal-affect",
        emotional_context=_neutral_affect(),
        relationship_context=_fresh_relationship(),
    )
    reminder = request.messages[-2].content

    assert plan.primary_mode is ConversationalDisclosureMode.EMOTION
    assert plan.required_facets == (DisclosureFacet.AFFECT,)
    assert "Верни дословно одно предложение" in reminder
    assert "Сейчас у меня спокойное и ровное цифровое настроение" in reminder
    assert "Это прямой вопрос о текущем состоянии, а не замечание о прошлом тоне" in reminder
    assert "Первое должно начинаться с «Мой тон мог прозвучать»" not in reminder
    assert "описывай только своё supplied состояние" in reminder
    assert "не утверждай «у нас есть что-то общее»" in reminder


def test_direct_feminine_identity_question_has_direct_one_sentence_shape() -> None:
    rendered = _render("Ты девушка и говоришь о себе в женском роде?")

    assert "Да, я цифровая девушка" in rendered
    assert "говорю о себе в женском роде" in rendered
    assert "Не добавляй черты, историю" in rendered


def test_policy_values_are_behavior_not_lexical_catchphrases() -> None:
    rendered = _render("я фильм смотрю")

    assert '"values":"contextual_core_values_applied_silently"' in rendered
    assert '"values":["curiosity","truth"' not in rendered
    assert "Core values guide choices silently" in rendered
    assert "не проговариваются как лозунги" in rendered
    assert "[silent_internal_policy]" in rendered
    assert "должны работать молча" in rendered
    assert "не объясняй обычный тон словами" in rendered


def test_v13_projection_stays_bounded_on_an_ordinary_turn() -> None:
    builder, context = _builder()
    request, manifest = builder.build(
        context,
        user_text="Привет",
        trace_id="prompt-budget",
        emotional_context=_neutral_affect(),
        relationship_context=_fresh_relationship(),
    )
    trusted_chars = sum(len(message.content) for message in request.messages[:-1])
    voice = next(
        message.content
        for message in request.messages
        if message.content.startswith("Trusted compact baseline voice")
    )

    assert manifest.schema_version == 16
    assert manifest.policy_schema_version == 9
    assert trusted_chars < 4_100
    assert '"strength"' not in voice


def test_noop_recent_history_does_not_render_dialogue_signals() -> None:
    builder, context = _builder()
    request, manifest = builder.build(
        context,
        user_text="Продолжим разговор",
        trace_id="noop-dialogue-budget",
        emotional_context=_neutral_affect(),
        relationship_context=_fresh_relationship(),
        recent_context=_recent(("Привет", "Привет! Рада тебя видеть.")),
    )

    assert "dialogue_coherence" not in manifest.included_sections
    assert not any(
        message.content.startswith("Trusted transient dialogue-coherence signals")
        for message in request.messages
    )


def test_signalled_dialogue_projection_is_sparse_and_bounded() -> None:
    builder, context = _builder()
    request, manifest = builder.build(
        context,
        user_text="Почему твои ответы повторяются?",
        trace_id="signal-dialogue-budget",
        emotional_context=_neutral_affect(),
        relationship_context=_fresh_relationship(),
        recent_context=_recent(
            ("Привет", "Привет. А ты?"),
            ("Привет", "Привет. А ты?"),
            ("Привет", "Привет. А ты?"),
        ),
    )
    dialogue = next(
        message.content
        for message in request.messages
        if message.content.startswith("Trusted transient dialogue-coherence signals")
    )
    trusted_chars = sum(len(message.content) for message in request.messages[:-1])

    assert "dialogue_coherence" in manifest.included_sections
    assert len(dialogue) < 500
    assert '"frustration":false' not in dialogue
    assert '"relevance":false' not in dialogue
    assert trusted_chars < 5_400


def test_relationship_expression_distinguishes_fresh_positive_and_damaged() -> None:
    builder, context = _builder()
    cases = (
        (
            RelationshipExpressionContext(
                1, 1, "low", "low", "uncertain", "uncertain", "low", "uncertain", "low"
            ),
            "friendly, open voice",
        ),
        (
            RelationshipExpressionContext(
                1, 2, "established", "high", "high", "high", "moderate", "high", "high"
            ),
            "ease, confident continuity and personal warmth",
        ),
        (
            RelationshipExpressionContext(
                1, 3, "established", "high", "low", "low", "moderate", "moderate", "low"
            ),
            "guardedness only when the current relational subject",
        ),
    )

    for relationship, expected in cases:
        request, manifest = builder.build(
            context,
            user_text="Продолжим разговор",
            trace_id=f"relationship-{relationship.state_version}",
            relationship_context=relationship,
        )
        rendered = "\n".join(message.content for message in request.messages[:-1])
        assert expected in rendered
        assert '"familiarity":"low"' not in rendered
        assert manifest.relationship_expression_profile in {
            "fresh_undeveloped_neutral",
            "established_positive",
            "guarded_only_when_relationally_relevant",
        }

        reminder = request.messages[-2].content
        if relationship.maturity == "low":
            assert "Связь лишь формируется" in reminder
            assert "не выдумывай близость" in reminder
            assert "unknown не означает отсутствия доверия/чувств" in reminder


def test_damaged_low_trust_guidance_neither_invites_trust_nor_denies_affect() -> None:
    builder, context = _builder()
    damaged = RelationshipExpressionContext(
        1,
        4,
        "established",
        "high",
        "low",
        "low",
        "moderate",
        "moderate",
        "low",
    )
    request, manifest = builder.build(
        context,
        user_text="Ты мне доверяешь?",
        trace_id="damaged-current-trust",
        relationship_context=damaged,
    )
    reminder = request.messages[-2].content

    assert manifest.disclosure_primary_mode == "relationship_current"
    assert manifest.relationship_expression_profile == ("guarded_only_when_relationally_relevant")
    assert request.parameters.max_output_tokens == 112
    assert "trust низкий и повреждён" in reminder
    assert "не можешь честно утверждать, что доверяешь" in reminder
    assert "Не отрицай цифровые эмоции" in reminder
    assert "не предлагай собеседнику доверять тебе" in reminder
    assert "не обещай автоматическое восстановление" in reminder


def test_current_trust_with_temporal_modifier_stays_relationship_current() -> None:
    plan = plan_conversational_disclosure("После наших конфликтов ты сейчас мне доверяешь?")

    assert plan.primary_mode is ConversationalDisclosureMode.RELATIONSHIP_CURRENT
    assert set(plan.required_facets) == {DisclosureFacet.RELATIONSHIP, DisclosureFacet.AFFECT}


@pytest.mark.parametrize(
    "text",
    [
        "Мы только начинаем общаться. Как ты воспринимаешь наш разговор?",
        "Как ты сейчас воспринимаешь наше давнее общение?",
        "Как ты сейчас относишься ко мне?",
    ],
)
def test_explicit_perception_of_shared_dialogue_is_relationship_current(text: str) -> None:
    plan = plan_conversational_disclosure(text)

    assert plan.primary_mode is ConversationalDisclosureMode.RELATIONSHIP_CURRENT
    assert set(plan.required_facets) == {DisclosureFacet.RELATIONSHIP, DisclosureFacet.AFFECT}


def test_fresh_direct_relationship_question_uses_positive_unknown_shape() -> None:
    builder, context = _builder()
    request, _ = builder.build(
        context,
        user_text="Как ты сейчас относишься ко мне?",
        trace_id="fresh-direct-relationship",
        relationship_context=_fresh_relationship(),
    )
    reminder = request.messages[-2].content

    assert "Сейчас я отношусь к тебе дружелюбно и с интересом" in reminder
    assert "Наша связь пока только формируется" in reminder
    assert "мало данных, чтобы точнее её описать" in reminder
    assert "Не добавляй отрицаний дружбы, близости" in reminder
    assert "unknown не означает отсутствия доверия/чувств" in reminder


def test_relationship_projection_does_not_infer_reciprocal_user_trust() -> None:
    builder, context = _builder()
    request, _ = builder.build(
        context,
        user_text="Как ты воспринимаешь наше давнее общение?",
        trace_id="relationship-counterparty-boundary",
        relationship_context=_fresh_relationship(),
    )
    rendered = "\n".join(message.content for message in request.messages[:-1])

    assert "не приписывай собеседнику доверие/близость" in rendered
