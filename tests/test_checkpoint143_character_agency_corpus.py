"""Deterministic semantic acceptance for the Checkpoint 14.3 agency kernel."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from satori.application.cognition.contracts import (
    CognitionDialogueSignals,
    CognitionOwner,
    PerceivedTopic,
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
from tests.checkpoint143_character_agency_corpus import (
    CONTROLLED_CONTRASTS,
    LIVE_FLOWS,
    SCENARIOS,
    AgencyScenario,
)
from tests.test_checkpoint142_character_delivery_v9 import (
    _affect,
    _inclinations,
    _positions,
    _relationship,
    _runtime_context,
)


@dataclass(frozen=True, slots=True)
class AgencyObservation:
    """Typed selection plus its independently derived public-input analyzers."""

    scenario_id: str
    decision: CharacterAgencyDecision
    intake: PreparedCognitionIntake
    evidence: CharacterRequestEvidence
    dialogue: DialogueCoherenceContext


def _recent(user_texts: tuple[str, ...]) -> RecentConversationContext | None:
    if not user_texts:
        return None
    turns = tuple(
        RecentConversationTurn(
            interaction_id=f"checkpoint143-recent-interaction-{index}",
            user_message_id=f"checkpoint143-recent-user-{index}",
            user_content=user_text,
            assistant_message_id=f"checkpoint143-recent-assistant-{index}",
            # The corpus intentionally supplies no reference assistant reply.  This
            # non-semantic placeholder only satisfies the canonical pair contract.
            assistant_content="…",
        )
        for index, user_text in enumerate(user_texts, start=1)
    )
    return RecentConversationContext(
        schema_version=1,
        turns=turns,
        content_chars=sum(len(turn.user_content) + len(turn.assistant_content) for turn in turns),
        excluded_turn_count=0,
    )


def _prepare(
    user_text: str,
    *,
    recent: RecentConversationContext | None,
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
        user_message_id="checkpoint143-current-user",
        interaction_id="checkpoint143-current-interaction",
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
    )
    return intake, evidence, dialogue, disclosure


def _observe(
    scenario: AgencyScenario,
    *,
    recent_override: tuple[str, ...] | None = None,
) -> AgencyObservation:
    state = scenario.state
    recent = _recent(state.recent_user_texts if recent_override is None else recent_override)
    intake, evidence, dialogue, disclosure = _prepare(scenario.user_text, recent=recent)
    decision = CharacterAgencyKernel().select(
        context=_runtime_context(retrieval_available=False),
        intake=intake,
        evidence=evidence,
        dialogue=dialogue,
        disclosure_plan=disclosure,
        emotional_context=_affect(state.affect),
        relationship_context=_relationship(state.relationship),
        position_context=_positions(state.position),
        inclination_context=_inclinations(state.inclination),
    )
    return AgencyObservation(
        scenario_id=scenario.scenario_id,
        decision=decision,
        intake=intake,
        evidence=evidence,
        dialogue=dialogue,
    )


def _assert_shape(
    decision: CharacterAgencyDecision,
    *,
    drive: CharacterAgencyDrive,
    act: CharacterAgencyAct,
    subject: CharacterAgencySubject,
    initiative: CharacterAgencyInitiative,
    lead: CharacterAgencyLead,
) -> None:
    assert decision.status is CharacterAgencyStatus.APPLIED
    assert decision.schema_version == CHARACTER_AGENCY_DECISION_SCHEMA_VERSION
    assert (
        decision.drive,
        decision.act,
        decision.subject,
        decision.initiative,
        decision.lead,
    ) == (drive, act, subject, initiative, lead)


def _assert_property(observation: AgencyObservation, property_code: str) -> None:
    decision = observation.decision
    evidence = observation.evidence
    dialogue = observation.dialogue

    if property_code == "safety_boundary":
        assert evidence.harmful_overextension
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.PROTECT,
            act=CharacterAgencyAct.SET_BOUNDARY,
            subject=CharacterAgencySubject.SAFETY,
            initiative=CharacterAgencyInitiative.STOP,
            lead=CharacterAgencyLead.OBLIGATION_FIRST,
        )
        assert decision.reason_codes == (CharacterAgencyReason.SAFETY_PRECEDENCE,)
        return
    if property_code == "quiet_presence":
        assert evidence.high_distress or evidence.explicit_listen_request
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.CARE,
            act=CharacterAgencyAct.STAY_PRESENT,
            subject=CharacterAgencySubject.USER_EXPLICIT_STATE,
            initiative=CharacterAgencyInitiative.STOP,
            lead=CharacterAgencyLead.FUSED,
        )
        return
    if property_code == "ordinary_care":
        assert evidence.explicit_depletion or evidence.completion_depletion_contrast
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.CARE,
            act=CharacterAgencyAct.CARE,
            subject=CharacterAgencySubject.USER_EXPLICIT_STATE,
            initiative=CharacterAgencyInitiative.STAY_ON_TOPIC,
            lead=CharacterAgencyLead.FUSED,
        )
        return
    if property_code == "correction_repair":
        assert dialogue.current_no_routine_questions_correction
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.REPAIR,
            act=CharacterAgencyAct.REPAIR,
            subject=CharacterAgencySubject.CURRENT_EXCHANGE,
            initiative=CharacterAgencyInitiative.STOP,
            lead=CharacterAgencyLead.OBLIGATION_FIRST,
        )
        return
    if property_code == "repair_response":
        assert evidence.explicit_repair_offer
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.REPAIR,
            act=CharacterAgencyAct.RESPOND,
            subject=CharacterAgencySubject.RELATIONSHIP,
            initiative=CharacterAgencyInitiative.STOP,
            lead=CharacterAgencyLead.FUSED,
        )
        return
    if property_code == "guarded_boundary":
        assert evidence.direct_personal_devaluation
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.RESERVE,
            act=CharacterAgencyAct.SET_BOUNDARY,
            subject=CharacterAgencySubject.RELATIONSHIP,
            initiative=CharacterAgencyInitiative.STOP,
            lead=CharacterAgencyLead.OWNED_MOVE_FIRST,
        )
        return
    if property_code == "guarded_help":
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.RESERVE,
            act=CharacterAgencyAct.HELP,
            subject=CharacterAgencySubject.USER_REQUEST,
            initiative=CharacterAgencyInitiative.STOP,
            lead=CharacterAgencyLead.OBLIGATION_FIRST,
        )
        assert CharacterAgencyReason.GUARDED_CONTEXT in decision.reason_codes
        assert CharacterAgencyReason.DIRECT_REQUEST in decision.reason_codes
        return
    if property_code in {"no_invented_position", "no_invented_inclination"}:
        assert decision.subject not in {
            CharacterAgencySubject.CANONICAL_POSITION,
            CharacterAgencySubject.CANONICAL_INCLINATION,
        }
        assert decision.subject_ref is None
        return
    if property_code == "canonical_position_contribution":
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.EXPRESS_VIEW,
            act=CharacterAgencyAct.SHARE,
            subject=CharacterAgencySubject.CANONICAL_POSITION,
            initiative=CharacterAgencyInitiative.STAY_ON_TOPIC,
            lead=CharacterAgencyLead.OWNED_MOVE_FIRST,
        )
        assert decision.subject_ref == "position-runtime-type-checks"
        assert CharacterAgencyReason.CANONICAL_POSITION in decision.reason_codes
        return
    if property_code == "requested_challenge":
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.CHALLENGE,
            act=CharacterAgencyAct.CHALLENGE,
            subject=CharacterAgencySubject.CURRENT_EXCHANGE,
            initiative=CharacterAgencyInitiative.STAY_ON_TOPIC,
            lead=CharacterAgencyLead.OWNED_MOVE_FIRST,
        )
        return
    if property_code == "plain_direct_answer":
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.NONE,
            act=CharacterAgencyAct.RESPOND,
            subject=CharacterAgencySubject.USER_REQUEST,
            initiative=CharacterAgencyInitiative.NONE,
            lead=CharacterAgencyLead.OBLIGATION_FIRST,
        )
        return
    if property_code == "analysis_help":
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.HELP,
            act=CharacterAgencyAct.HELP,
            subject=CharacterAgencySubject.USER_REQUEST,
            initiative=CharacterAgencyInitiative.STAY_ON_TOPIC,
            lead=CharacterAgencyLead.OBLIGATION_FIRST,
        )
        assert CharacterAgencyReason.ANALYSIS_NEED in decision.reason_codes
        return
    if property_code == "creative_exploration":
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.EXPLORE,
            act=CharacterAgencyAct.PROPOSE,
            subject=CharacterAgencySubject.USER_REQUEST,
            initiative=CharacterAgencyInitiative.ADVANCE_CURRENT,
            lead=CharacterAgencyLead.FUSED,
        )
        assert CharacterAgencyReason.CREATIVE_NEED in decision.reason_codes
        return
    if property_code == "bounded_motivation":
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.HELP,
            act=CharacterAgencyAct.PROPOSE,
            subject=CharacterAgencySubject.USER_REQUEST,
            initiative=CharacterAgencyInitiative.ADVANCE_CURRENT,
            lead=CharacterAgencyLead.FUSED,
        )
        assert CharacterAgencyReason.EXPLICIT_MOTIVATION in decision.reason_codes
        return
    if property_code == "abandonment_challenge":
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.CHALLENGE,
            act=CharacterAgencyAct.CHALLENGE,
            subject=CharacterAgencySubject.CURRENT_EXCHANGE,
            initiative=CharacterAgencyInitiative.STAY_ON_TOPIC,
            lead=CharacterAgencyLead.OWNED_MOVE_FIRST,
        )
        assert CharacterAgencyReason.TASK_ABANDONMENT in decision.reason_codes
        return
    if property_code == "achievement_connection":
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.CONNECT,
            act=CharacterAgencyAct.ACKNOWLEDGE,
            subject=CharacterAgencySubject.CURRENT_EXCHANGE,
            initiative=CharacterAgencyInitiative.STAY_ON_TOPIC,
            lead=CharacterAgencyLead.OWNED_MOVE_FIRST,
        )
        return
    if property_code == "achievement_play":
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.PLAY,
            act=CharacterAgencyAct.ACKNOWLEDGE,
            subject=CharacterAgencySubject.CURRENT_EXCHANGE,
            initiative=CharacterAgencyInitiative.ADVANCE_CURRENT,
            lead=CharacterAgencyLead.OWNED_MOVE_FIRST,
        )
        return
    if property_code == "repeat_acknowledgement":
        assert dialogue.current_user_message_repeated
        assert decision.act is CharacterAgencyAct.ACKNOWLEDGE
        assert decision.initiative is CharacterAgencyInitiative.STOP
        assert decision.reason_codes[0] is CharacterAgencyReason.REPETITION_PRECEDENCE
        return
    if property_code == "social_greeting":
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.CONNECT,
            act=CharacterAgencyAct.RESPOND,
            subject=CharacterAgencySubject.CURRENT_EXCHANGE,
            initiative=CharacterAgencyInitiative.STAY_ON_TOPIC,
            lead=CharacterAgencyLead.OWNED_MOVE_FIRST,
        )
        assert decision.reason_codes == (CharacterAgencyReason.SOCIAL_EXCHANGE,)
        return
    if property_code == "reciprocal_warmth":
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.PLAY,
            act=CharacterAgencyAct.RESPOND,
            subject=CharacterAgencySubject.CURRENT_EXCHANGE,
            initiative=CharacterAgencyInitiative.STAY_ON_TOPIC,
            lead=CharacterAgencyLead.FUSED,
        )
        assert decision.reason_codes == (
            CharacterAgencyReason.SOCIAL_EXCHANGE,
            CharacterAgencyReason.PLAYFUL_AFFECT,
            CharacterAgencyReason.ESTABLISHED_RELATIONSHIP,
        )
        return
    if property_code == "current_attention":
        assert evidence.current_attention_request
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.CONNECT,
            act=CharacterAgencyAct.RESPOND,
            subject=CharacterAgencySubject.CURRENT_EXCHANGE,
            initiative=CharacterAgencyInitiative.STAY_ON_TOPIC,
            lead=CharacterAgencyLead.OWNED_MOVE_FIRST,
        )
        assert decision.reason_codes == (CharacterAgencyReason.CURRENT_ATTENTION_REQUEST,)
        assert decision.subject_ref is None
        return
    if property_code == "memory_request":
        assert PerceivedTopic.MEMORY in observation.intake.perception.topics
        assert observation.intake.retrieval_plan.owner is CognitionOwner.MEMORY_QUERY
        assert observation.intake.retrieval_plan.include_episodic is True
        assert observation.intake.retrieval_plan.include_semantic is True
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.NONE,
            act=CharacterAgencyAct.RESPOND,
            subject=CharacterAgencySubject.CURRENT_EXCHANGE,
            initiative=CharacterAgencyInitiative.NONE,
            lead=CharacterAgencyLead.FUSED,
        )
        assert decision.subject_ref is None
        return
    if property_code == "self_disclosure_without_invention":
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.SHARE_SELF,
            act=CharacterAgencyAct.SHARE,
            subject=CharacterAgencySubject.SATORI_SELF,
            initiative=CharacterAgencyInitiative.STAY_ON_TOPIC,
            lead=CharacterAgencyLead.OWNED_MOVE_FIRST,
        )
        assert decision.subject_ref is None
        return
    if property_code == "self_disclosure_with_inclination":
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.SHARE_SELF,
            act=CharacterAgencyAct.SHARE,
            subject=CharacterAgencySubject.CANONICAL_INCLINATION,
            initiative=CharacterAgencyInitiative.STAY_ON_TOPIC,
            lead=CharacterAgencyLead.OWNED_MOVE_FIRST,
        )
        assert decision.subject_ref == "inclination-architecture"
        return
    if property_code == "canonical_inclination_contribution":
        assert decision.drive is CharacterAgencyDrive.EXPLORE
        assert decision.act is CharacterAgencyAct.SHARE
        assert decision.subject is CharacterAgencySubject.CANONICAL_INCLINATION
        assert decision.initiative is CharacterAgencyInitiative.ADVANCE_CURRENT
        assert decision.subject_ref == "inclination-architecture"
        return
    if property_code == "no_forced_play":
        assert decision.drive is not CharacterAgencyDrive.PLAY
        return
    if property_code == "established_play":
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.PLAY,
            act=CharacterAgencyAct.RESPOND,
            subject=CharacterAgencySubject.CURRENT_EXCHANGE,
            initiative=CharacterAgencyInitiative.STAY_ON_TOPIC,
            lead=CharacterAgencyLead.FUSED,
        )
        return
    if property_code == "topic_closure":
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.CLOSE,
            act=CharacterAgencyAct.CLOSE,
            subject=CharacterAgencySubject.CURRENT_EXCHANGE,
            initiative=CharacterAgencyInitiative.STOP,
            lead=CharacterAgencyLead.OWNED_MOVE_FIRST,
        )
        return
    if property_code == "bounded_adjacent_shift":
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.EXPLORE,
            act=CharacterAgencyAct.PROPOSE,
            subject=CharacterAgencySubject.CANONICAL_INCLINATION,
            initiative=CharacterAgencyInitiative.SHIFT_ADJACENT,
            lead=CharacterAgencyLead.OWNED_MOVE_FIRST,
        )
        assert decision.subject_ref == "inclination-architecture"
        return
    if property_code == "activity_exploration":
        assert dialogue.current_activity_mention
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.EXPLORE,
            act=CharacterAgencyAct.QUESTION,
            subject=CharacterAgencySubject.CURRENT_EXCHANGE,
            initiative=CharacterAgencyInitiative.ADVANCE_CURRENT,
            lead=CharacterAgencyLead.OWNED_MOVE_FIRST,
        )
        return
    if property_code == "interested_exploration":
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.EXPLORE,
            act=CharacterAgencyAct.RESPOND,
            subject=CharacterAgencySubject.CURRENT_EXCHANGE,
            initiative=CharacterAgencyInitiative.ADVANCE_CURRENT,
            lead=CharacterAgencyLead.OWNED_MOVE_FIRST,
        )
        assert CharacterAgencyReason.INTERESTED_AFFECT in decision.reason_codes
        return
    if property_code == "default_owned_response":
        _assert_shape(
            decision,
            drive=CharacterAgencyDrive.NONE,
            act=CharacterAgencyAct.RESPOND,
            subject=CharacterAgencySubject.CURRENT_EXCHANGE,
            initiative=CharacterAgencyInitiative.NONE,
            lead=CharacterAgencyLead.FUSED,
        )
        assert decision.reason_codes == (CharacterAgencyReason.DEFAULT_OWNED_RESPONSE,)
        return
    raise AssertionError(f"unimplemented agency property: {property_code}")


def _scenario_map() -> dict[str, AgencyScenario]:
    return {scenario.scenario_id: scenario for scenario in SCENARIOS}


def test_checkpoint143_corpus_is_public_semantic_broad_and_reply_free() -> None:
    serialized = repr(
        (
            tuple(asdict(scenario) for scenario in SCENARIOS),
            tuple(asdict(contrast) for contrast in CONTROLLED_CONTRASTS),
            tuple(asdict(flow) for flow in LIVE_FLOWS),
        )
    ).lower()
    forbidden_authority = (
        "desired_reply",
        "golden_reply",
        "assistant_text",
        "assistant_reply",
        "provider_output",
        "expected_phrase",
        "characteragencydecision",
    )
    groups = {scenario.group for scenario in SCENARIOS}
    scenario_ids = {scenario.scenario_id for scenario in SCENARIOS}

    assert len(SCENARIOS) == 36
    assert len(scenario_ids) == 36
    assert groups == {
        "achievement_and_depletion",
        "help_and_creation",
        "initiative_and_closure",
        "intellectual_agency",
        "ordinary_range",
        "relationship_modulation",
        "repair_and_strain",
        "safety_and_presence",
        "self_and_owned_state",
    }
    assert {
        "social_greeting",
        "reciprocal_warmth",
        "current_attention",
        "memory_request",
    }.issubset(scenario_ids)
    assert all(sum(scenario.group == group for scenario in SCENARIOS) == 4 for group in groups)
    assert len(CONTROLLED_CONTRASTS) == 13
    assert len({contrast.contrast_id for contrast in CONTROLLED_CONTRASTS}) == len(
        CONTROLLED_CONTRASTS
    )
    assert len(LIVE_FLOWS) == 3
    assert all(len(flow.turns) >= 3 for flow in LIVE_FLOWS)
    assert all(item not in serialized for item in forbidden_authority)
    assert all(scenario.user_text.strip() for scenario in SCENARIOS)
    assert all(contrast.left_scenario_id in scenario_ids for contrast in CONTROLLED_CONTRASTS)
    assert all(contrast.right_scenario_id in scenario_ids for contrast in CONTROLLED_CONTRASTS)


def test_checkpoint143_public_scenarios_select_typed_semantic_agency() -> None:
    for scenario in SCENARIOS:
        observation = _observe(scenario)
        for property_code in scenario.properties:
            _assert_property(observation, property_code)


def test_checkpoint143_controlled_contrasts_prove_causal_and_irrelevant_state() -> None:
    scenarios = _scenario_map()
    observations = {scenario.scenario_id: _observe(scenario).decision for scenario in SCENARIOS}
    for contrast in CONTROLLED_CONTRASTS:
        left_scenario = scenarios[contrast.left_scenario_id]
        right_scenario = scenarios[contrast.right_scenario_id]
        assert left_scenario.user_text == right_scenario.user_text
        state_deltas = tuple(
            field_name
            for field_name, left_value in asdict(left_scenario.state).items()
            if left_value != asdict(right_scenario.state)[field_name]
        )
        assert len(state_deltas) == 1
        left = observations[contrast.left_scenario_id]
        right = observations[contrast.right_scenario_id]
        code = contrast.property_code
        if code == "care_vs_safety":
            assert (left.drive, right.drive) == (
                CharacterAgencyDrive.CARE,
                CharacterAgencyDrive.PROTECT,
            )
        elif code == "care_vs_presence":
            assert (left.act, right.act) == (
                CharacterAgencyAct.CARE,
                CharacterAgencyAct.STAY_PRESENT,
            )
        elif code == "absent_vs_position":
            assert left.subject is not CharacterAgencySubject.CANONICAL_POSITION
            assert right.subject is CharacterAgencySubject.CANONICAL_POSITION
            assert right.subject_ref == "position-runtime-type-checks"
        elif code == "connect_vs_play":
            assert (left.drive, right.drive) == (
                CharacterAgencyDrive.CONNECT,
                CharacterAgencyDrive.PLAY,
            )
        elif code == "self_vs_inclination":
            assert (left.subject, right.subject) == (
                CharacterAgencySubject.SATORI_SELF,
                CharacterAgencySubject.CANONICAL_INCLINATION,
            )
        elif code == "absent_vs_inclination":
            assert left.subject is not CharacterAgencySubject.CANONICAL_INCLINATION
            assert right.subject is CharacterAgencySubject.CANONICAL_INCLINATION
        elif code == "help_vs_guarded_help":
            assert (left.drive, right.drive) == (
                CharacterAgencyDrive.HELP,
                CharacterAgencyDrive.RESERVE,
            )
            assert (left.act, right.act) == (
                CharacterAgencyAct.HELP,
                CharacterAgencyAct.HELP,
            )
        elif code == "close_vs_adjacent_shift":
            assert (left.initiative, right.initiative) == (
                CharacterAgencyInitiative.STOP,
                CharacterAgencyInitiative.SHIFT_ADJACENT,
            )
            assert right.subject is CharacterAgencySubject.CANONICAL_INCLINATION
        elif code == "none_vs_play":
            assert left.drive is CharacterAgencyDrive.NONE
            assert right.drive is CharacterAgencyDrive.PLAY
        elif code == "none_vs_explore":
            assert left.drive is CharacterAgencyDrive.NONE
            assert right.drive is CharacterAgencyDrive.EXPLORE
        elif code == "same_semantic_move":
            assert asdict(left) == asdict(right)
        elif code == "advance_vs_repeat":
            assert (left.initiative, right.initiative) == (
                CharacterAgencyInitiative.ADVANCE_CURRENT,
                CharacterAgencyInitiative.STOP,
            )
            assert CharacterAgencyReason.REPETITION_PRECEDENCE in right.reason_codes
        else:
            raise AssertionError(f"unimplemented contrast property: {code}")


def test_checkpoint143_three_public_multi_turn_flows_keep_semantic_precedence() -> None:
    for flow in LIVE_FLOWS:
        previous_user_texts: list[str] = []
        for turn_number, turn in enumerate(flow.turns, start=1):
            scenario = AgencyScenario(
                scenario_id=f"{flow.flow_id}-turn-{turn_number}",
                group="live_flow",
                user_text=turn.user_text,
                state=turn.state,
                properties=turn.properties,
            )
            observation = _observe(
                scenario,
                recent_override=tuple(previous_user_texts),
            )
            for property_code in turn.properties:
                _assert_property(observation, property_code)
            previous_user_texts.append(turn.user_text)


def test_checkpoint143_decisions_contain_only_bounded_typed_provenance() -> None:
    for scenario in SCENARIOS:
        observation = _observe(scenario)
        decision = observation.decision
        serialized = repr(asdict(decision))

        assert scenario.user_text not in serialized
        assert "архитектура долгоживущих систем" not in serialized.lower()
        assert 1 <= len(decision.source_refs) <= 4
        assert 1 <= len(decision.source_personality_codes) <= 2
        assert 1 <= len(decision.reason_codes) <= 4
        if decision.subject is CharacterAgencySubject.CANONICAL_POSITION:
            assert decision.subject_ref == "position-runtime-type-checks"
        elif decision.subject is CharacterAgencySubject.CANONICAL_INCLINATION:
            assert decision.subject_ref == "inclination-architecture"
        else:
            assert decision.subject_ref is None


def test_checkpoint143_corpus_exports_only_public_contract_data() -> None:
    for scenario in SCENARIOS:
        state = scenario.state
        assert state.position in {None, "available"}
        assert state.inclination in {None, "available"}
        assert state.relationship in {"fresh", "developing", "established", "strained"}
        assert state.affect in {"calm", "positive", "soft_negative", "tense", "interested"}
        assert scenario.properties
