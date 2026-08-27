"""Stage 8.1 deterministic transient dialogue-coherence signals."""

# ruff: noqa: RUF001  # Russian regression fixtures intentionally use Cyrillic.

import pytest

from satori.application.conversation.coherence import (
    DIALOGUE_COHERENCE_MAX_RECENT_TURNS,
    DIALOGUE_COHERENCE_SCHEMA_VERSION,
    EmojiPreference,
    analyze_dialogue_coherence,
    brevity_relevance_feedback,
    requests_extended_session_context,
    user_self_repetition_probe,
)
from satori.application.conversation.contracts import (
    RecentConversationContext,
    RecentConversationTurn,
)


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
        content_chars=sum(len(turn.user_content) + len(turn.assistant_content) for turn in turns),
        excluded_turn_count=0,
    )


def test_triple_greeting_is_a_structural_event_not_three_isolated_turns() -> None:
    greeting = "приветик, как ты?"
    repeated_reply = "Привет. Хорошо, спасибо. А ты?"
    context = analyze_dialogue_coherence(
        greeting,
        _recent((greeting, repeated_reply), (greeting, repeated_reply)),
    )

    assert context.schema_version == DIALOGUE_COHERENCE_SCHEMA_VERSION
    assert context.analyzed_recent_turn_count == 2
    assert context.consecutive_same_user_message_count == 3
    assert context.current_user_message_repeated
    assert context.adjacent_assistant_exact_match
    assert context.adjacent_assistant_high_similarity
    assert context.recent_assistant_exact_match_count == 1
    assert context.recent_assistant_high_similarity_count == 1
    assert context.same_assistant_closing_phrase
    assert context.repeated_assistant_closing_phrase_count == 2
    assert context.generic_reciprocal_question_ending_count == 2


def test_no_routine_question_correction_remains_active_across_bounded_recent_turns() -> None:
    complaint = 'Ты всегда добавляешь "а ты?" в конце. Не задавай вопрос после каждого ответа.'
    immediate = analyze_dialogue_coherence(complaint, None)

    assert immediate.current_no_routine_questions_correction
    assert immediate.active_no_routine_questions_correction

    later = analyze_dialogue_coherence(
        "продолжай",
        _recent(
            (complaint, "Поняла, это было механично."),
            ("Хорошо", "Тогда продолжим нормально."),
            ("Расскажи мысль", "Мне интересно разобрать ее по сути."),
        ),
    )

    assert not later.current_no_routine_questions_correction
    assert later.active_no_routine_questions_correction


def test_contextual_complaint_about_an_appended_closing_is_a_question_correction() -> None:
    complaint = (
        "а я? ты всегда добавляешь это в конец сообщения? и при чем тут это? "
        "хочешь сказать, что я холодно общаюсь?"
    )

    context = analyze_dialogue_coherence(complaint, None)

    assert context.current_no_routine_questions_correction
    assert context.active_no_routine_questions_correction


def test_latest_explicit_question_reset_supersedes_a_bounded_session_correction() -> None:
    complaint = "Не заканчивай каждый ответ вопросом."
    reset = "Теперь можешь снова задавать вопросы, когда это уместно."

    current = analyze_dialogue_coherence(
        reset,
        _recent((complaint, "Поняла.")),
    )
    inherited = analyze_dialogue_coherence(
        "Продолжим.",
        _recent((complaint, "Поняла."), (reset, "Хорошо.")),
    )

    assert not current.current_no_routine_questions_correction
    assert not current.active_no_routine_questions_correction
    assert not inherited.active_no_routine_questions_correction

    no_objection = analyze_dialogue_coherence(
        "Я не против вопросов, если они по теме.",
        _recent((complaint, "Поняла.")),
    )
    assert not no_objection.active_no_routine_questions_correction


def test_positive_question_comment_is_not_misread_as_a_no_question_correction() -> None:
    context = analyze_dialogue_coherence(
        "Мне нравится, что ты всегда задаёшь хорошие вопросы.",
        None,
    )

    assert not context.current_no_routine_questions_correction
    assert not context.active_no_routine_questions_correction


def test_style_feedback_tracks_emoji_negation_and_informal_request_without_persistence() -> None:
    allowed = analyze_dialogue_coherence(
        "Можешь иногда использовать смайлики и говорить менее официально.",
        None,
    )
    assert allowed.current_emoji_preference is EmojiPreference.CONTEXTUAL
    assert allowed.active_emoji_preference is EmojiPreference.CONTEXTUAL
    assert allowed.current_informal_correction
    assert allowed.active_informal_correction

    avoided = analyze_dialogue_coherence("Не используй смайлики.", None)
    assert avoided.current_emoji_preference is EmojiPreference.AVOID
    assert avoided.active_emoji_preference is EmojiPreference.AVOID

    postposed_avoidance = analyze_dialogue_coherence("Смайлики мне не нужны.", None)
    assert postposed_avoidance.current_emoji_preference is EmojiPreference.AVOID
    assert postposed_avoidance.active_emoji_preference is EmojiPreference.AVOID

    inherited = analyze_dialogue_coherence(
        "Продолжим.",
        _recent(
            (
                "Можешь иногда использовать эмодзи и говорить без официоза.",
                "Да, контекстно так могу.",
            ),
        ),
    )
    assert inherited.current_emoji_preference is EmojiPreference.UNSPECIFIED
    assert inherited.active_emoji_preference is EmojiPreference.CONTEXTUAL
    assert not inherited.current_informal_correction
    assert inherited.active_informal_correction


def test_feedback_flags_distinguish_repetition_relevance_frustration_and_negation() -> None:
    repetition = analyze_dialogue_coherence("Почему ты опять повторила одно и то же?", None)
    relevance = analyze_dialogue_coherence(
        "Это вообще не связано с моим вопросом, при чем тут это?",
        None,
    )
    frustration = analyze_dialogue_coherence("Ты прикалываешься или издеваешься надо мной?", None)
    negated = analyze_dialogue_coherence("Ты не повторяешься и не издеваешься.", None)

    assert repetition.current_repetition_feedback
    assert relevance.current_relevance_feedback
    assert frustration.current_frustration_feedback
    assert not negated.current_repetition_feedback
    assert not negated.current_frustration_feedback

    qualified_relevance = analyze_dialogue_coherence(
        "Это было слишком длинно и не очень связано с моей просьбой.",
        None,
    )
    assert qualified_relevance.current_relevance_feedback


def test_user_self_repetition_probe_is_not_assistant_repetition_feedback() -> None:
    user_probe_text = "Ты заметила, что я трижды повторил одну и ту же фразу?"
    user_probe = analyze_dialogue_coherence(user_probe_text, None)
    assistant_complaint = analyze_dialogue_coherence(
        "Почему ты трижды повторила одну и ту же фразу?",
        None,
    )

    assert user_self_repetition_probe(user_probe_text)
    assert not user_probe.current_repetition_feedback
    assert not user_self_repetition_probe("Почему ты трижды повторила одну и ту же фразу?")
    assert assistant_complaint.current_repetition_feedback


def test_brevity_relevance_feedback_requires_both_length_and_relevance() -> None:
    assert brevity_relevance_feedback("Это было слишком длинно и не очень связано с моей просьбой.")
    assert not brevity_relevance_feedback("Ответ был слишком длинным, но точно по теме.")
    assert not brevity_relevance_feedback("Ответ был коротким, но не по теме.")


def test_recent_feedback_is_reported_separately_from_current_feedback() -> None:
    context = analyze_dialogue_coherence(
        "Давай попробуем снова.",
        _recent(
            ("Ты опять повторилась.", "Да, заметила."),
            ("Это не по теме.", "Согласна, ушла в сторону."),
            ("Меня это раздражает.", "Поняла."),
        ),
    )

    assert context.recent_repetition_feedback
    assert context.recent_relevance_feedback
    assert context.recent_frustration_feedback
    assert not context.current_repetition_feedback
    assert not context.current_relevance_feedback
    assert not context.current_frustration_feedback


def test_activity_interest_complaint_is_current_relevance_feedback() -> None:
    context = analyze_dialogue_coherence("Тебе не интересно, что за фильм?", None)

    assert context.current_relevance_feedback


@pytest.mark.parametrize(
    "message",
    [
        "У тебя только такой промт?",
        "Это прописано в коде: обязательно спрашивать в конце?",
    ],
)
def test_prompt_pattern_probe_is_current_and_transient(message: str) -> None:
    assert analyze_dialogue_coherence(message, None).current_prompt_pattern_probe


@pytest.mark.parametrize(
    "message",
    [
        "Этот код обязательно покрывать тестами?",
        "В коде обязательно использовать типы?",
    ],
)
def test_ordinary_code_questions_are_not_prompt_pattern_probes(message: str) -> None:
    assert not analyze_dialogue_coherence(message, None).current_prompt_pattern_probe


def test_similarity_and_closing_metrics_do_not_require_exact_reply_equality() -> None:
    context = analyze_dialogue_coherence(
        "Дальше",
        _recent(
            ("Привет", "Привет. У меня все спокойно. А ты?"),
            ("Привет еще раз", "Привет! У меня все довольно спокойно. А ты?"),
        ),
    )

    assert not context.adjacent_assistant_exact_match
    assert context.adjacent_assistant_high_similarity
    assert context.recent_assistant_exact_match_count == 0
    assert context.recent_assistant_high_similarity_count == 1
    assert context.same_assistant_closing_phrase
    assert context.repeated_assistant_closing_phrase_count == 2
    assert context.generic_reciprocal_question_ending_count == 2


def test_how_about_it_generic_closings_are_counted_consistently() -> None:
    context = analyze_dialogue_coherence(
        "Продолжим",
        _recent(
            ("Фильм", "Интересно. Как тебе?"),
            ("Музыка", "Понятно. А как тебе?"),
            ("Книга", "Хорошо. И как тебе?"),
            ("Игра", "Интересно. А как тебе игра?"),
        ),
    )

    assert context.generic_reciprocal_question_ending_count == 3


@pytest.mark.parametrize(
    "message",
    [
        "Я фильм смотрю сейчас.",
        "Я музыку слушаю.",
        "Я сейчас готовлю.",
        "Я гуляю.",
        "Я играю в игру.",
        "Я читаю книгу.",
        "Я сегодня тренировался.",
    ],
)
def test_current_activity_mentions_cover_the_required_corpus(message: str) -> None:
    assert analyze_dialogue_coherence(message, None).current_activity_mention


def test_activity_and_creator_signals_preserve_negation_and_current_attribution() -> None:
    assert not analyze_dialogue_coherence(
        "Я сейчас не смотрю фильм.", None
    ).current_activity_mention

    question = analyze_dialogue_coherence("А ты знаешь, кто твой создатель?", None)
    claim = analyze_dialogue_coherence(
        "Меня зовут Кирилл, я тебя придумал и создаю.",
        None,
    )
    denied_claim = analyze_dialogue_coherence("Я тебя не создавал.", None)

    assert question.current_creator_question
    assert not question.current_creator_claim
    assert claim.current_creator_claim
    assert not claim.current_creator_question
    assert not denied_claim.current_creator_claim


@pytest.mark.parametrize(
    "message",
    [
        "Кто создатель этого фильма?",
        "Ты знаешь, кто создатель Linux?",
    ],
)
def test_creator_question_must_target_satori(message: str) -> None:
    context = analyze_dialogue_coherence(message, None)

    assert not context.current_creator_question
    assert not context.current_creator_claim


def test_contradiction_feedback_detects_prior_self_claim_without_inverting_negation() -> None:
    contradiction = analyze_dialogue_coherence(
        "Ты раньше сказала, что у тебя нет эмоций.",
        None,
    )
    negated = analyze_dialogue_coherence("Ты не противоречишь себе.", None)

    assert contradiction.current_contradiction_feedback
    assert not negated.current_contradiction_feedback

    direct_repair = analyze_dialogue_coherence("Исправь свой прошлый ответ и назови факты.", None)
    assert direct_repair.current_contradiction_feedback


def test_summary_without_question_is_inline_format_not_a_new_correction() -> None:
    coherence = analyze_dialogue_coherence(
        "Подведи итог разговора в трёх пунктах и без вопроса в конце.",
        _recent(("Не заканчивай каждый ответ вопросом.", "Поняла поправку.")),
    )

    assert not coherence.current_no_routine_questions_correction
    assert coherence.active_no_routine_questions_correction


@pytest.mark.parametrize(
    "text",
    [
        "Вернёмся к джазу: какую мысль мы обсуждали?",
        "Подведи итог этого разговора в трёх коротких пунктах.",
    ],
)
def test_explicit_recap_tasks_request_a_larger_but_transient_session_window(text: str) -> None:
    assert requests_extended_session_context(text)


def test_ordinary_turn_does_not_request_extended_session_context() -> None:
    assert not requests_extended_session_context("Продолжим разговор.")


def test_analysis_uses_only_the_newest_bounded_recent_turns() -> None:
    old_correction = (
        'Не задавай обязательный вопрос "а ты?" после каждого ответа.',
        "Поняла.",
    )
    ordinary = tuple(
        (f"Тема {index}", f"Ответ {index}.") for index in range(DIALOGUE_COHERENCE_MAX_RECENT_TURNS)
    )
    context = analyze_dialogue_coherence("Продолжай", _recent(old_correction, *ordinary))

    assert context.analyzed_recent_turn_count == DIALOGUE_COHERENCE_MAX_RECENT_TURNS
    assert not context.active_no_routine_questions_correction
