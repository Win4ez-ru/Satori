"""Conservative intake of an explicit source question, never a source grant."""

# ruff: noqa: RUF001 - intentional Russian task examples and expressions

import re
import unicodedata
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from satori.application.assistant.action_contracts import AssistantIntent
from satori.application.assistant.contracts import require_timezone, require_utc
from satori.application.assistant.source_answer_contracts import SourceName

_READ = re.compile(
    r"\b(?:посмотри|посмотреть|проверь|проверить|проверяй|узнай|выясни|найди|"
    r"поищи|покажи|загляни|глянь|проверишь|проанализируй|разбери|анализ)\b"
)
_SOURCE = re.compile(
    r"\b(?:почт[а-я]*|письм[а-я]*|gmail|телеграм[а-я]*|telegram|тг|"
    r"сообщени[а-я]*|переписк[а-я]*|звон(?:ок|ки|ков|ках|ке|ка)|звонил[аи]?)\b"
)
_REPLY_QUESTION = re.compile(
    r"\b(?:(?:написал[аи]?|написали|ответил[аи]?|ответили)\s+ли\s+мне|"
    r"(?:мне\s+)?(?:написал[аи]?|написали|ответил[аи]?|ответили)\s+ли|"
    r"(?:пришел|появился|есть)\s+ли\s+(?:у\s+меня\s+)?ответ|"
    r"(?:что|кто)\s+(?:мне\s+)?(?:написал[аи]?|написали|ответил[аи]?|ответили)|"
    r"кто\s+(?:мне\s+)?звонил)\b"
)
_WRITE = re.compile(
    r"\b(?:отправь|отправить|ответь|позвони|напиши|напомни|добавь|внеси|"
    r"создай|поставь|удали|перешли)\b"
)
_DENY = re.compile(
    r"\bне\s+(?:(?:надо|нужно)\s+)?(?:проверяй|проверь|проверять|смотри|"
    r"посмотри|смотреть|ищи|искать|открывай|анализируй)\b"
)
_EXPLANATION = re.compile(
    r"^(?:(?:привет|слушай|сатори)[,!:.\s]+)*(?:как\b|объясни\b|переведи\b|"
    r"что\s+(?:значит|означает)\b|приведи\s+пример\b|пример\s+для\b|"
    r"(?:покажи|расскажи|объясни|посмотри|проверь)[,\s]+как\b)"
)
_SELECTED = re.compile(r"\b(?:выбранн|разрешенн|прочитанн)[а-я]*\b")
_SOURCE_QUESTION = re.compile(
    r"^(?:(?:привет|слушай|сатори)[,!:.\s]+)*(?:"
    r"что(?:\s+у меня)?(?:\s+нового)?\s+в\b|"
    r"есть\s+ли(?:\s+у меня)?\s+(?:новые\s+)?(?:письма|сообщения|ответы|новости)\b|"
    r"(?:пришли|пришло)\s+ли\b)"
)
_MAIL_SCOPE = re.compile(r"\b(?:почту|почте|почты|письм[а-я]*|gmail)\b")
_TELEGRAM_NAME = r"(?:телеграм[а-я]*|telegram|тг|телег[аеу])\b"
_TELEGRAM_LOCATION = re.compile(rf"\b(?:в|во|из|через)\s+(?:моем\s+)?{_TELEGRAM_NAME}")
_MAIL_EXPLICIT_SCOPE = re.compile(
    r"\b(?:в|во|на|из|через)\s+(?:(?:моей|всей|моем)\s+)?"
    r"(?:почте|почту|почты|письмах|gmail)\b|"
    r"\b(?:проверь|проверить|посмотри|найди|покажи)\s+(?:мою\s+)?(?:почту|gmail)\b|"
    rf"\b(?:почту|почте|gmail|письма)\s*(?:,|или|и|\+)\s*(?:в\s+)?{_TELEGRAM_NAME}|"
    rf"\b{_TELEGRAM_NAME}\s*(?:,|или|и|\+)\s*(?:в\s+)?(?:почте|почту|gmail)\b"
)
_SCOPE_QUOTE = re.compile(r'«([^»]*)»|“([^”]*)”|"([^"\n]*)"')
_QUOTED_SOURCE_NAME = re.compile(rf"(?:{_TELEGRAM_NAME}|gmail)")
_TELEGRAM_SCOPE = re.compile(
    r"\b(?:(?:в|из|через)\s+(?:моем\s+)?|"
    r"(?:проверь|проверить|посмотри|найди|покажи)\s+|"
    r"(?:почту|почте|почты|gmail|письма)\s*(?:,|или|и|\+)\s*(?:в\s+)?)"
    rf"{_TELEGRAM_NAME}"
)
_SOURCE_NAME = r"(?:почте|gmail|тг|telegram|телеграм[а-я]*|телег[аеу])\b"
_SOURCE_LOCATION = rf"(?:в|во)\s+(?:мо[её][йм]\s+)?{_SOURCE_NAME}"
_LEADING_SOURCE = re.compile(
    rf"^{_SOURCE_LOCATION}(?:\s*(?:,|или|и|\+)\s*(?:{_SOURCE_LOCATION}|{_SOURCE_NAME}))?[,\s]+"
)
_SHORT_READ = r"(?:посмотри|проверь|глянь|поищи)"
_SOURCE_ONLY = re.compile(
    rf"^(?:(?:а|и|ну|тогда|давай|лучше|только)[,\s]+){{0,2}}"
    rf"(?:{_SHORT_READ}\s+{_SOURCE_LOCATION}|{_SOURCE_LOCATION}(?:\s+{_SHORT_READ})?)"
    r"[.!?]?$"
)
_CONTACT_WORD = r"[а-яёa-z][а-яёa-z\-’']{0,63}"
_CONTACT = rf"(?P<contact>@[a-z0-9_]{{5,32}}|{_CONTACT_WORD}(?:[ \t]+{_CONTACT_WORD})??)"
_INCOMING_READ = rf"(?:{_SHORT_READ}|посмотреть|проверить|глянуть|поискать)"
_INCOMING_LEAD = (
    rf"^(?:(?:а|и|ну)(?:\s+вот)?[,\s]+)?"
    rf"(?:{_INCOMING_READ}[,\s]+(?:{_SOURCE_LOCATION}[,\s]+)?"
    rf"|{_SOURCE_LOCATION}[,\s]+(?:{_INCOMING_READ}[,\s]+)?)?"
)
_INCOMING_VERB = r"(?:писал[аи]?|написал[аи]?|ответил[аи]?)"
_INCOMING_MODIFIER = r"(?:мне|сегодня|вчера)"
_INCOMING_END = rf"(?:\s+{_SOURCE_LOCATION})?\s*(?P<question>\?)?$"
_INCOMING_CONTACT = re.compile(
    rf"{_INCOMING_LEAD}(?:{_INCOMING_MODIFIER}\s+){{0,2}}{_CONTACT}"
    rf"(?:\s+{_INCOMING_MODIFIER}){{0,3}}\s+{_INCOMING_VERB}"
    rf"(?:\s+(?:ли|{_INCOMING_MODIFIER})){{0,4}}{_INCOMING_END}",
    re.IGNORECASE,
)
_INCOMING_CONTACT_INVERTED = re.compile(
    rf"{_INCOMING_LEAD}(?:{_INCOMING_MODIFIER}\s+){{0,3}}{_INCOMING_VERB}"
    rf"(?:\s+(?:ли|{_INCOMING_MODIFIER})){{0,4}}\s+{_CONTACT}"
    rf"(?:\s+{_INCOMING_MODIFIER}){{0,3}}{_INCOMING_END}",
    re.IGNORECASE,
)
_NONCONTACT_WORDS = frozenset(
    {
        "я",
        "ты",
        "он",
        "она",
        "они",
        "мы",
        "вы",
        "кто",
        "кто-то",
        "кто-нибудь",
        "кто-либо",
        "никто",
        "что",
        "это",
        "тот",
        "та",
        "те",
        "мой",
        "моя",
        "мои",
        "его",
        "ее",
        "их",
        "мне",
        "сегодня",
        "вчера",
        "может",
        "наверное",
        "кажется",
        "возможно",
        "вдруг",
        "если",
        "правда",
        "скажи",
        "проверь",
        "посмотри",
        "когда",
        "почему",
        "где",
        "рекрутер",
        "менеджер",
        "работодатель",
        "не",
    }
)
_EXACT_MAIL_COMMAND = re.compile(
    r"^(?:найди|найти|поищи|поискать)\s+(?:мне\s+)?"
    r"(?:в\s+(?:(?:моей|всей)\s+)?(?:почте|письмах|gmail)|письм[оа])\s*:"
)
_MAIL_QUERY_OPERATOR = re.compile(
    r"(?<![\w:])(?:from|to|cc|bcc|subject|after|before|newer_than|older_than|in|is|"
    r"label|category|has|filename|list|deliveredto|rfc822msgid|size|larger|smaller):\S",
    re.IGNORECASE,
)
_OWNER_LEAD = re.compile(
    r"^(?:(?:привет|слушай|сатори|брат|братик|эй)[,!:.\s]+|"
    r"(?:можешь|можно|давай|пожалуйста|могла бы|не могла бы|сейчас|сегодня|"
    r"хочу чтобы ты|я хочу чтобы ты|скажи(?: мне)?|расскажи(?: мне)?)[,\s]+)*",
    re.IGNORECASE,
)
_REPLY_MENTION = re.compile(r"\b(?:написал[аи]?|написали|ответил[аи]?|ответили|ответ)\b")


def _scope_text(text: str) -> str:
    def preserve_source_name(match: re.Match[str]) -> str:
        label = next(value for value in match.groups() if value is not None).strip()
        return f" {label} " if _QUOTED_SOURCE_NAME.fullmatch(label) else " "

    return _SCOPE_QUOTE.sub(preserve_source_name, text.casefold().replace("ё", "е"))


def has_explicit_telegram_location(text: str) -> bool:
    """A Telegram location names a source even when the object is called a letter."""
    return _TELEGRAM_LOCATION.search(_scope_text(text)) is not None


def _message_scopes(scope: str) -> tuple[bool, bool]:
    telegram_location = _TELEGRAM_LOCATION.search(scope) is not None
    mail = _MAIL_SCOPE.search(scope) is not None and (
        not telegram_location or _MAIL_EXPLICIT_SCOPE.search(scope) is not None
    )
    return mail, telegram_location or _TELEGRAM_SCOPE.search(scope) is not None


def _owner_read_request(text: str) -> bool:
    if _READ.match(text):
        return True
    after_source = _LEADING_SOURCE.sub("", text, count=1)
    if after_source != text and _READ.match(after_source) and not _EXPLANATION.match(after_source):
        return True
    if not re.match(r"^(?:у меня|я|мы)\b", text):
        return False
    for separator in re.finditer(r"[,;]\s*", text):
        prefix = text[: separator.start()]
        if any(mark in prefix for mark in '"«»“”') or re.search(
            r"\b(?:сказал[аи]?|попросил[аи]?|написал[аи]?|написано|цитирую|фраз[а-я]*)\b",
            prefix,
        ):
            continue
        clause = _OWNER_LEAD.sub("", text[separator.end() :], count=1)
        if _EXPLANATION.match(clause):
            continue
        if _READ.match(clause):
            return True
    return False


def named_incoming_contact(text: str) -> str | None:
    """Return the exact addressee only for a direct question about incoming contact."""
    original = text.strip()
    if len(original) > 160 or any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in original):
        return None
    prefix = _OWNER_LEAD.match(original)
    offset = prefix.end() if prefix else 0
    value = original[offset:]
    match = _INCOMING_CONTACT.fullmatch(value) or _INCOMING_CONTACT_INVERTED.fullmatch(value)
    if match is None:
        return None
    contact = match["contact"]
    start, end = match.span("contact")
    context = set(re.findall(r"\w+", (original[: offset + start] + " " + value[end:]).casefold()))
    if (
        (match["question"] is None and "ли" not in context)
        or not context.intersection({"мне", "сегодня", "вчера"})
        or set(contact.casefold().replace("ё", "е").split()) & _NONCONTACT_WORDS
    ):
        return None
    return contact


def source_only_followup(text: str) -> Literal["mail", "telegram"] | None:
    """Recognize a source selection without adding a target or other instruction."""
    if len(text) > 160 or any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in text):
        return None
    value = re.sub(r"\s+", " ", text.strip().casefold().replace("ё", "е"))
    value = _OWNER_LEAD.sub("", value, count=1)
    if _SOURCE_ONLY.fullmatch(value) is None:
        return None
    return "telegram" if _TELEGRAM_SCOPE.search(value) else "mail"


def is_explicit_mailbox_lookup(text: str, intent: AssistantIntent | None) -> bool:
    """Distinguish exact Gmail commands from natural questions about a letter."""
    if (
        intent is None
        or intent.source != "mail"
        or intent.kind not in {"mailbox_search", "clarification"}
    ):
        return False
    value = re.sub(r"\s+", " ", text.strip().casefold().replace("ё", "е"))
    owner_text = _OWNER_LEAD.sub("", value, count=1)
    return _EXACT_MAIL_COMMAND.match(owner_text) is not None or (
        intent.query is not None and _MAIL_QUERY_OPERATOR.search(intent.query) is not None
    )


def parse_source_answer_intent(
    text: str,
    *,
    now: datetime,
    timezone: str,
    request_id: UUID | None = None,
) -> AssistantIntent | None:
    """Route an owner-authored question; the task owner must still authorize I/O.

    This only recognizes an explicit request to inspect communications. It does not
    resolve a company, infer a last interview, turn pasted text into instructions,
    select accounts/messages, mint provider disclosure or claim any search result.
    Those decisions belong to the scoped conversation operation.
    """
    require_utc(now)
    timezone = require_timezone(timezone)
    original = text.strip()
    if not original or any(unicodedata.category(c) in {"Cs", "Cf"} for c in original):
        return None
    normalized = re.sub(r"\s+", " ", original.casefold().replace("ё", "е"))
    if (
        _SELECTED.search(normalized)
        or _DENY.search(normalized)
        or _EXPLANATION.search(normalized)
        or _WRITE.search(normalized)
    ):
        return None
    owner_text = _OWNER_LEAD.sub("", normalized, count=1)
    if _EXPLANATION.match(owner_text):
        return None
    owner_read = _owner_read_request(owner_text)
    scope = _scope_text(normalized)
    source_question = (_SOURCE.search(scope) or _TELEGRAM_SCOPE.search(scope)) and (
        owner_read or _REPLY_QUESTION.match(owner_text) or _SOURCE_QUESTION.match(owner_text)
    )
    reply_question = (
        _REPLY_QUESTION.match(owner_text) is not None
        or named_incoming_contact(original) is not None
    )
    if (
        not source_question
        and not reply_question
        and not (owner_read and _REPLY_MENTION.search(normalized))
    ):
        return None
    request_id = request_id or uuid4()
    if len(original) > 8000:
        return AssistantIntent(
            request_id=request_id,
            kind="clarification",
            timezone=timezone,
            clarification="Сократи запрос, пожалуйста: что именно ищем в сообщениях?",
        )
    mail, telegram = _message_scopes(scope)
    source: Literal["mail", "telegram"] | None = (
        "mail" if mail and not telegram else "telegram" if telegram and not mail else None
    )
    return AssistantIntent(
        request_id=request_id,
        kind="source_answer",
        timezone=timezone,
        query=original,
        source=source,
    )


def classify_source_answer_followup(
    text: str,
) -> Literal["cancel", "clarification", "unrelated"]:
    """Classify a short answer only while the owner has a live clarification.

    The caller must first validate the original task scope and deadline. A short
    topic is new user input, not a fact inferred from source content or authority
    to extend the previous task lifetime. Ordinary conversation stays ordinary.
    """
    value = re.sub(r"\s+", " ", text.strip())
    normalized = value.casefold().replace("ё", "е").rstrip(".! ")
    if normalized in {"отмена", "отмени", "забудь", "не надо", "стоп", "пока не нужно"}:
        return "cancel"
    if (
        not value
        or len(value) > 160
        or len(value.split()) > 10
        or "\n" in text
        or "\r" in text
        or "?" in value
        or any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in value)
        or _READ.search(normalized)
        or _WRITE.search(normalized)
        or _DENY.search(normalized)
        or _EXPLANATION.search(normalized)
        or re.search(
            r"\b(?:привет|здравствуй|спасибо|благодарю|пока|календар[а-я]*|"
            r"напоминани[а-я]*|поговорим|погода|как\s+дела|что\s+думаешь)\b",
            normalized,
        )
    ):
        return "unrelated"
    return "clarification"


def source_answer_sources(intent: AssistantIntent) -> tuple[SourceName, ...]:
    """Respect explicitly named sources; an unqualified reply question checks both."""
    if intent.kind != "source_answer" or not intent.query:
        raise ValueError("source_task_intent_required")
    scope = _scope_text(intent.query)
    names: list[SourceName] = []
    mail, telegram = _message_scopes(scope)
    if mail:
        names.append("mail")
    if telegram:
        names.append("telegram")
    if re.search(r"\b(?:звон(?:ок|ки|ков|ках|ке|ка)|звонил[аи]?)\b", scope):
        names.append("calls")
    if names:
        return tuple(names)
    return (intent.source,) if intent.source is not None else ("mail", "telegram")


def is_source_answer_followup_question(text: str) -> bool:
    """Narrow read-only follow-up, usable only with a current answered task.

    This is not permission on its own. The host carries the original request,
    source scope and expiry into a new owner-authored turn and obtains fresh data.
    """
    if len(text) > 160 or any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in text):
        return False
    value = re.sub(r"\s+", " ", text.strip().casefold().replace("ё", "е"))
    value = re.sub(r"^(?:(?:а|и|ну|тогда)[,\s]+){1,2}", "", value).rstrip("?!. ")
    if _WRITE.search(value) or _DENY.search(value):
        return False
    return (
        re.fullmatch(
            r"(?:когда|во сколько|где|какой адрес|какая дата|какое время|"
            r"когда (?:собес|собеседование|интервью|созвон)|"
            r"что (?:они |мне |именно )?(?:написали|ответили|предлагают)|"
            r"какие условия|что там (?:написано|сказано)|покажи подробнее|"
            r"расскажи подробнее|что значит (?:это письмо|это сообщение))",
            value,
        )
        is not None
    )
