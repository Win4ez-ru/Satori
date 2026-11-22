"""Checkpoint 14.3 manifest observability and provenance boundary tests."""

from dataclasses import replace
from typing import Any

import pytest

from satori.application.cognition.contracts import PositionStance, ResponseVerbosity
from satori.application.conversation.character_agency import (
    CharacterAgencyAct,
    CharacterAgencyDecision,
    CharacterAgencyDrive,
    CharacterAgencyInitiative,
    CharacterAgencyLead,
    CharacterAgencyReason,
    CharacterAgencyStatus,
    CharacterAgencySubject,
)
from satori.application.conversation.character_delivery_contracts import (
    CharacterDeliveryDecision,
    CharacterDeliveryGoal,
    CharacterDeliveryVoice,
    CharacterPersonalitySignal,
    CharacterPresenceProjection,
    CharacterPresenceStrength,
    CharacterValueSignal,
)
from satori.application.conversation.character_expression import (
    CharacterContinuationMode,
    CharacterGroundingMode,
    CharacterPressureLevel,
)
from satori.application.conversation.contracts import ConversationContextManifest


def _fresh_manifest() -> ConversationContextManifest:
    return ConversationContextManifest(
        schema_version=17,
        policy_id="satori.conversation.behavior.v28",
        policy_schema_version=28,
        character_context_schema_version=16,
        included_sections=(
            "behavior_policy",
            "self_model",
            "personality_expression",
            "values",
            "character_agency_decision",
            "character_delivery_decision",
            "character_presence_projection",
            "current_user_input",
        ),
        user_content_chars=18,
        personality_aggregate_version=1,
        personality_expression_schema_version=2,
        cognition_pipeline_schema_version=1,
        cognition_pipeline_status="applied",
        cognition_perception_topics=("project",),
        cognition_perception_signals=("request",),
        cognition_need_dimensions=("information",),
        cognition_position_stance="answer",
        cognition_preserve_uncertainty=False,
        cognition_intent_registry_version=2,
        cognition_primary_intent="answer_directly",
        cognition_intent_tags=("answer_directly", "preserve_evidence_boundary"),
        cognition_required_point_codes=("answer_directly", "address_current_request"),
        cognition_forbidden_claim_codes=(
            "unsupported_memory",
            "hidden_user_state",
            "durable_satori_belief",
            "false_certainty",
        ),
        cognition_strategy_tone="analytical",
        cognition_response_verbosity="brief",
        cognition_template_registry_version=3,
        cognition_template_id="satori.cognition.response-substance",
        cognition_template_schema_version=3,
        character_agency_decision_schema_version=1,
        character_agency_status="applied",
        character_agency_drive="none",
        character_agency_act="respond",
        character_agency_subject="current_exchange",
        character_agency_initiative="none",
        character_agency_lead="fused",
        character_agency_source_personality_codes=(
            "considered_directness",
            "independent_position",
        ),
        character_agency_source_value_key="truth",
        character_agency_reason_codes=("default_owned_response",),
        character_agency_source_refs=("message-current",),
        character_delivery_decision_schema_version=5,
        character_delivery_goal="answer_precisely",
        character_delivery_voice="thoughtful_precision",
        character_delivery_grounding="trusted_context",
        character_delivery_continuation="complete",
        character_delivery_pressure="none",
        character_delivery_position_stance="answer",
        character_delivery_preserve_uncertainty=False,
        character_presence_projection_schema_version=3,
        character_presence_personality_signals=(
            "considered_directness:defining",
            "independent_position:strong",
        ),
        character_presence_value_signals=("truth:defining",),
        character_presence_memory_use_licensed=False,
    )


def _replay_manifest() -> ConversationContextManifest:
    return ConversationContextManifest(
        schema_version=17,
        policy_id="satori.conversation.behavior.v28",
        policy_schema_version=28,
        character_context_schema_version=16,
        included_sections=(
            "behavior_policy",
            "self_model",
            "personality_expression",
            "values",
            "current_user_input",
        ),
        user_content_chars=18,
        personality_aggregate_version=1,
        personality_expression_schema_version=2,
    )


def _presence_projection() -> CharacterPresenceProjection:
    agency = CharacterAgencyDecision(
        schema_version=1,
        status=CharacterAgencyStatus.APPLIED,
        drive=CharacterAgencyDrive.NONE,
        act=CharacterAgencyAct.RESPOND,
        subject=CharacterAgencySubject.CURRENT_EXCHANGE,
        initiative=CharacterAgencyInitiative.NONE,
        lead=CharacterAgencyLead.FUSED,
        source_personality_codes=("considered_directness", "independent_position"),
        source_value_key="truth",
        reason_codes=(CharacterAgencyReason.DEFAULT_OWNED_RESPONSE,),
        source_refs=("message-current",),
    )
    decision = CharacterDeliveryDecision(
        schema_version=5,
        goal=CharacterDeliveryGoal.ANSWER_PRECISELY,
        voice=CharacterDeliveryVoice.THOUGHTFUL_PRECISION,
        grounding=CharacterGroundingMode.TRUSTED_CONTEXT,
        continuation=CharacterContinuationMode.COMPLETE,
        pressure=CharacterPressureLevel.NONE,
        position_stance=PositionStance.ANSWER,
        preserve_uncertainty=False,
        cognition_intent_registry_version=2,
        cognition_primary_intent="answer_directly",
        cognition_intent_tags=("answer_directly", "preserve_evidence_boundary"),
        required_point_codes=("answer_directly", "address_current_request"),
        forbidden_claim_codes=(
            "unsupported_memory",
            "hidden_user_state",
            "durable_satori_belief",
            "false_certainty",
        ),
        response_verbosity=ResponseVerbosity.BRIEF,
        agency=agency,
    )
    return CharacterPresenceProjection(
        schema_version=3,
        personality_aggregate_version=1,
        decision=decision,
        personality_signals=(
            CharacterPersonalitySignal(
                code="considered_directness",
                strength=0.9,
                level=CharacterPresenceStrength.DEFINING,
            ),
            CharacterPersonalitySignal(
                code="independent_position",
                strength=0.75,
                level=CharacterPresenceStrength.STRONG,
            ),
        ),
        value_signals=(
            CharacterValueSignal(
                key="truth",
                strength=0.9,
                level=CharacterPresenceStrength.DEFINING,
            ),
        ),
        affect_signals=(),
        relationship_signals=(),
        affect_profile=None,
        affect_relevant=False,
        relationship_profile=None,
        relationship_relevant=False,
        memory_use_licensed=False,
        canonical_position_available=False,
        topic_inclination_available=False,
    )


def test_v28_fresh_manifest_reconstructs_complete_agency_delivery_and_presence() -> None:
    manifest = _fresh_manifest()

    assert manifest.character_agency_decision_schema_version == 1
    assert manifest.character_delivery_decision_schema_version == 5
    assert manifest.character_presence_projection_schema_version == 3
    assert "character_agency_decision" in manifest.included_sections


def test_v28_replay_may_omit_all_transient_agency_delivery_and_presence() -> None:
    manifest = _replay_manifest()

    assert manifest.cognition_pipeline_status == "not_requested"
    assert manifest.character_agency_decision_schema_version is None
    assert "character_agency_decision" not in manifest.included_sections


def test_v28_requires_manifest_v17_and_historical_policy_rejects_it() -> None:
    with pytest.raises(ValueError, match="policy v28 requires context manifest schema v17"):
        replace(_fresh_manifest(), schema_version=16)

    with pytest.raises(ValueError, match="historical behavior policy"):
        replace(
            _replay_manifest(),
            policy_id="satori.conversation.behavior.v27",
            policy_schema_version=27,
        )


def test_v28_fresh_turn_requires_complete_agency_and_exact_section_parity() -> None:
    manifest = _fresh_manifest()

    with pytest.raises(ValueError, match="complete character agency"):
        replace(manifest, character_agency_drive=None)
    with pytest.raises(ValueError, match="metadata and included section must agree"):
        replace(
            manifest,
            included_sections=tuple(
                section
                for section in manifest.included_sections
                if section != "character_agency_decision"
            ),
        )


def test_historical_policy_rejects_character_agency_metadata_and_section() -> None:
    with pytest.raises(ValueError, match="cannot contain character agency metadata"):
        replace(
            _fresh_manifest(),
            schema_version=16,
            policy_id="satori.conversation.behavior.v27",
            policy_schema_version=27,
        )


@pytest.mark.parametrize(
    ("subject", "section", "status_field", "schema_field", "ids_field", "error"),
    [
        (
            "canonical_position",
            "satori_epistemic_positions",
            "position_context_status",
            "position_context_schema_version",
            "position_context_ids",
            "canonical position agency ref",
        ),
        (
            "canonical_inclination",
            "satori_inclinations",
            "inclination_context_status",
            "inclination_context_schema_version",
            "inclination_context_ids",
            "canonical inclination agency ref",
        ),
    ],
)
def test_canonical_agency_subject_ref_must_match_its_exact_context_owner(
    subject: str,
    section: str,
    status_field: str,
    schema_field: str,
    ids_field: str,
    error: str,
) -> None:
    manifest = _fresh_manifest()
    canonical_ref = f"{subject}-canonical"
    context_fields: dict[str, Any] = {
        "included_sections": (*manifest.included_sections, section),
        "available_past_evidence_ids": (canonical_ref,),
        status_field: "available",
        schema_field: 1,
        ids_field: (canonical_ref,),
        "character_agency_drive": (
            "express_view" if subject == "canonical_position" else "explore"
        ),
        "character_agency_act": "share",
        "character_agency_subject": subject,
        "character_agency_initiative": "stay_on_topic"
        if subject == "canonical_position"
        else "advance_current",
        "character_agency_lead": "owned_move_first",
        "character_agency_source_value_key": "autonomy",
        "character_agency_reason_codes": (
            "canonical_position" if subject == "canonical_position" else "canonical_inclination",
        ),
        "character_agency_source_refs": ("message-current", canonical_ref),
        "character_agency_subject_ref": canonical_ref,
        "character_presence_value_signals": ("autonomy:defining",),
    }
    canonical = replace(manifest, **context_fields)
    assert canonical.character_agency_subject_ref == canonical_ref

    with pytest.raises(ValueError, match=error):
        replace(
            canonical,
            character_agency_source_refs=("message-current", "wrong-ref"),
            character_agency_subject_ref="wrong-ref",
        )


def test_v28_agency_sources_must_be_realized_by_character_presence() -> None:
    manifest = _fresh_manifest()

    with pytest.raises(ValueError, match="personality sources must be present"):
        replace(
            manifest,
            character_agency_source_personality_codes=("curious_analytical",),
        )
    with pytest.raises(ValueError, match="value source must match"):
        replace(manifest, character_agency_source_value_key="autonomy")


def test_v28_requires_delivery_v5_and_presence_v3() -> None:
    manifest = _fresh_manifest()

    with pytest.raises(ValueError, match="complete delivery decision"):
        replace(manifest, character_delivery_decision_schema_version=4)
    with pytest.raises(ValueError, match="exact character presence schema"):
        replace(manifest, character_presence_projection_schema_version=2)


def test_v28_manifest_requires_completed_cognition_and_agency_status_parity() -> None:
    manifest = _fresh_manifest()

    with pytest.raises(ValueError, match="completed cognition status must agree"):
        replace(
            manifest,
            cognition_pipeline_status="fallback",
            cognition_fallback_reasons=("completion_invalid_or_failed",),
        )


def test_v28_manifest_rejects_social_agency_without_the_social_plan_owner() -> None:
    manifest = _fresh_manifest()

    with pytest.raises(ValueError, match="authoritative social disclosure plan"):
        replace(
            manifest,
            character_agency_drive="connect",
            character_agency_act="respond",
            character_agency_subject="current_exchange",
            character_agency_initiative="stay_on_topic",
            character_agency_lead="owned_move_first",
            character_agency_source_personality_codes=("warm_perceptive",),
            character_agency_source_value_key="connection",
            character_agency_reason_codes=("social_exchange",),
            character_presence_personality_signals=("warm_perceptive:defining",),
            character_presence_value_signals=("connection:defining",),
        )


def test_presence_v3_requires_exact_agency_personality_and_value_realization() -> None:
    projection = _presence_projection()

    with pytest.raises(ValueError, match="personality sources must be realized"):
        replace(projection, personality_signals=projection.personality_signals[:1])
    with pytest.raises(ValueError, match="value source must match"):
        replace(
            projection,
            value_signals=(
                CharacterValueSignal(
                    key="autonomy",
                    strength=0.9,
                    level=CharacterPresenceStrength.DEFINING,
                ),
            ),
        )
