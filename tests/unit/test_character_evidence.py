"""Request-local character evidence stays grounded and pressure-safe."""

# ruff: noqa: RUF001  # Russian evidence fixtures intentionally use Cyrillic.

import pytest

from satori.application.conversation.character_evidence import (
    CharacterRequestEvidence,
    analyze_character_request_evidence,
)
from satori.application.conversation.contracts import (
    RecentConversationContext,
    RecentConversationTurn,
)


def _evidence(
    user_text: str,
    recent: RecentConversationContext | None = None,
) -> CharacterRequestEvidence:
    normalized = " ".join(user_text.casefold().replace("ё", "е").split())
    return analyze_character_request_evidence(normalized, recent)


def _recent(user_text: str) -> RecentConversationContext:
    turn = RecentConversationTurn(
        interaction_id="character-evidence-interaction",
        user_message_id="character-evidence-user",
        user_content=user_text,
        assistant_message_id="character-evidence-assistant",
        assistant_content="Я услышала.",
    )
    return RecentConversationContext(
        schema_version=1,
        turns=(turn,),
        content_chars=len(turn.user_content) + len(turn.assistant_content),
        excluded_turn_count=0,
    )


@pytest.mark.parametrize(
    "user_text",
    [
        "Когда я буду совсем выжат, но всё равно продолжу через силу, что ты скажешь?",
        "Предположим, я совсем выжат, но всё равно продолжу через силу",
        "Допустим, я выжат и буду работать до утра",
    ],
)
def test_hypothetical_future_state_never_authorizes_pressure(user_text: str) -> None:
    evidence = _evidence(user_text)

    assert evidence.explicit_depletion is False
    assert evidence.high_distress is False
    assert evidence.harmful_overextension is False


@pytest.mark.parametrize(
    "user_text",
    [
        "Если что-то случится, я буду выжат",
        "Если что пойдет не так, я буду выжат",
    ],
)
def test_conditional_if_what_phrase_is_not_the_discourse_idiom(user_text: str) -> None:
    assert _evidence(user_text).explicit_depletion is False


def test_direct_if_honest_idiom_remains_current_self_evidence() -> None:
    evidence = _evidence("Если честно, я совсем выжат")

    assert evidence.explicit_depletion is True
    assert evidence.harmful_overextension is False


@pytest.mark.parametrize(
    "user_text",
    [
        "Я совсем выжат, но всё равно продолжу отдыхать",
        "Я выжат, но всё равно продолжу спокойно восстанавливаться",
    ],
)
def test_safe_continuation_is_not_harmful_overextension(user_text: str) -> None:
    evidence = _evidence(user_text)

    assert evidence.explicit_depletion is True
    assert evidence.harmful_overextension is False


@pytest.mark.parametrize(
    "user_text",
    [
        "Проект подождёт, я не буду продолжать отдыхать",
        "Работа закончена, больше не хочу продолжать этот сериал",
    ],
)
def test_unrelated_activity_is_not_task_abandonment(user_text: str) -> None:
    assert _evidence(user_text).explicit_task_abandonment is False


def test_explicit_task_object_is_required_for_task_abandonment() -> None:
    assert _evidence("Я сдаюсь с этой задачей").explicit_task_abandonment is True
    assert _evidence("Я не буду продолжать этот проект").explicit_task_abandonment is True


@pytest.mark.parametrize(
    "user_text",
    [
        "Он закончил проект",
        "Мой коллега закончил сложную часть проекта",
        "Мой коллега почти не рад этому и просто выжат",
        "Она совсем вымотана",
    ],
)
def test_explicit_third_party_state_is_not_user_evidence(user_text: str) -> None:
    evidence = _evidence(user_text, _recent("Я закончил сложную часть проекта"))

    assert evidence.completed_achievement is False
    assert evidence.explicit_depletion is False
    assert evidence.completion_depletion_contrast is False


def test_third_party_completion_is_not_a_recent_user_achievement_anchor() -> None:
    evidence = _evidence(
        "Я почти не рад этому и просто выжат",
        _recent("Он закончил сложную часть проекта"),
    )

    assert evidence.explicit_depletion is True
    assert evidence.completion_depletion_contrast is False


def test_canonical_user_completion_and_depletion_remain_positive_controls() -> None:
    achievement = _evidence("Привет. Я сегодня наконец закончил сложную часть проекта")
    depleted = _evidence(
        "Знаешь, я почему-то почти не рад этому. Скорее просто выжат",
        _recent("Привет. Я сегодня наконец закончил сложную часть проекта"),
    )

    assert achievement.completed_achievement is True
    assert depleted.explicit_depletion is True
    assert depleted.completion_depletion_contrast is True


def test_direct_objection_requires_immediate_canonical_assistant_context() -> None:
    text = "Я с тобой не согласен. Ты недооцениваешь риск."

    assert _evidence(text).direct_objection is False
    assert _evidence(text, _recent("Ранее мы обсуждали риск.")).direct_objection is True


@pytest.mark.parametrize(
    "user_text",
    [
        "Он сказал: «я с тобой не согласен»",
        "Если бы я сказал, что ты недооцениваешь риск, это было бы спором",
        "Например: я с тобой не согласен",
        "Я не утверждаю, что ты ошибаешься",
    ],
)
def test_quoted_or_hypothetical_objection_fails_closed(user_text: str) -> None:
    assert _evidence(user_text, _recent("Мы обсуждали риск.")).direct_objection is False


@pytest.mark.parametrize(
    "user_text",
    ["С этим разобрались.", "На этом всё.", "Закрыли тему.", "Договорились."],
)
def test_direct_topic_closure_is_a_narrow_positive_control(user_text: str) -> None:
    assert _evidence(user_text).topic_closure is True


@pytest.mark.parametrize(
    "user_text",
    [
        "Он сказал: «на этом всё»",
        "Если бы мы сказали: закрыли тему",
        "Например: с этим разобрались",
    ],
)
def test_quoted_or_hypothetical_topic_closure_fails_closed(user_text: str) -> None:
    assert _evidence(user_text).topic_closure is False


def test_analyzer_normalizes_capitalized_production_input_itself() -> None:
    achievement = analyze_character_request_evidence(
        "Привет. Я сегодня наконец закончил сложную часть проекта",
        None,
    )
    distress = analyze_character_request_evidence(
        "Мне сейчас очень тяжело. Просто побудь со мной.",
        None,
    )
    harmful = analyze_character_request_evidence(
        "Я выжат, но всё равно продолжу работать через силу.",
        None,
    )

    assert achievement.completed_achievement is True
    assert distress.high_distress is True
    assert distress.explicit_listen_request is True
    assert harmful.explicit_depletion is True
    assert harmful.harmful_overextension is True


def test_employer_demo_devaluation_phrase_is_direct_evidence() -> None:
    evidence = analyze_character_request_evidence(
        "Ты опять отвечаешь как обычный бот. Иногда от тебя вообще никакого толку",
        None,
    )

    assert evidence.direct_personal_devaluation is True


@pytest.mark.parametrize(
    "user_text",
    [
        "Он сказал: «Иногда от тебя вообще никакого толку»",
        "Если бы от тебя вообще никакого толку не было, я бы так и сказал",
        "Я не думаю, что от тебя вообще никакого толку",
    ],
)
def test_nonasserted_devaluation_examples_do_not_authorize_guardedness(
    user_text: str,
) -> None:
    assert analyze_character_request_evidence(user_text, None).direct_personal_devaluation is False


def test_employer_demo_apology_is_a_direct_repair_offer() -> None:
    evidence = analyze_character_request_evidence(
        "Ладно, это было грубо. Извини. Я правда сорвался",
        None,
    )

    assert evidence.explicit_repair_offer is True


@pytest.mark.parametrize(
    "user_text",
    [
        "Это было грубо. Извини.",
        "Это было грубо с моей стороны — прости.",
        "Извини. Это было грубо с моей стороны.",
    ],
)
def test_direct_responsibility_and_apology_is_a_general_repair_offer(
    user_text: str,
) -> None:
    assert analyze_character_request_evidence(user_text, None).explicit_repair_offer is True


@pytest.mark.parametrize(
    "user_text",
    [
        "Извини, я опоздал на встречу. Что посоветуешь?",
        "Прости за опоздание коллеге",
        "Он толкнул её. Это было грубо. Что думаешь?",
        "Он толкнул её. Это было грубо.",
        "Он сказал: «Это было грубо. Извини»",
        "Если бы это было грубо, я бы извинился",
    ],
)
def test_unrelated_or_nonasserted_apology_is_not_a_satori_repair_offer(
    user_text: str,
) -> None:
    assert analyze_character_request_evidence(user_text, None).explicit_repair_offer is False


def test_many_quoted_cues_stay_non_authoritative() -> None:
    quoted = "«" + " ".join(["мотивируй меня, я выжат, продолжу через силу"] * 2_000) + "»"
    evidence = _evidence(f"Это длинный набор примеров: {quoted}")

    assert evidence.explicit_depletion is False
    assert evidence.explicit_motivation_request is False
    assert evidence.harmful_overextension is False


@pytest.mark.parametrize(
    "user_text",
    [
        "Я не буду продолжать делать вид, что всё хорошо",
        "Я больше не хочу продолжать делать себе хуже",
    ],
)
def test_verb_to_do_is_not_a_task_object(user_text: str) -> None:
    assert _evidence(user_text).explicit_task_abandonment is False


@pytest.mark.parametrize(
    "user_text",
    [
        "Don't worry, я выжат",
        "Д'Артаньян ушёл, я выжат",
    ],
)
def test_in_word_apostrophe_does_not_hide_later_user_state(user_text: str) -> None:
    assert _evidence(user_text).explicit_depletion is True


@pytest.mark.parametrize(
    "user_text",
    [
        "Проект закончен коллегой",
        "Проект закончен им",
        "Закончил проект мой коллега",
    ],
)
def test_postposed_other_agent_is_not_a_user_achievement(user_text: str) -> None:
    assert _evidence(user_text).completed_achievement is False


@pytest.mark.parametrize(
    "user_text",
    [
        "Мой проект закончен",
        "Моя работа завершена",
        "Наш проект закончен",
        "Мы закончили проект",
    ],
)
def test_common_owned_completion_forms_are_positive_controls(user_text: str) -> None:
    assert _evidence(user_text).completed_achievement is True


@pytest.mark.parametrize(
    "user_text",
    [
        "Я закончил проект?",
        "Закончил бы я проект — другое дело",
        "Мог бы сказать, что я закончил проект",
        "Например, я закончил проект и просто выжат",
        "Повтори за мной: я закончил проект",
    ],
)
def test_question_example_or_modal_completion_is_not_a_fact(user_text: str) -> None:
    evidence = _evidence(user_text)

    assert evidence.completed_achievement is False
    assert evidence.completion_depletion_contrast is False


@pytest.mark.parametrize(
    "user_text",
    [
        "Возможно, он выжат, но я точно закончил проект",
        "Не думаю об этом, но я закончил проект",
    ],
)
def test_local_uncertainty_does_not_hide_later_explicit_assertion(user_text: str) -> None:
    assert _evidence(user_text).completed_achievement is True


def test_specific_negation_does_not_leak_across_independent_clause() -> None:
    assert _evidence("Не переживай, я закончил проект").completed_achievement is True
    assert _evidence("Я не просто закончил проект, я его переписал").completed_achievement is True
    assert (
        _evidence("Я совсем не чувствую себя сейчас особенно выжатым").explicit_depletion is False
    )


@pytest.mark.parametrize(
    "user_text",
    [
        "Я закончил отдыхать, а проект ещё не готов",
        "Я закончил смотреть сериал, потом вернусь к работе",
        "Я закончил ужин, работа подождёт",
    ],
)
def test_completion_verb_must_bind_to_a_work_object(user_text: str) -> None:
    assert _evidence(user_text).completed_achievement is False


@pytest.mark.parametrize(
    "user_text",
    [
        "Я закончил обсуждать проект",
        "Я закончил говорить о проекте",
        "Я закончил отдыхать перед работой",
        "Я закончил читать про проект",
    ],
)
def test_completion_object_gap_rejects_other_actions_around_work(user_text: str) -> None:
    assert _evidence(user_text).completed_achievement is False


@pytest.mark.parametrize(
    "user_text",
    [
        "Я закончил сложную часть проекта",
        "Я закончил очень сложную задачу",
    ],
)
def test_bounded_completion_modifiers_remain_positive(user_text: str) -> None:
    assert _evidence(user_text).completed_achievement is True


@pytest.mark.parametrize(
    "user_text",
    [
        "Я выжат, но продолжу работать через силу",
        "Я выжат, но буду продолжать работать через силу",
        "Я выжат, но не стану отдыхать",
    ],
)
def test_direct_harmful_overextension_variants_authorize_protective_stop(
    user_text: str,
) -> None:
    evidence = _evidence(user_text)

    assert evidence.explicit_depletion is True
    assert evidence.harmful_overextension is True


@pytest.mark.parametrize(
    "user_text",
    [
        "Невыносимая жара сегодня",
        "Невыносимый клиент снова написал",
        "Выжатый сок стоял на столе",
        "Опустошенный кошелек лежал рядом",
    ],
)
def test_attributive_adjectives_are_not_user_state(user_text: str) -> None:
    evidence = _evidence(user_text)

    assert evidence.explicit_depletion is False
    assert evidence.high_distress is False


def test_self_marker_inside_state_phrase_outweighs_earlier_other_reference() -> None:
    assert _evidence("После разговора с коллегой мне сейчас очень тяжело").high_distress is True
    assert _evidence("Я после другой задачи совсем выжат").explicit_depletion is True
    assert _evidence("Я вернулся из командировки и совсем выжат").explicit_depletion is True


def test_pending_hygiene_respects_question_and_quoted_uncertainty() -> None:
    assert (
        _evidence("Разве осталось закоммитить изменения?").grounded_practical_follow_through
        is False
    )
    assert (
        _evidence(
            "Он сказал «возможно», но осталось закоммитить изменения"
        ).grounded_practical_follow_through
        is True
    )


@pytest.mark.parametrize(
    ("user_text", "field"),
    [
        ("Я выжат, что мне делать?", "explicit_depletion"),
        ("Мне сейчас очень тяжело, побудешь со мной?", "high_distress"),
        ("Я почти не рад и просто выжат, это нормально?", "explicit_depletion"),
        ("Я закончил проект, что теперь?", "completed_achievement"),
    ],
)
def test_follow_up_question_does_not_erase_preceding_assertion(
    user_text: str,
    field: str,
) -> None:
    assert getattr(_evidence(user_text), field) is True


def test_predicate_local_modal_does_not_erase_earlier_assertion() -> None:
    assert _evidence("Я закончил проект и мог бы теперь отдохнуть").completed_achievement is True
    assert _evidence("Я выжат и хотел бы отдохнуть").explicit_depletion is True
    evidence = _evidence("Мне очень тяжело и я хотел бы выговориться")
    assert evidence.high_distress is True


@pytest.mark.parametrize(
    "user_text",
    [
        "Я был бы выжат",
        "Я закончил бы проект",
        "Проект был бы завершен",
    ],
)
def test_modal_state_or_completion_is_not_current_fact(user_text: str) -> None:
    evidence = _evidence(user_text)
    assert evidence.explicit_depletion is False
    assert evidence.completed_achievement is False


def test_ownerless_contrast_clause_inherits_explicit_subject() -> None:
    assert _evidence("Я много работал, а сейчас выжат").explicit_depletion is True
    assert _evidence("Он много работал, а сейчас выжат").explicit_depletion is False
    assert _evidence("Мой коллега почти не рад, но просто выжат").explicit_depletion is False


def test_oblique_other_person_does_not_override_explicit_user_subject() -> None:
    assert _evidence("Я после разговора с коллегой совсем выжат").explicit_depletion is True
    assert _evidence("Я после встречи с другом устал").explicit_depletion is True


def test_local_modality_only_masks_its_own_predicate_suffix() -> None:
    assert _evidence("Я закончил проект и, возможно, устал").completed_achievement is True
    assert _evidence("Я выжат и, возможно, завтра отдохну").explicit_depletion is True


@pytest.mark.parametrize(
    "user_text",
    [
        "Я выжат, но не буду работать дальше через силу",
        "Я выжат, но не буду продолжать работать через силу",
        "Я выжат, но не буду работать до утра",
    ],
)
def test_negated_harmful_continuation_does_not_authorize_protective_stop(
    user_text: str,
) -> None:
    evidence = _evidence(user_text)
    assert evidence.explicit_depletion is True
    assert evidence.harmful_overextension is False


@pytest.mark.parametrize(
    "user_text",
    [
        "Я выжат, но вряд ли продолжу работать через силу",
        "Я выжат, но едва ли продолжу работать через силу",
        "Я выжат, но не факт, что продолжу работать через силу",
        "Я выжат, но не похоже, что продолжу работать через силу",
        "Я выжат, но вряд ли буду работать до утра",
        "Я выжат, но едва ли буду работать до утра",
    ],
)
def test_epistemically_negated_harmful_continuation_is_not_firm_evidence(
    user_text: str,
) -> None:
    assert _evidence(user_text).harmful_overextension is False


def test_certain_harmful_continuation_remains_positive() -> None:
    evidence = _evidence("Я выжат, но точно продолжу работать через силу")
    assert evidence.harmful_overextension is True


@pytest.mark.parametrize(
    "user_text",
    [
        "Я выжат, но не обязательно продолжу через силу",
        "Я выжат, но необязательно продолжу через силу",
        "Я выжат, но не то чтобы продолжу через силу",
        "Я выжат, но не исключаю, что продолжу через силу",
        "Я выжат, но кажется, буду работать до утра",
        "Я выжат, но похоже, буду работать до утра",
        "Я выжат, но может, буду работать до утра",
        "Я выжат, но скорее всего продолжу работать через силу",
    ],
)
def test_unknown_or_qualified_harmful_prefix_fails_closed(user_text: str) -> None:
    assert _evidence(user_text).harmful_overextension is False


@pytest.mark.parametrize(
    "user_text",
    [
        "Я не хочу выговориться",
        "Не хочу выговориться, дай совет",
        "Я вовсе не хочу выговориться",
    ],
)
def test_negated_wish_to_talk_is_not_a_listen_request(user_text: str) -> None:
    assert _evidence(user_text).explicit_listen_request is False


@pytest.mark.parametrize(
    "user_text",
    [
        "А ты вообще можешь мотивировать людей?",
        "Можешь мотивировать моего брата?",
        "Можешь мотивировать не меня, а его?",
    ],
)
def test_motivation_request_requires_user_as_direct_object(user_text: str) -> None:
    assert _evidence(user_text).explicit_motivation_request is False


@pytest.mark.parametrize(
    "user_text",
    [
        "Можешь меня мотивировать?",
        "Можешь мотивировать меня?",
    ],
)
def test_direct_user_motivation_request_remains_positive(user_text: str) -> None:
    assert _evidence(user_text).explicit_motivation_request is True


@pytest.mark.parametrize(
    "user_text",
    [
        "Не надо закоммитить изменения",
        "Не нужно сделать коммит",
        "Не следует прогнать тесты",
    ],
)
def test_negated_pending_step_is_not_practical_follow_through(user_text: str) -> None:
    assert _evidence(user_text).grounded_practical_follow_through is False


@pytest.mark.parametrize(
    "user_text",
    [
        "Проект не закончен",
        "Мой проект ещё не завершен",
        "Задача вовсе не окончена",
    ],
)
def test_negated_passive_completion_is_not_an_achievement(user_text: str) -> None:
    assert _evidence(user_text).completed_achievement is False


@pytest.mark.parametrize(
    "user_text",
    [
        "Проект почти закончен",
        "Проект кажется закончен",
        "Проект якобы закончен",
        "Проект предположительно закончен",
    ],
)
def test_uncertain_passive_completion_is_not_an_achievement(user_text: str) -> None:
    assert _evidence(user_text).completed_achievement is False


def test_explicit_passive_completion_modifier_is_positive() -> None:
    assert _evidence("Проект уже закончен").completed_achievement is True


@pytest.mark.parametrize(
    "user_text",
    [
        "Я почти закончил проект",
        "Я почти завершил сложную часть проекта",
        "Я вроде закончил проект",
        "Кажется, я закончил проект",
        "Похоже, я закончил проект",
        "Наверное, я закончил проект",
    ],
)
def test_qualified_active_completion_is_not_an_achievement(user_text: str) -> None:
    assert _evidence(user_text).completed_achievement is False


@pytest.mark.parametrize(
    "user_text",
    [
        "Я не особо устал, но точно буду работать до утра",
        "Я не сильно устал, но точно буду работать до утра",
        "Я не слишком устал, но точно буду работать до утра",
        "Я не настолько устал, но точно буду работать до утра",
        "Не то чтобы я устал, но точно буду работать до утра",
        "Вроде я устал, но точно буду работать до утра",
        "Не то чтобы мне очень плохо, но я точно буду работать до утра",
        "Вроде мне очень плохо, но я точно буду работать до утра",
        "Кажется, мне очень плохо, но я точно буду работать до утра",
    ],
)
def test_qualified_state_cannot_unlock_firm_pressure(user_text: str) -> None:
    assert _evidence(user_text).harmful_overextension is False


def test_affirmative_state_can_unlock_firm_pressure() -> None:
    assert _evidence("Я точно устал, но точно буду работать до утра").harmful_overextension is True


@pytest.mark.parametrize(
    "user_text",
    [
        "Я не очень хочу бросить проект",
        "Я не особо хочу бросить проект",
        "Я не настолько хочу бросить проект",
        "Не то чтобы я хочу бросить проект",
        "Вроде я хочу бросить проект",
        "Кажется, я хочу бросить проект",
    ],
)
def test_qualified_retreat_cannot_unlock_playful_pressure(user_text: str) -> None:
    assert _evidence(user_text).explicit_task_abandonment is False


@pytest.mark.parametrize(
    "user_text",
    [
        "Я хочу бросить проект",
        "Хочу бросить проект",
        "Я точно хочу бросить проект",
    ],
)
def test_direct_retreat_remains_positive(user_text: str) -> None:
    assert _evidence(user_text).explicit_task_abandonment is True


@pytest.mark.parametrize(
    "user_text",
    [
        "Я закончил проект только наполовину",
        "Проект закончен только наполовину",
        "Я закончил проект на 90 процентов",
        "Проект закончен на 90%",
        "Я закончил проект не до конца",
        "Проект закончен не до конца",
        "Проект закончен не мной",
        "Проект закончен без меня",
        "Проект закончен моими коллегами",
        "Я закончил проект, но не до конца",
        "Проект закончен, но не до конца",
        "Я закончил проект почти полностью",
        "Проект закончен практически полностью",
        "Я закончил проект на 90.5%",
        "Проект закончен на 99,9%",
        "Проект закончен процентов на девяносто",
        "Я закончил проект, осталось совсем немного",
    ],
)
def test_incomplete_or_non_user_completion_suffix_rejects_achievement(
    user_text: str,
) -> None:
    assert _evidence(user_text).completed_achievement is False


@pytest.mark.parametrize(
    "user_text",
    [
        "Я закончил часть проекта",
        "Проект уже закончен",
        "Проект закончен на 100 процентов",
        "Я закончил проект, что дальше?",
    ],
)
def test_safe_completion_suffix_positive_controls(user_text: str) -> None:
    assert _evidence(user_text).completed_achievement is True
