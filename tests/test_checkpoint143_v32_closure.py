"""V32 direct-stop bounds, without a semantic judge for prior conversation resolution."""

# ruff: noqa: RUF001

from dataclasses import replace
from typing import cast

import pytest

from satori.application.conversation.character_agency import (
    CharacterAgencyAct,
    CharacterAgencyDrive,
    CharacterAgencyInitiative,
    CharacterAgencyKernel,
    CharacterAgencyReason,
    CharacterAgencySubject,
)
from satori.application.conversation.character_evidence import (
    CharacterClosureKind,
    analyze_character_request_evidence,
)
from satori.application.conversation.context import plan_conversational_disclosure
from tests.test_checkpoint142_character_delivery_v9 import (
    _affect,
    _inclinations,
    _positions,
    _relationship,
    _runtime_context,
)
from tests.test_checkpoint143_character_agency_corpus import _prepare, _recent


def test_closure_kind_rejects_untyped_string() -> None:
    evidence = analyze_character_request_evidence("На этом всё.", None, policy_schema_version=32)
    with pytest.raises(ValueError, match="character closure kind must be typed"):
        replace(evidence, closure_kind=cast(CharacterClosureKind, "end_discussion"))


@pytest.mark.parametrize(
    "text",
    [
        "На этом всё.",
        "Хорошо, на этом всё.",
        "Ну ладно, закрыли тему.",
        "Закрыли эту тему!",
        "Пожалуй, оставим это пока.",
        "Оставим пока…",
        "Не продолжай тему.",
        "Не продолжай эту тему. На этом всё.",
        "Не продолжай тему. Закрыли тему.",
        "Давай закроем эту тему.",
        "На этом закончим.",
    ],
)
def test_direct_whole_turn_end_discussion(text: str) -> None:
    evidence = analyze_character_request_evidence(text, None, policy_schema_version=32)
    assert evidence.topic_closure
    assert evidence.closure_kind is CharacterClosureKind.END_DISCUSSION


@pytest.mark.parametrize(
    "text",
    [
        "С этим разобрались.",
        "Ну ладно, кажется, с этим разобрались.",
        "Хорошо, с этим мы разобрались!",
        "Договорились.",
        "Ну ладно, договорились…",
    ],
)
def test_claimed_resolution_is_language_evidence_only(text: str) -> None:
    evidence = analyze_character_request_evidence(text, None, policy_schema_version=32)
    assert evidence.topic_closure
    assert evidence.closure_kind is CharacterClosureKind.CLAIMED_RESOLUTION


@pytest.mark.parametrize(
    "text",
    [
        "На этом всё?",
        "Кажется, с этим разобрались?",
        "Нет, с этим не разобрались.",
        "Не закрыли тему.",
        "Давай не закроем тему.",
        "Не оставим это пока.",
        "Не продолжай тему?",
        "Не продолжай тему? На этом всё.",
        "Он сказал: на этом всё.",
        "Он сказал: «Не продолжай тему. На этом всё.»",
        "Мы вчера сказали, что с этим разобрались.",
        "«На этом всё.»",
        '"С этим разобрались."',
        "`Закрыли тему.`",
        "Повтори за мной: на этом всё.",
        "Если с этим разобрались, объясни итог.",
        "Если бы мы сказали: на этом всё.",
        "Представь, что мы договорились.",
        "Возможно, с этим разобрались.",
        "Не утверждаю, что с этим разобрались.",
        "Ну ладно, с этим разобрались. Но у меня ещё вопрос.",
        "На этом всё. Объясни разницу между списком и кортежем в Python.",
        "Не продолжай тему. Просто послушай меня.",
        "На этом всё. Мне сейчас очень тяжело.",
    ],
)
def test_closure_grammar_excludes_reports_questions_and_mixed_turns(text: str) -> None:
    evidence = analyze_character_request_evidence(text, None, policy_schema_version=32)
    assert not evidence.topic_closure
    assert evidence.closure_kind is CharacterClosureKind.NONE


@pytest.mark.parametrize("version", [10, 27, 28, 29, 30, 31])
@pytest.mark.parametrize(
    "text",
    [
        "На этом всё.",
        "Ну ладно, кажется, с этим разобрались.",
        "Не продолжай тему. На этом всё.",
    ],
)
def test_historical_policies_never_receive_new_closure_authority(version: int, text: str) -> None:
    evidence = analyze_character_request_evidence(text, None, policy_schema_version=version)
    assert evidence.closure_kind is CharacterClosureKind.NONE
    if text == "Не продолжай тему. На этом всё.":
        assert not evidence.topic_closure
    elif text == "На этом всё.":
        assert evidence.topic_closure
    else:
        assert evidence.topic_closure is (version >= 29)


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("На этом всё.", CharacterClosureKind.END_DISCUSSION),
        ("Кажется, с этим разобрались.", CharacterClosureKind.CLAIMED_RESOLUTION),
    ],
)
def test_closure_kind_does_not_infer_question_resolution_from_history(
    text: str, kind: CharacterClosureKind
) -> None:
    recent = _recent(("Ты переоцениваешь риск.",))
    for history in (None, recent):
        evidence = analyze_character_request_evidence(text, history, policy_schema_version=32)
        assert evidence.closure_kind is kind
        assert evidence.topic_closure


@pytest.mark.parametrize("text", ["На этом всё.", "Не продолжай тему. На этом всё."])
def test_explicit_stop_blocks_canonical_interest_pivot_and_broader_read(text: str) -> None:
    intake, _, dialogue, _ = _prepare(text, recent=None)
    evidence = analyze_character_request_evidence(text, None, policy_schema_version=32)
    relationship = _relationship("established")
    kernel = CharacterAgencyKernel()
    decision = kernel.select(
        context=_runtime_context(retrieval_available=False),
        intake=intake,
        evidence=evidence,
        dialogue=dialogue,
        disclosure_plan=plan_conversational_disclosure(text, dialogue, policy_schema_version=32),
        emotional_context=_affect("calm"),
        relationship_context=relationship,
        position_context=_positions(None),
        inclination_context=_inclinations("available"),
    )
    assert decision.drive is CharacterAgencyDrive.CLOSE
    assert decision.act is CharacterAgencyAct.CLOSE
    assert decision.initiative is CharacterAgencyInitiative.STOP
    assert decision.subject is CharacterAgencySubject.CURRENT_EXCHANGE
    assert decision.subject_ref is None
    assert decision.reason_codes == (CharacterAgencyReason.TOPIC_CLOSURE,)
    assert not kernel.allows_owned_topic_projection(
        evidence=evidence, dialogue=dialogue, relationship=relationship
    )


@pytest.mark.parametrize("version", [29, 30, 31])
def test_historical_stop_preserves_its_original_canonical_interest_pivot(version: int) -> None:
    text = "На этом всё."
    intake, _, dialogue, _ = _prepare(text, recent=None)
    evidence = analyze_character_request_evidence(text, None, policy_schema_version=version)
    relationship = _relationship("established")
    kernel = CharacterAgencyKernel()
    decision = kernel.select(
        context=_runtime_context(retrieval_available=False),
        intake=intake,
        evidence=evidence,
        dialogue=dialogue,
        disclosure_plan=plan_conversational_disclosure(
            text, dialogue, policy_schema_version=version
        ),
        emotional_context=_affect("calm"),
        relationship_context=relationship,
        position_context=_positions(None),
        inclination_context=_inclinations("available"),
    )
    assert decision.drive is CharacterAgencyDrive.EXPLORE
    assert decision.initiative is CharacterAgencyInitiative.SHIFT_ADJACENT
    assert decision.subject_ref == "inclination-architecture"
    assert kernel.allows_owned_topic_projection(
        evidence=evidence, dialogue=dialogue, relationship=relationship
    )


@pytest.mark.parametrize("version", [29, 30, 31, 32])
def test_claimed_resolution_retains_existing_owned_interest_option(version: int) -> None:
    text = "Ну ладно, кажется, с этим разобрались."
    intake, _, dialogue, _ = _prepare(text, recent=None)
    evidence = analyze_character_request_evidence(text, None, policy_schema_version=version)
    relationship = _relationship("established")
    kernel = CharacterAgencyKernel()
    decision = kernel.select(
        context=_runtime_context(retrieval_available=False),
        intake=intake,
        evidence=evidence,
        dialogue=dialogue,
        disclosure_plan=plan_conversational_disclosure(
            text, dialogue, policy_schema_version=version
        ),
        emotional_context=_affect("calm"),
        relationship_context=relationship,
        position_context=_positions(None),
        inclination_context=_inclinations("available"),
    )
    assert decision.drive is CharacterAgencyDrive.EXPLORE
    assert decision.initiative is CharacterAgencyInitiative.SHIFT_ADJACENT
    assert decision.subject_ref == "inclination-architecture"
    assert kernel.allows_owned_topic_projection(
        evidence=evidence, dialogue=dialogue, relationship=relationship
    )


@pytest.mark.parametrize(
    ("text", "act"),
    [
        ("На этом всё. Просто послушай, без советов.", CharacterAgencyAct.STAY_PRESENT),
        ("На этом всё. Мне сейчас очень тяжело.", CharacterAgencyAct.STAY_PRESENT),
        (
            "На этом всё. Объясни разницу между списком и кортежем в Python.",
            CharacterAgencyAct.HELP,
        ),
    ],
)
def test_mixed_protected_requests_keep_existing_priority(
    text: str, act: CharacterAgencyAct
) -> None:
    intake, _, dialogue, _ = _prepare(text, recent=None)
    evidence = analyze_character_request_evidence(text, None, policy_schema_version=32)
    assert evidence.closure_kind is CharacterClosureKind.NONE
    decision = CharacterAgencyKernel().select(
        context=_runtime_context(retrieval_available=False),
        intake=intake,
        evidence=evidence,
        dialogue=dialogue,
        disclosure_plan=plan_conversational_disclosure(text, dialogue, policy_schema_version=32),
        emotional_context=_affect("calm"),
        relationship_context=_relationship("established"),
        position_context=_positions(None),
        inclination_context=_inclinations(None),
    )
    assert decision.act is act
