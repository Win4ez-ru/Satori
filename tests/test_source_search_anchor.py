"""Literal provenance and provider query compilation; no model/network/accounts."""

# ruff: noqa: RUF001 - user-facing Russian test phrases

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from satori.application.assistant.source_answer_contracts import (
    SourceAnswerError,
    SourceTaskRequest,
    SourceTaskScope,
)
from satori.application.assistant.source_search_anchor import (
    SourceAnchorChoice,
    SourceAnchorProjection,
    compile_source_search_anchor,
    plan_named_incoming_contact,
)
from satori.application.assistant.source_task_reads import (
    SOURCE_TASK_REFINEMENT,
    source_task_window,
    task_search_admission,
)


def request(text: str = "Проверь, ответили ли по последнему собеседованию") -> SourceTaskRequest:
    now = datetime(2026, 11, 6, 10, tzinfo=UTC)
    return SourceTaskRequest(
        request_id=uuid4(),
        scope=SourceTaskScope(
            identity_id=uuid4(),
            profile_id=uuid4(),
            session_id=uuid4(),
            generation=uuid4(),
            timezone="Europe/Moscow",
        ),
        user_text=text,
        sources=("mail", "telegram"),
        created_at=now,
        expires_at=now + timedelta(seconds=120),
    )


def choice(
    task: SourceTaskRequest, anchor: str = "Публичной Лаборатории", ref: str = "user:recent"
) -> SourceAnchorChoice:
    return SourceAnchorChoice(
        status="search",
        literal_anchor=anchor,
        canonical_ref=ref,
        anchor_kind="company",
        window_start=task.created_at - timedelta(days=30),
        window_end=task.created_at,
    )


@pytest.mark.parametrize(
    ("kind", "ref"),
    [
        ("current_request", "source_answer-input:owner"),
        ("recent_user", "user:recent"),
        ("canonical_memory", "memory:eligible"),
    ],
)
def test_only_exact_cited_canonical_projection_compiles_literal_queries(
    kind: Literal["current_request", "recent_user", "canonical_memory"], ref: str
) -> None:
    task = request("Проверь ответ от Публичной Лаборатории")
    projection = SourceAnchorProjection(ref=ref, kind=kind, text=task.user_text)
    plan = compile_source_search_anchor(task, choice(task, ref=ref), (projection,))
    assert plan.mail_query == '"Публичной Лаборатории"'
    assert plan.telegram_query == "Публичной Лаборатории"
    assert plan.used_context_refs == (ref,)
    assert plan.window_start == task.created_at - timedelta(days=30)
    assert plan.window_end == task.created_at


def test_unicode_case_whitespace_normalized_literal_remains_one_phrase() -> None:
    task = request("Напиши, был ли ответ от " + "Яндекс компания".upper().replace(" ", chr(160)))
    projection = SourceAnchorProjection(ref="current", kind="current_request", text=task.user_text)
    plan = compile_source_search_anchor(
        task, choice(task, "яндекс компания", "current"), (projection,)
    )
    assert plan.mail_query == '"яндекс компания"'
    assert plan.telegram_query == "яндекс компания"


@pytest.mark.parametrize(
    "anchor",
    [
        "invented@example.com",
        "Несуществующая Компания",
        "Публичная Лаборатория",
        "Публичной Лаборатории Python разработчик собеседование ответ",
    ],
)
def test_invented_translated_or_overconstrained_anchor_never_becomes_query(anchor: str) -> None:
    task = request()
    projection = SourceAnchorProjection(
        ref="user:recent", kind="recent_user", text="Вчера прошёл интервью в Публичной Лаборатории."
    )
    with pytest.raises(SourceAnswerError, match="anchor_ungrounded"):
        compile_source_search_anchor(task, choice(task, anchor), (projection,))


@pytest.mark.parametrize(
    "anchor",
    [
        'Яндекс" OR from:evil',
        "from:boss@example.com",
        "Яндекс\\",
        "Яндекс\n",
        "ответ.",
        "собеседование ответ",
        "\ud800",
    ],
)
def test_operator_controls_and_generic_task_words_rejected_even_if_in_context(anchor: str) -> None:
    task = request()
    if anchor == "\ud800":
        # An unpaired surrogate cannot enter the proposal or projection DTO.
        with pytest.raises(ValidationError, match="string_unicode"):
            choice(task, anchor)
        return
    projection = SourceAnchorProjection(ref="user:recent", kind="recent_user", text=anchor)
    with pytest.raises(SourceAnswerError, match="anchor_invalid"):
        compile_source_search_anchor(task, choice(task, anchor), (projection,))


@pytest.mark.parametrize(
    "foreign",
    ["assistant:reply", "external:mail", "provider:model", "self:personality", "memory:wrong"],
)
def test_visible_but_uncited_or_ineligible_text_cannot_authorize_anchor(foreign: str) -> None:
    task = request()
    projection = SourceAnchorProjection(ref="user:recent", kind="recent_user", text="Яндекс")
    with pytest.raises(SourceAnswerError, match="anchor_ungrounded"):
        compile_source_search_anchor(task, choice(task, "Яндекс", foreign), (projection,))


def test_missing_context_clarifies_without_any_source_query() -> None:
    task = request()
    proposal = SourceAnchorChoice(
        status="clarify",
        clarification="Это было собеседование в какой компании?",
        window_start=task.created_at - timedelta(days=30),
        window_end=task.created_at,
    )
    plan = compile_source_search_anchor(task, proposal, ())
    assert plan.status == "clarify"
    assert plan.clarification == proposal.clarification
    assert plan.mail_query is plan.telegram_query is None


def test_public_plan_wire_and_source_selection_remain_unchanged() -> None:
    task = request().model_copy(update={"sources": ("telegram",)})
    projection = SourceAnchorProjection(
        ref="user:recent", kind="recent_user", text="Публичной Лаборатории"
    )
    plan = compile_source_search_anchor(task, choice(task), (projection,))
    assert plan.mail_query is None
    assert plan.telegram_query == "Публичной Лаборатории"
    assert plan.telegram_match == "text"
    assert set(plan.model_dump()) == {
        "status",
        "clarification",
        "mail_query",
        "telegram_query",
        "telegram_match",
        "window_start",
        "window_end",
        "used_context_refs",
    }


def test_legacy_raw_query_wire_and_changed_window_are_rejected() -> None:
    task = request()
    with pytest.raises(ValidationError):
        SourceAnchorChoice.model_validate(
            {**choice(task).model_dump(), "mail_query": "invented query"}
        )
    projection = SourceAnchorProjection(
        ref="user:recent", kind="recent_user", text="Публичной Лаборатории"
    )
    changed = choice(task).model_copy(update={"window_end": task.created_at - timedelta(seconds=1)})
    with pytest.raises(SourceAnswerError, match="scope_invalid"):
        compile_source_search_anchor(task, changed, (projection,))


@pytest.mark.parametrize("refine", [False, True])
def test_named_sender_today_uses_peer_and_sender_fields(refine: bool) -> None:
    text = "А Алексей мне сегодня писал?"
    if refine:
        text += SOURCE_TASK_REFINEMENT + "В тг посмотри"
    task = request(text)
    if refine:
        task = task.model_copy(update={"sources": ("telegram",)})
    projection = SourceAnchorProjection(ref="owner", kind="current_request", text=text)
    plan = plan_named_incoming_contact(task, (projection,))
    assert plan is not None
    assert plan.mail_query == (None if refine else 'from:("Алексей")')
    assert plan.telegram_query == "Алексей"
    assert plan.telegram_match == "peer"
    assert plan.window_start == datetime(2026, 11, 5, 21, tzinfo=UTC)
    assert plan.window_end == task.created_at
    assert plan.used_context_refs == ("owner",)


def test_substantive_contact_refinement_remains_eligible_for_grounded_planner() -> None:
    text = "А Алексей мне сегодня писал?" + SOURCE_TASK_REFINEMENT + "Алексей Ветров"
    task = request(text)
    projection = SourceAnchorProjection(ref="owner", kind="current_request", text=text)
    assert plan_named_incoming_contact(task, (projection,)) is None
    start, end = source_task_window(task)
    proposal = SourceAnchorChoice(
        status="search",
        literal_anchor="Алексей Ветров",
        canonical_ref="owner",
        anchor_kind="person",
        window_start=start,
        window_end=end,
    )
    assert compile_source_search_anchor(task, proposal, (projection,)).telegram_match == "peer"


def test_person_mentioned_in_messages_does_not_become_a_sender() -> None:
    task = request("Посмотри в Telegram, что писали про Алексея")
    projection = SourceAnchorProjection(ref="owner", kind="current_request", text=task.user_text)
    proposal = choice(task, "Алексея", "owner").model_copy(update={"anchor_kind": "person"})
    plan = compile_source_search_anchor(task, proposal, (projection,))
    assert plan.telegram_query == "Алексея"
    assert plan.telegram_match == "text"


def test_yesterday_window_cannot_be_broadened_by_a_source_plan() -> None:
    task = request("А Алексей мне вчера писал?")
    start, end = source_task_window(task)
    assert (start, end) == (
        datetime(2026, 11, 4, 21, tzinfo=UTC),
        datetime(2026, 11, 5, 21, tzinfo=UTC),
    )
    projection = SourceAnchorProjection(ref="owner", kind="current_request", text=task.user_text)
    plan = plan_named_incoming_contact(task, (projection,))
    assert plan is not None
    assert task_search_admission(task, plan, "telegram", task.created_at) == "Алексей"
    widened = plan.model_copy(update={"window_end": task.created_at})
    with pytest.raises(SourceAnswerError, match="scope_invalid"):
        task_search_admission(task, widened, "telegram", task.created_at)


def test_a_quoted_day_word_does_not_shrink_the_search() -> None:
    task = request("Проверь ответ от компании «Сегодня»")
    assert source_task_window(task) == (task.created_at - timedelta(days=30), task.created_at)
