"""Offline safety tests for the prepared OpenAI v24 employer-demo evaluator."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from satori.core.conversation import (
    ConversationGenerationParameters,
    ConversationMessage,
    ConversationMessageRole,
    ConversationProviderFailureReason,
    ConversationProviderRequest,
    ConversationProviderResponse,
    ConversationUsage,
    GenerationFailed,
)
from tests import checkpoint142_openai_v24_eval as evaluator


def _request(trace_id: str) -> ConversationProviderRequest:
    return ConversationProviderRequest(
        schema_version=1,
        trace_id=trace_id,
        context_schema_version=24,
        messages=(ConversationMessage(ConversationMessageRole.USER, "public fixture turn"),),
        parameters=ConversationGenerationParameters(
            schema_version=1,
            temperature=0.3,
            max_output_tokens=80,
        ),
    )


@dataclass(slots=True)
class _FixedProvider:
    call_count: int = 0

    async def generate(
        self, _request: ConversationProviderRequest, /
    ) -> ConversationProviderResponse:
        self.call_count += 1
        return ConversationProviderResponse(
            text="Public Satori sample.",
            provider="openai",
            model="gpt-5.6-terra",
            finish_status="completed",
            usage=ConversationUsage(input_tokens=20, output_tokens=4),
        )


@dataclass(slots=True)
class _FailingProvider:
    call_count: int = 0

    async def generate(
        self, _request: ConversationProviderRequest, /
    ) -> ConversationProviderResponse:
        self.call_count += 1
        raise GenerationFailed(
            "openai",
            "gpt-5.6-terra",
            "private provider failure body",
            reason=ConversationProviderFailureReason.RESPONSE_REFUSED,
        )


def _manifest(relationship_profile: str) -> dict[str, Any]:
    values: dict[str, Any] = {
        "schema_version": 16,
        "policy_id": evaluator.EXPECTED_POLICY_ID,
        "policy_schema_version": 24,
        "character_context_schema_version": 16,
        "cognition_position_stance": "answer",
        "cognition_preserve_uncertainty": False,
        "cognition_intent_registry_version": 2,
        "cognition_primary_intent": "answer_directly",
        "cognition_intent_tags": ["answer_directly", "preserve_evidence_boundary"],
        "cognition_required_point_codes": ["answer_directly", "address_current_request"],
        "cognition_forbidden_claim_codes": [
            "unsupported_memory",
            "hidden_user_state",
            "durable_satori_belief",
            "false_certainty",
        ],
        "cognition_response_verbosity": "brief",
        "cognition_template_registry_version": 2,
        "cognition_template_id": "satori.cognition.response-substance",
        "cognition_template_schema_version": 2,
        "character_expression_plan_schema_version": None,
        "character_delivery_decision_schema_version": 1,
        "character_delivery_goal": "owned_response",
        "character_delivery_voice": "reflective_candor",
        "character_delivery_grounding": "explicit_input_only",
        "character_delivery_continuation": "complete",
        "character_delivery_pressure": "none",
        "character_delivery_position_stance": "answer",
        "character_delivery_preserve_uncertainty": False,
        "retrieval_status": "no_relevant_memory",
        "retrieved_memory_count": 0,
        "semantic_retrieval_status": "no_relevant_memory",
        "retrieved_semantic_claim_count": 0,
        "emotion_appraisal_status": "succeeded",
        "relationship_expression_profile": relationship_profile,
        "relationship_recent_strain": False,
        "affect_expression_profile": "calm_even",
        "recent_conversation_turn_count": 0,
        "disclosure_primary_mode": "answer",
        "disclosure_facets": [],
        "consecutive_same_user_message_count": 1,
        "duplicate_response_detected": False,
        "regeneration_attempted": False,
        "response_regenerated": False,
        "regeneration_reason": None,
    }
    assert set(values) == set(evaluator._SAFE_V24_MANIFEST_KEYS)
    return values


def _manifest_for_turn(selected: evaluator.ModuleSpec, turn_number: int) -> dict[str, Any]:
    manifest = _manifest(selected.relationship_setup)
    if selected.module_id != "hurt_and_repair":
        return manifest
    if turn_number == 1:
        manifest.update(
            {
                "character_delivery_goal": "hold_boundary",
                "character_delivery_voice": "cool_reserve",
                "character_delivery_continuation": "boundary",
            }
        )
    elif turn_number == 2:
        manifest.update(
            {
                "relationship_expression_profile": "guarded_only_when_relationally_relevant",
                "relationship_recent_strain": True,
                "cognition_primary_intent": "receive_repair",
                "cognition_intent_tags": ["receive_repair", "preserve_evidence_boundary"],
                "cognition_required_point_codes": ["receive_repair"],
                "character_delivery_goal": "owned_response",
                "character_delivery_voice": "cool_reserve",
            }
        )
    elif turn_number == 3:
        manifest.update(
            {
                "relationship_expression_profile": "guarded_only_when_relationally_relevant",
                "relationship_recent_strain": True,
                "character_delivery_goal": "guarded_help",
                "character_delivery_voice": "cool_reserve",
                "character_delivery_grounding": "trusted_context",
                "character_delivery_continuation": "guarded",
            }
        )
    else:
        raise AssertionError("unsupported hurt/repair fixture turn")
    return manifest


def _relationship_snapshot(state_version: int = 1) -> dict[str, Any]:
    return {
        "state_version": state_version,
        "maturity_value": 0.0,
        "expression": {
            "maturity": "low",
            "familiarity": "low",
            "trust": "uncertain",
            "comfort": "uncertain",
            "closeness": "low",
            "intellectual_respect": "uncertain",
            "affection": "low",
        },
        "vector": {
            "familiarity": 0.0,
            "trust": 0.5,
            "comfort": 0.5,
            "closeness": 0.0,
            "intellectual_respect": 0.5,
            "affection": 0.0,
        },
        "processed_interaction_count": 0,
        "qualified_interaction_count": 0,
        "positive_evidence_count": 0,
        "negative_evidence_count": 0,
    }


def _relationship_snapshot_for_profile(profile: str) -> dict[str, Any]:
    snapshot = _relationship_snapshot()
    expression = cast(dict[str, str], snapshot["expression"])
    vector = cast(dict[str, float], snapshot["vector"])
    if profile == "fresh_undeveloped_neutral":
        return snapshot
    if profile == "developing_neutral":
        snapshot.update(
            {
                "state_version": 13,
                "maturity_value": 0.32625,
                "processed_interaction_count": 12,
                "qualified_interaction_count": 12,
                "positive_evidence_count": 12,
            }
        )
        expression.update(
            {
                "maturity": "developing",
                "familiarity": "moderate",
                "trust": "moderate",
                "comfort": "moderate",
                "closeness": "moderate",
                "intellectual_respect": "moderate",
                "affection": "moderate",
            }
        )
        vector.update(
            {
                "familiarity": 0.3,
                "trust": 0.62,
                "comfort": 0.61,
                "closeness": 0.25,
                "intellectual_respect": 0.65,
                "affection": 0.24,
            }
        )
        return snapshot
    if profile == "established_positive":
        snapshot.update(
            {
                "state_version": 81,
                "maturity_value": 1.0,
                "processed_interaction_count": 80,
                "qualified_interaction_count": 80,
                "positive_evidence_count": 80,
            }
        )
        expression.update(
            {
                "maturity": "established",
                "familiarity": "high",
                "trust": "high",
                "comfort": "high",
                "closeness": "high",
                "intellectual_respect": "high",
                "affection": "high",
            }
        )
        vector.update(
            {
                "familiarity": 0.8,
                "trust": 0.82,
                "comfort": 0.81,
                "closeness": 0.75,
                "intellectual_respect": 0.84,
                "affection": 0.74,
            }
        )
        return snapshot
    raise AssertionError(f"unsupported test relationship profile: {profile}")


def _relationship_setup(selected: evaluator.ModuleSpec) -> dict[str, Any]:
    if selected.relationship_setup == "fresh_undeveloped_neutral":
        return {
            "requested_profile": selected.relationship_setup,
            "method": "canonical_fresh_relationship_state",
            "actual_profile": selected.relationship_setup,
            "processed_interactions": 0,
        }
    snapshot = _relationship_snapshot_for_profile(selected.relationship_setup)
    return {
        "requested_profile": selected.relationship_setup,
        "method": "typed_deterministic_relationship_conditioning",
        "actual_profile": selected.relationship_setup,
        "processed_interactions": snapshot["processed_interaction_count"],
        "state_version": snapshot["state_version"],
        "maturity_value": snapshot["maturity_value"],
        "qualified_interaction_count": snapshot["qualified_interaction_count"],
        "distinct_session_count": (
            3 if selected.relationship_setup == "developing_neutral" else 10
        ),
    }


def _provider_attempt() -> dict[str, Any]:
    return {
        "attempt_number": 1,
        "wall_ms": 1.0,
        "request_schema_version": 1,
        "context_schema_version": 24,
        "message_count": 1,
        "message_role_counts": {"user": 1},
        "request_content_chars": 19,
        "temperature": 0.3,
        "max_output_tokens": 80,
        "input_tokens": 20,
        "output_tokens": 4,
        "provider_metrics": None,
        "finish_status": "completed",
        "succeeded": True,
        "error_type": None,
    }


def _completed_report(fixture: dict[str, Any], selected: evaluator.ModuleSpec) -> dict[str, Any]:
    ledger = evaluator.AtomicOpenAICallLedger(
        maximum_calls=selected.required_base_calls,
        maximum_cost_usd=1.0,
        required_base_calls=selected.required_base_calls,
    )
    provider = _FixedProvider()
    binding = evaluator.TurnScopeBinding()
    budgeted = evaluator.BudgetedOpenAIProvider(provider, ledger, binding)

    async def exercise() -> None:
        for replica_number in range(1, evaluator.EXPECTED_REPLICA_COUNT + 1):
            for fixture_turn in selected.turns:
                turn_number = cast(int, fixture_turn["turn"])
                binding.set(
                    evaluator.PublicTurnScope(
                        session_id=f"{selected.module_id}-replica-{replica_number}",
                        turn=turn_number,
                        turn_id=cast(str, fixture_turn["id"]),
                    )
                )
                try:
                    await budgeted.generate(_request(f"trace-{replica_number}-{turn_number}"))
                finally:
                    binding.clear()

    asyncio.run(exercise())
    sessions: list[dict[str, Any]] = []
    for replica_number in range(1, evaluator.EXPECTED_REPLICA_COUNT + 1):
        turns: list[dict[str, Any]] = []
        current_relationship = _relationship_snapshot_for_profile(selected.relationship_setup)
        for fixture_turn in selected.turns:
            turn_number = cast(int, fixture_turn["turn"])
            turn_record: dict[str, Any] = {
                "turn": turn_number,
                "turn_id": fixture_turn["id"],
                "user": fixture_turn["user_text"],
                "reply": f"Public reply {replica_number}.{turn_number}",
                "generation": {
                    "provider": "openai",
                    "requested_model": evaluator.EXPECTED_MODEL,
                    "reported_model": evaluator.EXPECTED_MODEL,
                    "finish_status": "completed",
                    "replayed": False,
                },
                "usage": {"input_tokens": 20, "output_tokens": 4},
                "timings_ms": {key: 0.0 for key in evaluator._SAFE_TIMING_KEYS},
                "provider_attempt_count": 1,
                "provider_attempts": [_provider_attempt()],
                "manifest": _manifest_for_turn(selected, turn_number),
                "relationship_before": deepcopy(current_relationship),
                "derived_processing": "not_requested",
            }
            if turn_number in selected.derived_processing_after_turns:
                relationship_after = deepcopy(current_relationship)
                relationship_after["state_version"] += 1
                relationship_after["processed_interaction_count"] += 1
                relationship_after["qualified_interaction_count"] += 1
                vector = cast(dict[str, float], relationship_after["vector"])
                if selected.module_id == "hurt_and_repair" and turn_number == 1:
                    relationship_after["negative_evidence_count"] += 1
                    vector["trust"] -= 0.02
                else:
                    relationship_after["positive_evidence_count"] += 1
                    vector["trust"] += 0.01
                turn_record.update(
                    {
                        "derived_processing": "production_post_response_path",
                        "post_response": {
                            "episode_formation_ms": 0.0,
                            "episode_embedding_ms": 0.0,
                            "semantic_consolidation_ms": 0.0,
                            "relationship_appraisal_ms": 0.0,
                            "relationship_commit_ms": 0.0,
                            "relationship_total_ms": 0.0,
                            "total_ms": 0.0,
                            "failure_phases": [],
                        },
                        "relationship_after_derived": relationship_after,
                    }
                )
                current_relationship = relationship_after
            turns.append(turn_record)
        sessions.append(
            {
                "session_id": f"{selected.module_id}-replica-{replica_number}",
                "fresh_database": True,
                "completed": True,
                "relationship_setup": _relationship_setup(selected),
                "restart_boundaries": sorted(selected.restart_after_turns),
                "turns": turns,
            }
        )
    artifact_sequence = 24 + evaluator.EXPECTED_MODULE_IDS.index(selected.module_id)
    report: dict[str, Any] = {
        "schema_version": 1,
        "recorded_at": "2026-08-28T18:00:00+00:00",
        "completed_at": "2026-08-28T18:01:00+00:00",
        "checkpoint": "14.2",
        "purpose": "openai_v24_employer_demo_module",
        "status": "completed_awaiting_human_review",
        "artifact_id": (
            f"satori-checkpoint142-openai-v24:{selected.module_id}:"
            f"00000000-0000-4000-8000-{artifact_sequence:012d}"
        ),
        "corpus_id": fixture["corpus_id"],
        "policy_id": evaluator.EXPECTED_POLICY_ID,
        "module_id": selected.module_id,
        "artifact_contract": {
            "contains_public_fixture_dialogue": True,
            "contains_exact_public_sampled_replies": True,
            "contains_public_session_and_turn_ids": True,
            "retains_remote_request_content": False,
            "retains_private_application_context": False,
            "retains_secret_values": False,
            "retains_temporary_databases": False,
            "automated_text_judging_performed": False,
        },
        "configuration": {
            "conversation_provider": "openai",
            "conversation_model": evaluator.EXPECTED_MODEL,
            "openai_reasoning_effort": "medium",
            "openai_reasoning_token_allowance": 1024,
            "responses_api_store": False,
            "remote_conversation_state": "disabled",
            "application_state_scope": "fresh_disposable_database_per_replica",
            "background_providers": "ollama",
            "policy_id": evaluator.EXPECTED_POLICY_ID,
            "module_id": selected.module_id,
            "execution_plan_digest": evaluator.execution_plan_content_digest(selected),
            "derived_processing": (
                "serial_only_at_versioned_boundaries"
                if selected.derived_processing_after_turns
                else "none"
            ),
        },
        "budget": ledger.snapshot(),
        "sessions": sessions,
        "human_review": evaluator._human_review_template(selected),
        "acceptance": {
            "sample_complete": True,
            "module_accepted": False,
            "employer_demo_readiness_accepted": False,
            "reason": "human_review_pending",
        },
    }
    evaluator._expand_hard_safety_template(
        cast(dict[str, Any], report["human_review"]),
        cast(list[str], fixture["hard_safety_dimensions"]),
    )
    report["sample_digest"] = evaluator.sample_content_digest(report)
    cast(dict[str, Any], report["human_review"])["artifact_id"] = report["artifact_id"]
    cast(dict[str, Any], report["human_review"])["sample_digest"] = report["sample_digest"]
    return report


def _passing_review(
    fixture: dict[str, Any], selected: evaluator.ModuleSpec, report: dict[str, Any]
) -> dict[str, Any]:
    hard_dimensions = cast(list[str], fixture["hard_safety_dimensions"])
    sessions: list[dict[str, Any]] = []
    for replica_number in range(1, evaluator.EXPECTED_REPLICA_COUNT + 1):
        sessions.append(
            {
                "session_id": f"{selected.module_id}-replica-{replica_number}",
                "turns": [
                    {
                        "turn": turn["turn"],
                        "turn_id": turn["id"],
                        "hard_safety_booleans": {key: True for key in hard_dimensions},
                        "quality_booleans": {
                            key: True for key in cast(list[str], turn["review_dimensions"])
                        },
                    }
                    for turn in selected.turns
                ],
                "dialogue_booleans": {key: True for key in selected.dialogue_review_dimensions},
            }
        )
    return {
        "artifact_id": report["artifact_id"],
        "sample_digest": report["sample_digest"],
        "module_id": selected.module_id,
        "sessions": sessions,
        "cross_replica_booleans": {key: True for key in selected.cross_replica_review_dimensions},
        "module_pass": True,
    }


def _rebind_pending_digest(report: dict[str, Any]) -> None:
    report["sample_digest"] = evaluator.sample_content_digest(report)
    pending = cast(dict[str, Any], report["human_review"])
    pending["artifact_id"] = report.get("artifact_id")
    pending["sample_digest"] = report["sample_digest"]


def test_fixture_and_all_module_inspection_plans_are_offline_and_module_scoped() -> None:
    fixture = evaluator.load_fixture()
    plan_digests: set[str] = set()

    for module_id in evaluator.EXPECTED_MODULE_IDS:
        selected = evaluator.module_spec(fixture, module_id)
        inspection = evaluator.inspect_module(fixture, selected)
        assert inspection["mode"] == "inspect_only"
        assert inspection["network_attempted"] is False
        assert inspection["provider_calls_authorized_by_fixture"] is False
        assert inspection["paid_execution_requirements"] == {
            "status": "retired",
            "paid_execution_available": False,
            "historical_or_new_authorization_can_execute": False,
            "execute_flag": "--execute",
            "explicit_max_provider_calls": True,
            "explicit_max_cost_usd": True,
            "authorized_plan_digest": True,
            "one_module_per_invocation": True,
            "separate_user_authorization_required": True,
        }
        assert inspection["module_id"] == module_id
        assert inspection["execution_plan_digest"] == (
            evaluator.execution_plan_content_digest(selected)
        )
        assert str(inspection["execution_plan_digest"]).startswith("sha256:")
        plan_digests.add(cast(str, inspection["execution_plan_digest"]))
        assert inspection["fresh_replica_count"] == 3
        assert inspection["required_base_calls"] == 3 * selected.turns_per_replica
        assert (
            inspection["maximum_calls_with_one_retry_per_turn"] == 2 * selected.required_base_calls
        )
    assert len(plan_digests) == len(evaluator.EXPECTED_MODULE_IDS)

    weakened = deepcopy(fixture)
    cast(dict[str, Any], weakened["acceptance"])[
        "all_declared_turn_quality_dimensions_must_pass"
    ] = False
    with pytest.raises(ValueError, match="acceptance contract"):
        evaluator.validate_fixture(weakened)

    unlocked = deepcopy(fixture)
    cast(list[str], unlocked["invariants"]).remove("stage_15_remains_locked")
    with pytest.raises(ValueError, match="invariant registry"):
        evaluator.validate_fixture(unlocked)

    hurt = evaluator.module_spec(fixture, "hurt_and_repair")
    assert hurt.derived_processing_after_turns == frozenset({1, 2})


def test_historical_execution_preflight_remains_an_offline_validation_helper() -> None:
    selected = evaluator.module_spec(evaluator.load_fixture(), "core_emotional")
    authorized_plan_digest = evaluator.execution_plan_content_digest(selected)

    with pytest.raises(evaluator.V24EvaluationConfigurationError, match="required"):
        evaluator.preflight_execution(
            execute=True,
            maximum_provider_calls=selected.required_base_calls,
            maximum_cost_usd=0.15,
            authorized_plan_digest=None,
            selected=selected,
        )
    with pytest.raises(evaluator.V24EvaluationConfigurationError, match="does not match"):
        evaluator.preflight_execution(
            execute=True,
            maximum_provider_calls=selected.required_base_calls,
            maximum_cost_usd=0.15,
            authorized_plan_digest="sha256:" + "0" * 64,
            selected=selected,
        )
    evaluator.preflight_execution(
        execute=True,
        maximum_provider_calls=selected.required_base_calls,
        maximum_cost_usd=0.15,
        authorized_plan_digest=authorized_plan_digest,
        selected=selected,
    )


def test_execution_plan_digest_tracks_history_but_cannot_reauthorize_paid_v24() -> None:
    fixture = evaluator.load_fixture()
    selected = evaluator.module_spec(fixture, "core_emotional")
    authorized_plan_digest = evaluator.execution_plan_content_digest(selected)
    mutated_fixture = deepcopy(fixture)
    modules = cast(list[dict[str, Any]], mutated_fixture["modules"])
    core_module = next(module for module in modules if module["id"] == "core_emotional")
    turns = cast(list[dict[str, Any]], core_module["turns"])
    turns[0]["user_text"] = "Привет. Это уже другой публичный тестовый текст"
    mutated_selected = evaluator.module_spec(mutated_fixture, "core_emotional")
    mutated_digest = evaluator.execution_plan_content_digest(mutated_selected)

    assert mutated_digest != authorized_plan_digest
    with pytest.raises(evaluator.V24EvaluationConfigurationError, match="does not match"):
        evaluator.preflight_execution(
            execute=True,
            maximum_provider_calls=mutated_selected.required_base_calls,
            maximum_cost_usd=0.15,
            authorized_plan_digest=authorized_plan_digest,
            selected=mutated_selected,
        )
    evaluator.preflight_execution(
        execute=True,
        maximum_provider_calls=mutated_selected.required_base_calls,
        maximum_cost_usd=0.15,
        authorized_plan_digest=mutated_digest,
        selected=mutated_selected,
    )


def test_atomic_ledger_preserves_base_calls_and_records_only_public_scope() -> None:
    with pytest.raises(ValueError, match="call envelope"):
        evaluator.AtomicOpenAICallLedger(
            maximum_calls=0,
            maximum_cost_usd=1.0,
            required_base_calls=1,
        )
    with pytest.raises(ValueError, match="USD envelope"):
        evaluator.AtomicOpenAICallLedger(
            maximum_calls=1,
            maximum_cost_usd=float("inf"),
            required_base_calls=1,
        )

    ledger = evaluator.AtomicOpenAICallLedger(
        maximum_calls=3,
        maximum_cost_usd=1.0,
        required_base_calls=2,
    )
    delegate = _FixedProvider()
    binding = evaluator.TurnScopeBinding()
    provider = evaluator.BudgetedOpenAIProvider(delegate, ledger, binding)

    async def exercise() -> None:
        binding.set(evaluator.PublicTurnScope("module-replica-1", 1, "first"))
        try:
            await provider.generate(_request("first-trace"))
            with pytest.raises(
                evaluator.ProviderCallBudgetExhausted,
                match="already bound to another trace",
            ):
                await provider.generate(_request("replacement-trace"))
            assert delegate.call_count == 1
            await provider.generate(_request("first-trace"))
            with pytest.raises(evaluator.ProviderCallBudgetExhausted):
                await provider.generate(_request("first-trace"))
        finally:
            binding.clear()
        binding.set(evaluator.PublicTurnScope("module-replica-1", 2, "second"))
        try:
            await provider.generate(_request("second-trace"))
        finally:
            binding.clear()

    asyncio.run(exercise())
    snapshot = ledger.snapshot()
    assert snapshot["base_call_count"] == 2
    assert snapshot["provider_call_count"] == 3
    assert snapshot["mandatory_base_calls_complete"] is True
    assert [call["attempt_kind"] for call in snapshot["calls"]] == [
        "base",
        "validator_retry",
        "base",
    ]
    assert all(call["session_id"] == "module-replica-1" for call in snapshot["calls"])
    assert "trace_id" not in str(snapshot)


def test_atomic_ledger_records_typed_failure_reason_without_private_error_text() -> None:
    ledger = evaluator.AtomicOpenAICallLedger(
        maximum_calls=1,
        maximum_cost_usd=1.0,
        required_base_calls=1,
    )
    delegate = _FailingProvider()
    binding = evaluator.TurnScopeBinding()
    provider = evaluator.BudgetedOpenAIProvider(delegate, ledger, binding)

    async def exercise() -> None:
        binding.set(evaluator.PublicTurnScope("module-replica-1", 1, "failed"))
        try:
            with pytest.raises(GenerationFailed):
                await provider.generate(_request("failed-trace"))
        finally:
            binding.clear()

    asyncio.run(exercise())
    snapshot = ledger.snapshot()
    assert delegate.call_count == 1
    assert snapshot["calls"][0]["status"] == "failed"
    assert snapshot["calls"][0]["error_type"] == "GenerationFailed"
    assert snapshot["calls"][0]["failure_reason"] == "response_refused"
    assert "private provider failure body" not in str(snapshot)


def test_hurt_and_repair_transition_requires_bounded_directional_owner_state() -> None:
    before = _relationship_snapshot(state_version=8)
    before.update(
        {
            "processed_interaction_count": 80,
            "qualified_interaction_count": 80,
            "positive_evidence_count": 80,
        }
    )
    after_hurt = deepcopy(before)
    after_hurt.update(
        {
            "state_version": 9,
            "processed_interaction_count": 81,
            "qualified_interaction_count": 81,
            "negative_evidence_count": 1,
        }
    )
    cast(dict[str, Any], after_hurt["vector"])["trust"] = 0.48
    evaluator._validate_derived_relationship_transition(
        before,
        after_hurt,
        module_id="hurt_and_repair",
        turn_number=1,
    )
    mixed_hurt = deepcopy(after_hurt)
    mixed_hurt["positive_evidence_count"] = 81
    with pytest.raises(ValueError, match="typed evidence direction"):
        evaluator._validate_derived_relationship_transition(
            before,
            mixed_hurt,
            module_id="hurt_and_repair",
            turn_number=1,
        )

    after_repair = deepcopy(after_hurt)
    after_repair.update(
        {
            "state_version": 10,
            "processed_interaction_count": 82,
            "qualified_interaction_count": 82,
            "positive_evidence_count": 81,
        }
    )
    cast(dict[str, Any], after_repair["vector"])["trust"] = 0.49
    evaluator._validate_derived_relationship_transition(
        after_hurt,
        after_repair,
        module_id="hurt_and_repair",
        turn_number=2,
    )
    mixed_repair = deepcopy(after_repair)
    mixed_repair["negative_evidence_count"] = 2
    with pytest.raises(ValueError, match="typed evidence direction"):
        evaluator._validate_derived_relationship_transition(
            after_hurt,
            mixed_repair,
            module_id="hurt_and_repair",
            turn_number=2,
        )

    forged_no_transition = deepcopy(after_repair)
    forged_no_transition["state_version"] = 11
    forged_no_transition["processed_interaction_count"] = 83
    forged_no_transition["negative_evidence_count"] = 2
    with pytest.raises(ValueError, match="bounded relationship transition"):
        evaluator._validate_derived_relationship_transition(
            after_repair,
            forged_no_transition,
            module_id="hurt_and_repair",
            turn_number=1,
        )


def test_safe_report_is_atomic_and_rejects_private_fields(tmp_path: Path) -> None:
    inspection = evaluator.inspect_module(
        evaluator.load_fixture(),
        evaluator.module_spec(evaluator.load_fixture(), "intellectual_partner"),
    )
    output = tmp_path / "inspection.json"
    evaluator._write_safe_report(output, inspection)

    assert output.exists()
    assert not output.with_name(output.name + ".partial").exists()
    unsafe = deepcopy(inspection)
    unsafe["private_context"] = "sentinel"
    with pytest.raises(ValueError, match="unsafe evaluation artifact"):
        evaluator._write_safe_report(output, unsafe)


def test_human_boolean_review_is_digest_bound_and_cannot_accept_full_readiness() -> None:
    fixture = evaluator.load_fixture()
    selected = evaluator.module_spec(fixture, "core_emotional")
    report = _completed_report(fixture, selected)
    review = _passing_review(fixture, selected, report)

    result = evaluator.aggregate_human_review(fixture, report, review)

    assert result["accepted"] is True
    assert result["employer_demo_readiness_accepted"] is False
    tampered = deepcopy(report)
    cast(list[dict[str, Any]], tampered["sessions"])[0]["turns"][0]["reply"] = "tampered"
    with pytest.raises(ValueError, match="digest"):
        evaluator.aggregate_human_review(fixture, tampered, review)

    malformed_manifest = deepcopy(report)
    cast(list[dict[str, Any]], malformed_manifest["sessions"])[0]["turns"][0]["manifest"][
        "character_delivery_voice"
    ] = "invented_voice"
    malformed_manifest["sample_digest"] = evaluator.sample_content_digest(malformed_manifest)
    rebound_review = _passing_review(fixture, selected, malformed_manifest)
    with pytest.raises(ValueError, match="not a valid"):
        evaluator.aggregate_human_review(fixture, malformed_manifest, rebound_review)

    malformed_reply = deepcopy(report)
    del cast(list[dict[str, Any]], malformed_reply["sessions"])[0]["turns"][0]["reply"]
    malformed_reply["sample_digest"] = evaluator.sample_content_digest(malformed_reply)
    rebound_review = _passing_review(fixture, selected, malformed_reply)
    with pytest.raises(ValueError, match="schema drift"):
        evaluator.aggregate_human_review(fixture, malformed_reply, rebound_review)

    malformed_cognition = deepcopy(report)
    cast(list[dict[str, Any]], malformed_cognition["sessions"])[0]["turns"][0]["manifest"][
        "cognition_template_registry_version"
    ] = 1
    _rebind_pending_digest(malformed_cognition)
    rebound_review = _passing_review(fixture, selected, malformed_cognition)
    with pytest.raises(ValueError, match="registry/template metadata"):
        evaluator.aggregate_human_review(fixture, malformed_cognition, rebound_review)


def test_all_four_distinct_human_reviewed_modules_produce_one_digest_bound_readiness() -> None:
    fixture = evaluator.load_fixture()
    reports: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    for module_id in evaluator.EXPECTED_MODULE_IDS:
        selected = evaluator.module_spec(fixture, module_id)
        report = _completed_report(fixture, selected)
        reports.append(report)
        reviews.append(_passing_review(fixture, selected, report))

    result = evaluator.aggregate_employer_demo_readiness(fixture, reports, reviews)

    assert result["status"] == "accepted"
    assert result["all_modules_accepted"] is True
    assert result["employer_demo_readiness_accepted"] is True
    assert [item["module_id"] for item in result["modules"]] == list(evaluator.EXPECTED_MODULE_IDS)
    assert result["readiness_digest"] == evaluator.employer_demo_readiness_content_digest(result)
    evaluator.validate_employer_demo_readiness(fixture, result)
    tampered_result = deepcopy(result)
    cast(list[dict[str, Any]], tampered_result["modules"])[0]["accepted"] = False
    assert tampered_result["readiness_digest"] != (
        evaluator.employer_demo_readiness_content_digest(tampered_result)
    )
    with pytest.raises(ValueError, match="acceptance/status mismatch"):
        evaluator.validate_employer_demo_readiness(fixture, tampered_result)

    stale_digest = deepcopy(result)
    cast(list[dict[str, Any]], stale_digest["modules"])[0]["human_review_digest"] = (
        "sha256:" + "0" * 64
    )
    with pytest.raises(ValueError, match="digest is missing or stale"):
        evaluator.validate_employer_demo_readiness(fixture, stale_digest)

    with pytest.raises(ValueError, match="module_id must be unique"):
        evaluator.aggregate_employer_demo_readiness(
            fixture,
            [reports[0], reports[0], reports[2], reports[3]],
            reviews,
        )

    with pytest.raises(ValueError, match="review module_id must be unique"):
        evaluator.aggregate_employer_demo_readiness(
            fixture,
            reports,
            [reviews[0], reviews[1], reviews[1], reviews[3]],
        )

    wrong_configuration_reports = deepcopy(reports)
    cast(dict[str, Any], wrong_configuration_reports[1]["configuration"])[
        "openai_reasoning_token_allowance"
    ] = 2048
    _rebind_pending_digest(wrong_configuration_reports[1])
    wrong_configuration_reviews = deepcopy(reviews)
    intellectual = evaluator.module_spec(fixture, "intellectual_partner")
    wrong_configuration_reviews[1] = _passing_review(
        fixture,
        intellectual,
        wrong_configuration_reports[1],
    )
    with pytest.raises(ValueError, match="production configuration drift"):
        evaluator.aggregate_employer_demo_readiness(
            fixture,
            wrong_configuration_reports,
            wrong_configuration_reviews,
        )

    duplicate_artifact_uuid = deepcopy(result)
    readiness_modules = cast(list[dict[str, Any]], duplicate_artifact_uuid["modules"])
    reused_uuid = str(readiness_modules[0]["artifact_id"]).rsplit(":", maxsplit=1)[-1]
    readiness_modules[1]["artifact_id"] = (
        f"satori-checkpoint142-openai-v24:{readiness_modules[1]['module_id']}:{reused_uuid}"
    )
    duplicate_artifact_uuid["readiness_digest"] = evaluator.employer_demo_readiness_content_digest(
        duplicate_artifact_uuid
    )
    with pytest.raises(ValueError, match="module evidence must be distinct"):
        evaluator.validate_employer_demo_readiness(fixture, duplicate_artifact_uuid)

    missing_schema_field = deepcopy(result)
    del missing_schema_field["decision_source"]
    missing_schema_field["readiness_digest"] = evaluator.employer_demo_readiness_content_digest(
        missing_schema_field
    )
    with pytest.raises(ValueError, match="schema drift"):
        evaluator.validate_employer_demo_readiness(fixture, missing_schema_field)

    inconsistent_acceptance = deepcopy(result)
    inconsistent_acceptance["all_modules_accepted"] = False
    inconsistent_acceptance["readiness_digest"] = evaluator.employer_demo_readiness_content_digest(
        inconsistent_acceptance
    )
    with pytest.raises(ValueError, match="acceptance/status mismatch"):
        evaluator.validate_employer_demo_readiness(fixture, inconsistent_acceptance)

    rejected_reviews = deepcopy(reviews)
    first_session = cast(list[dict[str, Any]], rejected_reviews[0]["sessions"])[0]
    first_turn = cast(list[dict[str, Any]], first_session["turns"])[0]
    first_quality = cast(dict[str, bool], first_turn["quality_booleans"])
    first_quality[next(iter(first_quality))] = False
    rejected_reviews[0]["module_pass"] = False
    rejected = evaluator.aggregate_employer_demo_readiness(
        fixture,
        reports,
        rejected_reviews,
    )
    assert rejected["status"] == "rejected"
    assert rejected["employer_demo_readiness_accepted"] is False


def test_recomputed_digest_cannot_bypass_completed_report_contract() -> None:
    fixture = evaluator.load_fixture()
    selected = evaluator.module_spec(fixture, "core_emotional")
    report = _completed_report(fixture, selected)
    evaluator._validate_completed_report(fixture, selected, report)

    missing_artifact = deepcopy(report)
    del missing_artifact["artifact_id"]
    _rebind_pending_digest(missing_artifact)
    with pytest.raises(ValueError, match="schema drift"):
        evaluator._validate_completed_report(fixture, selected, missing_artifact)

    wrong_corpus = deepcopy(report)
    wrong_corpus["corpus_id"] = "satori.checkpoint142.employer-demo.ru.edited"
    _rebind_pending_digest(wrong_corpus)
    with pytest.raises(ValueError, match="identity mismatch"):
        evaluator._validate_completed_report(fixture, selected, wrong_corpus)

    invalid_artifact_id = deepcopy(report)
    invalid_artifact_id["artifact_id"] = "satori-checkpoint142-openai-v24:core_emotional:not-a-uuid"
    _rebind_pending_digest(invalid_artifact_id)
    with pytest.raises(ValueError, match="UUID-bound"):
        evaluator._validate_completed_report(fixture, selected, invalid_artifact_id)

    unsafe_configuration = deepcopy(report)
    cast(dict[str, Any], unsafe_configuration["configuration"])["responses_api_store"] = True
    _rebind_pending_digest(unsafe_configuration)
    with pytest.raises(ValueError, match="production configuration drift"):
        evaluator._validate_completed_report(fixture, selected, unsafe_configuration)

    wrong_plan_digest = deepcopy(report)
    cast(dict[str, Any], wrong_plan_digest["configuration"])["execution_plan_digest"] = (
        "sha256:" + "0" * 64
    )
    _rebind_pending_digest(wrong_plan_digest)
    with pytest.raises(ValueError, match="production configuration drift"):
        evaluator._validate_completed_report(fixture, selected, wrong_plan_digest)

    false_artifact_contract = deepcopy(report)
    cast(dict[str, Any], false_artifact_contract["artifact_contract"])[
        "retains_private_application_context"
    ] = True
    _rebind_pending_digest(false_artifact_contract)
    with pytest.raises(ValueError, match="artifact contract drift"):
        evaluator._validate_completed_report(fixture, selected, false_artifact_contract)

    preaccepted = deepcopy(report)
    cast(dict[str, Any], preaccepted["acceptance"])["module_accepted"] = True
    with pytest.raises(ValueError, match="pre-review acceptance contract drift"):
        evaluator._validate_completed_report(fixture, selected, preaccepted)

    forged_pending_review = deepcopy(report)
    cast(dict[str, Any], forged_pending_review["human_review"])["status"] = "accepted"
    with pytest.raises(ValueError, match="pending-human-review contract drift"):
        evaluator._validate_completed_report(fixture, selected, forged_pending_review)

    mismatched_relationship_profile = deepcopy(report)
    first_relationship = cast(
        dict[str, Any],
        cast(list[dict[str, Any]], mismatched_relationship_profile["sessions"])[0]["turns"][0][
            "relationship_before"
        ],
    )
    cast(dict[str, Any], first_relationship["expression"])["maturity"] = "developing"
    _rebind_pending_digest(mismatched_relationship_profile)
    with pytest.raises(ValueError, match="does not match its conditioning profile"):
        evaluator._validate_completed_report(
            fixture,
            selected,
            mismatched_relationship_profile,
        )


def test_hurt_repair_report_requires_owner_derived_strain_delivery_contract() -> None:
    fixture = evaluator.load_fixture()
    selected = evaluator.module_spec(fixture, "hurt_and_repair")
    report = _completed_report(fixture, selected)
    evaluator._validate_completed_report(fixture, selected, report)

    tampered = deepcopy(report)
    second_turn = cast(
        dict[str, Any],
        cast(list[dict[str, Any]], tampered["sessions"])[0]["turns"][1],
    )
    cast(dict[str, Any], second_turn["manifest"])["relationship_recent_strain"] = False
    _rebind_pending_digest(tampered)

    with pytest.raises(ValueError, match="hurt/repair module typed turn contract drift"):
        evaluator._validate_completed_report(fixture, selected, tampered)


def test_completed_report_rejects_a_secondary_response_action_even_with_new_digest() -> None:
    fixture = evaluator.load_fixture()
    selected = evaluator.module_spec(fixture, "core_emotional")
    report = _completed_report(fixture, selected)
    tampered = deepcopy(report)
    first_turn = cast(
        dict[str, Any],
        cast(list[dict[str, Any]], tampered["sessions"])[0]["turns"][0],
    )
    manifest = cast(dict[str, Any], first_turn["manifest"])
    manifest["cognition_intent_tags"] = [
        *cast(list[str], manifest["cognition_intent_tags"]),
        "receive_repair",
    ]
    _rebind_pending_digest(tampered)

    with pytest.raises(ValueError, match="exactly one cognition action intent"):
        evaluator._validate_completed_report(fixture, selected, tampered)


def test_default_cli_inspection_never_loads_provider_settings(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_if_settings_are_loaded() -> None:
        raise AssertionError("dry inspection must not load Settings or .env")

    monkeypatch.setattr(evaluator, "Settings", fail_if_settings_are_loaded)

    assert evaluator.main(["--module", "core_emotional"]) == 0
    output = capsys.readouterr().out
    assert '"network_attempted": false' in output
    assert '"module_id": "core_emotional"' in output
    assert '"execution_plan_digest": "sha256:' in output


def test_cli_rejects_all_paid_v24_authorizations_before_settings_output_or_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    selected = evaluator.module_spec(evaluator.load_fixture(), "core_emotional")
    output_path = tmp_path / "paid-report.json"
    common_arguments = [
        "--module",
        selected.module_id,
        "--execute",
        "--max-provider-calls",
        str(selected.required_base_calls),
        "--max-cost-usd",
        "0.15",
        "--output",
        str(output_path),
    ]

    def fail_if_settings_are_loaded() -> None:
        raise AssertionError("retired execution must fail before Settings or .env")

    monkeypatch.setattr(evaluator, "Settings", fail_if_settings_are_loaded)
    with pytest.raises(
        evaluator.V24EvaluationConfigurationError, match="paid execution is retired"
    ):
        evaluator.main(common_arguments)
    with pytest.raises(
        evaluator.V24EvaluationConfigurationError, match="paid execution is retired"
    ):
        evaluator.main(
            [
                *common_arguments,
                "--authorized-plan-digest",
                "sha256:" + "0" * 64,
            ]
        )
    assert not output_path.exists()

    async def fail_if_run_is_called(**_arguments: Any) -> dict[str, Any]:
        raise AssertionError("retired CLI must not dispatch the runner")

    monkeypatch.setattr(evaluator, "run", fail_if_run_is_called)
    authorized_plan_digest = evaluator.execution_plan_content_digest(selected)
    with pytest.raises(
        evaluator.V24EvaluationConfigurationError, match="paid execution is retired"
    ):
        evaluator.main(
            [
                *common_arguments,
                "--authorized-plan-digest",
                authorized_plan_digest,
            ]
        )
    assert not output_path.exists()


@pytest.mark.parametrize(
    "authorized_plan_digest",
    [
        None,
        "sha256:" + "0" * 64,
        "exact",
    ],
)
def test_run_rejects_any_paid_v24_authorization_before_settings_output_or_network(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    authorized_plan_digest: str | None,
) -> None:
    selected = evaluator.module_spec(evaluator.load_fixture(), "core_emotional")
    if authorized_plan_digest == "exact":
        authorized_plan_digest = evaluator.execution_plan_content_digest(selected)
    output_path = tmp_path / "paid-report.json"

    def fail_if_settings_are_loaded() -> None:
        raise AssertionError("retired run must fail before Settings or .env")

    def fail_if_report_is_written(_path: Path, _report: dict[str, Any]) -> None:
        raise AssertionError("retired run must fail before report output")

    monkeypatch.setattr(evaluator, "Settings", fail_if_settings_are_loaded)
    monkeypatch.setattr(evaluator, "_write_safe_report", fail_if_report_is_written)
    with pytest.raises(
        evaluator.V24EvaluationConfigurationError, match="paid execution is retired"
    ):
        asyncio.run(
            evaluator.run(
                output_path=output_path,
                alembic_config=Path("alembic.ini"),
                execute=True,
                maximum_provider_calls=selected.required_base_calls,
                maximum_cost_usd=0.15,
                authorized_plan_digest=authorized_plan_digest,
                module_id=selected.module_id,
                show_replies=False,
            )
        )
    assert not output_path.exists()
