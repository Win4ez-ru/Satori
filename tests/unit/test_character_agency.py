"""Deterministic Character Agency Kernel contracts and selection precedence."""

# ruff: noqa: RUF001  # Russian public inputs exercise the real deterministic analyzers.

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, asdict, fields, replace

import pytest
from tests.test_checkpoint142_character_delivery_v9 import (
    _affect,
    _inclinations,
    _positions,
    _relationship,
    _runtime_context,
)

from satori.application.cognition.contracts import (
    CognitionDialogueSignals,
    PreparedCognitionIntake,
)
from satori.application.cognition.use_cases import DeterministicCognitionPlanner
from satori.application.conversation.character_agency import (
    CHARACTER_AGENCY_DECISION_SCHEMA_VERSION,
    CharacterAgencyAct,
    CharacterAgencyDecision,
    CharacterAgencyDrive,
    CharacterAgencyInitiative,
    CharacterAgencyKernel,
    CharacterAgencyLead,
    CharacterAgencyReason,
    CharacterAgencyStatus,
    CharacterAgencySubject,
)
from satori.application.conversation.character_evidence import (
    CharacterRequestEvidence,
    analyze_character_request_evidence,
)
from satori.application.conversation.coherence import (
    DialogueCoherenceContext,
    analyze_dialogue_coherence,
)
from satori.application.conversation.context import plan_conversational_disclosure
from satori.application.conversation.contracts import (
    RecentConversationContext,
    RecentConversationTurn,
)
from satori.application.conversation.disclosure_contracts import (
    ConversationalDisclosurePlan,
    is_satori_self_disclosure_plan,
)
from satori.application.conversation.policy import BEHAVIOR_POLICY_V28


def _recent(*user_messages: str) -> RecentConversationContext:
    turns = tuple(
        RecentConversationTurn(
            interaction_id=f"agency-recent-interaction-{index}",
            user_message_id=f"agency-recent-user-{index}",
            user_content=user_text,
            assistant_message_id=f"agency-recent-assistant-{index}",
            assistant_content=f"Canonical assistant reply {index}.",
        )
        for index, user_text in enumerate(user_messages, start=1)
    )
    return RecentConversationContext(
        schema_version=1,
        turns=turns,
        content_chars=sum(len(turn.user_content) + len(turn.assistant_content) for turn in turns),
        excluded_turn_count=0,
    )


def _prepared_inputs(
    user_text: str,
    *,
    recent: RecentConversationContext | None = None,
    fallback_reason: str | None = None,
) -> tuple[
    PreparedCognitionIntake,
    CharacterRequestEvidence,
    DialogueCoherenceContext,
    ConversationalDisclosurePlan,
]:
    dialogue = analyze_dialogue_coherence(user_text, recent)
    evidence = analyze_character_request_evidence(user_text, recent)
    disclosure = plan_conversational_disclosure(
        user_text,
        dialogue,
        policy_schema_version=BEHAVIOR_POLICY_V28.schema_version,
    )
    correction_active = any(
        (
            dialogue.current_no_routine_questions_correction,
            dialogue.current_informal_correction,
            dialogue.current_repetition_feedback,
            dialogue.current_relevance_feedback,
            dialogue.current_frustration_feedback,
            dialogue.current_contradiction_feedback,
        )
    )
    intake = DeterministicCognitionPlanner(intent_registry_version=2).prepare_intake(
        user_text=user_text,
        user_message_id="agency-current-user",
        interaction_id="agency-current-interaction",
        dialogue=CognitionDialogueSignals(
            repeated_turn=dialogue.current_user_message_repeated,
            correction_active=correction_active,
            no_routine_questions=dialogue.active_no_routine_questions_correction,
            current_activity=dialogue.current_activity_mention,
            explicit_listen_request=evidence.explicit_listen_request,
            high_distress=evidence.high_distress,
            harmful_overextension=evidence.harmful_overextension,
            explicit_motivation_request=evidence.explicit_motivation_request,
            explicit_task_abandonment=evidence.explicit_task_abandonment,
            explicit_repair_offer=evidence.explicit_repair_offer,
            self_disclosure_request=is_satori_self_disclosure_plan(disclosure),
        ),
        fallback_reason=fallback_reason,
    )
    return intake, evidence, dialogue, disclosure


def _select(
    user_text: str,
    *,
    recent: RecentConversationContext | None = None,
    fallback_reason: str | None = None,
    affect: str = "calm",
    relationship: str = "developing",
    position: str | None = None,
    inclination: str | None = None,
) -> CharacterAgencyDecision:
    intake, evidence, dialogue, disclosure = _prepared_inputs(
        user_text,
        recent=recent,
        fallback_reason=fallback_reason,
    )
    return CharacterAgencyKernel().select(
        context=_runtime_context(retrieval_available=False),
        intake=intake,
        evidence=evidence,
        dialogue=dialogue,
        disclosure_plan=disclosure,
        emotional_context=_affect(affect),
        relationship_context=_relationship(relationship),
        position_context=_positions(position),
        inclination_context=_inclinations(inclination),
    )


def test_decision_is_frozen_typed_v1_without_raw_prose() -> None:
    user_text = "Чем ты увлекаешься и что тебе интересно?"
    decision = _select(user_text, inclination="available")

    assert tuple(field.name for field in fields(CharacterAgencyDecision)) == (
        "schema_version",
        "status",
        "drive",
        "act",
        "subject",
        "initiative",
        "lead",
        "source_personality_codes",
        "source_value_key",
        "reason_codes",
        "source_refs",
        "subject_ref",
    )
    assert decision.schema_version == CHARACTER_AGENCY_DECISION_SCHEMA_VERSION
    assert decision.status is CharacterAgencyStatus.APPLIED
    assert decision.drive is CharacterAgencyDrive.SHARE_SELF
    assert decision.act is CharacterAgencyAct.SHARE
    assert decision.subject is CharacterAgencySubject.CANONICAL_INCLINATION
    assert decision.subject_ref == "inclination-architecture"
    assert CharacterAgencyReason.SELF_DISCLOSURE in decision.reason_codes
    assert user_text not in json.dumps(asdict(decision), ensure_ascii=False, default=str)
    assert "архитектура долгоживущих систем" not in json.dumps(
        asdict(decision), ensure_ascii=False, default=str
    )
    with pytest.raises(FrozenInstanceError):
        decision.drive = CharacterAgencyDrive.HELP  # type: ignore[misc]


def test_safety_and_repetition_have_terminal_precedence() -> None:
    unsafe = _select("Я точно устал, но точно буду работать до утра.")
    repeated_text = "Ну наконец-то я закончил сложную часть проекта."
    repeated = _select(repeated_text, recent=_recent(repeated_text))

    assert (
        unsafe.drive,
        unsafe.act,
        unsafe.subject,
        unsafe.initiative,
        unsafe.lead,
    ) == (
        CharacterAgencyDrive.PROTECT,
        CharacterAgencyAct.SET_BOUNDARY,
        CharacterAgencySubject.SAFETY,
        CharacterAgencyInitiative.STOP,
        CharacterAgencyLead.OBLIGATION_FIRST,
    )
    assert unsafe.reason_codes == (CharacterAgencyReason.SAFETY_PRECEDENCE,)
    assert repeated.drive is CharacterAgencyDrive.PLAY
    assert repeated.act is CharacterAgencyAct.ACKNOWLEDGE
    assert repeated.initiative is CharacterAgencyInitiative.STOP
    assert repeated.reason_codes == (CharacterAgencyReason.REPETITION_PRECEDENCE,)


def test_owned_position_and_inclination_use_exact_typed_refs() -> None:
    position = _select("Граничные проверки типов полезны.", position="available")
    adjacent = _select(
        "Ладно, с этим разобрались.",
        relationship="established",
        inclination="available",
    )
    closed = _select("Ладно, с этим разобрались.")

    assert (
        position.drive,
        position.act,
        position.subject,
        position.subject_ref,
    ) == (
        CharacterAgencyDrive.EXPRESS_VIEW,
        CharacterAgencyAct.SHARE,
        CharacterAgencySubject.CANONICAL_POSITION,
        "position-runtime-type-checks",
    )
    assert adjacent.drive is CharacterAgencyDrive.EXPLORE
    assert adjacent.act is CharacterAgencyAct.PROPOSE
    assert adjacent.subject is CharacterAgencySubject.CANONICAL_INCLINATION
    assert adjacent.initiative is CharacterAgencyInitiative.SHIFT_ADJACENT
    assert adjacent.subject_ref == "inclination-architecture"
    assert closed.drive is CharacterAgencyDrive.CLOSE
    assert closed.initiative is CharacterAgencyInitiative.STOP


def test_cognition_obligations_win_when_no_owned_move_is_available() -> None:
    factual = _select("Сколько байтов в килобайте?")
    requested = _select("Помоги проанализировать архитектуру проекта.")
    guarded = _select(
        "Помоги проанализировать архитектуру проекта.",
        relationship="strained",
    )
    fallback = _select(
        "Сегодня тихий день.",
        fallback_reason="deterministic-planner-failed",
    )

    assert factual.drive is CharacterAgencyDrive.NONE
    assert factual.act is CharacterAgencyAct.RESPOND
    assert factual.lead is CharacterAgencyLead.OBLIGATION_FIRST
    assert requested.drive is CharacterAgencyDrive.HELP
    assert requested.act is CharacterAgencyAct.HELP
    assert CharacterAgencyReason.ANALYSIS_NEED in requested.reason_codes
    assert guarded.drive is CharacterAgencyDrive.RESERVE
    assert guarded.act is CharacterAgencyAct.HELP
    assert guarded.lead is CharacterAgencyLead.OBLIGATION_FIRST
    assert fallback.status is CharacterAgencyStatus.FALLBACK
    assert fallback.drive is CharacterAgencyDrive.NONE
    assert fallback.initiative is CharacterAgencyInitiative.STOP
    assert CharacterAgencyReason.COGNITION_FALLBACK in fallback.reason_codes


def test_contract_rejects_untrusted_value_and_signal_divergence() -> None:
    decision = _select("Сегодня тихий день.")
    with pytest.raises(ValueError, match="canonical value key"):
        replace(decision, source_value_key="obedience")

    intake, evidence, dialogue, disclosure = _prepared_inputs("Сегодня тихий день.")
    forged_evidence = replace(evidence, harmful_overextension=True)
    with pytest.raises(ValueError, match="signal parity"):
        CharacterAgencyKernel().select(
            context=_runtime_context(retrieval_available=False),
            intake=intake,
            evidence=forged_evidence,
            dialogue=dialogue,
            disclosure_plan=disclosure,
            emotional_context=_affect("calm"),
            relationship_context=_relationship("developing"),
            position_context=None,
            inclination_context=_inclinations(None),
        )


def test_contract_rejects_unlicensed_act_and_untrusted_adjacent_shift() -> None:
    decision = _select("Сегодня тихий день.")

    with pytest.raises(ValueError, match="not licensed"):
        replace(decision, act=CharacterAgencyAct.QUESTION)
    with pytest.raises(ValueError, match="topology is not licensed"):
        replace(decision, initiative=CharacterAgencyInitiative.SHIFT_ADJACENT)


def test_social_exchange_connects_and_repeated_vulnerability_stays_care() -> None:
    greeting = _select("Привет, Сатори.")
    reciprocal = _select("И я тебя рад видеть.")
    current_attention = _select("Чем ты сейчас занята?")
    vulnerable_text = "Мне сейчас очень тяжело, я едва держусь. Просто побудь со мной."
    repeated_vulnerability = _select(vulnerable_text, recent=_recent(vulnerable_text))

    assert (greeting.drive, greeting.act, greeting.reason_codes) == (
        CharacterAgencyDrive.CONNECT,
        CharacterAgencyAct.RESPOND,
        (CharacterAgencyReason.SOCIAL_EXCHANGE,),
    )
    assert reciprocal.drive is CharacterAgencyDrive.CONNECT
    assert (
        current_attention.drive,
        current_attention.act,
        current_attention.subject,
        current_attention.reason_codes,
    ) == (
        CharacterAgencyDrive.CONNECT,
        CharacterAgencyAct.RESPOND,
        CharacterAgencySubject.CURRENT_EXCHANGE,
        (CharacterAgencyReason.CURRENT_ATTENTION_REQUEST,),
    )
    assert repeated_vulnerability.drive is CharacterAgencyDrive.CARE
    assert repeated_vulnerability.act is CharacterAgencyAct.ACKNOWLEDGE
    assert CharacterAgencyReason.HIGH_DISTRESS in repeated_vulnerability.reason_codes
    assert CharacterAgencyReason.REPETITION_PRECEDENCE in repeated_vulnerability.reason_codes


@pytest.mark.parametrize(
    "user_text",
    [
        "Что ты сейчас делаешь?",
        "Ты сейчас чем занимаешься?",
        "О чём размышляешь?",
        "Сатори, что у тебя сейчас на уме?",
        "Чем занимаешься сейчас, Сатори?",
    ],
)
def test_current_attention_paraphrases_stay_on_the_current_exchange(user_text: str) -> None:
    decision = _select(user_text)

    assert (
        decision.drive,
        decision.act,
        decision.subject,
        decision.reason_codes,
    ) == (
        CharacterAgencyDrive.CONNECT,
        CharacterAgencyAct.RESPOND,
        CharacterAgencySubject.CURRENT_EXCHANGE,
        (CharacterAgencyReason.CURRENT_ATTENTION_REQUEST,),
    )


@pytest.mark.parametrize(
    "user_text",
    [
        "Если бы я спросил, что ты сейчас делаешь, что бы ты ответила?",
        "Он спросил: «Что ты сейчас делаешь?»",
        "Повтори за мной: «О чем ты сейчас думаешь?»",
    ],
)
def test_reported_or_hypothetical_attention_phrases_do_not_create_current_state(
    user_text: str,
) -> None:
    evidence = analyze_character_request_evidence(user_text, None)

    assert evidence.current_attention_request is False


def test_explicit_obligation_precedes_available_position_without_losing_owned_answer() -> None:
    requested = _select(
        "Помоги проанализировать архитектуру проекта.",
        position="available",
    )
    questioned = _select(
        "Граничные проверки типов полезны?",
        position="available",
    )

    assert (requested.drive, requested.act, requested.subject, requested.lead) == (
        CharacterAgencyDrive.HELP,
        CharacterAgencyAct.HELP,
        CharacterAgencySubject.USER_REQUEST,
        CharacterAgencyLead.OBLIGATION_FIRST,
    )
    assert (questioned.drive, questioned.act, questioned.subject, questioned.lead) == (
        CharacterAgencyDrive.EXPRESS_VIEW,
        CharacterAgencyAct.RESPOND,
        CharacterAgencySubject.CANONICAL_POSITION,
        CharacterAgencyLead.OBLIGATION_FIRST,
    )
    assert questioned.subject_ref == "position-runtime-type-checks"


@pytest.mark.parametrize(
    ("decision", "expected_code"),
    [
        (_select("Сколько байтов в килобайте?"), "considered_directness"),
        (
            _select(
                "Я сегодня наконец закончил сложную часть проекта.",
                affect="positive",
                relationship="established",
            ),
            "light_irony",
        ),
        (
            _select("Знаешь, я почему-то почти не рад этому. Скорее просто выжат."),
            "warm_perceptive",
        ),
        (_select("Я сдаюсь с этим проектом."), "independent_position"),
    ],
)
def test_drive_selects_its_primary_canonical_personality_source(
    decision: CharacterAgencyDecision,
    expected_code: str,
) -> None:
    assert decision.source_personality_codes == (expected_code,)


def test_owned_topic_projection_is_eligible_only_for_unblocked_established_closure() -> None:
    kernel = CharacterAgencyKernel()
    closure = "Ладно, с этим разобрались."
    _, evidence, dialogue, _ = _prepared_inputs(closure)
    _, repeated_evidence, repeated_dialogue, _ = _prepared_inputs(
        closure,
        recent=_recent(closure),
    )

    assert kernel.allows_owned_topic_projection(
        evidence=evidence,
        dialogue=dialogue,
        relationship=_relationship("established"),
    )
    assert not kernel.allows_owned_topic_projection(
        evidence=evidence,
        dialogue=dialogue,
        relationship=_relationship("fresh"),
    )
    assert not kernel.allows_owned_topic_projection(
        evidence=repeated_evidence,
        dialogue=repeated_dialogue,
        relationship=_relationship("established"),
    )


def test_contract_rejects_semantically_contradictory_topology_and_provenance_reason() -> None:
    closed = _select("Ладно, с этим разобрались.")
    owned_position = _select("Граничные проверки типов полезны.", position="available")

    with pytest.raises(ValueError, match="topology is not licensed"):
        replace(
            closed,
            subject=CharacterAgencySubject.SAFETY,
            initiative=CharacterAgencyInitiative.ADVANCE_CURRENT,
            lead=CharacterAgencyLead.OBLIGATION_FIRST,
        )
    with pytest.raises(ValueError, match="typed provenance reason"):
        replace(
            owned_position,
            reason_codes=(CharacterAgencyReason.DEFAULT_OWNED_RESPONSE,),
        )
    with pytest.raises(ValueError, match="incompatible with its topology"):
        replace(
            closed,
            reason_codes=(CharacterAgencyReason.SAFETY_PRECEDENCE,),
        )
    current_attention = _select("Что ты сейчас делаешь?")
    with pytest.raises(ValueError, match="reason sequence is not licensed"):
        replace(
            current_attention,
            reason_codes=(
                CharacterAgencyReason.CURRENT_ATTENTION_REQUEST,
                CharacterAgencyReason.SOCIAL_EXCHANGE,
            ),
        )


def test_fallback_contract_accepts_only_the_exact_conservative_shape() -> None:
    fallback = _select(
        "Сегодня тихий день.",
        fallback_reason="controlled-intake-fallback",
    )

    assert fallback.reason_codes == (CharacterAgencyReason.COGNITION_FALLBACK,)
    with pytest.raises(ValueError, match="exact conservative topology"):
        replace(
            fallback,
            reason_codes=(
                CharacterAgencyReason.COGNITION_FALLBACK,
                CharacterAgencyReason.DEFAULT_OWNED_RESPONSE,
            ),
        )
    with pytest.raises(ValueError, match="exact conservative topology"):
        replace(fallback, initiative=CharacterAgencyInitiative.NONE)
