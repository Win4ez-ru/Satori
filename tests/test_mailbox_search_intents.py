"""Whole-account search command routing, distinct from explicitly selected local lookup."""

# ruff: noqa: RUF001 - intentional Russian command fixtures

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from satori.application.assistant.action_contracts import AssistantIntent
from satori.application.assistant.intents import parse_assistant_intent
from satori.application.assistant.mail_lookup import parse_mail_chat_read_intent

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "query"),
    [
        ("Есть у меня новые письма?", "in:inbox"),
        ("Что у меня на почте?", "in:inbox"),
        ("Сатори, можешь посмотреть, пришли ли письма сегодня?", "in:inbox"),
        ("Покажи входящие", "in:inbox"),
        ("Есть ли у меня непрочитанные письма?", "in:inbox is:unread"),
        ("Найди письмо от info@example.com", 'from:("info@example.com")'),
        ("Найди письмо с темой Билеты в Амстердам", 'subject:("Билеты в Амстердам")'),
        ("Посмотри в почте, есть ли письмо от Acme сегодня?", 'from:("Acme")'),
    ],
)
def test_natural_mail_only_request_compiles_to_local_metadata(text: str, query: str) -> None:
    intent = parse_mail_chat_read_intent(text, timezone="Europe/Moscow")
    assert intent is not None
    assert (intent.kind, intent.source, intent.query) == ("mailbox_search", "mail", query)


@pytest.mark.parametrize(
    "text",
    [
        "У меня есть новые письма.",
        "У меня есть письмо от Acme.",
        "Он сказал: «Проверь почту»",
        "Покажи входящие и отправь ответ",
        "Найди письмо от Acme и отправь ответ",
        "В почте или в Telegram посмотри, есть ли ответ от Acme",
        "Есть ли письмо от Acme в Telegram?",
        "Есть ли письмо от них?",
        "Найди письмо от Иван Иванов про билеты",
        "Найди письмо о собеседовании",
        "Найди в почте: newer_than:30d",
        "Найди письмо от Acme from:other@example.com",
    ],
)
def test_local_mail_parser_preserves_other_scopes_and_rejects_ambient_reads(text: str) -> None:
    assert parse_mail_chat_read_intent(text, timezone="Europe/Moscow") is None


@pytest.mark.parametrize(
    ("text", "query"),
    [
        ("Найди в почте: билеты", "билеты"),
        ("Привет, найди письмо про билеты", "билеты"),
        ("Сатори, найди мне письмо от shop@example.com", "from:(shop@example.com)"),
        ("Можешь найти мне письмо от Ситидрайв", "from:(Ситидрайв)"),
        ('Найди письмо от "Иван Иванов"', 'from:("Иван Иванов")'),
        ("Найди письмо от Иван Иванов", "from:(Иван Иванов)"),
        ("Найди письмо от Иван Иванов про билеты", "from:(Иван Иванов) билеты"),
        (
            "Найди письмо от shop@example.com с темой Билеты в Амстердам",
            "from:(shop@example.com) subject:(Билеты в Амстердам)",
        ),
        ("Найди письмо с темой Билеты в Амстердам", "subject:(Билеты в Амстердам)"),
        ("Найди во всей почте: билеты", "билеты"),
        ("Помоги найти письмо от shop@example.com", "from:(shop@example.com)"),
        ("Найди пожалуйста у меня в почте билеты", "билеты"),
        ("Найди в моей почте: Билет  АБВ", "Билет  АБВ"),
        (
            "Поищи в Gmail from:shop@example.com after:2026/11/01",
            "from:shop@example.com after:2026/11/01",
        ),
        ("Найди в письмах: <b>Привет</b>", "<b>Привет</b>"),
        ("Найди в почте:" + "я" * 512, "я" * 512),
    ],
)
def test_mailbox_query_routes_without_selected_snapshot(text: str, query: str) -> None:
    request_id = uuid4()
    result = parse_assistant_intent(text, now=NOW, timezone="Europe/Moscow", request_id=request_id)
    assert result is not None
    assert result.kind == "mailbox_search"
    assert result.source == "mail"
    assert result.query == query
    assert result.request_id == request_id
    assert result.timezone == "Europe/Moscow"
    assert query not in repr(result)


@pytest.mark.parametrize("query", ["", "   ", "я" * 513, "a\nb", "a\x00b", "a\u202eb"])
def test_invalid_mailbox_query_is_action_clarification(query: str) -> None:
    result = parse_assistant_intent("Найди в почте:" + query, now=NOW, timezone="UTC")
    assert result is not None
    assert result.kind == "clarification"
    assert result.source == "mail"
    assert result.query is None


@pytest.mark.parametrize("scope", ["выбранной", "разрешённой", "разрешенной"])
def test_explicit_selected_scope_retains_local_search(scope: str) -> None:
    result = parse_assistant_intent(f"Найди в {scope} почте: Билет  АБВ", now=NOW, timezone="UTC")
    assert result is not None
    assert result.kind == "selected_lookup"
    assert result.query == "Билет  АБВ"
    too_long = parse_assistant_intent(
        f"Найди в {scope} почте:" + "я" * 129, now=NOW, timezone="UTC"
    )
    assert too_long is not None
    assert too_long.kind == "clarification"


@pytest.mark.parametrize(
    "text",
    [
        "Не найди письмо от shop@example.com",
        "Как найти письмо от shop@example.com?",
        'Он написал: "Найди письмо от shop@example.com"',
        "Я завтра найду в почте билеты",
        "Найди в телеграмме письмо от shop@example.com",
        "Найди в почтении смысл",
    ],
)
def test_description_negation_and_other_sources_do_not_search_mail(text: str) -> None:
    result = parse_assistant_intent(text, now=NOW, timezone="UTC")
    assert result is None or result.source != "mail"


@pytest.mark.parametrize("source", [None, "telegram"])
def test_mailbox_intent_requires_exact_mail_source(source: Literal["telegram"] | None) -> None:
    with pytest.raises(ValidationError):
        AssistantIntent(
            request_id=uuid4(), kind="mailbox_search", source=source, query="needle", timezone="UTC"
        )
