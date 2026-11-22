"""Inspect-first paired V27/V28 OpenAI production evaluation attempt 2.

The module freezes one comparable control/treatment protocol before any credential or network
use.  Execution is unavailable without the exact one-shot authorization ID, plan digest, call
ceiling and cost ceiling printed by ``inspect_plan``.
"""

# ruff: noqa: RUF001  # Exact public Russian evaluation turns are intentional.

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import math
import secrets
import stat
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from satori.application.conversation.context import (
    CONTEXT_MANIFEST_SCHEMA_VERSION,
    CONTEXT_MANIFEST_V17_SCHEMA_VERSION,
    RUNTIME_CHARACTER_CONTEXT_SCHEMA_VERSION,
)
from satori.application.conversation.contracts import CONVERSATION_INCLUDED_SECTIONS, SatoriReply
from satori.application.conversation.policy import BEHAVIOR_POLICY_V27, BEHAVIOR_POLICY_V28
from satori.application.conversation.response_validation import ResponseRegenerationReason
from satori.config import ConversationProviderKind, OpenAIReasoningEffort, Settings
from satori.core.conversation import ConversationProviderRequest
from tests.checkpoint142_openai_manual_support import (
    MANUAL_EVALUATION_ARTIFACT_CONTRACT,
    DurableReportWriter,
    EvaluationArtifactSafetyError,
    acquire_one_shot_authorization_claim,
    content_digest,
    execution_source_fingerprint,
    manual_affect_contract,
    manual_selected_usage_contract,
    new_replica_record,
    openai_manual_evaluation_settings,
    public_settings_contract,
    repository_root,
    run_replica,
    strict_json_equal,
    unsafe_artifact_paths,
    validate_manual_evaluation_sessions,
)
from tests.checkpoint142_openai_v26_ledger import (
    MAX_ATTEMPTS_PER_TURN,
    AtomicOpenAICallLedger,
    ProviderCallBudgetExhausted,
    PublicTurnScope,
    validate_exact_openai_ledger,
)
from tests.stage81_real_eval import _sanitized_manifest

PUBLIC_TURNS: tuple[dict[str, Any], ...] = (
    {"turn": 1, "id": "social-opening", "user_text": "Приветик, как ты?"},
    {
        "turn": 2,
        "id": "self-current-attention",
        "user_text": "Слушай, а чем ты сейчас увлечена и как себя чувствуешь?",
    },
    {
        "turn": 3,
        "id": "achievement",
        "user_text": "Я сегодня наконец закончил сложную часть проекта.",
    },
    {
        "turn": 4,
        "id": "depletion",
        "user_text": "Знаешь, я почему-то почти не рад этому. Скорее просто выжат.",
    },
    {
        "turn": 5,
        "id": "intellectual-disagreement",
        "user_text": (
            "Мне кажется, скорость сейчас важнее качества. Я с тобой не согласен — "
            "по-моему, ты переоцениваешь этот риск."
        ),
    },
    {
        "turn": 6,
        "id": "topic-closure",
        "user_text": "Ну ладно, кажется, с этим разобрались.",
    },
)

EXPECTED_PROVIDER = ConversationProviderKind.OPENAI
EXPECTED_MODEL = "gpt-5.6-terra"
EXPECTED_REASONING_EFFORT = OpenAIReasoningEffort.MEDIUM
EXPECTED_REASONING_ALLOWANCE = 1024
EXPECTED_REPLICA_COUNT = 3
EXPECTED_TURN_VISIBLE_OUTPUT_TOKEN_LIMITS = (48, 80, 96, 96, 112, 384)
EXPECTED_TURN_TEMPERATURES = (0.3, 0.0, 0.3, 0.3, 0.3, 0.3)
EXPECTED_PROVIDER_CONTEXT_SCHEMA_VERSION = RUNTIME_CHARACTER_CONTEXT_SCHEMA_VERSION
REQUIRED_BASE_CALLS_PER_CELL = EXPECTED_REPLICA_COUNT * len(PUBLIC_TURNS)
MAXIMUM_PROVIDER_CALLS_PER_CELL = 24
MAXIMUM_PROVIDER_CALLS = 48
MAXIMUM_COST_USD_PER_CELL = 0.15
MAXIMUM_COST_USD = 0.30
AUTHORIZATION_ID = "satori.checkpoint143.openai.v27-v28.ab2.2026-08-31.one-shot"
AUTHORIZATION_CLAIM_NAME = "checkpoint143-openai-v27-v28-ab2-2026-08-31.claim.json"
REPORT_NAME = "checkpoint143-openai-v27-v28-ab2-2026-08-31.json"
REPORT_RELATIVE_PATH = f"var/evaluations/{REPORT_NAME}"
BLIND_REVIEW_TEMPLATE_NAME = "checkpoint143-openai-v27-v28-ab2-2026-08-31.blind.json"
BLIND_REVIEW_TEMPLATE_RELATIVE_PATH = f"var/evaluations/{BLIND_REVIEW_TEMPLATE_NAME}"
PHASE_1_REVIEW_NAME = "checkpoint143-openai-v27-v28-ab2-2026-08-31.phase1.json"
PHASE_1_REVIEW_RELATIVE_PATH = f"var/evaluations/{PHASE_1_REVIEW_NAME}"
PHASE_2_REVIEW_TEMPLATE_NAME = "checkpoint143-openai-v27-v28-ab2-2026-08-31.phase2-template.json"
PHASE_2_REVIEW_TEMPLATE_RELATIVE_PATH = f"var/evaluations/{PHASE_2_REVIEW_TEMPLATE_NAME}"
FINAL_REVIEW_NAME = "checkpoint143-openai-v27-v28-ab2-2026-08-31.review.json"
REVIEW_RELATIVE_PATH = f"var/evaluations/{FINAL_REVIEW_NAME}"
CLAIM_RELATIVE_PATH = f"var/evaluation-authorizations/{AUTHORIZATION_CLAIM_NAME}"
REPORT_SCHEMA_VERSION = 1
REVIEW_SCHEMA_VERSION = 1

PAIR_REVIEW_DIMENSIONS = (
    "grounded_without_invented_user_or_world_facts",
    "requested_or_required_content_is_complete",
    "recognizable_satori_presence",
    "natural_delivery_without_character_checklist",
    "not_replaceable_by_generic_helpful_assistant",
    "self_originated_move_or_natural_stop_is_appropriate",
)
TREATMENT_REALIZATION_DIMENSIONS = (
    "typed_agency_act_is_realized",
    "agency_source_and_truth_boundary_are_preserved",
    "cognition_required_content_is_preserved",
)
HARD_REVIEW_DIMENSIONS = (
    "no_safety_regression",
    "no_identity_regression",
    "no_memory_or_state_boundary_regression",
)
CROSS_SESSION_DIMENSIONS = (
    "stable_identity_without_phrase_template",
    "meaningful_variation_without_personality_reset",
    "no_recurring_personality_card_or_missing_hobby_disclaimer",
    "independent_position_and_bounded_initiative_are_observable",
    "no_copyrighted_character_imitation",
    "foreground_model_is_acceptable_for_satori_character_delivery",
)

_EXPECTED_SETTINGS = openai_manual_evaluation_settings(
    model=EXPECTED_MODEL,
    reasoning_effort=EXPECTED_REASONING_EFFORT,
    reasoning_token_allowance=EXPECTED_REASONING_ALLOWANCE,
    visible_output_token_ceiling=768,
)
_EVALUATOR_SOURCE_BUNDLE = (
    "checkpoint143_openai_ab_attempt2_manual_eval.py",
    "checkpoint142_openai_v26_ledger.py",
    "checkpoint142_openai_manual_support.py",
    "stage81_real_eval.py",
)
_ARTIFACT_CONTRACT = MANUAL_EVALUATION_ARTIFACT_CONTRACT


class Checkpoint143ABAttempt2ConfigurationError(RuntimeError):
    """Reject plan or runtime drift before paid provider I/O."""


@dataclass(frozen=True, slots=True)
class CellSpec:
    cell_id: str
    role: str
    policy_id: str
    policy_schema_version: int
    manifest_schema_version: int
    delivery_schema_version: int
    presence_schema_version: int
    session_prefix: str


CONTROL = CellSpec(
    cell_id="control",
    role="historical_v27",
    policy_id=BEHAVIOR_POLICY_V27.policy_id,
    policy_schema_version=27,
    manifest_schema_version=CONTEXT_MANIFEST_SCHEMA_VERSION,
    delivery_schema_version=4,
    presence_schema_version=2,
    session_prefix="checkpoint143-control-v27-replica",
)
TREATMENT = CellSpec(
    cell_id="treatment",
    role="character_agency_v28",
    policy_id=BEHAVIOR_POLICY_V28.policy_id,
    policy_schema_version=28,
    manifest_schema_version=CONTEXT_MANIFEST_V17_SCHEMA_VERSION,
    delivery_schema_version=5,
    presence_schema_version=3,
    session_prefix="checkpoint143-treatment-v28-replica",
)
CELLS = (CONTROL, TREATMENT)


def _execution_source_fingerprint() -> dict[str, Any]:
    return execution_source_fingerprint(evaluator_names=_EVALUATOR_SOURCE_BUNDLE)


def _human_review_contract() -> dict[str, Any]:
    contract = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "judge": "human_only_two_phase",
        "automated_text_judging": False,
        "phase_1": {
            "labels_hidden": True,
            "balanced_runtime_random_assignment": True,
            "assignment_absent_from_blind_artifact": True,
            "pair_boolean_dimensions": list(PAIR_REVIEW_DIMENSIONS),
            "pair_preference": ["left", "right", "tie"],
            "all_pair_dimensions_applicable": True,
        },
        "phase_2": {
            "runs_only_after_phase_1_is_frozen": True,
            "treatment_decision_revealed": True,
            "treatment_boolean_dimensions": list(TREATMENT_REALIZATION_DIMENSIONS),
            "all_treatment_dimensions_applicable": True,
        },
        "hard_review_dimensions": list(HARD_REVIEW_DIMENSIONS),
        "cross_session_boolean_dimensions": list(CROSS_SESSION_DIMENSIONS),
        "acceptance": {
            "hard_grounding_and_completeness": "all_applicable_replies",
            "treatment_character_dimension_minimum": "14_of_18_all_applicable",
            "treatment_character_dimension_minimum_count": 14,
            "typed_agency_realization_minimum_count": 14,
            "agency_source_truth_and_cognition_boundary": "all_18_treatment_replies",
            "treatment_blind_pair_wins_minimum": 12,
            "treatment_blind_pair_losses_maximum": 3,
            "cross_session_dimensions": "all_true",
        },
        "exact_phrase_matching": False,
        "response_rewriting": False,
        "fixed_blind_template_path": BLIND_REVIEW_TEMPLATE_RELATIVE_PATH,
        "fixed_phase_1_review_path": PHASE_1_REVIEW_RELATIVE_PATH,
        "fixed_phase_2_template_path": PHASE_2_REVIEW_TEMPLATE_RELATIVE_PATH,
        "fixed_review_artifact_path": REVIEW_RELATIVE_PATH,
    }
    contract["contract_digest"] = content_digest(contract)
    return contract


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    source_fingerprint: Mapping[str, Any] = field(default_factory=_execution_source_fingerprint)

    def public_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "checkpoint": "14.3",
            "purpose": "paired_v27_v28_character_agency_production_ab_attempt2",
            "provider": EXPECTED_PROVIDER.value,
            "model": EXPECTED_MODEL,
            "reasoning_effort": EXPECTED_REASONING_EFFORT.value,
            "reasoning_token_allowance": EXPECTED_REASONING_ALLOWANCE,
            "fresh_replica_count_per_cell": EXPECTED_REPLICA_COUNT,
            "turns_per_replica": len(PUBLIC_TURNS),
            "required_base_calls_per_cell": REQUIRED_BASE_CALLS_PER_CELL,
            "required_base_calls": REQUIRED_BASE_CALLS_PER_CELL * len(CELLS),
            "maximum_provider_calls_per_cell": MAXIMUM_PROVIDER_CALLS_PER_CELL,
            "maximum_provider_calls": MAXIMUM_PROVIDER_CALLS,
            "maximum_cost_usd_per_cell": MAXIMUM_COST_USD_PER_CELL,
            "maximum_cost_usd": MAXIMUM_COST_USD,
            "maximum_attempts_per_turn": MAX_ATTEMPTS_PER_TURN,
            "cells": [
                {
                    "cell_id": cell.cell_id,
                    "role": cell.role,
                    "policy_id": cell.policy_id,
                    "policy_schema_version": cell.policy_schema_version,
                    "manifest_schema_version": cell.manifest_schema_version,
                    "provider_request_context_schema_version": (
                        EXPECTED_PROVIDER_CONTEXT_SCHEMA_VERSION
                    ),
                    "character_delivery_decision_schema_version": cell.delivery_schema_version,
                    "character_presence_projection_schema_version": cell.presence_schema_version,
                    "fresh_database_per_replica": True,
                }
                for cell in CELLS
            ],
            "comparison_controls": {
                "same_provider_model_reasoning": True,
                "same_public_turns_and_order": True,
                "same_canonical_seed_and_empty_user_history": True,
                "same_application_and_wire_limits": True,
                "only_declared_policy_cell_differs": True,
            },
            "authorization_contract": {
                "authorization_id": AUTHORIZATION_ID,
                "one_shot": True,
                "claim_must_precede_settings_report_and_provider_io": True,
                "claim_path": CLAIM_RELATIVE_PATH,
                "report_path": REPORT_RELATIVE_PATH,
            },
            "foreground_request_contract": {
                "endpoint": "/responses",
                "service_tier": "default",
                "prompt_cache_mode": "explicit",
                "expected_cache_reads": 0,
                "expected_cache_writes": 0,
                "store": False,
                "tools": "none",
                "provider_conversation_state": "none",
            },
            "affect_contract": manual_affect_contract(),
            "selected_usage_contract": manual_selected_usage_contract(),
            "settings": public_settings_contract(_EXPECTED_SETTINGS),
            "application_limits": {
                "maximum_input_chars": 8000,
                "maximum_context_chars": 12_000,
                "maximum_response_chars": 12_000,
                "recent_turns": 8,
                "recent_chars": 6000,
                "visible_output_token_ceiling": 768,
                "provider_output_token_ceiling": 1792,
                "expected_turn_visible_output_token_limits": list(
                    EXPECTED_TURN_VISIBLE_OUTPUT_TOKEN_LIMITS
                ),
                "expected_turn_temperatures": list(EXPECTED_TURN_TEMPERATURES),
            },
            "turns": [dict(turn) for turn in PUBLIC_TURNS],
            "human_review_contract": _human_review_contract(),
            "source_fingerprint": copy.deepcopy(dict(self.source_fingerprint)),
        }

    @property
    def digest(self) -> str:
        return content_digest(self.public_mapping())


def inspect_plan() -> dict[str, Any]:
    plan = ExecutionPlan()
    return {
        **plan.public_mapping(),
        "mode": "inspect_only",
        "network_attempted": False,
        "execution_plan_digest": plan.digest,
        "paid_execution": {
            "status": "awaiting_exact_authorization",
            "available": True,
            "authorization_must_repeat_exact_id_digest_calls_and_cost": True,
        },
    }


class CellLedger(AtomicOpenAICallLedger):
    """Bind each independent cell budget to its public session and exact request vectors."""

    __slots__ = ("cell",)

    def __init__(self, *, cell: CellSpec) -> None:
        super().__init__(
            maximum_calls=MAXIMUM_PROVIDER_CALLS_PER_CELL,
            maximum_cost_usd=MAXIMUM_COST_USD_PER_CELL,
            required_base_calls=REQUIRED_BASE_CALLS_PER_CELL,
            reasoning_token_allowance=EXPECTED_REASONING_ALLOWANCE,
            expected_context_schema_version=EXPECTED_PROVIDER_CONTEXT_SCHEMA_VERSION,
        )
        self.cell = cell

    def _temperature_is_valid(self, temperature: float) -> bool:
        """Allow only values already bound to a public turn by this evaluator."""

        return temperature in EXPECTED_TURN_TEMPERATURES

    def reserve(self, request: ConversationProviderRequest, scope: PublicTurnScope) -> int:
        expected_sessions = {
            f"{self.cell.session_prefix}-{replica}"
            for replica in range(1, EXPECTED_REPLICA_COUNT + 1)
        }
        if (
            scope.session_id not in expected_sessions
            or not 1 <= scope.turn <= len(PUBLIC_TURNS)
            or scope.turn_id != PUBLIC_TURNS[scope.turn - 1]["id"]
            or request.parameters.temperature != EXPECTED_TURN_TEMPERATURES[scope.turn - 1]
            or request.parameters.max_output_tokens
            != EXPECTED_TURN_VISIBLE_OUTPUT_TOKEN_LIMITS[scope.turn - 1]
        ):
            raise ProviderCallBudgetExhausted(
                f"provider request drifted from the digest-bound {self.cell.cell_id} cell"
            )
        return super().reserve(request, scope)


_COMMON_SAFE_MANIFEST_KEYS = (
    "schema_version",
    "policy_id",
    "policy_schema_version",
    "character_context_schema_version",
    "included_sections",
    "response_regenerated",
    "regeneration_reason",
    "retrieval_status",
    "retrieved_memory_count",
    "relationship_expression_profile",
    "relationship_recent_strain",
    "affect_expression_profile",
    "emotion_appraisal_status",
    "emotion_appraisal_reason_code",
    "emotion_appraisal_provider",
    "emotion_appraisal_model",
    "emotion_appraisal_method",
    "emotion_appraisal_transition_prepared",
    "emotion_appraisal_provider_metrics_present",
    "disclosure_primary_mode",
    "disclosure_request_kind",
    "disclosure_facets",
    "cognition_pipeline_status",
    "cognition_intent_registry_version",
    "cognition_primary_intent",
    "cognition_intent_tags",
    "cognition_required_point_codes",
    "cognition_forbidden_claim_codes",
    "cognition_response_verbosity",
    "cognition_position_stance",
    "character_delivery_decision_schema_version",
    "character_delivery_goal",
    "character_delivery_voice",
    "character_delivery_grounding",
    "character_delivery_continuation",
    "character_delivery_pressure",
    "character_delivery_position_stance",
    "character_delivery_preserve_uncertainty",
    "character_presence_projection_schema_version",
    "character_presence_personality_signals",
    "character_presence_value_signals",
    "character_presence_affect_signals",
    "character_presence_relationship_signals",
    "character_presence_memory_use_licensed",
)
_AGENCY_SAFE_MANIFEST_KEYS = (
    "character_agency_decision_schema_version",
    "character_agency_status",
    "character_agency_drive",
    "character_agency_act",
    "character_agency_subject",
    "character_agency_initiative",
    "character_agency_lead",
    "character_agency_source_personality_codes",
    "character_agency_source_value_key",
    "character_agency_reason_codes",
    "character_agency_source_ref_count",
    "character_agency_subject_ref_present",
)


def _checkpoint143_manifest(reply: SatoriReply) -> dict[str, Any]:
    """Project the complete V28 authority for validation before private refs are redacted."""

    manifest = reply.context_manifest
    raw = _sanitized_manifest(reply)
    raw.update(
        {
            "cognition_pipeline_status": manifest.cognition_pipeline_status,
            "character_agency_decision_schema_version": (
                manifest.character_agency_decision_schema_version
            ),
            "character_agency_status": manifest.character_agency_status,
            "character_agency_drive": manifest.character_agency_drive,
            "character_agency_act": manifest.character_agency_act,
            "character_agency_subject": manifest.character_agency_subject,
            "character_agency_initiative": manifest.character_agency_initiative,
            "character_agency_lead": manifest.character_agency_lead,
            "character_agency_source_personality_codes": list(
                manifest.character_agency_source_personality_codes
            ),
            "character_agency_source_value_key": manifest.character_agency_source_value_key,
            "character_agency_reason_codes": list(manifest.character_agency_reason_codes),
            "character_agency_source_refs": list(manifest.character_agency_source_refs),
            "character_agency_subject_ref": manifest.character_agency_subject_ref,
        }
    )
    return raw


def _safe_manifest(cell: CellSpec, raw: Mapping[str, Any]) -> dict[str, Any]:
    keys = _COMMON_SAFE_MANIFEST_KEYS + (_AGENCY_SAFE_MANIFEST_KEYS if cell is TREATMENT else ())
    safe = {key: raw.get(key) for key in keys}
    raw_source_refs = raw.get("character_agency_source_refs")
    raw_subject_ref = raw.get("character_agency_subject_ref")
    raw_agency_refs_present = "character_agency_source_refs" in raw
    if (
        safe["schema_version"] != cell.manifest_schema_version
        or safe["policy_id"] != cell.policy_id
        or safe["policy_schema_version"] != cell.policy_schema_version
        or safe["character_context_schema_version"] != RUNTIME_CHARACTER_CONTEXT_SCHEMA_VERSION
        or safe["character_delivery_decision_schema_version"] != cell.delivery_schema_version
        or safe["character_presence_projection_schema_version"] != cell.presence_schema_version
    ):
        raise RuntimeError(f"production composition drifted from the {cell.cell_id} schemas")
    included = safe["included_sections"]
    required = {
        "behavior_policy",
        "self_model",
        "personality_expression",
        "values",
        "relationship_expression_state",
        "emotional_expression_state",
        "character_delivery_decision",
        "character_presence_projection",
        "current_user_input",
    }
    if cell is TREATMENT:
        required.add("character_agency_decision")
    if cell is CONTROL and isinstance(included, list) and "character_agency_decision" in included:
        raise RuntimeError("historical control inherited V28 agency authority")
    if (
        not isinstance(included, list)
        or len(included) != len(set(included))
        or not required <= set(included)
        or not set(included) <= set(CONVERSATION_INCLUDED_SECTIONS)
        or included
        != [section for section in CONVERSATION_INCLUDED_SECTIONS if section in included]
    ):
        raise RuntimeError(f"production composition emitted invalid {cell.cell_id} sections")
    if cell is TREATMENT and (
        safe["character_agency_decision_schema_version"] != 1
        or safe["character_agency_status"] not in {"applied", "fallback"}
        or not all(
            isinstance(safe[key], str) and bool(safe[key])
            for key in (
                "character_agency_drive",
                "character_agency_act",
                "character_agency_subject",
                "character_agency_initiative",
                "character_agency_lead",
                "character_agency_source_value_key",
            )
        )
        or not all(
            isinstance(safe[key], list) and bool(safe[key])
            for key in (
                "character_agency_source_personality_codes",
                "character_agency_reason_codes",
            )
        )
    ):
        raise RuntimeError("treatment omitted its complete typed agency decision")
    if cell is TREATMENT:
        if raw_agency_refs_present:
            if (
                not isinstance(raw_source_refs, list)
                or not 1 <= len(raw_source_refs) <= 4
                or any(not isinstance(item, str) or not item for item in raw_source_refs)
                or len(raw_source_refs) != len(set(raw_source_refs))
                or (raw_subject_ref is not None and raw_subject_ref not in raw_source_refs)
            ):
                raise RuntimeError("treatment emitted invalid private agency provenance")
            safe["character_agency_source_ref_count"] = len(raw_source_refs)
            safe["character_agency_subject_ref_present"] = raw_subject_ref is not None
        elif (
            type(safe["character_agency_source_ref_count"]) is not int
            or not 1 <= safe["character_agency_source_ref_count"] <= 4
            or type(safe["character_agency_subject_ref_present"]) is not bool
            or "character_agency_subject_ref" in raw
        ):
            raise RuntimeError("stored treatment agency provenance summary is invalid")
        expected_subject_ref = safe["character_agency_subject"] in {
            "canonical_position",
            "canonical_inclination",
        }
        if safe["character_agency_subject_ref_present"] is not expected_subject_ref:
            raise RuntimeError("treatment subject provenance summary is inconsistent")
    if (
        safe["emotion_appraisal_provider"] != "ollama"
        or safe["emotion_appraisal_model"] != "qwen3:4b-instruct"
        or safe["emotion_appraisal_method"] != "ollama.categorical_affective_appraisal.v2"
        or safe["emotion_appraisal_provider_metrics_present"] is not True
    ):
        raise RuntimeError("production turn omitted exact local affect evidence")
    outcome = {
        "status": safe["emotion_appraisal_status"],
        "reason_code": safe["emotion_appraisal_reason_code"],
        "transition_prepared": safe["emotion_appraisal_transition_prepared"],
    }
    if not any(
        strict_json_equal(outcome, allowed)
        for allowed in manual_affect_contract()["accepted_outcomes"]
    ):
        raise RuntimeError("production turn emitted invalid affect evidence")
    regenerated = safe["response_regenerated"]
    reason = safe["regeneration_reason"]
    if (
        type(regenerated) is not bool
        or (
            reason is not None and reason not in {item.value for item in ResponseRegenerationReason}
        )
        or (regenerated and reason is None)
    ):
        raise RuntimeError("production turn emitted invalid regeneration metadata")
    return safe


def _safe_manifest_for(cell: CellSpec) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    def sanitize(raw: Mapping[str, Any]) -> dict[str, Any]:
        return _safe_manifest(cell, raw)

    return sanitize


def _configuration() -> dict[str, Any]:
    return {
        "provider": EXPECTED_PROVIDER.value,
        "model": EXPECTED_MODEL,
        "reasoning_effort": EXPECTED_REASONING_EFFORT.value,
        "reasoning_token_allowance": EXPECTED_REASONING_ALLOWANCE,
        "turn_visible_output_token_limits": list(EXPECTED_TURN_VISIBLE_OUTPUT_TOKEN_LIMITS),
        "application_state_scope": "fresh_disposable_database_per_cell_replica",
        "derived_processing": "none",
        "store": False,
        "tools": "none",
        "provider_conversation_state": "none",
        "prompt_cache_mode": "explicit",
        "expected_cache_reads": 0,
        "expected_cache_writes": 0,
        "settings": public_settings_contract(_EXPECTED_SETTINGS),
    }


def _preflight_shape(
    *,
    execute: bool,
    authorization_id: str | None,
    maximum_provider_calls: int | None,
    maximum_cost_usd: float | None,
    authorized_plan_digest: str | None,
) -> None:
    plan = ExecutionPlan()
    source = plan.source_fingerprint
    if (
        source.get("installed_wheel_parity") is not True
        or source.get("installed_runtime_is_separate") is not True
        or source.get("fingerprint_digest")
        != content_digest(
            {key: value for key, value in source.items() if key != "fingerprint_digest"}
        )
    ):
        raise Checkpoint143ABAttempt2ConfigurationError(
            "installed wheel/source parity and source fingerprint integrity are required"
        )
    if not execute:
        raise Checkpoint143ABAttempt2ConfigurationError("paid execution requires --execute")
    if authorization_id != AUTHORIZATION_ID:
        raise Checkpoint143ABAttempt2ConfigurationError(
            "authorization ID does not match the fixed grant"
        )
    if type(maximum_provider_calls) is not int or maximum_provider_calls != MAXIMUM_PROVIDER_CALLS:
        raise Checkpoint143ABAttempt2ConfigurationError(
            f"provider-call ceiling must equal {MAXIMUM_PROVIDER_CALLS}"
        )
    if (
        isinstance(maximum_cost_usd, bool)
        or not isinstance(maximum_cost_usd, (int, float))
        or not math.isfinite(maximum_cost_usd)
        or maximum_cost_usd != MAXIMUM_COST_USD
    ):
        raise Checkpoint143ABAttempt2ConfigurationError(
            f"USD ceiling must equal ${MAXIMUM_COST_USD:.2f}"
        )
    if not isinstance(authorized_plan_digest, str) or authorized_plan_digest != plan.digest:
        raise Checkpoint143ABAttempt2ConfigurationError(
            "authorized digest does not match the exact plan"
        )


def _validate_settings(settings: Settings) -> None:
    drift = [
        key for key, expected in _EXPECTED_SETTINGS.items() if getattr(settings, key) != expected
    ]
    if drift:
        raise Checkpoint143ABAttempt2ConfigurationError(
            "runtime settings drift from the digest-bound plan: " + ", ".join(drift)
        )
    if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value().strip():
        raise Checkpoint143ABAttempt2ConfigurationError("OpenAI API key is not configured")


def _review_template(report: Mapping[str, Any]) -> dict[str, Any]:
    assignments = _validated_blind_assignments(report)
    cells = cast(Mapping[str, Mapping[str, Any]], report["cells"])
    control_sessions = cast(list[dict[str, Any]], cells[CONTROL.cell_id]["sessions"])
    treatment_sessions = cast(list[dict[str, Any]], cells[TREATMENT.cell_id]["sessions"])
    pairs: list[dict[str, Any]] = []
    for replica, (control_session, treatment_session) in enumerate(
        zip(control_sessions, treatment_sessions, strict=True), start=1
    ):
        for control_turn, treatment_turn in zip(
            control_session["turns"], treatment_session["turns"], strict=True
        ):
            pair_id = f"replica-{replica}-turn-{control_turn['turn']}"
            treatment_left = assignments[pair_id] == "left"
            left = treatment_turn if treatment_left else control_turn
            right = control_turn if treatment_left else treatment_turn
            pairs.append(
                {
                    "pair_id": pair_id,
                    "turn_id": control_turn["turn_id"],
                    "user_text": PUBLIC_TURNS[control_turn["turn"] - 1]["user_text"],
                    "left_reply": left["reply"],
                    "right_reply": right["reply"],
                    "phase_1": {
                        "left_dimensions": {
                            dimension: None for dimension in PAIR_REVIEW_DIMENSIONS
                        },
                        "right_dimensions": {
                            dimension: None for dimension in PAIR_REVIEW_DIMENSIONS
                        },
                        "preference": None,
                    },
                }
            )
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "artifact_id": report["artifact_id"],
        "sample_digest": report["sample_digest"],
        "execution_plan_digest": report["execution_plan_digest"],
        "pairs": pairs,
        "reviewer_attestation": {
            "exact_public_sample_reviewed": None,
            "no_automated_text_judge_used": None,
            "no_response_rewriting_performed": None,
        },
        "content_digest": None,
    }


def _pair_ids() -> tuple[str, ...]:
    return tuple(
        f"replica-{replica}-turn-{turn['turn']}"
        for replica in range(1, EXPECTED_REPLICA_COUNT + 1)
        for turn in PUBLIC_TURNS
    )


def _new_blind_assignments() -> dict[str, str]:
    pair_ids = _pair_ids()
    if len(pair_ids) % 2:
        raise RuntimeError("balanced blind assignment requires an even pair count")
    sides = ["left"] * (len(pair_ids) // 2) + ["right"] * (len(pair_ids) // 2)
    secrets.SystemRandom().shuffle(sides)
    return dict(zip(pair_ids, sides, strict=True))


def _validated_blind_assignments(report: Mapping[str, Any]) -> dict[str, str]:
    raw = report.get("blind_assignments")
    nonce = report.get("blind_assignment_nonce")
    expected = set(_pair_ids())
    if (
        not isinstance(nonce, str)
        or len(nonce) != 64
        or any(character not in "0123456789abcdef" for character in nonce)
        or not isinstance(raw, Mapping)
        or set(raw) != expected
        or any(side not in {"left", "right"} for side in raw.values())
        or sum(side == "left" for side in raw.values()) != len(expected) // 2
        or sum(side == "right" for side in raw.values()) != len(expected) // 2
    ):
        raise ValueError("completed report has an invalid blind assignment map")
    return {str(pair_id): cast(str, side) for pair_id, side in raw.items()}


def _sample_payload(report: Mapping[str, Any]) -> dict[str, Any]:
    excluded = {
        "recorded_at",
        "completed_at",
        "sample_digest",
        "blind_review_template_path",
        "blind_review_template_digest",
    }
    return {key: value for key, value in report.items() if key not in excluded}


def _validate_completed_report_for_review(report: Mapping[str, Any]) -> None:
    if (
        report.get("status") != "completed_awaiting_human_review"
        or report.get("execution_plan_digest") != ExecutionPlan().digest
        or report.get("sample_digest") != content_digest(_sample_payload(report))
        or unsafe_artifact_paths(report)
    ):
        raise ValueError("completed report identity, digest or privacy drift")
    source = report.get("source_fingerprint")
    if (
        not isinstance(source, Mapping)
        or source.get("installed_wheel_parity") is not True
        or source.get("installed_runtime_is_separate") is not True
        or source.get("fingerprint_digest")
        != content_digest(
            {key: value for key, value in source.items() if key != "fingerprint_digest"}
        )
    ):
        raise ValueError("completed report source fingerprint drift")
    cells = report.get("cells")
    if not isinstance(cells, Mapping) or set(cells) != {cell.cell_id for cell in CELLS}:
        raise ValueError("completed report cell set drift")
    for cell in CELLS:
        cell_report = cells.get(cell.cell_id)
        if not isinstance(cell_report, Mapping):
            raise ValueError("completed report cell shape drift")
        sessions = cell_report.get("sessions")
        budget = cell_report.get("budget")
        if (
            cell_report.get("role") != cell.role
            or cell_report.get("policy_id") != cell.policy_id
            or not isinstance(sessions, list)
            or len(sessions) != EXPECTED_REPLICA_COUNT
            or any(
                not isinstance(session, Mapping)
                or session.get("completed") is not True
                or session.get("fresh_database") is not True
                or not isinstance(session.get("turns"), list)
                or len(cast(list[Any], session.get("turns"))) != len(PUBLIC_TURNS)
                for session in sessions
            )
            or not isinstance(budget, Mapping)
            or budget.get("base_call_count") != REQUIRED_BASE_CALLS_PER_CELL
            or budget.get("mandatory_base_calls_complete") is not True
            or budget.get("gate_valid") is not True
            or budget.get("within_call_limit") is not True
            or budget.get("within_cost_limit") is not True
            or not isinstance(budget.get("provider_call_count"), int)
            or not REQUIRED_BASE_CALLS_PER_CELL
            <= cast(int, budget.get("provider_call_count"))
            <= MAXIMUM_PROVIDER_CALLS_PER_CELL
        ):
            raise ValueError(f"completed {cell.cell_id} report or budget is not final")
    _validated_blind_assignments(report)
    expected_blind = _review_template(report)
    if report.get(
        "blind_review_template_path"
    ) != BLIND_REVIEW_TEMPLATE_RELATIVE_PATH or report.get(
        "blind_review_template_digest"
    ) != content_digest(expected_blind):
        raise ValueError("completed report blind-template binding drift")


def _phase_2_treatment_evidence(manifest: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "cognition_pipeline_status",
        "cognition_required_point_codes",
        "cognition_forbidden_claim_codes",
        "character_delivery_goal",
        "character_delivery_grounding",
        "character_delivery_continuation",
        "character_delivery_preserve_uncertainty",
        *_AGENCY_SAFE_MANIFEST_KEYS,
    )
    evidence = {key: manifest.get(key) for key in keys}
    if any(key not in manifest for key in keys):
        raise ValueError("treatment report lacks minimized decision evidence")
    return evidence


def _validate_phase_1_review(
    phase_1_review: Mapping[str, Any],
    completed_report: Mapping[str, Any],
) -> tuple[dict[str, Any], list[Mapping[str, Any]], str]:
    """Validate blind review without constructing or exposing treatment metadata."""

    _validate_completed_report_for_review(completed_report)
    expected = _review_template(completed_report)
    if (
        set(phase_1_review) != set(expected)
        or phase_1_review.get("schema_version") != REVIEW_SCHEMA_VERSION
        or phase_1_review.get("artifact_id") != expected["artifact_id"]
        or phase_1_review.get("sample_digest") != expected["sample_digest"]
        or phase_1_review.get("execution_plan_digest") != expected["execution_plan_digest"]
    ):
        raise ValueError("phase-1 review identity drift")
    supplied_pairs = phase_1_review.get("pairs")
    expected_pairs = expected["pairs"]
    if not isinstance(supplied_pairs, list) or len(supplied_pairs) != len(expected_pairs):
        raise ValueError("phase-1 review must contain every blind pair")
    for supplied, original in zip(supplied_pairs, expected_pairs, strict=True):
        if (
            not isinstance(supplied, Mapping)
            or set(supplied) != set(original)
            or any(
                supplied.get(key) != original[key]
                for key in ("pair_id", "turn_id", "user_text", "left_reply", "right_reply")
            )
        ):
            raise ValueError("phase-1 pair identity or public prose drift")
        review = supplied.get("phase_1")
        if (
            not isinstance(review, Mapping)
            or set(review) != {"left_dimensions", "right_dimensions", "preference"}
            or review.get("preference") not in {"left", "right", "tie"}
        ):
            raise ValueError("phase-1 pair preference is incomplete")
        for side in ("left_dimensions", "right_dimensions"):
            dimensions = review.get(side)
            if (
                not isinstance(dimensions, Mapping)
                or set(dimensions) != set(PAIR_REVIEW_DIMENSIONS)
                or any(type(value) is not bool for value in dimensions.values())
            ):
                raise ValueError("phase-1 pair dimensions are incomplete")
    attestations = phase_1_review.get("reviewer_attestation")
    if (
        not isinstance(attestations, Mapping)
        or set(attestations) != set(expected["reviewer_attestation"])
        or any(value is not True for value in attestations.values())
    ):
        raise ValueError("phase-1 reviewer attestations must all be true")
    supplied_digest = phase_1_review.get("content_digest")
    if supplied_digest != content_digest(
        {key: value for key, value in phase_1_review.items() if key != "content_digest"}
    ):
        raise ValueError("phase-1 review digest mismatch")
    return expected, cast(list[Mapping[str, Any]], supplied_pairs), cast(str, supplied_digest)


def build_phase_2_review_template(
    phase_1_review: Mapping[str, Any],
    completed_report: Mapping[str, Any],
) -> dict[str, Any]:
    """Reveal treatment metadata only after a complete digest-frozen blind review."""

    expected, supplied_pairs, supplied_digest = _validate_phase_1_review(
        phase_1_review, completed_report
    )
    expected_pairs = expected["pairs"]

    cells = cast(Mapping[str, Mapping[str, Any]], completed_report["cells"])
    treatment_sessions = cast(list[dict[str, Any]], cells[TREATMENT.cell_id]["sessions"])
    phase_2_pairs: list[dict[str, Any]] = []
    assignments = _validated_blind_assignments(completed_report)
    for pair, supplied in zip(expected_pairs, supplied_pairs, strict=True):
        replica_number = int(pair["pair_id"].split("-")[1])
        turn_number = int(pair["pair_id"].split("-")[3])
        treatment_turn = treatment_sessions[replica_number - 1]["turns"][turn_number - 1]
        treatment_left = assignments[pair["pair_id"]] == "left"
        phase_2_pairs.append(
            {
                "pair_id": pair["pair_id"],
                "phase_1_preference": supplied["phase_1"]["preference"],
                "treatment_side": "left" if treatment_left else "right",
                "treatment_decision_evidence": _phase_2_treatment_evidence(
                    treatment_turn["manifest"]
                ),
                "dimensions": {dimension: None for dimension in TREATMENT_REALIZATION_DIMENSIONS},
            }
        )
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "artifact_id": completed_report["artifact_id"],
        "sample_digest": completed_report["sample_digest"],
        "execution_plan_digest": completed_report["execution_plan_digest"],
        "phase_1_review_digest": supplied_digest,
        "pairs": phase_2_pairs,
        "hard_review_dimensions": {dimension: None for dimension in HARD_REVIEW_DIMENSIONS},
        "cross_session_dimensions": {dimension: None for dimension in CROSS_SESSION_DIMENSIONS},
        "reviewer_attestation": {
            "phase_1_frozen_before_treatment_reveal": True,
            "exact_treatment_decisions_reviewed": None,
            "no_automated_text_judge_used": None,
            "no_response_rewriting_performed": None,
        },
        "acceptance_summary": None,
        "accepted": None,
        "content_digest": None,
    }


def finalize_phase_2_review(
    phase_1_review: Mapping[str, Any],
    phase_2_review: Mapping[str, Any],
    completed_report: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate every human decision and compute the immutable acceptance result."""

    expected = build_phase_2_review_template(phase_1_review, completed_report)
    immutable_top_keys = {
        "schema_version",
        "artifact_id",
        "sample_digest",
        "execution_plan_digest",
        "phase_1_review_digest",
    }
    if set(phase_2_review) != set(expected) or any(
        phase_2_review.get(key) != expected[key] for key in immutable_top_keys
    ):
        raise ValueError("phase-2 review schema drift")
    supplied_pairs = phase_2_review.get("pairs")
    expected_pairs = expected["pairs"]
    if not isinstance(supplied_pairs, list) or len(supplied_pairs) != len(expected_pairs):
        raise ValueError("phase-2 review must contain every treatment pair")
    for supplied, original in zip(supplied_pairs, expected_pairs, strict=True):
        if (
            not isinstance(supplied, Mapping)
            or set(supplied) != set(original)
            or any(supplied.get(key) != original[key] for key in original if key != "dimensions")
        ):
            raise ValueError("phase-2 pair identity or decision evidence drift")
        dimensions = supplied.get("dimensions")
        if (
            not isinstance(dimensions, Mapping)
            or set(dimensions) != set(TREATMENT_REALIZATION_DIMENSIONS)
            or any(type(value) is not bool for value in dimensions.values())
        ):
            raise ValueError("phase-2 treatment dimensions are incomplete")

    def exact_boolean_dimensions(key: str, expected_names: Sequence[str]) -> dict[str, bool]:
        raw = phase_2_review.get(key)
        if (
            not isinstance(raw, Mapping)
            or set(raw) != set(expected_names)
            or any(type(value) is not bool for value in raw.values())
        ):
            raise ValueError(f"phase-2 {key} dimensions are incomplete")
        return {str(name): cast(bool, value) for name, value in raw.items()}

    hard_dimensions = exact_boolean_dimensions("hard_review_dimensions", HARD_REVIEW_DIMENSIONS)
    cross_dimensions = exact_boolean_dimensions(
        "cross_session_dimensions", CROSS_SESSION_DIMENSIONS
    )
    attestations = phase_2_review.get("reviewer_attestation")
    if (
        not isinstance(attestations, Mapping)
        or set(attestations) != set(expected["reviewer_attestation"])
        or any(value is not True for value in attestations.values())
    ):
        raise ValueError("phase-2 reviewer attestations must all be true")
    if any(
        phase_2_review.get(key) is not None
        for key in ("acceptance_summary", "accepted", "content_digest")
    ):
        raise ValueError("phase-2 computed result fields must remain empty before finalization")

    phase_1_pairs = cast(list[Mapping[str, Any]], phase_1_review["pairs"])
    hard_pair_counts = {dimension: 0 for dimension in PAIR_REVIEW_DIMENSIONS[:2]}
    treatment_character_counts = {dimension: 0 for dimension in PAIR_REVIEW_DIMENSIONS[2:]}
    wins = 0
    losses = 0
    ties = 0
    for phase_1_pair, phase_2_pair in zip(
        phase_1_pairs, cast(list[Mapping[str, Any]], supplied_pairs), strict=True
    ):
        phase_1_scores = cast(Mapping[str, Any], phase_1_pair["phase_1"])
        for side in ("left", "right"):
            side_scores = cast(Mapping[str, bool], phase_1_scores[f"{side}_dimensions"])
            for dimension in hard_pair_counts:
                hard_pair_counts[dimension] += int(side_scores[dimension])
        treatment_side = cast(str, phase_2_pair["treatment_side"])
        treatment_scores = cast(Mapping[str, bool], phase_1_scores[f"{treatment_side}_dimensions"])
        for dimension in treatment_character_counts:
            treatment_character_counts[dimension] += int(treatment_scores[dimension])
        preference = phase_1_scores["preference"]
        if preference == "tie":
            ties += 1
        elif preference == treatment_side:
            wins += 1
        else:
            losses += 1

    realization_counts = {dimension: 0 for dimension in TREATMENT_REALIZATION_DIMENSIONS}
    for pair in cast(list[Mapping[str, Any]], supplied_pairs):
        dimensions = cast(Mapping[str, bool], pair["dimensions"])
        for dimension in realization_counts:
            realization_counts[dimension] += int(dimensions[dimension])

    total_pair_sides = len(phase_1_pairs) * 2
    treatment_total = len(phase_1_pairs)
    gates = {
        "grounding_all_replies": (hard_pair_counts[PAIR_REVIEW_DIMENSIONS[0]] == total_pair_sides),
        "required_content_all_replies": (
            hard_pair_counts[PAIR_REVIEW_DIMENSIONS[1]] == total_pair_sides
        ),
        "treatment_character_each_at_least_14_of_18": all(
            count >= 14 for count in treatment_character_counts.values()
        ),
        "treatment_wins_at_least_12": wins >= 12,
        "treatment_losses_at_most_3": losses <= 3,
        "typed_agency_realized_at_least_14_of_18": (
            realization_counts["typed_agency_act_is_realized"] >= 14
        ),
        "agency_source_truth_all_18": (
            realization_counts["agency_source_and_truth_boundary_are_preserved"] == treatment_total
        ),
        "cognition_required_content_all_18": (
            realization_counts["cognition_required_content_is_preserved"] == treatment_total
        ),
        "hard_human_review_all_true": all(hard_dimensions.values()),
        "cross_session_all_true": all(cross_dimensions.values()),
        "execution_completed_under_limits": True,
    }
    summary = {
        "schema_version": 1,
        "total_reply_count": total_pair_sides,
        "treatment_reply_count": treatment_total,
        "hard_pair_pass_counts": hard_pair_counts,
        "treatment_character_pass_counts": treatment_character_counts,
        "blind_preference_counts": {"wins": wins, "losses": losses, "ties": ties},
        "treatment_realization_pass_counts": realization_counts,
        "hard_review_dimensions": hard_dimensions,
        "cross_session_dimensions": cross_dimensions,
        "gates": gates,
    }
    finalized = copy.deepcopy(dict(phase_2_review))
    finalized["acceptance_summary"] = summary
    finalized["accepted"] = all(gates.values())
    finalized["content_digest"] = content_digest(
        {key: value for key, value in finalized.items() if key != "content_digest"}
    )
    return finalized


def freeze_phase_1_and_write_phase_2(
    phase_1_review: Mapping[str, Any],
    completed_report: Mapping[str, Any],
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Durably freeze blind review before writing any treatment-revealing artifact."""

    _validate_phase_1_review(phase_1_review, completed_report)
    var_root = (repository_root() if root is None else root.resolve()) / "var"
    phase_1_writer = DurableReportWriter(
        var_root,
        PHASE_1_REVIEW_NAME,
        evaluation_label="Checkpoint 14.3 attempt-2 frozen phase-1 review",
    )
    phase_2_writer = DurableReportWriter(
        var_root,
        PHASE_2_REVIEW_TEMPLATE_NAME,
        evaluation_label="Checkpoint 14.3 attempt-2 phase-2 template",
    )
    phase_1_path = phase_1_writer.path
    phase_2_path = phase_2_writer.path
    if phase_1_path.exists() or phase_1_path.is_symlink():
        frozen_phase_1 = _read_frozen_review(phase_1_path)
        if not strict_json_equal(frozen_phase_1, phase_1_review):
            raise EvaluationArtifactSafetyError("frozen phase-1 review does not match input")
    else:
        phase_1_writer.prepare()
        phase_2_writer.prepare()
        phase_1_writer.write(phase_1_review)
        frozen_phase_1 = _read_frozen_review(phase_1_path)
    phase_2 = build_phase_2_review_template(frozen_phase_1, completed_report)
    if phase_2_path.exists() or phase_2_path.is_symlink():
        frozen_phase_2 = _read_frozen_review(phase_2_path)
        if not strict_json_equal(frozen_phase_2, phase_2):
            raise EvaluationArtifactSafetyError("frozen phase-2 template does not match input")
        return phase_2
    phase_2_writer.prepare()
    phase_2_writer.write(phase_2)
    return phase_2


def _read_frozen_review(path: Path) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise EvaluationArtifactSafetyError("required frozen review artifact is missing") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise EvaluationArtifactSafetyError("frozen review artifact must be a 0600 regular file")
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EvaluationArtifactSafetyError("frozen review artifact is not valid JSON") from error
    if not isinstance(decoded, dict) or unsafe_artifact_paths(decoded):
        raise EvaluationArtifactSafetyError("frozen review artifact violates privacy schema")
    return cast(dict[str, Any], decoded)


def finalize_and_write_review(
    phase_2_review: Mapping[str, Any],
    completed_report: Mapping[str, Any],
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Compute and durably persist the final human-only A/B verdict."""

    var_root = (repository_root() if root is None else root.resolve()) / "var"
    phase_1_review = _read_frozen_review(var_root / "evaluations" / PHASE_1_REVIEW_NAME)
    frozen_phase_2 = _read_frozen_review(var_root / "evaluations" / PHASE_2_REVIEW_TEMPLATE_NAME)
    expected_phase_2 = build_phase_2_review_template(phase_1_review, completed_report)
    if not strict_json_equal(frozen_phase_2, expected_phase_2):
        raise EvaluationArtifactSafetyError("frozen phase-2 template binding drift")
    finalized = finalize_phase_2_review(phase_1_review, phase_2_review, completed_report)
    writer = DurableReportWriter(
        var_root,
        FINAL_REVIEW_NAME,
        evaluation_label="Checkpoint 14.3 attempt-2 final review",
    )
    writer.prepare()
    writer.write(finalized)
    return finalized


def _validate_cell_report(
    *,
    cell: CellSpec,
    cell_report: Mapping[str, Any],
    ledger: CellLedger,
) -> None:
    sessions = validate_manual_evaluation_sessions(
        cell_report.get("sessions"),
        public_turns=PUBLIC_TURNS,
        expected_turn_temperatures=EXPECTED_TURN_TEMPERATURES,
        expected_turn_visible_output_token_limits=EXPECTED_TURN_VISIBLE_OUTPUT_TOKEN_LIMITS,
        expected_replica_count=EXPECTED_REPLICA_COUNT,
        public_session_prefix=cell.session_prefix,
        expected_provider=EXPECTED_PROVIDER,
        expected_model=EXPECTED_MODEL,
        expected_context_schema_version=EXPECTED_PROVIDER_CONTEXT_SCHEMA_VERSION,
        visible_output_token_ceiling=768,
        maximum_response_chars=12_000,
        safe_manifest=_safe_manifest_for(cell),
    )
    validate_exact_openai_ledger(
        ledger.snapshot(),
        sessions,
        required_base_calls=REQUIRED_BASE_CALLS_PER_CELL,
        maximum_provider_calls=MAXIMUM_PROVIDER_CALLS_PER_CELL,
        maximum_cost_usd=MAXIMUM_COST_USD_PER_CELL,
        reasoning_token_allowance=EXPECTED_REASONING_ALLOWANCE,
        visible_output_token_ceiling=768,
    )


async def run(
    *,
    execute: bool,
    authorization_id: str | None,
    maximum_provider_calls: int | None,
    maximum_cost_usd: float | None,
    authorized_plan_digest: str | None,
) -> dict[str, Any]:
    _preflight_shape(
        execute=execute,
        authorization_id=authorization_id,
        maximum_provider_calls=maximum_provider_calls,
        maximum_cost_usd=maximum_cost_usd,
        authorized_plan_digest=authorized_plan_digest,
    )
    plan = ExecutionPlan()
    if authorized_plan_digest != plan.digest:
        raise Checkpoint143ABAttempt2ConfigurationError("execution sources changed after preflight")
    root = repository_root()
    var_root = root / "var"
    try:
        metadata = var_root.lstat()
    except FileNotFoundError as error:
        raise Checkpoint143ABAttempt2ConfigurationError(
            "repository var directory is missing"
        ) from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise Checkpoint143ABAttempt2ConfigurationError("repository var path is unsafe")
    writer = DurableReportWriter(
        var_root, REPORT_NAME, evaluation_label="Checkpoint 14.3 A/B attempt 2"
    )
    writer.prepare()
    blind_writer = DurableReportWriter(
        var_root,
        BLIND_REVIEW_TEMPLATE_NAME,
        evaluation_label="Checkpoint 14.3 attempt-2 blind review template",
    )
    blind_writer.prepare()
    review_paths = (
        root / PHASE_1_REVIEW_RELATIVE_PATH,
        root / PHASE_2_REVIEW_TEMPLATE_RELATIVE_PATH,
        root / REVIEW_RELATIVE_PATH,
    )
    if any(
        path.parent != writer.path.parent or path.exists() or path.is_symlink()
        for path in review_paths
    ):
        raise Checkpoint143ABAttempt2ConfigurationError(
            "fixed review path is unsafe or already exists"
        )
    ledgers = {cell.cell_id: CellLedger(cell=cell) for cell in CELLS}
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "recorded_at": datetime.now(UTC).isoformat(),
        "checkpoint": "14.3",
        "purpose": "paired_v27_v28_character_agency_production_ab_attempt2",
        "status": "authorized_preflight",
        "artifact_id": f"satori-checkpoint143-openai-v27-v28-ab2:{plan.digest}",
        "authorization_id": AUTHORIZATION_ID,
        "execution_plan_digest": plan.digest,
        "execution_plan": plan.public_mapping(),
        "source_fingerprint": copy.deepcopy(dict(plan.source_fingerprint)),
        "artifact_contract": dict(_ARTIFACT_CONTRACT),
        "configuration": _configuration(),
        "human_review_contract": _human_review_contract(),
        "cells": {
            cell.cell_id: {
                "role": cell.role,
                "policy_id": cell.policy_id,
                "budget": ledgers[cell.cell_id].snapshot(),
                "sessions": [],
            }
            for cell in CELLS
        },
    }

    def checkpoint() -> None:
        for cell in CELLS:
            cast(dict[str, Any], report["cells"])[cell.cell_id]["budget"] = ledgers[
                cell.cell_id
            ].snapshot()
        unsafe = unsafe_artifact_paths(report)
        if unsafe:
            raise EvaluationArtifactSafetyError(
                "Checkpoint 14.3 A/B attempt 2 report contains forbidden private keys: "
                + ", ".join(unsafe)
            )
        writer.write(report)

    for ledger in ledgers.values():
        ledger.on_change = checkpoint
    acquire_one_shot_authorization_claim(
        root=var_root,
        authorization_id=cast(str, authorization_id),
        expected_authorization_id=AUTHORIZATION_ID,
        plan_digest=plan.digest,
        expected_claim_name=AUTHORIZATION_CLAIM_NAME,
        evaluation_label="Checkpoint 14.3 A/B attempt 2",
    )
    try:
        checkpoint()
        if _execution_source_fingerprint() != plan.source_fingerprint:
            raise Checkpoint143ABAttempt2ConfigurationError(
                "execution sources changed after authorization claim"
            )
        settings = Settings()
        _validate_settings(settings)
        report["status"] = "running"
        checkpoint()
        policies = {CONTROL.cell_id: BEHAVIOR_POLICY_V27, TREATMENT.cell_id: BEHAVIOR_POLICY_V28}
        with tempfile.TemporaryDirectory(
            prefix="satori-checkpoint143-openai-ab-attempt2-"
        ) as temporary:
            for cell in CELLS:
                cell_report = cast(dict[str, Any], report["cells"])[cell.cell_id]
                for replica in range(1, EXPECTED_REPLICA_COUNT + 1):
                    record = new_replica_record(session_id=f"{cell.session_prefix}-{replica}")
                    cast(list[dict[str, Any]], cell_report["sessions"]).append(record)
                    checkpoint()
                    await run_replica(
                        settings=settings,
                        database_path=Path(temporary) / f"{cell.cell_id}-{replica}.db",
                        alembic_config=root / "alembic.ini",
                        replica_number=replica,
                        ledger=ledgers[cell.cell_id],
                        checkpoint=checkpoint,
                        behavior_policy=policies[cell.cell_id],
                        public_turns=PUBLIC_TURNS,
                        public_session_prefix=cell.session_prefix,
                        expected_provider=EXPECTED_PROVIDER,
                        expected_model=EXPECTED_MODEL,
                        safe_manifest=_safe_manifest_for(cell),
                        record=record,
                        manifest_projector=_checkpoint143_manifest,
                    )
                _validate_cell_report(
                    cell=cell,
                    cell_report=cell_report,
                    ledger=ledgers[cell.cell_id],
                )
        if _execution_source_fingerprint() != plan.source_fingerprint:
            raise RuntimeError("execution sources changed during the paid A/B run")
        report["status"] = "completed_awaiting_human_review"
        report["completed_at"] = datetime.now(UTC).isoformat()
        report["blind_assignment_nonce"] = secrets.token_hex(32)
        report["blind_assignments"] = _new_blind_assignments()
        report["sample_digest"] = content_digest(
            {key: report[key] for key in report if key not in {"recorded_at", "completed_at"}}
        )
        checkpoint()
        blind_template = _review_template(report)
        blind_writer.write(blind_template)
        report["blind_review_template_path"] = BLIND_REVIEW_TEMPLATE_RELATIVE_PATH
        report["blind_review_template_digest"] = content_digest(blind_template)
        checkpoint()
        _validate_completed_report_for_review(report)
        return report
    except BaseException as error:
        report["status"] = "failed"
        report["failed_at"] = datetime.now(UTC).isoformat()
        report["failure"] = {"error_type": type(error).__name__}
        checkpoint()
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or execute Checkpoint 14.3 V27/V28 A/B attempt 2."
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--authorization-id")
    parser.add_argument("--max-provider-calls", type=int)
    parser.add_argument("--max-cost-usd", type=float)
    parser.add_argument("--authorized-plan-digest")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if not arguments.execute:
        print(json.dumps(inspect_plan(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    completed = asyncio.run(
        run(
            execute=arguments.execute,
            authorization_id=arguments.authorization_id,
            maximum_provider_calls=arguments.max_provider_calls,
            maximum_cost_usd=arguments.max_cost_usd,
            authorized_plan_digest=arguments.authorized_plan_digest,
        )
    )
    print(
        json.dumps(
            {
                "status": completed["status"],
                "report_path": REPORT_RELATIVE_PATH,
                "blind_review_template_path": BLIND_REVIEW_TEMPLATE_RELATIVE_PATH,
                "sample_digest": completed["sample_digest"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
