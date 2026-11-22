"""Offline plan, isolation and fail-closed tests for the Checkpoint 14.3 paid A/B."""

from __future__ import annotations

import copy
from typing import cast

import pytest

from satori.core.conversation import (
    ConversationGenerationParameters,
    ConversationMessage,
    ConversationMessageRole,
    ConversationProviderRequest,
)
from tests.checkpoint142_openai_manual_support import content_digest, manual_affect_contract
from tests.checkpoint142_openai_v26_ledger import ProviderCallBudgetExhausted, PublicTurnScope
from tests.checkpoint143_openai_ab_manual_eval import (
    AUTHORIZATION_ID,
    CELLS,
    CONTROL,
    CROSS_SESSION_DIMENSIONS,
    EXPECTED_REPLICA_COUNT,
    EXPECTED_TURN_VISIBLE_OUTPUT_TOKEN_LIMITS,
    MAXIMUM_COST_USD,
    MAXIMUM_PROVIDER_CALLS,
    PAIR_REVIEW_DIMENSIONS,
    PUBLIC_TURNS,
    TREATMENT,
    TREATMENT_REALIZATION_DIMENSIONS,
    CellLedger,
    Checkpoint143ABConfigurationError,
    ExecutionPlan,
    _preflight_shape,
    _review_template,
    _safe_manifest,
    build_phase_2_review_template,
    inspect_plan,
)


def _request(*, schema: int, limit: int = 64) -> ConversationProviderRequest:
    return ConversationProviderRequest(
        schema_version=1,
        trace_id="trace-checkpoint143-ab",
        context_schema_version=schema,
        messages=(ConversationMessage(ConversationMessageRole.USER, "public fixture"),),
        parameters=ConversationGenerationParameters(
            schema_version=1,
            temperature=0.3,
            max_output_tokens=limit,
        ),
    )


def _manifest(cell: object) -> dict[str, object]:
    active = TREATMENT if cell is TREATMENT else CONTROL
    affect = manual_affect_contract()["accepted_outcomes"][0]
    included = [
        section
        for section in (
            "behavior_policy",
            "self_model",
            "personality_expression",
            "values",
            "relationship_expression_state",
            "emotional_expression_state",
            "character_agency_decision",
            "character_delivery_decision",
            "character_presence_projection",
            "current_user_input",
        )
        if active is TREATMENT or section != "character_agency_decision"
    ]
    raw: dict[str, object] = {
        "schema_version": active.manifest_schema_version,
        "policy_id": active.policy_id,
        "policy_schema_version": active.policy_schema_version,
        "character_context_schema_version": 16,
        "included_sections": included,
        "response_regenerated": False,
        "regeneration_reason": None,
        "retrieval_status": "not_requested",
        "retrieved_memory_count": 0,
        "emotion_appraisal_status": affect["status"],
        "emotion_appraisal_reason_code": affect["reason_code"],
        "emotion_appraisal_transition_prepared": affect["transition_prepared"],
        "emotion_appraisal_provider": "ollama",
        "emotion_appraisal_model": "qwen3:4b-instruct",
        "emotion_appraisal_method": "ollama.categorical_affective_appraisal.v2",
        "emotion_appraisal_provider_metrics_present": True,
        "character_delivery_decision_schema_version": active.delivery_schema_version,
        "character_presence_projection_schema_version": active.presence_schema_version,
    }
    if active is TREATMENT:
        raw.update(
            {
                "character_agency_decision_schema_version": 1,
                "character_agency_status": "applied",
                "character_agency_drive": "connect",
                "character_agency_act": "respond",
                "character_agency_subject": "current_exchange",
                "character_agency_initiative": "stay_on_topic",
                "character_agency_lead": "owned_move_first",
                "character_agency_source_personality_codes": ["warm_perceptive"],
                "character_agency_source_value_key": "connection",
                "character_agency_reason_codes": ["social_exchange"],
                "character_agency_source_refs": ["message-current"],
                "character_agency_subject_ref": None,
            }
        )
    return raw


def _fake_report() -> dict[str, object]:
    cells: dict[str, object] = {}
    for cell in CELLS:
        sessions = []
        for replica in range(1, EXPECTED_REPLICA_COUNT + 1):
            sessions.append(
                {
                    "session_id": f"{cell.session_prefix}-{replica}",
                    "turns": [
                        {
                            "turn": fixture["turn"],
                            "turn_id": fixture["id"],
                            "reply": f"{cell.cell_id}-{replica}-{fixture['turn']}",
                            "context_manifest": _manifest(cell),
                        }
                        for fixture in PUBLIC_TURNS
                    ],
                }
            )
        cells[cell.cell_id] = {"sessions": sessions}
    return {
        "artifact_id": "checkpoint143-ab-fixture",
        "sample_digest": "sha256:" + "1" * 64,
        "execution_plan_digest": "sha256:" + "2" * 64,
        "cells": cells,
    }


def test_inspect_plan_freezes_two_comparable_cells_without_network() -> None:
    plan = inspect_plan()

    assert plan["checkpoint"] == "14.3"
    assert plan["mode"] == "inspect_only"
    assert plan["network_attempted"] is False
    assert plan["required_base_calls"] == 36
    assert plan["maximum_provider_calls"] == 48
    assert plan["maximum_cost_usd"] == 0.30
    assert plan["fresh_replica_count_per_cell"] == 3
    assert plan["turns_per_replica"] == 6
    assert [cell["policy_schema_version"] for cell in plan["cells"]] == [27, 28]
    assert plan["source_fingerprint"]["installed_wheel_parity"] is True
    assert plan["execution_plan_digest"] == ExecutionPlan().digest


def test_exact_preflight_rejects_authority_call_cost_and_digest_drift() -> None:
    digest = ExecutionPlan().digest
    _preflight_shape(
        execute=True,
        authorization_id=AUTHORIZATION_ID,
        maximum_provider_calls=MAXIMUM_PROVIDER_CALLS,
        maximum_cost_usd=MAXIMUM_COST_USD,
        authorized_plan_digest=digest,
    )

    with pytest.raises(Checkpoint143ABConfigurationError):
        _preflight_shape(
            execute=True,
            authorization_id="wrong",
            maximum_provider_calls=MAXIMUM_PROVIDER_CALLS,
            maximum_cost_usd=MAXIMUM_COST_USD,
            authorized_plan_digest=digest,
        )
    with pytest.raises(Checkpoint143ABConfigurationError):
        _preflight_shape(
            execute=True,
            authorization_id=AUTHORIZATION_ID,
            maximum_provider_calls=MAXIMUM_PROVIDER_CALLS - 1,
            maximum_cost_usd=MAXIMUM_COST_USD,
            authorized_plan_digest=digest,
        )
    with pytest.raises(Checkpoint143ABConfigurationError):
        _preflight_shape(
            execute=True,
            authorization_id=AUTHORIZATION_ID,
            maximum_provider_calls=MAXIMUM_PROVIDER_CALLS,
            maximum_cost_usd=MAXIMUM_COST_USD - 0.01,
            authorized_plan_digest=digest,
        )
    with pytest.raises(Checkpoint143ABConfigurationError):
        _preflight_shape(
            execute=True,
            authorization_id=AUTHORIZATION_ID,
            maximum_provider_calls=MAXIMUM_PROVIDER_CALLS,
            maximum_cost_usd=MAXIMUM_COST_USD,
            authorized_plan_digest="sha256:" + "0" * 64,
        )


def test_cell_ledgers_bind_policy_schema_session_turn_and_cap() -> None:
    control = CellLedger(cell=CONTROL)
    treatment = CellLedger(cell=TREATMENT)

    assert (
        control.reserve(
            _request(schema=16),
            PublicTurnScope(f"{CONTROL.session_prefix}-1", 1, PUBLIC_TURNS[0]["id"]),
        )
        == 1
    )
    assert (
        treatment.reserve(
            _request(schema=17),
            PublicTurnScope(f"{TREATMENT.session_prefix}-1", 1, PUBLIC_TURNS[0]["id"]),
        )
        == 1
    )
    with pytest.raises(ProviderCallBudgetExhausted):
        CellLedger(cell=TREATMENT).reserve(
            _request(schema=16),
            PublicTurnScope(f"{TREATMENT.session_prefix}-1", 1, PUBLIC_TURNS[0]["id"]),
        )
    with pytest.raises(ProviderCallBudgetExhausted):
        CellLedger(cell=CONTROL).reserve(
            _request(schema=16, limit=EXPECTED_TURN_VISIBLE_OUTPUT_TOKEN_LIMITS[1]),
            PublicTurnScope(f"{CONTROL.session_prefix}-1", 1, PUBLIC_TURNS[0]["id"]),
        )


def test_manifest_sanitizer_isolates_historical_control_and_complete_treatment() -> None:
    control = _safe_manifest(CONTROL, _manifest(CONTROL))
    treatment = _safe_manifest(TREATMENT, _manifest(TREATMENT))

    assert "character_agency_decision_schema_version" not in control
    assert treatment["character_agency_decision_schema_version"] == 1
    assert treatment["character_agency_status"] == "applied"
    raw_control = _manifest(CONTROL)
    raw_control["included_sections"] = [
        *cast(list[str], raw_control["included_sections"]),
        "character_agency_decision",
    ]
    with pytest.raises(RuntimeError, match="historical control"):
        _safe_manifest(CONTROL, raw_control)
    raw_treatment = _manifest(TREATMENT)
    raw_treatment["character_agency_reason_codes"] = []
    with pytest.raises(RuntimeError, match="complete typed agency"):
        _safe_manifest(TREATMENT, raw_treatment)


def test_phase_one_is_actually_blind_and_phase_two_requires_digest_frozen_review() -> None:
    report = _fake_report()
    phase_1 = _review_template(report)
    serialized = str(phase_1)

    assert len(phase_1["pairs"]) == 18
    assert "treatment_side" not in serialized
    assert "treatment_agency" not in serialized
    assert all(
        set(pair["phase_1"]["left_dimensions"]) == set(PAIR_REVIEW_DIMENSIONS)
        for pair in phase_1["pairs"]
    )

    completed = copy.deepcopy(phase_1)
    for pair in completed["pairs"]:
        pair["phase_1"]["preference"] = "left"
        pair["phase_1"]["left_dimensions"] = {
            dimension: True for dimension in PAIR_REVIEW_DIMENSIONS
        }
        pair["phase_1"]["right_dimensions"] = {
            dimension: True for dimension in PAIR_REVIEW_DIMENSIONS
        }
    completed["reviewer_attestation"] = {key: True for key in completed["reviewer_attestation"]}
    completed["content_digest"] = content_digest(
        {key: value for key, value in completed.items() if key != "content_digest"}
    )

    phase_2 = build_phase_2_review_template(completed, report)
    assert len(phase_2["pairs"]) == 18
    assert set(phase_2["cross_session_dimensions"]) == set(CROSS_SESSION_DIMENSIONS)
    assert all(
        set(pair["dimensions"]) == set(TREATMENT_REALIZATION_DIMENSIONS)
        for pair in phase_2["pairs"]
    )
    tampered = copy.deepcopy(completed)
    tampered["pairs"][0]["left_reply"] = "rewritten"
    with pytest.raises(ValueError, match="prose drift"):
        build_phase_2_review_template(tampered, report)
