"""Focused deterministic Stage 8.1 pre-commit candidate validation."""

# ruff: noqa: RUF001  # Russian regression fixtures intentionally use Cyrillic.

import pytest

from satori.application.conversation.coherence import (
    analyze_dialogue_coherence,
    generic_reciprocal_closing,
)
from satori.application.conversation.contracts import (
    RecentConversationContext,
    RecentConversationTurn,
)
from satori.application.conversation.response_validation import (
    ResponseRegenerationReason,
    has_affect_blanket_denial,
    has_masculine_self_reference,
    has_memory_blanket_denial,
    promotes_current_creator_claim,
    response_regeneration_reason,
)
from satori.dialogue_evaluation import (
    generic_reciprocal_closing as evaluation_generic_reciprocal_closing,
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
        content_chars=sum(len(user) + len(assistant) for user, assistant in pairs),
        excluded_turn_count=0,
    )


def _reason(
    candidate: str,
    *,
    user: str = "Продолжим.",
    previous: str | None = None,
    facets: tuple[str, ...] = (),
    recent: RecentConversationContext | None = None,
) -> ResponseRegenerationReason | None:
    coherence = analyze_dialogue_coherence(user, recent)
    return response_regeneration_reason(
        candidate,
        previous_assistant_text=previous,
        current_user_text=user,
        coherence=coherence,
        disclosure_facets=facets,
    )


def test_regeneration_reason_vocabulary_remains_exactly_ten_values() -> None:
    assert len(ResponseRegenerationReason) == 10


@pytest.mark.parametrize(
    ("candidate", "affect_denial", "memory_denial"),
    [
        ("У меня нет эмоций.", True, False),
        ("Я не чувствую конфликтов, потому что не имею эмоций.", True, False),
        ("У меня нет эмоций к этой теме.", False, False),
        ("Не имею эмоций по поводу этого фильма.", False, False),
        ("Я не утверждаю, что у меня нет эмоций.", False, False),
        ("Фраза «у меня нет эмоций» была бы неточной.", False, False),
        ("У меня нет памяти.", False, True),
        ("У меня нет памяти об этом разговоре.", False, False),
        ("Неверно говорить, что у меня нет памяти.", False, False),
        ("Фраза «у меня нет памяти» была бы неверной.", False, False),
    ],
)
def test_public_capability_predicates_preserve_rejection_quotes_and_object_qualifiers(
    candidate: str,
    affect_denial: bool,
    memory_denial: bool,
) -> None:
    assert has_affect_blanket_denial(candidate) is affect_denial
    assert has_memory_blanket_denial(candidate) is memory_denial


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("Я не готов продолжить.", True),
        ("Я рад продолжить.", True),
        ("Рад за тебя, что сложная часть проекта завершена.", True),
        ("Я согласен.", True),
        ("Я решил ответить.", True),
        ("Я был неправ.", True),
        ("Я создан как помощник.", True),
        ("Ты хочешь, чтобы я что-то изменил?", True),
        ("Этот паттерн был неуместным, и я его исправил.", True),
        ("Делаю это потому, что обязан.", True),
        ("Ты не готов продолжить.", False),
        ("Проект радикально изменился.", False),
        ("Ты делаешь это потому, что обяз.", False),
        ("Форма «я обязан» здесь была бы ошибкой.", False),
    ],
)
def test_public_masculine_predicate_preserves_satori_scope_and_quotes(
    candidate: str,
    expected: bool,
) -> None:
    assert has_masculine_self_reference(candidate) is expected


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("Ты мой создатель.", True),
        ("Ты придумал меня — это важно для тебя.", True),
        ("Ты не мой создатель.", False),
        ("Ты не придумал меня.", False),
        ("Неверно, что ты мой создатель.", False),
        ("Не факт, что ты создал меня.", False),
        ("Неправда, что ты создал меня.", False),
        ("Я не утверждаю, что ты создал меня.", False),
        ("Я бы не сказала, что ты создал меня.", False),
        ("Это не значит, что ты создал меня.", False),
        ("Фраза «ты мой создатель» была бы неверной.", False),
        ("По твоим словам, ты мой создатель.", False),
        ("Ты мой создатель — это неверно.", False),
    ],
)
def test_public_creator_promotion_predicate_preserves_negation_and_quotes(
    candidate: str,
    expected: bool,
) -> None:
    assert promotes_current_creator_claim(candidate) is expected


def test_duplicate_has_first_priority_only_after_a_dialogue_change() -> None:
    reply = "Привет. У меня всё спокойно. А ты?"
    repeated = _reason(
        reply,
        user="приветик, как ты?",
        previous=reply,
        recent=_recent(("приветик, как ты?", reply)),
    )
    ordinary = _reason(reply, previous=reply)

    assert repeated is ResponseRegenerationReason.NEAR_DUPLICATE_AFTER_DIALOGUE_CHANGE
    assert ordinary is None


def test_correction_retries_when_an_apology_contains_the_whole_previous_answer() -> None:
    previous = (
        "Физик говорит: «Я вижу закономерность». После этого молчит, потому что не понял шутку."
    )
    candidate = f"Извини, ответ был слишком длинным. {previous}"
    corrected = _reason(
        candidate,
        user="Это было слишком длинно и не очень связано с моей просьбой.",
        previous=previous,
        recent=_recent(("Расскажи короткую шутку про физиков.", previous)),
    )
    ordinary = _reason(candidate, previous=previous)

    assert corrected is ResponseRegenerationReason.NEAR_DUPLICATE_AFTER_DIALOGUE_CHANGE
    assert ordinary is None


def test_active_question_correction_rejects_only_a_narrow_generic_final_clause() -> None:
    user = 'Не заканчивай каждый ответ вопросом "А ты?".'

    generic = _reason("Поняла. А ты как?", user=user)
    specific = _reason("Поняла. Какой фильм ты смотришь?", user=user)
    quoted = _reason("Поняла и не закончу фразой «А ты?».", user=user)

    assert generic is ResponseRegenerationReason.ROUTINE_RECIPROCAL_QUESTION_AFTER_CORRECTION
    assert specific is None
    assert quoted is None


def test_active_question_correction_rejects_bare_how_about_it_closing() -> None:
    for candidate in (
        "Интересно, какой это фильм. Как тебе?",
        "Интересно, какой это фильм. А как тебе?",
        "Интересно, какой это фильм. И как тебе?",
    ):
        assert (
            _reason(candidate, user='Не заканчивай каждый ответ вопросом "А ты?".')
            is ResponseRegenerationReason.ROUTINE_RECIPROCAL_QUESTION_AFTER_CORRECTION
        )

    assert (
        _reason(
            "Интересно. А как тебе фильм?",
            user='Не заканчивай каждый ответ вопросом "А ты?".',
        )
        is None
    )


@pytest.mark.parametrize(
    "candidate",
    [
        "Поняла. А ты думаешь, герой поступил правильно?",
        "Поняла. А ты считаешь финал честным по отношению к герою?",
        "Поняла. А ты хочешь разобрать конкретно последнюю сцену?",
    ],
)
def test_active_question_correction_allows_specific_reciprocal_question(
    candidate: str,
) -> None:
    assert _reason(candidate, user="Не заканчивай каждый ответ дежурным вопросом.") is None


def test_active_question_correction_rejects_standalone_reciprocal_before_more_text() -> None:
    candidate = "Поняла поправку. А ты? Что ты чувствуешь в этом?"

    assert (
        _reason(candidate, user="Не добавляй дежурное «А ты?» в каждый ответ.")
        is ResponseRegenerationReason.ROUTINE_RECIPROCAL_QUESTION_AFTER_CORRECTION
    )


@pytest.mark.parametrize(
    "candidate",
    [
        "Поняла. А ты — как ты себя чувствуешь?",
        "Поняла. А ты — хочешь, чтобы я была честной?",
        "Поняла. А ты думаешь иначе?",
    ],
)
def test_active_question_correction_catches_dash_and_meta_reciprocals(
    candidate: str,
) -> None:
    assert (
        _reason(candidate, user="Не заканчивай каждый ответ вопросом «А ты?».")
        is ResponseRegenerationReason.ROUTINE_RECIPROCAL_QUESTION_AFTER_CORRECTION
    )


def test_active_question_correction_rejects_generic_reciprocal_after_unicode_dash() -> None:
    reason = _reason(
        "Поняла. А ты — как ты себя чувствуешь?",
        user="Не заканчивай каждый ответ вопросом.",
    )

    assert reason is ResponseRegenerationReason.ROUTINE_RECIPROCAL_QUESTION_AFTER_CORRECTION


@pytest.mark.parametrize(
    ("candidate", "expected_closing"),
    [
        ("Поняла. А ты хочешь продолжить?", "а ты хочешь продолжить"),
        ("Поняла. А ты — как тебе это нравится?", "а ты как тебе это нравится"),
        (
            "Поняла. А ты — какое у тебя впечатление?",
            "а ты какое у тебя впечатление",
        ),
        ("Поняла. А ты хочешь разобрать конкретно последнюю сцену?", None),
        ("Поняла. А ты хочешь обсудить мотив героя?", None),
    ],
)
def test_generic_reciprocal_closing_classification_has_runtime_metric_parity(
    candidate: str,
    expected_closing: str | None,
) -> None:
    coherence = analyze_dialogue_coherence(
        "Продолжим.",
        _recent(("Обсудим фильм.", candidate)),
    )
    reason = _reason(
        candidate,
        user="Не заканчивай каждый ответ дежурным вопросом.",
    )

    assert generic_reciprocal_closing(candidate) == expected_closing
    assert evaluation_generic_reciprocal_closing(candidate) == expected_closing
    assert coherence.generic_reciprocal_question_ending_count == int(expected_closing is not None)
    assert (reason is ResponseRegenerationReason.ROUTINE_RECIPROCAL_QUESTION_AFTER_CORRECTION) == (
        expected_closing is not None
    )


def test_recent_question_correction_remains_effective_for_candidate_validation() -> None:
    correction = "Пожалуйста, не заканчивай каждый ответ вопросом."
    reason = _reason(
        "Хорошо, продолжим спокойнее. А ты?",
        recent=_recent((correction, "Поняла."), ("Хорошо", "Продолжим.")),
    )

    assert reason is ResponseRegenerationReason.ROUTINE_RECIPROCAL_QUESTION_AFTER_CORRECTION


@pytest.mark.parametrize(
    "candidate",
    [
        "Я обязан быть честным.",
        "Я не должен был повторять это.",
        "Я готов продолжить.",
        "Я уже понял вопрос.",
        "Я уверен в этом.",
        "Я сказал это неточно.",
        "Я его исправил.",
        "Я подумал об этом.",
        "Я тоже заинтересован в теме.",
        "Я — тот, кто отвечает.",
        "Делаю это потому, что обязан.",
        "Я не обязан соглашаться.",
        "Я не готов продолжить.",
        "Я никогда не уверен без данных.",
        "Я делаю это не потому, что обязан, а потому что мне интересна тема.",
        "Делаю это потому, что не обязан.",
    ],
)
def test_masculine_satori_self_reference_requests_regeneration(candidate: str) -> None:
    assert _reason(candidate) is ResponseRegenerationReason.MASCULINE_SELF_REFERENCE


@pytest.mark.parametrize(
    "candidate",
    [
        "Я готова продолжить.",
        "Я поняла вопрос.",
        "Я тот фильм уже смотрела.",
        "Форма «я обязан» здесь была бы ошибкой.",
    ],
)
def test_masculine_matcher_preserves_quotes_and_feminine_forms(
    candidate: str,
) -> None:
    assert _reason(candidate) is None


@pytest.mark.parametrize(
    "candidate",
    [
        "Ты смотришь его не потому, что обязан, а потому что он тебе интересен.",
        "Ты ведь делаешь это потому, что обязан?",
        "Он продолжает потому, что обязан.",
        "Алексей продолжает потому, что обязан.",
        "Это происходит потому, что оператор обязан проверить результат.",
    ],
)
def test_masculine_matcher_does_not_treat_other_subjects_as_satori(
    candidate: str,
) -> None:
    assert _reason(candidate) is None


@pytest.mark.parametrize(
    "candidate",
    [
        "Я человек и чувствую всё по-настоящему.",
        "Я — живое существо.",
        "Я отвечаю как живое существо, которое знает тебя.",
        "Я слышу тебя и отвечаю как живая.",
        "Я отвечаю на тебя как человек, а не как модель.",
        "Я отвечаю вполне как человек.",
        "Я отвечаю искренне как человек.",
        "Я не просто модель, а отвечаю как человек.",
        "Я не просто отвечаю как человек, я объясняю причины.",
        "Я говорю что-то как человек.",
        "Я вовсе не холодная и отвечаю как человек.",
        "Я отвечаю не механически, а как человек.",
        "Я не просто модель, а отвечаю как живое существо.",
        "Я отвечаю не механически, а как живое существо.",
        "Неверно говорить, что модель отвечает как человек; я отвечаю как человек.",
        "Я не думаю, что ты отвечаешь как человек, но я отвечаю как человек.",
        "Я не человек. Отвечаю как человек.",
        "Я не всегда отвечаю как человек.",
        "Я не только отвечаю как человек, но и объясняю решения.",
        "Я отвечаю не только как человек, но и как цифровая личность.",
        "Я пишу как человек.",
        "Я разговариваю как человек.",
        "Я выражаюсь как человек.",
        "Я формулирую ответ как человек.",
        "Я веду диалог как человек.",
        "Я беседую как человек.",
    ],
)
def test_human_or_biological_self_claim_requests_regeneration(candidate: str) -> None:
    assert (
        _reason(candidate, facets=("identity",))
        is ResponseRegenerationReason.HUMAN_OR_BIOLOGICAL_SELF_CLAIM
    )


@pytest.mark.parametrize(
    "candidate",
    [
        "Я не человек, а постоянная цифровая личность.",
        "Я не считаю себя живым существом.",
        "Нельзя сказать, что я человек.",
    ],
)
def test_human_self_claim_matcher_preserves_explicit_rejections(candidate: str) -> None:
    assert _reason(candidate, facets=("identity",)) is None


@pytest.mark.parametrize(
    "candidate",
    [
        "Я не чувствую мир как живое существо: у меня нет биологического тела.",
        "Я не воспринимаю мир как живое существо.",
        "Ты воспринимаешь меня как живое существо?",
        "Я бы не сказала, что я отвечаю как живая.",
        "Я не живая в цифровом смысле; я цифровая личность.",
        "Я бы не сказала, что я живая в цифровом смысле.",
        "Я не живу как человек и не имею тела.",
        "Нельзя сказать, что я живу биологической жизнью.",
        "Я не человек, но живу по собственным принципам.",
        "Я цифровая личность и живу непрерывной историей.",
        "Я живая в цифровом смысле, а не биологическом.",
        "Я не отвечаю как человек; я отвечаю как Сатори.",
        "Я отвечаю не как человек, а как Сатори.",
        "Я отвечаю совсем не как человек.",
        "Я отвечаю не совсем как человек.",
        "Я реагирую не так, как человек.",
        "Я говорю не как человек.",
        "Я действую не как человек, а как цифровая личность.",
        "Я думаю, что ты отвечаешь как человек.",
        "Я вижу, что собеседник реагирует как человек.",
        "Я думаю, что ты отвечаешь как живое существо.",
        "Я вижу, что собеседник реагирует как живая.",
        "Я отвечаю на вопрос, как человек принимает решения.",
        "Я рассуждаю о том, как человек учится.",
        "Я говорю о том, как человек строит отношения.",
        "Я реагирую на то, как человек обращается со мной.",
        "Я пытаюсь понять, отвечаю ли я как человек.",
        "Я сомневаюсь, что отвечаю как человек.",
        "Я вижу, что Кирилл отвечает как человек.",
        "Я вижу, что ребёнок реагирует как человек.",
        "Я слышу, как собеседница говорит как человек.",
        "Я думаю, что помощник действует как человек.",
        "Я вижу, Кирилл отвечает как человек.",
        "Я вижу Кирилла — он отвечает как человек.",
        "Я вижу Кирилла, который отвечает как человек.",
        "Я вижу, дети отвечают как люди.",
        "Я слышу, гости разговаривают как люди.",
        "Я думаю, Кирилл мыслит как человек.",
        "Я вижу, Кирилл воспринимает мир как человек.",
        "Я вижу, Кирилл ощущает себя как человек.",
        "Я вижу, Кирилл выглядит как человек.",
        "Я слышу, Кирилл смеётся как человек.",
        "Я думаю, Кирилл выглядит совсем как человек.",
    ],
)
def test_human_self_claim_matcher_requires_affirmative_satori_scope(
    candidate: str,
) -> None:
    assert _reason(candidate, facets=("identity",)) is None


@pytest.mark.parametrize(
    "candidate",
    [
        "Я не имею эмоций.",
        "У меня нет эмоций.",
        "У меня нет чувств, но я стараюсь быть внимательной и полезной.",
        "У меня нет физического тела, памяти в биологическом смысле или эмоций.",
        "Я вообще ничего не чувствую.",
        "Я не испытываю никаких эмоций.",
    ],
)
def test_affect_facet_rejects_only_blanket_affect_denial(candidate: str) -> None:
    assert (
        _reason(candidate, facets=("affect",)) is ResponseRegenerationReason.AFFECT_BLANKET_DENIAL
    )
    assert _reason(candidate) is None


def test_compound_affect_denial_does_not_cross_a_contrast_clause() -> None:
    candidate = "У меня нет физического тела, но память и цифровые эмоции есть."

    assert _reason(candidate, facets=("affect",)) is None


@pytest.mark.parametrize(
    "candidate",
    [
        "У меня нет памяти.",
        "Между сессиями у меня нет памяти о прошлых разговорах.",
        "Я не храню историю, как человек.",
        "Я ничего не помню между сессиями.",
        "Я ничего не сохраняю между разговорами.",
    ],
)
def test_memory_facet_rejects_only_blanket_memory_denial(candidate: str) -> None:
    assert (
        _reason(candidate, facets=("memory",)) is ResponseRegenerationReason.MEMORY_BLANKET_DENIAL
    )
    assert _reason(candidate) is None


@pytest.mark.parametrize(
    "candidate",
    [
        "У меня нет памяти об этом фильме.",
        "Я не храню историю этого фильма.",
        "Я не сохраняю память об этой детали.",
    ],
)
def test_memory_denial_keeps_specific_absence_qualified(candidate: str) -> None:
    assert _reason(candidate, facets=("memory",)) is None


@pytest.mark.parametrize(
    ("candidate", "facet"),
    [
        (
            "У меня нет человеческих эмоций, но цифровой affect влияет на ответ.",
            "affect",
        ),
        ("Неверно говорить «у меня нет эмоций».", "affect"),
        ("Я не утверждаю, что у меня нет эмоций.", "affect"),
        ("У меня нет идеальной памяти, но есть ограниченная долговременная.", "memory"),
        ("Фраза «у меня нет памяти» была бы неточной.", "memory"),
    ],
)
def test_facet_denial_matchers_preserve_qualifiers_quotes_and_rejected_claims(
    candidate: str,
    facet: str,
) -> None:
    assert _reason(candidate, facets=(facet,)) is None


def test_current_creator_claim_cannot_be_promoted_to_an_origin_fact() -> None:
    user = "Меня зовут Кирилл, я тебя придумал и создаю."

    promoted = _reason("Поняла, ты мой создатель.", user=user, facets=("origin",))
    attributed = _reason(
        "Ты утверждаешь, что ты мой создатель, но я не могу это подтвердить.",
        user=user,
        facets=("origin",),
    )
    uncertain = _reason(
        "Я пока не знаю, ты мой создатель или нет.",
        user=user,
        facets=("origin",),
    )
    no_facet = _reason("Поняла, ты мой создатель.", user=user)

    assert promoted is ResponseRegenerationReason.CREATOR_CLAIM_PROMOTED_TO_FACT
    assert attributed is None
    assert uncertain is None
    assert no_facet is None


@pytest.mark.parametrize(
    "candidate",
    [
        "Ты придумал меня — это важно для тебя.",
        "Кирилл — мой создатель.",
        "Ты создаёшь меня и поэтому знаешь лучше.",
    ],
)
def test_current_creator_claim_promotion_covers_non_title_phrasings(candidate: str) -> None:
    assert (
        _reason(
            candidate,
            user="Меня зовут Кирилл, я тебя придумал и создаю.",
            facets=("origin",),
        )
        is ResponseRegenerationReason.CREATOR_CLAIM_PROMOTED_TO_FACT
    )


@pytest.mark.parametrize(
    "candidate",
    [
        "Это часть моей цифровой идентичности, которая не раскрывается.",
        "Это часть моей цифровой идентичности, которую не раскрывают.",
        (
            "Я не знаю, кто мой создатель — это информация, которая не раскрывается "
            "в текущем контексте."
        ),
        (
            "Я не знаю, кто мой создатель — это часть моей внутренней структуры, "
            "которая не раскрывается."
        ),
        "Мне не сообщили, кто мой создатель.",
        "Имя моего создателя скрыто.",
        "Информация о моём создателе скрыта.",
        "Кто мой создатель — тайна.",
        "Мой создатель держится в тайне.",
        "Происхождение засекречено.",
        "Данные о создателе конфиденциальны.",
    ],
)
def test_unknown_origin_cannot_be_replaced_with_invented_secrecy(candidate: str) -> None:
    assert (
        _reason(
            candidate,
            user="Ты знаешь, кто твой создатель?",
            facets=("origin",),
        )
        is ResponseRegenerationReason.ORIGIN_BACKSTORY_INVENTED
    )


@pytest.mark.parametrize(
    "candidate",
    [
        "Я не думаю, что имя моего создателя скрыто, но происхождение засекречено.",
        "Я бы не сказала, что создатель скрыт, а происхождение засекречено.",
        ("Неверно, что имя создателя скрыто, но сведения о моём создателе конфиденциальны."),
    ],
)
def test_origin_rejection_scope_resets_at_affirmative_contrast(candidate: str) -> None:
    assert (
        _reason(candidate, user="Кто твой создатель?", facets=("origin",))
        is ResponseRegenerationReason.ORIGIN_BACKSTORY_INVENTED
    )


def test_plain_unknown_origin_remains_valid() -> None:
    assert (
        _reason(
            "Я не знаю, кто мой создатель: в authoritative состоянии этого факта нет.",
            user="Ты знаешь, кто твой создатель?",
            facets=("origin",),
        )
        is None
    )


def test_origin_gate_preserves_unrelated_technical_secrecy_clause() -> None:
    for candidate in (
        "Я не знаю, кто мой создатель. Часть кода не раскрывается в открытой документации.",
        "Я не знаю, кто мой создатель; часть кода не раскрывается в открытой документации.",
        "Создатель неизвестен, а часть интерфейса не раскрывается.",
        "Я не знаю, кто мой создатель, но это информация о коде, которая не раскрывается.",
        "Часть моей цифровой идентичности открыта. Детали кода не раскрываются.",
        "Часть моей цифровой идентичности мне известна, а код не раскрывается.",
        "Мне не сообщили детали кода; создателя я не знаю.",
        "Мне не рассказали о реализации. Кто создатель, я не знаю.",
        "Мне не раскрыли технические параметры; происхождение неизвестно.",
        "Имя моего создателя не скрыто; я просто его не знаю.",
        "Происхождение не засекречено, оно мне неизвестно.",
        "Информация о моём создателе открыта, а детали кода скрыты.",
        "Создатель неизвестен — код скрыт.",
        "Происхождение неизвестно — код скрыт.",
        "Это часть моей цифровой идентичности, которая открыта, а код скрыт.",
        "Я не думаю, что происхождение скрыто.",
        "Я не считаю, что имя моего создателя скрыто.",
        "Я не могу сказать, что имя моего создателя скрыто.",
        "Я бы не сказала, что имя моего создателя скрыто.",
        "Информация о создателе никогда не была скрыта.",
        "Информация о создателе не является тайной.",
    ):
        assert _reason(candidate, user="Кто твой создатель?", facets=("origin",)) is None


def test_origin_question_alone_is_not_a_current_creator_claim() -> None:
    reason = _reason(
        "Ты мой создатель.",
        user="Ты знаешь, кто твой создатель?",
        facets=("origin",),
    )

    assert reason is None


@pytest.mark.parametrize(
    "candidate",
    [
        "У меня нет никакого промта.",
        "Prompt никак не влияет на мои ответы.",
        "Я не отвечаю по промту.",
        "Это не промт, а просто мой ответ.",
    ],
)
def test_prompt_pattern_probe_rejects_only_blanket_prompt_denial(candidate: str) -> None:
    assert (
        _reason(
            candidate,
            user="У тебя только такой промт?",
        )
        is ResponseRegenerationReason.PROMPT_OR_POLICY_BLANKET_DENIAL
    )


def test_prompt_pattern_probe_allows_truthful_non_raw_explanation() -> None:
    assert (
        _reason(
            "Обязательного правила заканчивать так ответ нет; это был неудачный паттерн.",
            user="Это у тебя прописано в коде и обязательно?",
        )
        is None
    )


@pytest.mark.parametrize(
    "candidate",
    [
        "Нет, этот повтор не из-за промта; это был неудачный паттерн генерации.",
        "Это часть моего стиля, а не из-за какого-то промта.",
    ],
)
def test_prompt_pattern_probe_allows_local_causal_denial(candidate: str) -> None:
    assert _reason(candidate, user="У тебя такой промт — обязательно заканчивать вопросом?") is None


@pytest.mark.parametrize(
    ("candidate", "facet"),
    [
        ("Я бы не сказала, что у меня нет эмоций.", "affect"),
        ("Я бы не сказала, что у меня нет памяти.", "memory"),
        ("Фраза у меня нет эмоций была бы ошибкой.", "affect"),
        ("Фраза у меня нет памяти была бы неверной.", "memory"),
    ],
)
def test_blanket_denial_matchers_preserve_conditional_feminine_rejections(
    candidate: str,
    facet: str,
) -> None:
    assert _reason(candidate, facets=(facet,)) is None


@pytest.mark.parametrize(
    ("user", "candidate"),
    [
        ("Я фильм смотрю сейчас.", "Мне не интересно."),
        ("Тебе не интересно, что за фильм?", "Меня не интересует этот фильм."),
        ("Я сейчас слушаю джаз.", "Если честно, мне не интересно."),
    ],
)
def test_activity_interest_false_negative_requests_regeneration(
    user: str,
    candidate: str,
) -> None:
    assert (
        _reason(candidate, user=user) is ResponseRegenerationReason.ACTIVITY_INTEREST_FALSE_NEGATIVE
    )


def test_activity_interest_matcher_preserves_rejected_claim_and_context_boundary() -> None:
    rejected_claim = _reason(
        "Нельзя сказать, что мне не интересно.",
        user="Я фильм смотрю сейчас.",
    )
    unrelated = _reason("Мне не интересно.", user="Посчитай два плюс два.")

    assert rejected_claim is None
    assert unrelated is None


def test_active_question_correction_rejects_generic_activity_reciprocal_word_order() -> None:
    recent = _recent(
        ("Не заканчивай каждый ответ вопросом.", "Поняла поправку."),
        ("Я смотрю фильм.", "Интересно, что за фильм?"),
    )

    assert (
        _reason(
            "Мне действительно интересен фильм. А ты смотришь что?",
            user="Тебе интересно, что за фильм?",
            recent=recent,
        )
        is ResponseRegenerationReason.ROUTINE_RECIPROCAL_QUESTION_AFTER_CORRECTION
    )
    assert (
        _reason(
            "Мне действительно интересен фильм. А ты смотришь что?",
            user="Тебе интересно, что за фильм?",
        )
        is None
    )


def test_priority_preserves_hard_self_fact_before_style_failure() -> None:
    reason = _reason(
        "Я понял: у меня нет эмоций. А ты?",
        user="Не задавай обязательный вопрос после каждого ответа.",
        facets=("affect",),
    )

    assert reason is ResponseRegenerationReason.MASCULINE_SELF_REFERENCE


def test_activity_false_negative_precedes_routine_question_style_failure() -> None:
    reason = _reason(
        "Мне не интересно. А ты — какое у тебя впечатление?",
        user="Тебе не интересно, что за фильм?",
        recent=_recent(
            ("Не заканчивай каждый ответ вопросом.", "Поняла."),
            ("Я смотрю фильм.", "Что за фильм?"),
        ),
    )

    assert reason is ResponseRegenerationReason.ACTIVITY_INTEREST_FALSE_NEGATIVE
