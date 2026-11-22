"""Typed read-tool selection for a real foreground owner request."""

# ruff: noqa: RUF001 - deliberate Russian date modifiers and clarification text.

import re
from datetime import date, timedelta
from typing import Literal, Self
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from satori.application.assistant.action_contracts import AssistantIntent
from satori.application.assistant.calendar_chat_lexing import (
    calendar_date_spans,
    resolve_calendar_day,
)
from satori.application.assistant.source_answer_contracts import (
    SourceDTO,
    SourceName,
    SourceTaskRequest,
)
from satori.application.assistant.source_task_reads import SOURCE_TASK_REFINEMENT


class AssistantReadRoute(SourceDTO):
    action: Literal["chat", "calendar_agenda", "source_answer", "clarify"]
    sources: tuple[SourceName, ...] = Field(default=(), max_length=3)
    day_literal: str | None = Field(default=None, min_length=1, max_length=80)
    clarification: str | None = Field(default=None, min_length=1, max_length=600)
    sender_literal: str | None = Field(default=None, min_length=1, max_length=200, repr=False)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (
            len(set(self.sources)) != len(self.sources)
            or (self.action == "source_answer") != bool(self.sources)
            or (self.action == "calendar_agenda") != (self.day_literal is not None)
            or (self.action == "clarify") != (self.clarification is not None)
            or (self.sender_literal is not None and self.action != "source_answer")
        ):
            raise ValueError("assistant_route_invalid")
        return self


def route_intent(request: SourceTaskRequest, route: AssistantReadRoute) -> AssistantIntent | None:
    """Compile a bounded proposal. Existing source owners authorize every actual read."""
    if route.action == "chat":
        return None
    if route.action == "clarify":
        return AssistantIntent(
            request_id=request.request_id,
            kind="clarification",
            timezone=request.scope.timezone,
            clarification=route.clarification,
        )
    if route.action == "calendar_agenda":
        assert route.day_literal is not None
        anchor = request.created_at.astimezone(ZoneInfo(request.scope.timezone)).date()
        if route.day_literal.casefold() not in request.user_text.casefold():
            raise ValueError("assistant_route_date_not_literal")
        resolved = resolve_calendar_day(route.day_literal, anchor_date=anchor)
        latest = request.user_text.rsplit(SOURCE_TASK_REFINEMENT, 1)[-1]
        text = latest if calendar_date_spans(latest) else request.user_text
        spans = calendar_date_spans(text)
        whole = resolve_calendar_day(text, anchor_date=anchor)
        if resolved.status != "resolved" or (spans and whole.day != resolved.day):
            raise ValueError("assistant_route_date_mismatch")
        day = date.fromisoformat(resolved.day)
        prefix = text[: spans[0].start][-64:].casefold() if spans else ""
        # The shared literal lexer intentionally ignores surrounding words. Do
        # not silently turn a past/qualified weekday into the next occurrence.
        if re.search(r"прошл(?:ый|ую|ое)\s+$", prefix):
            day = anchor - timedelta(days=(anchor.weekday() - day.weekday()) % 7 or 7)
        elif re.search(r"следующ(?:ий|ую|ее)\s+$", prefix):
            day = anchor + timedelta(days=(day.weekday() - anchor.weekday()) % 7 or 7)
        elif re.search(r"(?:недел|через|позапрошл|будущ)", prefix):
            return AssistantIntent(
                request_id=request.request_id,
                kind="clarification",
                timezone=request.scope.timezone,
                clarification="Уточни дату, пожалуйста: какое число ты имеешь в виду?",
            )
        return AssistantIntent(
            request_id=request.request_id,
            kind="calendar_agenda",
            timezone=request.scope.timezone,
            day=day,
        )
    if not set(route.sources) <= set(request.sources):
        raise ValueError("assistant_route_scope_mismatch")
    if (
        route.sender_literal is not None
        and route.sender_literal.casefold() not in request.user_text.casefold()
    ):
        raise ValueError("assistant_route_sender_not_literal")
    return AssistantIntent(
        request_id=request.request_id,
        kind="source_answer",
        timezone=request.scope.timezone,
        query=request.user_text,
        source="mail"
        if route.sources == ("mail",)
        else "telegram"
        if route.sources == ("telegram",)
        else None,
    )
