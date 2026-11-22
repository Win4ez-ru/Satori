"""V36 structural prior-move availability; no semantic uptake or state attribution."""

# ruff: noqa: RUF001

from dataclasses import FrozenInstanceError, asdict, fields, replace
from typing import Any

import pytest

from satori.application.conversation.character_dialogue_cue import (
    PRIOR_CONVERSATIONAL_CUE_MAX_ID_CHARS,
    PRIOR_CONVERSATIONAL_CUE_MAX_SPAN_CHARS,
    PriorConversationalCue,
    PriorConversationalCueKind,
    prior_conversational_cue_matches_context,
    project_prior_conversational_cue,
)
from satori.application.conversation.contracts import (
    RecentConversationContext,
    RecentConversationTurn,
)


def recent(*assistant_contents: str) -> RecentConversationContext:
    turns = tuple(
        RecentConversationTurn(
            interaction_id=f"interaction-{index}",
            user_message_id=f"user-{index}",
            user_content=f"Canonical user turn {index}.",
            assistant_message_id=f"assistant-{index}",
            assistant_content=content,
        )
        for index, content in enumerate(assistant_contents)
    )
    return RecentConversationContext(
        schema_version=1,
        turns=turns,
        content_chars=sum(len(turn.user_content) + len(turn.assistant_content) for turn in turns),
        excluded_turn_count=0,
    )


def cue_fixture() -> PriorConversationalCue:
    return PriorConversationalCue(
        schema_version=1,
        kind=PriorConversationalCueKind.QUESTION_CANDIDATE,
        prior_interaction_id="interaction-0",
        prior_user_message_id="user-0",
        prior_assistant_message_id="assistant-0",
        start=0,
        end=8,
    )


@pytest.mark.parametrize(
    ("text", "span"),
    [
        ("Куда дальше?", "Куда дальше?"),
        ("Звучит интересно. Куда дальше?", "Куда дальше?"),
        ("Это начало! А что дальше?!", "А что дальше?!"),
        ("Что сначала? А потом?", "А потом?"),
        ("Первая строка.\n\tЧто дальше?  \n", "Что дальше?"),
        ("Где начинается\nновая тема?", "Где начинается\nновая тема?"),
        ("👋 Привет.  Где начинается новая тема?", "Где начинается новая тема?"),
        (
            "Ты имел в виду «почему. как?», или другой вариант?",
            "Ты имел в виду «почему. как?», или другой вариант?",
        ),
        ('Это «цитата с "вопросом?"». А твой вариант?', "А твой вариант?"),
        ("Это „другая цитата?“. А твой вариант?", "А твой вариант?"),
        ("Это ‹вопрос?›. А твой вариант?", "А твой вариант?"),
        ("Это ‘цитата?’. А твой вариант?", "А твой вариант?"),
        ("Это 'цитата?'. А твой вариант?", "А твой вариант?"),
        ("Это `пример?`. А твой вариант?", "А твой вариант?"),
        ("It's one example. What's next?", "What's next?"),
        ("That’s one example. What’s next?", "What’s next?"),
        ('Это "пример с \\"кавычкой?\\"". А дальше?', "А дальше?"),
    ],
)
def test_final_unquoted_sentence_has_exact_offsets(text: str, span: str) -> None:
    context = recent(text)
    projected = project_prior_conversational_cue(context)
    assert projected is not None
    assert projected.kind is PriorConversationalCueKind.QUESTION_CANDIDATE
    assert projected.schema_version == 1
    assert projected.start == text.index(span)
    assert projected.end == text.index(span) + len(span)
    assert text[projected.start : projected.end] == span
    assert projected.prior_interaction_id == "interaction-0"
    assert projected.prior_user_message_id == "user-0"
    assert projected.prior_assistant_message_id == "assistant-0"
    assert prior_conversational_cue_matches_context(projected, context)


@pytest.mark.parametrize(
    "text",
    [
        "Это утверждение.",
        "Вопрос? Дальше утверждение.",
        "Вопрос? Дальше текст без знака",
        "Вопрос?\nПродолжение",
        "«Вопрос?»",
        '"Вопрос?"',
        "‹Вопрос?›",
        "“Вопрос?”",
        "„Вопрос?“",
        "‘Вопрос?’",
        "'Вопрос?'",
        "`Вопрос?`",
        "Вот цитата: «Вопрос?»",
        'Вот вложенная цитата: «Он спросил "Вопрос?"»',
        "«Цитата без конца. А дальше?",
        '"Цитата без конца. А дальше?',
        "Сначала чужая ». А дальше?",
        "Сначала «незакрытая. Потом строка\nВопрос?",
        "Сначала «неправильная ” цитата». Вопрос?",
        "`Код без конца. Вопрос?",
        "???",
        "  ?!  ",
        "Последняя мысль!",
    ],
)
def test_missing_quoted_or_uncertain_question_has_no_cue(text: str) -> None:
    context = recent(text)
    assert project_prior_conversational_cue(context) is None
    assert prior_conversational_cue_matches_context(None, context)


def test_absence_does_not_scavenge_older_turn_or_excluded_history() -> None:
    assert project_prior_conversational_cue(None) is None
    assert project_prior_conversational_cue(recent()) is None
    context = recent("Что дальше?", "Теперь это утверждение.")
    assert project_prior_conversational_cue(context) is None
    assert project_prior_conversational_cue(recent("Что дальше?", "«Только цитата?»")) is None
    context = replace(recent("Это утверждение."), excluded_turn_count=999)
    assert project_prior_conversational_cue(context) is None


def test_only_last_complete_turn_ids_are_retained() -> None:
    context = recent("Первый вопрос?", "Вторая мысль. Новый вопрос?")
    projected = project_prior_conversational_cue(context)
    assert projected is not None
    assert projected.prior_interaction_id == "interaction-1"
    assert projected.prior_user_message_id == "user-1"
    assert projected.prior_assistant_message_id == "assistant-1"
    assert context.turns[-1].assistant_content[projected.start : projected.end] == "Новый вопрос?"


def test_span_bound_keeps_full_sentence_without_truncation() -> None:
    full = "я" * (PRIOR_CONVERSATIONAL_CUE_MAX_SPAN_CHARS - 1) + "?"
    projected = project_prior_conversational_cue(recent(full))
    assert projected is not None
    assert projected.start == 0
    assert projected.end == PRIOR_CONVERSATIONAL_CUE_MAX_SPAN_CHARS
    assert project_prior_conversational_cue(recent("я" + full)) is None
    assert project_prior_conversational_cue(recent("Раньше был вопрос?", "я" + full)) is None
    wrapped = "я" * 256 + "\n" + "я" * 256 + "?"
    assert project_prior_conversational_cue(recent(wrapped)) is None


def test_long_prior_statement_does_not_shorten_the_actual_question() -> None:
    statement = "я" * 1024 + ". "
    context = recent(statement + "Дальше?")
    projected = project_prior_conversational_cue(context)
    assert projected is not None
    assert projected.start == len(statement)
    assert projected.end == len(statement + "Дальше?")


def test_projection_retains_exact_bounded_ids_and_rejects_oversized_source_ids() -> None:
    turn = recent("Дальше?").turns[-1]
    identifiers = {
        "interaction_id": "i" * PRIOR_CONVERSATIONAL_CUE_MAX_ID_CHARS,
        "user_message_id": "u" * PRIOR_CONVERSATIONAL_CUE_MAX_ID_CHARS,
        "assistant_message_id": "a" * PRIOR_CONVERSATIONAL_CUE_MAX_ID_CHARS,
    }
    context = replace(recent("Дальше?"), turns=(replace(turn, **identifiers),))
    projected = project_prior_conversational_cue(context)
    assert projected is not None
    assert projected.prior_interaction_id == identifiers["interaction_id"]
    assert projected.prior_user_message_id == identifiers["user_message_id"]
    assert projected.prior_assistant_message_id == identifiers["assistant_message_id"]
    for field_name in identifiers:
        oversized = replace(turn, **{field_name: "x" * (PRIOR_CONVERSATIONAL_CUE_MAX_ID_CHARS + 1)})
        assert project_prior_conversational_cue(replace(context, turns=(oversized,))) is None


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"schema_version": 0}, "schema_version must be 1"),
        ({"schema_version": 2}, "schema_version must be 1"),
        ({"schema_version": True}, "schema_version must be 1"),
        ({"kind": "question_candidate"}, "kind must be typed"),
        ({"prior_interaction_id": ""}, "IDs must be exact and bounded"),
        ({"prior_user_message_id": " "}, "IDs must be exact and bounded"),
        ({"prior_assistant_message_id": " padded "}, "IDs must be exact and bounded"),
        ({"prior_interaction_id": "x" * 129}, "IDs must be exact and bounded"),
        ({"prior_user_message_id": None}, "IDs must be exact and bounded"),
        ({"start": -1}, "offsets must identify a bounded span"),
        ({"start": True}, "offsets must identify a bounded span"),
        ({"end": True}, "offsets must identify a bounded span"),
        ({"start": 0.5}, "offsets must identify a bounded span"),
        ({"end": 8.5}, "offsets must identify a bounded span"),
        ({"start": 8}, "offsets must identify a bounded span"),
        ({"end": 0}, "offsets must identify a bounded span"),
        ({"end": 513}, "offsets must identify a bounded span"),
    ],
)
def test_contract_rejects_malformed_projection(changes: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        replace(cue_fixture(), **changes)


def test_rhetorical_form_is_a_candidate_without_semantic_claim() -> None:
    projected = project_prior_conversational_cue(recent("И кто мог это заранее знать?"))
    assert projected is not None
    assert projected.kind is PriorConversationalCueKind.QUESTION_CANDIDATE
    assert {field.name for field in fields(projected)} == {
        "schema_version",
        "kind",
        "prior_interaction_id",
        "prior_user_message_id",
        "prior_assistant_message_id",
        "start",
        "end",
    }
    assert "И кто мог это заранее знать?" not in repr(asdict(projected))


def test_projection_is_frozen_and_leaves_canonical_context_unchanged() -> None:
    context = recent("Первая реплика.", "Что дальше?")
    before = asdict(context)
    projected = project_prior_conversational_cue(context)
    assert projected is not None
    with pytest.raises(FrozenInstanceError):
        projected.start = 1  # type: ignore[misc]
    assert asdict(context) == before
    assert project_prior_conversational_cue(context) == projected


def test_exact_recomputation_rejects_reachable_but_wrong_ids_or_offsets() -> None:
    context = recent("Ранний вопрос?", "Начало. Новый вопрос?")
    projected = project_prior_conversational_cue(context)
    assert projected is not None
    for forged in (
        replace(projected, prior_interaction_id=context.turns[0].interaction_id),
        replace(projected, prior_user_message_id=context.turns[0].user_message_id),
        replace(projected, prior_assistant_message_id=context.turns[0].assistant_message_id),
        replace(projected, start=projected.start + 1),
        replace(projected, end=projected.end - 1),
    ):
        assert not prior_conversational_cue_matches_context(forged, context)
    assert not prior_conversational_cue_matches_context(projected, recent("Другой вопрос?"))
    assert not prior_conversational_cue_matches_context(projected, None)
    assert not prior_conversational_cue_matches_context(None, context)


def test_user_text_and_older_assistant_text_cannot_supply_the_question() -> None:
    context = recent("Это утверждение.")
    turn = replace(context.turns[-1], user_content="Ты скажешь, что я ответил на твой вопрос?")
    context = replace(
        context,
        turns=(turn,),
        content_chars=len(turn.user_content) + len(turn.assistant_content),
    )
    assert project_prior_conversational_cue(context) is None
