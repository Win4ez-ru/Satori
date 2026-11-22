"""Sender semantics survive a semantic route into the actual bounded search plan."""

# ruff: noqa: RUF001 - public synthetic Russian owner wording.

import json
from typing import Literal

import pytest
from pydantic import ValidationError

from satori.application.assistant.source_answer_contracts import (
    SourceAnswerError,
    SourceTaskRequest,
    source_digest,
)
from satori.application.assistant.source_search_anchor import (
    SourceAnchorChoice,
    SourceAnchorProjection,
    compile_source_search_anchor,
    plan_named_incoming_contact,
)
from satori.application.assistant.source_task_reads import source_task_window, task_search_admission
from tests.test_source_search_anchor import request

PUBLIC_REQUEST = "Здорово, что мне Алексей писал в тг?"


def task_with_hint(text: str = PUBLIC_REQUEST, hint: str = "Алексей") -> SourceTaskRequest:
    return SourceTaskRequest.model_validate({**request(text).model_dump(), "sender_literal": hint})


@pytest.mark.parametrize("sources", [("mail", "telegram"), ("mail",), ("telegram",)])
def test_original_failure_compiles_sender_search_for_only_selected_sources(
    sources: tuple[Literal["mail", "telegram"], ...],
) -> None:
    task = task_with_hint().model_copy(update={"sources": sources})
    projection = SourceAnchorProjection(ref="owner", kind="current_request", text=task.user_text)
    plan = plan_named_incoming_contact(task, (projection,))
    assert plan is not None
    assert plan.mail_query == ('from:("Алексей")' if "mail" in sources else None)
    assert plan.telegram_query == ("Алексей" if "telegram" in sources else None)
    assert plan.telegram_match == ("peer" if "telegram" in sources else "text")
    assert plan.used_context_refs == ("owner",)
    assert (plan.window_start, plan.window_end) == source_task_window(task)
    for source in sources:
        # The same plan shape is accepted by the real source-owner admission contract.
        task_search_admission(task, plan, source, task.created_at)


def test_model_anchor_choice_cannot_lose_the_explicit_sender_relation() -> None:
    task = task_with_hint()
    start, end = source_task_window(task)
    plan = compile_source_search_anchor(
        task,
        SourceAnchorChoice(
            status="search",
            literal_anchor="Алексей",
            canonical_ref="owner",
            anchor_kind="person",
            window_start=start,
            window_end=end,
        ),
        (SourceAnchorProjection(ref="owner", kind="current_request", text=task.user_text),),
    )
    assert plan.mail_query == 'from:("Алексей")'
    assert plan.telegram_match == "peer"


def test_topic_person_stays_text_without_a_sender_hint() -> None:
    task = request("Здорово, что в тг писали про Алексея?")
    start, end = source_task_window(task)
    assert plan_named_incoming_contact(task, ()) is None
    plan = compile_source_search_anchor(
        task,
        SourceAnchorChoice(
            status="search",
            literal_anchor="Алексея",
            canonical_ref="owner",
            anchor_kind="person",
            window_start=start,
            window_end=end,
        ),
        (SourceAnchorProjection(ref="owner", kind="current_request", text=task.user_text),),
    )
    assert plan.mail_query == '"Алексея"'
    assert plan.telegram_match == "text"


@pytest.mark.parametrize("hint", ["Борис", "", " ", "А" * 201, "Алексей OR from:evil"])
def test_invented_empty_or_oversized_sender_hint_is_rejected(hint: str) -> None:
    with pytest.raises(ValidationError):
        task_with_hint(hint=hint)


def test_hint_remains_private_in_repr_and_exact_on_json_wire() -> None:
    task = task_with_hint()
    assert "Алексей" not in repr(task)
    assert json.loads(task.model_dump_json())["sender_literal"] == "Алексей"
    assert SourceTaskRequest.model_validate_json(task.model_dump_json()) == task
    without = task.model_copy(update={"sender_literal": None})
    assert "sender_literal" not in without.model_dump()
    assert task.digest != without.digest
    assert without.digest == source_digest(without.model_dump(mode="json"))


@pytest.mark.parametrize("kind", ["person", "company"])
def test_hint_cannot_authorize_different_person_or_company(
    kind: Literal["person", "company"],
) -> None:
    task = task_with_hint(PUBLIC_REQUEST + " Обсуждали Бориса и компанию Лаборатория.")
    start, end = source_task_window(task)
    with pytest.raises(SourceAnswerError, match="sender_mismatch"):
        compile_source_search_anchor(
            task,
            SourceAnchorChoice(
                status="search",
                literal_anchor="Бориса" if kind == "person" else "Лаборатория",
                canonical_ref="owner",
                anchor_kind=kind,
                window_start=start,
                window_end=end,
            ),
            (SourceAnchorProjection(ref="owner", kind="current_request", text=task.user_text),),
        )


def test_sender_hint_needs_current_owner_projection_not_memory_only() -> None:
    task = task_with_hint()
    start, end = source_task_window(task)
    with pytest.raises(SourceAnswerError, match="anchor_ungrounded"):
        plan_named_incoming_contact(
            task, (SourceAnchorProjection(ref="old", kind="recent_user", text="Алексей"),)
        )
    with pytest.raises(SourceAnswerError, match="sender_mismatch"):
        compile_source_search_anchor(
            task,
            SourceAnchorChoice(
                status="search",
                literal_anchor="Алексей",
                canonical_ref="memory",
                anchor_kind="person",
                window_start=start,
                window_end=end,
            ),
            (SourceAnchorProjection(ref="memory", kind="canonical_memory", text="Алексей"),),
        )


@pytest.mark.parametrize("hint", ["from:evil", 'Имя" OR is:unread', "Имя\nКод"])
def test_literal_hint_still_cannot_emit_operators_or_controls(hint: str) -> None:
    task = task_with_hint("Кто-то упомянул " + hint, hint)
    with pytest.raises(SourceAnswerError, match="anchor_invalid"):
        plan_named_incoming_contact(
            task,
            (SourceAnchorProjection(ref="owner", kind="current_request", text=task.user_text),),
        )
