"""Prior relation changes one ordinary lead, never owners or protected obligations."""

# ruff: noqa: RUF001

import json
from dataclasses import asdict, replace

import pytest

from satori.application.cognition.contracts import (
    CognitionDialogueSignals,
    PreparedCognitionIntake,
)
from satori.application.cognition.use_cases import DeterministicCognitionPlanner
from satori.application.conversation.character_agency import (
    CharacterAgencyDecision,
    CharacterAgencyKernel,
    CharacterAgencyLead,
    CharacterAgencyReason,
)
from satori.application.conversation.character_evidence import (
    CharacterClosureKind,
    CharacterRequestEvidence,
    analyze_character_request_evidence,
)
from satori.application.conversation.coherence import (
    DialogueCoherenceContext,
    analyze_dialogue_coherence,
)
from satori.application.conversation.context import (
    ConversationRequestBuilder,
    plan_conversational_disclosure,
)
from satori.application.conversation.contracts import (
    ConversationContextManifest,
    RecentConversationContext,
    RecentConversationTurn,
)
from satori.application.conversation.dialogue_relation_contracts import (
    PriorDialogueRelation,
    PriorDialogueRelationKind,
    PriorDialogueRelationUncertainty,
)
from satori.application.conversation.disclosure_contracts import (
    ConversationalDisclosurePlan,
    is_satori_self_disclosure_plan,
)
from satori.application.conversation.policy import BEHAVIOR_POLICY_V35, BEHAVIOR_POLICY_V36
from satori.core.conversation import ConversationMessageRole, ConversationProviderRequest
from tests.checkpoint143_v34_corpus import affect_context
from tests.test_checkpoint142_character_delivery_v9 import _runtime_context

CURRENT = "У старого фонаря теперь снова работает переключатель."
PRIOR = "Мне всё ещё интересно, почему он перестал работать."


def _prepared_inputs(
    text: str, *, recent: RecentConversationContext
) -> tuple[
    PreparedCognitionIntake,
    CharacterRequestEvidence,
    DialogueCoherenceContext,
    ConversationalDisclosurePlan,
]:
    """Use the same versioned analyzers as the isolated V35 candidate."""

    dialogue = analyze_dialogue_coherence(text, recent, policy_schema_version=35)
    evidence = analyze_character_request_evidence(text, recent, policy_schema_version=35)
    disclosure = plan_conversational_disclosure(text, dialogue, policy_schema_version=35)
    intake = DeterministicCognitionPlanner(
        intent_registry_version=2, monotonic=lambda: 0.0
    ).prepare_intake(
        user_text=text,
        user_message_id="agency-current-user",
        interaction_id="agency-current-interaction",
        dialogue=CognitionDialogueSignals(
            repeated_turn=dialogue.current_user_message_repeated,
            correction_active=any(
                (
                    dialogue.current_no_routine_questions_correction,
                    dialogue.current_informal_correction,
                    dialogue.current_repetition_feedback,
                    dialogue.current_relevance_feedback,
                    dialogue.current_frustration_feedback,
                    dialogue.current_contradiction_feedback,
                )
            ),
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
    )
    return intake, evidence, dialogue, disclosure


def recent() -> RecentConversationContext:
    turn = RecentConversationTurn(
        interaction_id="prior-interaction",
        user_message_id="prior-user",
        user_content="Фонарь сломался.",
        assistant_message_id="prior-assistant",
        assistant_content=PRIOR,
    )
    return RecentConversationContext(1, (turn,), len(turn.user_content) + len(PRIOR), 0)


def relation(
    *,
    kind: PriorDialogueRelationKind = PriorDialogueRelationKind.SHIFT_FROM_PRIOR_MOVE,
    uncertainty: PriorDialogueRelationUncertainty = PriorDialogueRelationUncertainty.SUPPORTED,
) -> PriorDialogueRelation:
    return PriorDialogueRelation(
        schema_version=1,
        kind=kind,
        uncertainty=uncertainty,
        current_user_message_id="agency-current-user",
        prior_interaction_id="prior-interaction",
        prior_user_message_id="prior-user",
        prior_assistant_message_id="prior-assistant",
        prior_assistant_span=None if kind is PriorDialogueRelationKind.ABSTAIN else (0, len(PRIOR)),
        current_user_span=None if kind is PriorDialogueRelationKind.ABSTAIN else (0, len(CURRENT)),
    )


def select(
    text: str = CURRENT, *, candidate: PriorDialogueRelation | None = None
) -> CharacterAgencyDecision:
    intake, evidence, dialogue, _ = _prepared_inputs(text, recent=recent())
    return CharacterAgencyKernel().select(
        context=_runtime_context(retrieval_available=False),
        intake=intake,
        evidence=evidence,
        dialogue=dialogue,
        disclosure_plan=plan_conversational_disclosure(text, dialogue, policy_schema_version=35),
        emotional_context=affect_context("neutral"),
        relationship_context=None,
        position_context=None,
        inclination_context=None,
        prior_dialogue_relation=candidate,
    )


def build(
    *, candidate: PriorDialogueRelation | None = None, enabled: bool = True
) -> tuple[ConversationProviderRequest, ConversationContextManifest]:
    context = _runtime_context(retrieval_available=False)
    intake, evidence, dialogue, _ = _prepared_inputs(CURRENT, recent=recent())
    cognition = DeterministicCognitionPlanner(
        intent_registry_version=2, monotonic=lambda: 0.0
    ).complete(
        intake,
        interaction_id="agency-current-interaction",
        available_evidence_ids=recent().user_evidence_ids,
        prepared_affect=None,
        curiosity_influence=0.0,
    )
    return ConversationRequestBuilder(
        BEHAVIOR_POLICY_V35, 16000, 0.3, 384, prior_dialogue_relation_enabled=enabled
    ).build(
        context,
        user_text=CURRENT,
        current_user_message_id="agency-current-user",
        trace_id="relation-unit",
        recent_context=recent(),
        emotional_context=affect_context("neutral"),
        cognition_trace=cognition,
        dialogue_context=dialogue,
        character_evidence=evidence,
        character_agency=select(candidate=candidate),
    )


def test_supported_shift_changes_only_lead_and_transient_contract() -> None:
    before, after = select(), select(candidate=relation())
    assert before.lead is CharacterAgencyLead.FUSED
    assert after.lead is CharacterAgencyLead.OWNED_MOVE_FIRST
    assert before.schema_version == 1
    assert after.schema_version == 3
    assert after.reason_codes == (
        CharacterAgencyReason.DEFAULT_OWNED_RESPONSE,
        CharacterAgencyReason.PRIOR_DIALOGUE_RELATION,
    )
    old, new = asdict(before), asdict(after)
    for key in ("schema_version", "lead", "reason_codes", "prior_dialogue_relation"):
        old.pop(key)
        new.pop(key)
    assert new == old
    assert {"prior-user", "prior-assistant", "prior-interaction"}.isdisjoint(after.source_refs)


@pytest.mark.parametrize("kind", list(PriorDialogueRelationKind))
@pytest.mark.parametrize("uncertainty", list(PriorDialogueRelationUncertainty))
def test_nonshift_and_uncertain_are_exact_selection_controls(
    kind: PriorDialogueRelationKind, uncertainty: PriorDialogueRelationUncertainty
) -> None:
    if (
        kind is PriorDialogueRelationKind.SHIFT_FROM_PRIOR_MOVE
        and uncertainty is PriorDialogueRelationUncertainty.SUPPORTED
    ):
        return
    assert select(candidate=relation(kind=kind, uncertainty=uncertainty)) == select()


@pytest.mark.parametrize(
    "text",
    [
        "Объясни два пункта: что делает append и чем он отличается от extend в Python.",
        "Мне тяжело. Просто послушай, без советов.",
        "Я выжат, но всё равно продолжу работать через силу.",
        "Это было грубо с моей стороны. Извини.",
        "Не продолжай тему. На этом всё.",
        "Не задавай в конце дежурный вопрос. Отвечай прямо.",
        "Как себя чувствуешь?",
        "Я сегодня читаю научную статью.",
    ],
)
def test_relation_cannot_override_selected_obligations_or_other_owned_moves(text: str) -> None:
    assert select(text, candidate=relation()) == select(text)


def test_stop_control_has_actual_v35_end_discussion_evidence() -> None:
    text = "Не продолжай тему. На этом всё."
    _, evidence, _, _ = _prepared_inputs(text, recent=recent())
    assert evidence.topic_closure is True
    assert evidence.closure_kind is CharacterClosureKind.END_DISCUSSION
    assert select(text).drive.value == "close"
    assert select(text).initiative.value == "stop"
    assert select(text, candidate=relation()) == select(text)


def test_builder_requires_optin_and_exact_last_pair() -> None:
    with pytest.raises(ValueError, match="schema must match"):
        build(candidate=relation(), enabled=False)
    for forged in (
        replace(relation(), prior_assistant_message_id="foreign-assistant"),
        replace(relation(), prior_interaction_id="older-interaction"),
        replace(relation(), prior_assistant_span=(0, len(PRIOR) + 1)),
        replace(relation(), current_user_span=(0, len(CURRENT) + 1)),
    ):
        with pytest.raises(ValueError, match="exact current and recent input"):
            build(candidate=forged)
    with pytest.raises(ValueError, match="supported current shift schema 3"):
        build(candidate=replace(relation(), current_user_message_id="foreign-current"))


def test_relation_context_preserves_final_tail_and_original_roles() -> None:
    baseline, baseline_manifest = build(enabled=False)
    request, manifest = build(candidate=relation())
    assert baseline_manifest.schema_version == 17
    assert manifest.schema_version == 20
    assert manifest.character_agency_decision_schema_version == 3
    assert manifest.character_prior_dialogue_relation == relation()
    assert request.parameters == baseline.parameters
    assert request.messages[-2].role is ConversationMessageRole.DEVELOPER
    assert request.messages[-1] == baseline.messages[-1]
    datum = request.messages[-3]
    assert datum.role is ConversationMessageRole.USER
    data = json.loads(datum.content)["prior_dialogue_relation_data"]
    assert data["prior_assistant_excerpt"] == PRIOR
    assert data["current_user_excerpt"] == CURRENT
    assert request.messages[:-3] == baseline.messages[:-2]
    assert "prior_dialogue_relation_data" in manifest.included_sections


def test_enabled_inactive_request_is_exact_baseline_wire() -> None:
    baseline, _ = build(enabled=False)
    enabled, manifest = build()
    assert enabled == baseline
    assert manifest.prior_dialogue_relation_status == "skipped"
    assert "prior_dialogue_relation_data" not in manifest.included_sections


@pytest.mark.parametrize("kind", ["continues", "closes", "prior_already_closed"])
def test_supported_nonshift_metadata_does_not_claim_an_active_agency(kind: str) -> None:
    _, manifest = build()
    classified = replace(
        manifest,
        prior_dialogue_relation_status="applied",
        prior_dialogue_relation_reason="proposal_accepted",
        prior_dialogue_relation_kind=kind,
        prior_dialogue_relation_uncertainty="supported",
    )
    assert classified.character_agency_decision_schema_version == 1
    assert classified.character_prior_dialogue_relation is None
    assert "prior_dialogue_relation_data" not in classified.included_sections


def test_manifest_cannot_promote_unselected_or_older_relation() -> None:
    _, inactive = build()
    with pytest.raises(ValueError, match="requires its active agency relation"):
        replace(
            inactive,
            prior_dialogue_relation_status="applied",
            prior_dialogue_relation_reason="proposal_accepted",
            prior_dialogue_relation_kind="shift_from_prior_move",
            prior_dialogue_relation_uncertainty="supported",
        )
    _, active = build(candidate=relation())
    with pytest.raises(ValueError, match="matching classified recent metadata"):
        replace(active, recent_conversation_user_message_ids=("prior-user", "newer-user"))


def test_builder_rejects_combining_policy36_with_relation_candidate() -> None:
    with pytest.raises(ValueError, match="V35 control"):
        ConversationRequestBuilder(
            BEHAVIOR_POLICY_V36, 16000, 0.3, 384, prior_dialogue_relation_enabled=True
        )


def test_schema3_cannot_be_forged_without_exact_active_relation() -> None:
    with pytest.raises(ValueError, match="schema 3 requires an active"):
        replace(select(), schema_version=3)
    with pytest.raises(ValueError, match="exact reason and topology"):
        replace(select(candidate=relation()), lead=CharacterAgencyLead.FUSED)
    with pytest.raises(ValueError, match="topology is not licensed"):
        replace(select(candidate=relation()), schema_version=1)
