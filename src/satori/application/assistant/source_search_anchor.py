"""Private literal search proposal and deterministic provider-query compiler."""

import re
import unicodedata
from datetime import datetime, timedelta
from typing import Literal, Self

from pydantic import Field, model_validator

from satori.application.assistant.contracts import require_utc
from satori.application.assistant.source_answer_contracts import (
    SourceAnswerError,
    SourceDTO,
    SourceSearchPlan,
    SourceTaskRequest,
)
from satori.application.assistant.source_answer_intake import (
    named_incoming_contact,
    source_only_followup,
)
from satori.application.assistant.source_task_reads import (
    SOURCE_TASK_REFINEMENT,
    source_task_window,
)


class SourceAnchorProjection(SourceDTO):
    ref: str = Field(min_length=1, max_length=256)
    kind: Literal["current_request", "recent_user", "canonical_memory"]
    text: str = Field(min_length=1, max_length=8000, repr=False)


class SourceAnchorChoice(SourceDTO):
    """The model selects a grounded name; it never authors provider query syntax."""

    status: Literal["search", "clarify"]
    clarification: str | None = Field(default=None, min_length=1, max_length=1200, repr=False)
    literal_anchor: str | None = Field(default=None, min_length=1, max_length=160, repr=False)
    canonical_ref: str | None = Field(default=None, min_length=1, max_length=256)
    anchor_kind: Literal["company", "person", "address"] | None = None
    window_start: datetime
    window_end: datetime

    @model_validator(mode="after")
    def coherent(self) -> Self:
        require_utc(self.window_start)
        require_utc(self.window_end)
        search = self.status == "search"
        if (
            not timedelta(0) < self.window_end - self.window_start <= timedelta(days=30)
            or search != (self.clarification is None)
            or search != (self.literal_anchor is not None)
            or search != (self.canonical_ref is not None)
            or search != (self.anchor_kind is not None)
        ):
            raise ValueError("source_anchor_choice_invalid")
        return self


def normalized_literal(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


_GENERIC_TASK_WORDS = frozenset(
    [
        "ответ",
        "ответы",
        "ответили",
        "\u0441\u043e\u0431\u0435\u0441",
        "\u0441\u043e\u0431\u0435\u0441\u0443",
        "собеседование",
        "собеседования",
        "интервью",
        "письмо",
        "письма",
        "сообщение",
        "сообщения",
        "вакансия",
        "вакансии",
        "работа",
        "работы",
        "разработчик",
        "разработчика",
        "interview",
        "interviews",
        "reply",
        "replies",
        "response",
        "responses",
        "mail",
        "message",
        "messages",
        "developer",
        "job",
        "role",
    ]
)


def compile_source_search_anchor(
    request: SourceTaskRequest,
    choice: SourceAnchorChoice,
    projections: tuple[SourceAnchorProjection, ...],
) -> SourceSearchPlan:
    """One literal name and one exact eligible reference; no broadening or retry."""
    request = SourceTaskRequest.model_validate(request)
    choice = SourceAnchorChoice.model_validate(choice)
    projections = tuple(SourceAnchorProjection.model_validate(item) for item in projections)
    if (choice.window_start, choice.window_end) != source_task_window(request) or len(
        {item.ref for item in projections}
    ) != len(projections):
        raise SourceAnswerError("source_plan_scope_invalid")
    if choice.status == "clarify":
        return SourceSearchPlan(
            status="clarify",
            clarification=choice.clarification,
            window_start=choice.window_start,
            window_end=choice.window_end,
        )
    assert choice.literal_anchor is not None
    assert choice.canonical_ref is not None
    raw = choice.literal_anchor
    anchor = " ".join(unicodedata.normalize("NFC", raw).split())
    # Quotes, colon, slashes, braces and controls cannot inject Gmail operators.
    punctuation = frozenset(" .@_+-'\u2019&")
    if (
        any(unicodedata.category(char)[0] == "C" for char in raw)
        or not anchor
        or len(anchor.encode("utf-8")) > 384
        or any(
            not (unicodedata.category(char)[0] in {"L", "M", "N"} or char in punctuation)
            for char in anchor
        )
        or not any(char.isalnum() for char in anchor)
        or set(re.findall(r"[^\W_]+", normalized_literal(anchor))) <= _GENERIC_TASK_WORDS
    ):
        raise SourceAnswerError("source_plan_anchor_invalid")
    selected = next((item for item in projections if item.ref == choice.canonical_ref), None)
    if selected is None or normalized_literal(anchor) not in normalized_literal(selected.text):
        raise SourceAnswerError("source_plan_anchor_ungrounded")
    if request.sender_literal is not None and (
        choice.anchor_kind != "person"
        or normalized_literal(anchor) != normalized_literal(request.sender_literal)
        or selected.kind != "current_request"
    ):
        raise SourceAnswerError("source_plan_sender_mismatch")
    original = request.user_text.partition(SOURCE_TASK_REFINEMENT)[0]
    incoming = choice.anchor_kind == "person" and (
        request.sender_literal is not None or named_incoming_contact(original) is not None
    )
    mail_query = f'from:("{anchor}")' if incoming else f'"{anchor}"'
    return SourceSearchPlan(
        status="search",
        mail_query=mail_query if "mail" in request.sources else None,
        telegram_query=anchor if "telegram" in request.sources else None,
        telegram_match="peer" if incoming and "telegram" in request.sources else "text",
        window_start=choice.window_start,
        window_end=choice.window_end,
        used_context_refs=(selected.ref,),
    )


def plan_named_incoming_contact(
    request: SourceTaskRequest, projections: tuple[SourceAnchorProjection, ...]
) -> SourceSearchPlan | None:
    request = SourceTaskRequest.model_validate(request)
    original, *refinements = request.user_text.split(SOURCE_TASK_REFINEMENT)
    name = request.sender_literal or named_incoming_contact(original)
    if name is None or any(source_only_followup(text) is None for text in refinements):
        return None
    current = next((item for item in projections if item.kind == "current_request"), None)
    if current is None:
        raise SourceAnswerError("source_plan_anchor_ungrounded")
    start, end = source_task_window(request)
    return compile_source_search_anchor(
        request,
        SourceAnchorChoice(
            status="search",
            literal_anchor=name,
            canonical_ref=current.ref,
            anchor_kind="person",
            window_start=start,
            window_end=end,
        ),
        projections,
    )
