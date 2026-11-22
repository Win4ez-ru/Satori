"""Explicit mailbox and selected-mail queries; no I/O or model inference."""

# ruff: noqa: RUF001 - intentional Russian command syntax

import re
import unicodedata
from uuid import UUID, uuid4

from satori.application.assistant.action_contracts import AssistantIntent
from satori.application.assistant.mail_search_contracts import valid_mail_search_query
from satori.application.assistant.source_answer_intake import has_explicit_telegram_location

MAIL_LOOKUP_QUERY_LIMIT = 128
_PREFIX = re.compile(
    r"\A\s*найди\s+в\s+"
    r"(?:разреш[её]нной|выбранной)\s+(?:почте|письмах)"
    r"(?=$|[\s,:])(?:[ \t]*[,:][ \t]*|[ \t]+)?",
    re.IGNORECASE,
)
_MAILBOX_PREFIX = re.compile(
    r"\A\s*(?:(?:привет|сатори)[,!]?\s+)?(?:пожалуйста[, ]+)?"
    r"(?:(?:можешь|можно|помоги)\s+)?(?:найди|найти|поищи|поискать)\s+"
    r"(?:пожалуйста[, ]+)?(?:мне\s+)?(?:у\s+меня\s+)?"
    r"(?:(?:в|во)\s+(?:(?:моей|всей)\s+)?(?:почте|письмах|gmail)|"
    r"письм(?:о|а)\s+в\s+(?:почте|gmail)|(?P<implicit_letter>письм(?:о|а)))"
    r"(?=$|[\s,:])(?:[ \t]*[,:][ \t]*|[ \t]+)?",
    re.IGNORECASE,
)
_CHAT_LEAD = re.compile(
    r"^(?:(?:привет|слушай|сатори)[,!.\s]+|"
    r"(?:можешь|можно|пожалуйста|скажи(?: мне)?)[,\s]+)*",
    re.IGNORECASE,
)
_CHAT_DAY = r"(?:сегодня|вчера)"
_CHAT_LOCATION = r"(?:(?:в|на)\s+(?:(?:моей|моем)\s+)?(?:почте|gmail)|в\s+входящих)"
_CHAT_READ = r"(?:покажи|проверь|посмотри|показать|проверить|посмотреть)"
_CHAT_WRAPPED_QUESTION = re.compile(rf"^{_CHAT_READ}(?:\s+{_CHAT_LOCATION})?\s*,\s*")
_CHAT_INBOX = re.compile(
    rf"^(?:что(?: у меня)?(?: нового)?\s+{_CHAT_LOCATION}(?:\s+{_CHAT_DAY})?|"
    rf"(?P<status>(?:у меня\s+)?есть(?: ли)?(?: у меня)?(?:\s+{_CHAT_DAY})?\s+"
    rf"(?:(?:новые|непрочитанные)\s+)?письма(?:\s+{_CHAT_LOCATION})?(?:\s+{_CHAT_DAY})?|"
    rf"(?:пришли|пришло)(?: ли)?(?: мне)?(?:\s+{_CHAT_DAY})?\s+"
    rf"(?:новые\s+)?письма(?:\s+{_CHAT_LOCATION})?(?:\s+{_CHAT_DAY})?)|"
    rf"(?:что(?:\s+{_CHAT_DAY})?\s+пришло|кто(?: мне)?(?:\s+{_CHAT_DAY})?\s+написал)"
    rf"\s+(?:(?:в|на) почту|{_CHAT_LOCATION})(?:\s+{_CHAT_DAY})?|"
    rf"{_CHAT_READ}\s+"
    rf"(?:(?:мою\s+)?почту|gmail|(?:мои\s+)?входящие(?: письма)?|"
    rf"(?:новые|непрочитанные) письма)(?:\s+{_CHAT_DAY})?|"
    rf"{_CHAT_LOCATION}\s+(?:проверь|посмотри))\s*[?.!]?$"
)
_CHAT_NATURAL_FILTER = re.compile(r"^(?P<kind>от|с темой)\s+(?P<literal>.+)$", re.IGNORECASE)
_CHAT_SENDER_QUESTION = re.compile(
    rf"^(?:(?:проверь|посмотри|проверить|посмотреть)(?:\s+{_CHAT_LOCATION})?[,\s]+)?"
    rf"(?:(?:у меня\s+)?есть(?: ли)?(?: у меня)?|пришл[оаи](?: ли)?(?: мне)?)"
    rf"(?:\s+{_CHAT_DAY})?\s+(?:(?:новое|новые)\s+)?письм[оа]\s+от\s+(?P<literal>.+)$",
    re.IGNORECASE,
)
_CHAT_TRAILING_SCOPE = re.compile(
    rf"(?:\s+{_CHAT_LOCATION})?(?:\s+{_CHAT_DAY})$|\s+{_CHAT_LOCATION}$", re.IGNORECASE
)
_CHAT_UNRESOLVED_SENDER = re.compile(
    r"^(?:него|не[её]|них|меня|тебя|нас|вас|кого-то|того|той|этого|этой)(?:\s|$)",
    re.IGNORECASE,
)
_CHAT_EXTRA_FILTER = re.compile(r"\s+(?:про|о|по теме|с темой)\s+", re.IGNORECASE)
_CHAT_OTHER_ACTION = re.compile(
    r"\s+(?:и|потом|затем)\s+(?:ответь|отправь|перешли|удали|напиши|позвони)\b",
    re.IGNORECASE,
)


def _mailbox_query(value: str) -> str:
    """Translate only explicit sender/subject connectors; retain Gmail search syntax."""
    query = value.strip()
    sender = re.fullmatch(
        r"от\s+(.+?)(?:\s+(про|о|по\s+теме|с\s+темой)\s+(.+))?", query, re.IGNORECASE
    )
    if sender is not None:
        result = f"from:({sender.group(1)})"
        if sender.group(3) is not None:
            suffix = sender.group(3)
            if re.fullmatch(r"с\s+темой", sender.group(2) or "", re.IGNORECASE):
                suffix = f"subject:({suffix})"
            result += " " + suffix
        return result
    subject = re.fullmatch(r"с\s+темой\s+(.+)", query, re.IGNORECASE)
    if subject is not None:
        return f"subject:({subject.group(1)})"
    topic = re.match(r"(?:про|о|по\s+теме)\s+", query, re.IGNORECASE)
    return query[topic.end() :] if topic is not None else query


def valid_mail_lookup_query(query: str) -> bool:
    return (
        bool(query.strip())
        and len(query) <= MAIL_LOOKUP_QUERY_LIMIT
        and not any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in query)
    )


def parse_mail_lookup_intent(
    text: str, *, timezone: str, request_id: UUID
) -> AssistantIntent | None:
    """Explicit selected scope remains local; ordinary requests search connected Gmail."""
    match = _PREFIX.match(text)
    if match is None:
        match = _MAILBOX_PREFIX.match(text)
        if match is None:
            return None
        if match["implicit_letter"] and has_explicit_telegram_location(text[match.end() :]):
            return None
        query = _mailbox_query(text[match.end() :])
        if not valid_mail_search_query(query):
            return AssistantIntent(
                request_id=request_id,
                kind="clarification",
                source="mail",
                timezone=timezone,
                clarification="Что найти в почте? Укажи слова, отправителя или запрос Gmail "
                "длиной до 512 символов, например «Найди письмо от shop@example.com».",
            )
        return AssistantIntent(
            request_id=request_id,
            kind="mailbox_search",
            timezone=timezone,
            source="mail",
            query=query,
        )
    query = text[match.end() :]
    if not valid_mail_lookup_query(query):
        return AssistantIntent(
            request_id=request_id,
            kind="clarification",
            source="mail",
            timezone=timezone,
            clarification="Укажи точный текст для поиска в выбранных письмах: "
            "от 1 до 128 символов, без управляющих символов. Регистр учитывается.",
        )
    return AssistantIntent(
        request_id=request_id, kind="selected_lookup", timezone=timezone, source="mail", query=query
    )


def parse_mail_chat_read_intent(
    text: str, *, timezone: str, request_id: UUID | None = None
) -> AssistantIntent | None:
    """Recognize direct metadata questions for the conversation, with no raw Gmail syntax."""
    original = text.strip()
    if (
        not original
        or len(original) > 512
        or any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in original)
        or has_explicit_telegram_location(original)
    ):
        return None
    owner_text = _CHAT_LEAD.sub("", original, count=1)
    normalized = " ".join(owner_text.casefold().replace("ё", "е").split())
    question = _CHAT_WRAPPED_QUESTION.sub("", normalized, count=1)
    inbox = _CHAT_INBOX.fullmatch(question)
    query: str | None
    if inbox is not None and (
        inbox["status"] is None
        or question != normalized
        or question.endswith("?")
        or " ли " in question
    ):
        query = "in:inbox is:unread" if "непрочитанные" in normalized else "in:inbox"
    else:
        query = _mail_chat_filter(owner_text)
        if query is None:
            return None
    return AssistantIntent(
        request_id=request_id or uuid4(),
        kind="mailbox_search",
        timezone=timezone,
        source="mail",
        query=query,
    )


def _mail_chat_filter(text: str) -> str | None:
    prefix = _MAILBOX_PREFIX.match(text)
    if prefix is not None:
        expression = text[prefix.end() :].rstrip("?!. ")
        match = _CHAT_NATURAL_FILTER.fullmatch(expression)
        if match is None:
            return None
        kind, literal = match["kind"].casefold(), match["literal"]
    else:
        match = _CHAT_SENDER_QUESTION.fullmatch(text.rstrip("?!. "))
        if match is None or (
            not text.endswith("?")
            and " ли " not in text.casefold()
            and re.match(_CHAT_READ, text.casefold()) is None
        ):
            return None
        kind, literal = "от", match["literal"]
    # Copy the owner's exact name/topic, bounded to literal query syntax. A question
    # with an implicit target or another source stays with the contextual dispatcher.
    if kind == "от":
        literal = _CHAT_TRAILING_SCOPE.sub("", literal, count=1)
    literal = " ".join(literal.strip().split())
    if (literal.startswith('"') and literal.endswith('"')) or (
        literal.startswith("«") and literal.endswith("»")
    ):
        literal = literal[1:-1].strip()
    if (
        not literal
        or len(literal.encode("utf-8")) > 384
        or not any(char.isalnum() for char in literal)
        or any(not (char.isalnum() or char in " .@_+-’'&") for char in literal)
        or _CHAT_OTHER_ACTION.search(literal) is not None
        or (
            kind == "от"
            and (_CHAT_UNRESOLVED_SENDER.match(literal) or _CHAT_EXTRA_FILTER.search(literal))
        )
        or re.search(
            r"\b(?:в|из|через|и|или)\s+(?:в\s+)?(?:telegram|тг|телеграм\w*|телег[аеу])\b",
            literal,
            re.I,
        )
    ):
        return None
    field = "from" if kind == "от" else "subject"
    return f'{field}:("{literal}")'
