"""Explicit user-authored reply text only; no Telegram scope or effect authority."""

import re
from uuid import UUID

from satori.application.assistant.action_contracts import AssistantIntent
from satori.application.assistant.telegram_reply_contracts import ReplyDraft

_PREFIX = re.compile(
    r"^\s*(?:ответь|подготовь ответ) в (?:telegram|телеграме)"
    r"(?: на выбранное сообщение)?\s*:",
    re.IGNORECASE,
)


def parse_telegram_reply_text(
    text: str, *, request_id: UUID, timezone: str
) -> AssistantIntent | None:
    """Everything after the colon is literal user text, including whitespace."""
    prefix = _PREFIX.match(text)
    if prefix is None:
        return None
    value = text[prefix.end() :]
    try:
        if not 0 < len(value) <= 4000:
            raise ValueError("reply_text_invalid")
        ReplyDraft.plain_text(value)
    except (ValueError, UnicodeError):
        return AssistantIntent(
            request_id=request_id,
            kind="clarification",
            timezone=timezone,
            source="telegram",
            clarification="После двоеточия нужен точный текст ответа: от 1 до 4000 символов. "
            "Выбор адресата, проверка и разрешение отправки выполняются отдельно в Telegram.",
        )
    return AssistantIntent(
        request_id=request_id, kind="telegram_reply", timezone=timezone, reply_text=value
    )
