"""Deterministic local search of an existing Telegram owner's current bounded page.

This module creates no read, grant, connection, durable state or model disclosure.
Result records are transient data and must be revalidated before each presentation.
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from satori.application.assistant.contracts import LocalPrincipal
from satori.desktop.telegram_port import Message, WireState

MAX_QUERY_CHARS = 128
MAX_QUERY_BYTES = 512
MAX_HITS = 10
MAX_EXCERPT_CHARS = 512
MAX_EXCERPT_BYTES = 2048
MAX_PAGE_MESSAGES = 50
MAX_PAGE_BODY_BYTES = 262144

SearchFailure = Literal[
    "runtime_unavailable",
    "foreground_required",
    "fresh_page_required",
    "clock_invalid",
    "page_invalid",
    "source_changed",
]


class _DTO(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class TelegramPageSearchQuery(_DTO):
    request_id: UUID
    needle: str = Field(min_length=1, max_length=MAX_QUERY_CHARS, repr=False)
    case_sensitive: bool = False
    limit: int = Field(default=5, ge=1, le=MAX_HITS)

    @field_validator("needle")
    @classmethod
    def bounded_literal(cls, value: str) -> str:
        if (
            value != value.strip()
            or not value.strip()
            or len(value.encode("utf-8")) > MAX_QUERY_BYTES
            or any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value)
        ):
            raise ValueError("telegram_search_query_invalid")
        return value

    @field_validator("request_id")
    @classmethod
    def nonzero_request(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("telegram_search_query_invalid")
        return value


class TelegramPageSearchScope(_DTO):
    principal: LocalPrincipal = Field(repr=False)
    generation: int = Field(ge=0)
    epoch: int = Field(ge=0)
    selected_token: str = Field(min_length=1, max_length=80, repr=False)
    page_id: str = Field(min_length=1, max_length=80, repr=False)
    session_deadline: float = Field(gt=0, allow_inf_nan=False, repr=False)
    selection_deadline: float = Field(gt=0, allow_inf_nan=False, repr=False)
    page_deadline: float = Field(gt=0, allow_inf_nan=False, repr=False)
    checked_at_monotonic: float = Field(ge=0, allow_inf_nan=False, repr=False)
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def utc_observation(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0):
            raise ValueError("telegram_search_observation_invalid")
        return value


class TelegramPageSearchHit(_DTO):
    handle: str = Field(min_length=1, max_length=80, repr=False)
    sender: str = Field(max_length=1024, repr=False)
    source_date_unix: int = Field(ge=1, le=(1 << 63) - 1)
    excerpt: str = Field(max_length=MAX_EXCERPT_CHARS, repr=False)
    clipped_before: bool
    clipped_after: bool


class TelegramPageSearchResult(_DTO):
    source_kind: Literal["telegram_current_page"] = "telegram_current_page"
    coverage: Literal["current_selected_bounded_page_only"] = "current_selected_bounded_page_only"
    matching: Literal["literal_no_regex_no_semantic_inference"] = (
        "literal_no_regex_no_semantic_inference"
    )
    retention: Literal["transient_no_canonical_or_learning"] = "transient_no_canonical_or_learning"
    tts_eligible: Literal[False] = False
    complete_history: Literal[False] = False
    query: TelegramPageSearchQuery = Field(repr=False)
    status: Literal["matches", "no_match", "unavailable"]
    reason: SearchFailure | None = None
    scope: TelegramPageSearchScope | None = Field(default=None, repr=False)
    hits: tuple[TelegramPageSearchHit, ...] = Field(default=(), max_length=MAX_HITS, repr=False)
    searched_messages: int = Field(default=0, ge=0, le=MAX_PAGE_MESSAGES)
    matching_messages: int = Field(default=0, ge=0, le=MAX_PAGE_MESSAGES)
    source_omitted: int = Field(default=0, ge=0, le=50)
    hits_truncated: bool = False
    expires_at_monotonic: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def coherent_result(self) -> TelegramPageSearchResult:
        if self.status == "unavailable":
            if (
                self.reason is None
                or self.scope is not None
                or self.hits
                or self.searched_messages
                or self.matching_messages
                or self.source_omitted
                or self.hits_truncated
                or self.expires_at_monotonic is not None
            ):
                raise ValueError("telegram_search_result_invalid")
        elif (
            self.reason is not None
            or self.scope is None
            or self.expires_at_monotonic is None
            or self.expires_at_monotonic
            != min(
                self.scope.session_deadline, self.scope.selection_deadline, self.scope.page_deadline
            )
            or self.matching_messages > self.searched_messages
            or len(self.hits) != min(self.query.limit, self.matching_messages)
            or self.hits_truncated != (self.matching_messages > len(self.hits))
            or (self.status == "matches") != bool(self.hits)
        ):
            raise ValueError("telegram_search_result_invalid")
        return self


class _Qualification(Protocol):
    @property
    def principal(self) -> LocalPrincipal: ...


class TelegramSearchOwner(Protocol):
    """Implemented by the existing runtime controller, supplied by trusted composition."""

    principal: LocalPrincipal
    generation: int
    state: WireState | None
    visible: bool
    closing: bool
    shutting_down: bool
    clock: Callable[[], float]
    _observed_at: datetime | None

    @property
    def live(self) -> bool: ...

    @property
    def qualification(self) -> _Qualification | None: ...


class _Unavailable(ValueError):
    def __init__(self, code: SearchFailure) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class _Capture:
    scope: TelegramPageSearchScope
    messages: tuple[Message, ...]
    omitted: int


def _wire_text(value: str, cap: int, *, empty: bool = False) -> bool:
    return (
        type(value) is str
        and (bool(value) or empty)
        and "\0" not in value
        and len(value.encode("utf-8")) <= cap
    )


def _capture(owner: TelegramSearchOwner) -> _Capture:
    try:
        now = owner.clock()
    except Exception:
        raise _Unavailable("clock_invalid") from None
    if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
        raise _Unavailable("clock_invalid")
    if (
        owner.closing is not False
        or owner.shutting_down is not False
        or owner.live is not True
        or owner.qualification is None
        or owner.qualification.principal != owner.principal
    ):
        raise _Unavailable("runtime_unavailable")
    if owner.visible is not True:
        raise _Unavailable("foreground_required")
    state = owner.state
    if (
        state is None
        or state.terminal is not False
        or state.closed is not False
        or state.confirmed is not True
    ):
        raise _Unavailable("fresh_page_required")
    if (
        state.last_clock is None
        or type(state.last_clock) not in (int, float)
        or not math.isfinite(state.last_clock)
        or now < state.last_clock
    ):
        raise _Unavailable("clock_invalid")
    deadlines = (state.session_deadline, state.selection_deadline, state.page_deadline)
    if any(type(t) not in (int, float) or not math.isfinite(t) for t in deadlines):
        raise _Unavailable("clock_invalid")
    if (
        not state.selected
        or not state.page_id
        or state.stale is not False
        or state.pending
        or any(now >= deadline for deadline in deadlines)
    ):
        raise _Unavailable("fresh_page_required")
    try:
        if (
            not _wire_text(state.selected, 80)
            or not _wire_text(state.page_id, 80)
            or type(state.messages) is not tuple
            or len(state.messages) > MAX_PAGE_MESSAGES
            or type(state.omitted) is not int
            or not 0 <= state.omitted <= 50
        ):
            raise _Unavailable("page_invalid")
        handles: set[str] = set()
        total = 0
        for message in state.messages:
            if (
                type(message) is not Message
                or not _wire_text(message.handle, 80)
                or message.handle in handles
                or not _wire_text(message.sender, 1024)
                or not _wire_text(message.text, 16384, empty=True)
                or type(message.date) is not int
                or not 1 <= message.date <= (1 << 63) - 1
            ):
                raise _Unavailable("page_invalid")
            handles.add(message.handle)
            total += len(message.text.encode("utf-8"))
        if total > MAX_PAGE_BODY_BYTES or owner._observed_at is None:
            raise _Unavailable("page_invalid")
        scope = TelegramPageSearchScope(
            principal=owner.principal,
            generation=owner.generation,
            epoch=state.epoch,
            selected_token=state.selected,
            page_id=state.page_id,
            session_deadline=float(state.session_deadline),
            selection_deadline=float(state.selection_deadline),
            page_deadline=float(state.page_deadline),
            checked_at_monotonic=float(now),
            observed_at=owner._observed_at,
        )
    except (UnicodeError, ValidationError):
        raise _Unavailable("page_invalid") from None
    return _Capture(scope, state.messages, state.omitted)


def _same_source(first: _Capture, second: _Capture) -> bool:
    a, b = first.scope, second.scope
    return (
        a.model_dump(exclude={"checked_at_monotonic"})
        == b.model_dump(exclude={"checked_at_monotonic"})
        and b.checked_at_monotonic >= a.checked_at_monotonic
        and first.messages == second.messages
        and first.omitted == second.omitted
    )


def _span(text: str, needle: str, case_sensitive: bool) -> tuple[int, int] | None:
    if case_sensitive:
        found = text.find(needle)
        return (found, found + len(needle)) if found >= 0 else None
    folded = [char.casefold() for char in text]
    query = needle.casefold()
    found = "".join(folded).find(query)
    if found < 0:
        return None
    start = end = offset = 0
    for index, char in enumerate(folded):
        after = offset + len(char)
        if offset <= found < after:
            start = index
        if after >= found + len(query):
            end = index + 1
            break
        offset = after
    return start, end


def _hits(
    capture: _Capture, query: TelegramPageSearchQuery
) -> tuple[tuple[TelegramPageSearchHit, ...], int]:
    hits: list[TelegramPageSearchHit] = []
    count = 0
    for message in capture.messages:
        span = _span(message.text, query.needle, query.case_sensitive)
        if span is None:
            continue
        count += 1
        if len(hits) >= query.limit:
            continue
        first, after = span
        start = max(0, first - min(64, MAX_EXCERPT_CHARS - (after - first)))
        end = min(len(message.text), start + MAX_EXCERPT_CHARS)
        excerpt = message.text[start:end]
        # Every scalar uses at most four UTF-8 bytes; the public bound remains explicit.
        if len(excerpt.encode("utf-8")) > MAX_EXCERPT_BYTES:
            raise _Unavailable("page_invalid")
        hits.append(
            TelegramPageSearchHit(
                handle=message.handle,
                sender=message.sender,
                source_date_unix=message.date,
                excerpt=excerpt,
                clipped_before=start > 0,
                clipped_after=end < len(message.text),
            )
        )
    return tuple(hits), count


def search_current_page(
    owner: TelegramSearchOwner, query: TelegramPageSearchQuery
) -> TelegramPageSearchResult:
    """Synchronous, bounded and side-effect free; caller gets no source admission power."""
    checked = TelegramPageSearchQuery.model_validate(query)
    try:
        first = _capture(owner)
        hits, count = _hits(first, checked)
        final = _capture(owner)
        if not _same_source(first, final):
            raise _Unavailable("source_changed")
        scope = final.scope
        return TelegramPageSearchResult(
            query=checked,
            status="matches" if count else "no_match",
            scope=scope,
            hits=hits,
            searched_messages=len(final.messages),
            matching_messages=count,
            source_omitted=final.omitted,
            hits_truncated=count > len(hits),
            expires_at_monotonic=min(
                scope.session_deadline, scope.selection_deadline, scope.page_deadline
            ),
        )
    except _Unavailable as error:
        return TelegramPageSearchResult(query=checked, status="unavailable", reason=error.code)


def result_is_current(owner: TelegramSearchOwner, result: TelegramPageSearchResult) -> bool:
    """Recompute from owner; a cached or caller-built result is never disclosure authority."""
    try:
        checked = TelegramPageSearchResult.model_validate(result)
        if checked.status == "unavailable" or checked.scope is None:
            return False
        current = search_current_page(owner, checked.query)
        if current.status == "unavailable" or current.scope is None:
            return False
        return (
            checked.scope.model_dump(exclude={"checked_at_monotonic"})
            == current.scope.model_dump(exclude={"checked_at_monotonic"})
            and current.scope.checked_at_monotonic >= checked.scope.checked_at_monotonic
            and checked.hits == current.hits
            and checked.status == current.status
            and checked.searched_messages == current.searched_messages
            and checked.matching_messages == current.matching_messages
            and checked.source_omitted == current.source_omitted
            and checked.hits_truncated == current.hits_truncated
        )
    except (ValidationError, ValueError):
        return False
