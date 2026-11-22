"""Shared deterministic admission and bounded projection for one source task."""

import re
from datetime import UTC, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from satori.application.assistant.mail_lookup import parse_mail_chat_read_intent
from satori.application.assistant.source_answer_contracts import (
    SourceAnswerError,
    SourceCoverage,
    SourceName,
    SourceObservation,
    SourceSearchPlan,
    SourceTaskRequest,
    SourceTaskSnapshot,
)

MAX_TASK_TEXT_BYTES = 32768
MAX_TASK_OBSERVATIONS = 20
SOURCE_TASK_REFINEMENT = "\nУточнение пользователя: "  # noqa: RUF001 - owner turn delimiter in Russian

_QUOTED = re.compile(r'«[^»]*»|“[^”]*”|"[^"\n]*"')
_LOCAL_DAY = re.compile(r"\b(?:(не)\s+)?(сегодня|вчера)\b", re.IGNORECASE)


def source_task_window(request: SourceTaskRequest) -> tuple[datetime, datetime]:
    """Resolve explicit local days from the owner question or its latest refinement."""
    end = request.created_at
    zone = ZoneInfo(request.scope.timezone)
    today = end.astimezone(zone).date()
    for turn in reversed(request.user_text.split(SOURCE_TASK_REFINEMENT)):
        days = {
            word.casefold()
            for negated, word in _LOCAL_DAY.findall(_QUOTED.sub(" ", turn))
            if not negated
        }
        if not days:
            continue
        first = today - timedelta(days=int("вчера" in days))
        start = datetime.combine(first, time.min, zone).astimezone(UTC)
        if "сегодня" not in days:
            return start, datetime.combine(today, time.min, zone).astimezone(UTC)
        return start, max(start + timedelta(microseconds=1), end)
    return end - timedelta(days=30), end


def plan_mail_chat_read(request: SourceTaskRequest) -> SourceSearchPlan | None:
    """Compile the actual owner question into a short metadata-only Gmail task."""
    if request.sources != ("mail",):
        return None
    intent = parse_mail_chat_read_intent(
        request.user_text, timezone=request.scope.timezone, request_id=request.request_id
    )
    if intent is None or intent.query is None:
        return None
    start, end = source_task_window(request)
    if intent.query == "in:inbox" and end - start == timedelta(days=30):
        zone = ZoneInfo(request.scope.timezone)
        today = datetime.combine(end.astimezone(zone).date(), time.min, zone).astimezone(UTC)
        start = min(today, end - timedelta(microseconds=1))
    return SourceSearchPlan(
        status="search",
        mail_query=intent.query,
        mail_headers_only=True,
        window_start=start,
        window_end=end,
    )


def task_search_admission(
    request: SourceTaskRequest, plan: SourceSearchPlan, source: SourceName, now: datetime
) -> str:
    window_start, window_end = source_task_window(request)
    if (
        source not in request.sources
        or plan.status != "search"
        or not request.created_at <= now < request.expires_at
        or plan.window_start < window_start
        or plan.window_end > window_end
        or (plan.mail_headers_only and plan != plan_mail_chat_read(request))
    ):
        raise SourceAnswerError("source_task_scope_invalid")
    query = plan.mail_query if source == "mail" else plan.telegram_query
    if source == "calls" or query is None or not query.strip():
        raise SourceAnswerError("source_task_query_unavailable")
    return query


def clip_utf8(value: str, maximum: int) -> tuple[str, bool]:
    raw = value.encode("utf-8")
    if len(raw) <= maximum:
        return value, False
    return raw[:maximum].decode("utf-8", errors="ignore"), True


def task_snapshot(
    request: SourceTaskRequest,
    plan: SourceSearchPlan,
    source: SourceName,
    now: datetime,
    items: tuple[SourceObservation, ...],
    *,
    status: Literal["searched", "partial", "unavailable", "error"],
    more: bool = False,
    detail: str = "",
) -> SourceTaskSnapshot:
    # This is a provider-independent budget, not permission to silently erase omissions.
    kept: list[SourceObservation] = []
    remaining = MAX_TASK_TEXT_BYTES
    clipped = False
    for raw_item in items[:MAX_TASK_OBSERVATIONS]:
        item = SourceObservation.model_validate(raw_item.model_dump())
        if (
            item.source != source
            or not request.created_at <= item.observed_at <= now
            or (
                item.event_at is not None
                and not plan.window_start <= item.event_at < plan.window_end
            )
        ):
            raise SourceAnswerError("source_task_projection_scope_invalid")
        text, changed = clip_utf8(item.text, min(remaining, 4000))
        remaining -= len(text.encode("utf-8"))
        clipped = clipped or changed
        kept.append(
            item.model_copy(
                update={
                    "text": text,
                    "completeness": "partial" if changed else item.completeness,
                }
            )
        )
    clipped = clipped or len(items) > MAX_TASK_OBSERVATIONS
    complete_projection = "metadata_only" if plan.mail_headers_only else "complete"
    partial = more or clipped or any(item.completeness != complete_projection for item in kept)
    coverage = SourceCoverage(
        source=source,
        status="partial" if status == "searched" and partial else status,
        query=(
            plan.mail_query
            if source == "mail"
            else plan.telegram_query
            if source == "telegram"
            else None
        ),
        window_start=plan.window_start,
        window_end=plan.window_end,
        more=more or clipped,
        detail=detail,
    )
    # Header metadata can also exhaust the finite JSON envelope. Retire whole trailing
    # observations, explicitly reporting partial coverage rather than hiding truncation.
    while True:
        candidate = SourceTaskSnapshot.model_construct(
            request_id=request.request_id,
            scope=request.scope,
            plan_digest=plan.digest,
            observed_at=now,
            items=tuple(kept),
            coverage=(coverage,),
        )
        if len(candidate.model_dump_json().encode("utf-8")) <= 48 * 1024:
            # Validation failures unrelated to size must reject; never silently drop
            # malformed observations and misreport them as successful truncation.
            return SourceTaskSnapshot.model_validate(candidate.model_dump())
        if not kept:
            raise SourceAnswerError("source_task_projection_invalid")
        kept.pop()
        coverage = coverage.model_copy(update={"status": "partial", "more": True})
