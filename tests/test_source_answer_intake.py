"""Actual source questions are operations; instructions/examples and effects are not."""

# ruff: noqa: RUF001 - intentional Russian owner input

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from satori.application.assistant.action_contracts import AssistantIntent
from satori.application.assistant.intents import parse_assistant_intent
from satori.application.assistant.mail_lookup import (
    parse_mail_chat_read_intent,
    parse_mail_lookup_intent,
)
from satori.application.assistant.source_answer_intake import (
    classify_source_answer_followup,
    is_explicit_mailbox_lookup,
    is_source_answer_followup_question,
    named_incoming_contact,
    parse_source_answer_intent,
    source_answer_sources,
    source_only_followup,
)

NOW = datetime(2026, 11, 6, 8, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "sources"),
    [
        ("Найди письмо от Acme в Telegram?", ("telegram",)),
        ("Найди письмо в Telegram от Acme", ("telegram",)),
        ("Найди письмо от Acme в «Telegram»?", ("telegram",)),
        ("Посмотри в Телеграме письма от Почты России", ("telegram",)),
        ("Найди письмо от Acme в почте и в Telegram", ("mail", "telegram")),
    ],
)
def test_explicit_telegram_scope_beats_implicit_gmail_letter_noun(
    text: str, sources: tuple[str, ...]
) -> None:
    assert parse_mail_chat_read_intent(text, timezone="Europe/Moscow") is None
    assert parse_mail_lookup_intent(text, timezone="Europe/Moscow", request_id=uuid4()) is None
    intent = parse_assistant_intent(text, now=NOW, timezone="Europe/Moscow")
    assert intent is not None
    assert intent.kind == "source_answer"
    assert source_answer_sources(intent) == sources


@pytest.mark.parametrize(
    "text",
    ["Найди письмо от Telegram", "Найди письмо с темой «В Telegram новый выпуск»"],
)
def test_telegram_name_in_mail_sender_or_quoted_subject_is_not_a_source(text: str) -> None:
    intent = parse_assistant_intent(text, now=NOW, timezone="Europe/Moscow")
    assert intent is not None
    assert intent.kind == "mailbox_search"
    assert intent.source == "mail"


@pytest.mark.parametrize(
    ("text", "source"),
    [
        ("привет, посмотри, написали ли мне по собесу последнему", None),
        ("Проверь почту и Telegram: есть ли ответ от рекрутера?", None),
        ("Найди письмо от info@example.com", "mail"),
        ("Найди в почте: билеты", "mail"),
        ("Посмотри в ТГ, что мне ответили по собеседованию", "telegram"),
        ("Мне ответили ли по собеседованию?", None),
        ("Проанализируй последний звонок", None),
        ("Можешь проверить почту по последнему интервью?", "mail"),
        ("Привет, у меня вчера был собес, посмотри, написали ли мне", None),
        ("Скажи, написали ли мне по собесу", None),
    ],
)
def test_task_intake_preserves_owner_text_and_request_identity(
    text: str, source: str | None
) -> None:
    request_id = uuid4()
    value = parse_source_answer_intent(
        text, now=NOW, timezone="Europe/Moscow", request_id=request_id
    )
    assert value is not None
    assert value.kind == "source_answer"
    assert value.query == text
    assert value.source == source
    assert value.request_id == request_id


@pytest.mark.parametrize(
    "text",
    [
        "Привет, как дела?",
        "Расскажи, как лучше пройти собеседование",
        "Как проверить почту?",
        "Переведи: проверь почту",
        "Не проверяй почту",
        "Не надо искать письма",
        "Ответь в Telegram: спасибо за приглашение",
        "Напомни завтра проверить почту",
        "Найди в выбранной почте приглашение",
        "Что значит: написали ли мне?",
        "Что ответить другу в Telegram?",
        "Есть ли у Telegram API?",
        "Что делать с письмом?",
        "Сегодня написали мне из компании после собеседования.",
        "Рекрутер ответил мне в Telegram.",
        "Мне ответили из компании после интервью.",
        "Сегодня появился ответ по собеседованию.",
        "Сегодня пришел ответ на почту.",
        "Я знаю, что мне написали в телеграмме.",
        "Я знаю, кто мне звонил вчера.",
        "Сегодня друг сказал: проверь почту",
        "Он попросил меня проверить почту",
        "Я сказал другу, проверь почту",
        "У меня в письме написано, проверь почту",
        "Расскажи про Telegram",
        "Скажи, что такое Gmail?",
        "Покажи как работает Gmail",
        "Братик, покажи как работает Gmail",
        "Покажи, как работает Gmail",
        "У меня есть вопрос, покажи, как работает Gmail",
    ],
)
def test_no_ambient_source_access_or_effect_reclassification(text: str) -> None:
    assert parse_source_answer_intent(text, now=NOW, timezone="Europe/Moscow") is None


def test_legacy_mail_and_selected_modes_remain_explicit_and_mobile_sees_new_question() -> None:
    direct = parse_assistant_intent(
        "Найди письмо от info@example.com", now=NOW, timezone="Europe/Moscow"
    )
    selected = parse_assistant_intent(
        "Найди в выбранной почте приглашение", now=NOW, timezone="Europe/Moscow"
    )
    question = parse_assistant_intent(
        "посмотри, написали ли мне по собесу последнему", now=NOW, timezone="Europe/Moscow"
    )
    assert direct is not None
    assert direct.kind == "mailbox_search"
    assert selected is not None
    assert selected.kind == "selected_lookup"
    assert question is not None
    assert question.kind == "source_answer"


def test_new_task_bound_does_not_widen_legacy_literal_search() -> None:
    value = parse_source_answer_intent(
        "Проверь почту: " + "тема " * 250, now=NOW, timezone="Europe/Moscow"
    )
    assert value is not None
    assert value.kind == "source_answer"
    with pytest.raises(ValidationError):
        AssistantIntent(
            request_id=uuid4(), kind="selected_lookup", timezone="Europe/Moscow", query="x" * 1001
        )


@pytest.mark.parametrize("topic", ["Яндекс", "В Сбере", "Data Engineer, в прошлую пятницу"])
def test_live_clarification_may_accept_a_short_topic(topic: str) -> None:
    assert classify_source_answer_followup(topic) == "clarification"


@pytest.mark.parametrize(
    "text", ["Привет", "Спасибо!", "Что у меня завтра?", "Проверь почту", "Напомни завтра", "a\nb"]
)
def test_pending_question_does_not_capture_unrelated_conversation(text: str) -> None:
    assert classify_source_answer_followup(text) == "unrelated"


def test_explicit_cancellation_is_distinct_from_a_new_topic() -> None:
    assert classify_source_answer_followup("Не надо") == "cancel"


@pytest.mark.parametrize(
    ("text", "sources"),
    [
        ("Проанализируй последний звонок", ("calls",)),
        ("Проверь письма и звонки", ("mail", "calls")),
        ("Проверь почту и ТГ", ("mail", "telegram")),
        ("Посмотри, написали ли мне по собесу", ("mail", "telegram")),
        ("Посмотри, написали ли мне по собесу в Почта Банк", ("mail", "telegram")),
        ("Проверь почту, что компания Telegram ответила по собеседованию", ("mail",)),
        ("Проверь почту по теме «В Telegram новый выпуск»", ("mail",)),
        ("В почте или в Telegram посмотри, есть ли ответ от Acme", ("mail", "telegram")),
        ("Проверь Gmail или телегу, есть ли ответ от Acme", ("mail", "telegram")),
    ],
)
def test_named_sources_never_silently_add_another_account_surface(
    text: str, sources: tuple[str, ...]
) -> None:
    intent = parse_source_answer_intent(text, now=NOW, timezone="Europe/Moscow")
    assert intent is not None
    assert source_answer_sources(intent) == sources


@pytest.mark.parametrize("text", ["а во сколько?", "Когда собеседование?", "И что они написали?"])
def test_short_source_followup_can_stay_with_current_answer(text: str) -> None:
    assert is_source_answer_followup_question(text)


@pytest.mark.parametrize(
    "text", ["Как дела?", "Что у меня завтра?", "Ответь, что приду", "Создай созвон", "Спасибо"]
)
def test_followup_never_grants_an_effect_or_steals_unrelated_question(text: str) -> None:
    assert not is_source_answer_followup_question(text)


@pytest.mark.parametrize(
    ("text", "contact", "sources"),
    [
        ("А Алексей мне сегодня писал?", "Алексей", ("mail", "telegram")),
        ("В Telegram Алексей Иванов вчера писал мне?", "Алексей Иванов", ("telegram",)),
        ("Проверь, Алексей писал ли мне сегодня", "Алексей", ("mail", "telegram")),
        ("Мне сегодня писал Алексей?", "Алексей", ("mail", "telegram")),
        ("Писал ли мне Алексей сегодня?", "Алексей", ("mail", "telegram")),
        ("В телеграме проверь, писал ли мне Алексей сегодня?", "Алексей", ("telegram",)),
        ("Посмотри в тг, писал ли мне Алексей сегодня", "Алексей", ("telegram",)),
        ("Сегодня Алексей писал?", "Алексей", ("mail", "telegram")),
        ("А @public_contact мне сегодня писал?", "@public_contact", ("mail", "telegram")),
        ("Привет, Алексей мне сегодня писал?", "Алексей", ("mail", "telegram")),
        ("Сатори, Алексей мне сегодня писал?", "Алексей", ("mail", "telegram")),
        ("Можешь проверить, Алексей мне сегодня писал?", "Алексей", ("mail", "telegram")),
        ("Сатори, сегодня Алексей писал?", "Алексей", ("mail", "telegram")),
    ],
)
def test_direct_incoming_question_keeps_the_literal_contact_and_source(
    text: str, contact: str, sources: tuple[str, ...]
) -> None:
    assert named_incoming_contact(text) == contact
    intent = parse_source_answer_intent(text, now=NOW, timezone="Europe/Moscow")
    assert intent is not None
    assert intent.query == text
    assert source_answer_sources(intent) == sources


@pytest.mark.parametrize(
    "text",
    [
        "Алексей мне сегодня писал.",
        "Он мне сегодня писал?",
        "Может, Алексей мне сегодня писал?",
        "«Алексей мне сегодня писал?»",
        "Сатори, «Алексей мне сегодня писал?»",
        "Алексей вчера писал тебе?",
        "В ТГ посмотри, как сделать бота",
    ],
)
def test_no_incoming_contact_authority_in_reported_or_speculative_text(text: str) -> None:
    assert named_incoming_contact(text) is None
    assert parse_source_answer_intent(text, now=NOW, timezone="Europe/Moscow") is None


@pytest.mark.parametrize(
    ("text", "source"),
    [
        ("В тг посмотри", "telegram"),
        ("Сатори, в тг посмотри", "telegram"),
        ("В телеге проверь", "telegram"),
        ("Проверь в почте", "mail"),
    ],
)
def test_source_first_inspection_has_an_explicit_scope(text: str, source: str) -> None:
    assert source_only_followup(text) == source
    intent = parse_source_answer_intent(text, now=NOW, timezone="Europe/Moscow")
    assert intent is not None
    assert intent.source == source
    assert source_answer_sources(intent) == (source,)


@pytest.mark.parametrize(
    ("text", "exact"),
    [
        ("Найди в почте: newer_than:30d", True),
        ("Найди письмо от info@example.com", True),
        ("Найди письмо с темой Интервью", True),
        ("Найди письмо о собеседовании", False),
    ],
)
def test_exact_gmail_command_precedence_keeps_natural_source_questions(
    text: str, exact: bool
) -> None:
    intent = parse_assistant_intent(text, now=NOW, timezone="Europe/Moscow")
    assert intent is not None
    assert intent.kind == "mailbox_search"
    assert is_explicit_mailbox_lookup(text, intent) is exact
