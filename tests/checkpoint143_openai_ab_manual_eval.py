"""Inspect-first paired V27/V28 OpenAI production evaluation for Checkpoint 14.3.

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
from satori.application.conversation.contracts import CONVERSATION_INCLUDED_SECTIONS
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
EXPECTED_TURN_VISIBLE_OUTPUT_TOKEN_LIMITS = (64, 160, 96, 112, 160, 96)
REQUIRED_BASE_CALLS_PER_CELL = EXPECTED_REPLICA_COUNT * len(PUBLIC_TURNS)
MAXIMUM_PROVIDER_CALLS_PER_CELL = 24
MAXIMUM_PROVIDER_CALLS = 48
MAXIMUM_COST_USD_PER_CELL = 0.15
MAXIMUM_COST_USD = 0.30
AUTHORIZATION_ID = "satori.checkpoint143.openai.v27-v28.ab1.2026-08-31.one-shot"
AUTHORIZATION_CLAIM_NAME = "checkpoint143-openai-v27-v28-ab1-2026-08-31.claim.json"
REPORT_NAME = "checkpoint143-openai-v27-v28-ab1-2026-08-31.json"
REPORT_RELATIVE_PATH = f"var/evaluations/{REPORT_NAME}"
BLIND_REVIEW_TEMPLATE_NAME = "checkpoint143-openai-v27-v28-ab1-2026-08-31.blind.json"
BLIND_REVIEW_TEMPLATE_RELATIVE_PATH = f"var/evaluations/{BLIND_REVIEW_TEMPLATE_NAME}"
REVIEW_RELATIVE_PATH = "var/evaluations/checkpoint143-openai-v27-v28-ab1-2026-08-31.review.json"
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
    "checkpoint143_openai_ab_manual_eval.py",
    "checkpoint142_openai_v26_ledger.py",
    "checkpoint142_openai_manual_support.py",
    "stage81_real_eval.py",
)
_ARTIFACT_CONTRACT = MANUAL_EVALUATION_ARTIFACT_CONTRACT


class Checkpoint143ABConfigurationError(RuntimeError):
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
            "pair_boolean_dimensions": list(PAIR_REVIEW_DIMENSIONS),
            "pair_preference": ["left", "right", "tie"],
        },
        "phase_2": {
            "runs_only_after_phase_1_is_frozen": True,
            "treatment_decision_revealed": True,
            "treatment_boolean_dimensions": list(TREATMENT_REALIZATION_DIMENSIONS),
        },
        "cross_session_boolean_dimensions": list(CROSS_SESSION_DIMENSIONS),
        "acceptance": {
            "hard_grounding_and_completeness": "all_applicable_replies",
            "treatment_character_dimension_minimum": "75_percent_applicable",
            "treatment_blind_pair_wins_minimum": 12,
            "treatment_blind_pair_losses_maximum": 3,
            "cross_session_dimensions": "all_true",
        },
        "exact_phrase_matching": False,
        "response_rewriting": False,
        "fixed_blind_template_path": BLIND_REVIEW_TEMPLATE_RELATIVE_PATH,
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
            "purpose": "paired_v27_v28_character_agency_production_ab",
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
    """Bind each independent cell budget to its own public session and cap vector."""

    __slots__ = ("cell",)

    def __init__(self, *, cell: CellSpec) -> None:
        super().__init__(
            maximum_calls=MAXIMUM_PROVIDER_CALLS_PER_CELL,
            maximum_cost_usd=MAXIMUM_COST_USD_PER_CELL,
            required_base_calls=REQUIRED_BASE_CALLS_PER_CELL,
            reasoning_token_allowance=EXPECTED_REASONING_ALLOWANCE,
            expected_context_schema_version=cell.manifest_schema_version,
        )
        self.cell = cell

    def reserve(self, request: ConversationProviderRequest, scope: PublicTurnScope) -> int:
        expected_sessions = {
            f"{self.cell.session_prefix}-{replica}"
            for replica in range(1, EXPECTED_REPLICA_COUNT + 1)
        }
        if (
            scope.session_id not in expected_sessions
            or not 1 <= scope.turn <= len(PUBLIC_TURNS)
            or scope.turn_id != PUBLIC_TURNS[scope.turn - 1]["id"]
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
    "character_agency_source_refs",
    "character_agency_subject_ref",
)


def _safe_manifest(cell: CellSpec, raw: Mapping[str, Any]) -> dict[str, Any]:
    keys = _COMMON_SAFE_MANIFEST_KEYS + (_AGENCY_SAFE_MANIFEST_KEYS if cell is TREATMENT else ())
    safe = {key: raw.get(key) for key in keys}
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
                "character_agency_source_refs",
            )
        )
    ):
        raise RuntimeError("treatment omitted its complete typed agency decision")
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
    if not execute:
        raise Checkpoint143ABConfigurationError("paid execution requires --execute")
    if authorization_id != AUTHORIZATION_ID:
        raise Checkpoint143ABConfigurationError("authorization ID does not match the fixed grant")
    if type(maximum_provider_calls) is not int or maximum_provider_calls != MAXIMUM_PROVIDER_CALLS:
        raise Checkpoint143ABConfigurationError(
            f"provider-call ceiling must equal {MAXIMUM_PROVIDER_CALLS}"
        )
    if (
        isinstance(maximum_cost_usd, bool)
        or not isinstance(maximum_cost_usd, (int, float))
        or not math.isfinite(maximum_cost_usd)
        or maximum_cost_usd != MAXIMUM_COST_USD
    ):
        raise Checkpoint143ABConfigurationError(f"USD ceiling must equal ${MAXIMUM_COST_USD:.2f}")
    if (
        not isinstance(authorized_plan_digest, str)
        or authorized_plan_digest != ExecutionPlan().digest
    ):
        raise Checkpoint143ABConfigurationError("authorized digest does not match the exact plan")


def _validate_settings(settings: Settings) -> None:
    drift = [
        key for key, expected in _EXPECTED_SETTINGS.items() if getattr(settings, key) != expected
    ]
    if drift:
        raise Checkpoint143ABConfigurationError(
            "runtime settings drift from the digest-bound plan: " + ", ".join(drift)
        )
    if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value().strip():
        raise Checkpoint143ABConfigurationError("OpenAI API key is not configured")


def _review_template(report: Mapping[str, Any]) -> dict[str, Any]:
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
            treatment_left = (
                int(content_digest({"sample": report["sample_digest"], "pair": pair_id})[-1], 16)
                % 2
                == 0
            )
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


def build_phase_2_review_template(
    phase_1_review: Mapping[str, Any],
    completed_report: Mapping[str, Any],
) -> dict[str, Any]:
    """Reveal treatment metadata only after a complete digest-frozen blind review."""

    expected = _review_template(completed_report)
    if (
        phase_1_review.get("schema_version") != REVIEW_SCHEMA_VERSION
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
        if not isinstance(supplied, Mapping) or any(
            supplied.get(key) != original[key]
            for key in ("pair_id", "turn_id", "user_text", "left_reply", "right_reply")
        ):
            raise ValueError("phase-1 pair identity or public prose drift")
        review = supplied.get("phase_1")
        if not isinstance(review, Mapping) or review.get("preference") not in {
            "left",
            "right",
            "tie",
        }:
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

    cells = cast(Mapping[str, Mapping[str, Any]], completed_report["cells"])
    treatment_sessions = cast(list[dict[str, Any]], cells[TREATMENT.cell_id]["sessions"])
    phase_2_pairs: list[dict[str, Any]] = []
    for pair, supplied in zip(expected_pairs, supplied_pairs, strict=True):
        replica_number = int(pair["pair_id"].split("-")[1])
        turn_number = int(pair["pair_id"].split("-")[3])
        treatment_turn = treatment_sessions[replica_number - 1]["turns"][turn_number - 1]
        treatment_left = (
            int(
                content_digest(
                    {"sample": completed_report["sample_digest"], "pair": pair["pair_id"]}
                )[-1],
                16,
            )
            % 2
            == 0
        )
        phase_2_pairs.append(
            {
                "pair_id": pair["pair_id"],
                "phase_1_preference": supplied["phase_1"]["preference"],
                "treatment_side": "left" if treatment_left else "right",
                "treatment_agency": treatment_turn["context_manifest"],
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
        "cross_session_dimensions": {dimension: None for dimension in CROSS_SESSION_DIMENSIONS},
        "reviewer_attestation": {
            "phase_1_frozen_before_treatment_reveal": True,
            "exact_treatment_decisions_reviewed": None,
            "no_automated_text_judge_used": None,
            "no_response_rewriting_performed": None,
        },
        "accepted": None,
        "content_digest": None,
    }


def _validate_cell_report(
    *,
    cell: CellSpec,
    cell_report: Mapping[str, Any],
    ledger: CellLedger,
) -> None:
    sessions = validate_manual_evaluation_sessions(
        cell_report.get("sessions"),
        public_turns=PUBLIC_TURNS,
        expected_turn_temperatures=(0.3,) * len(PUBLIC_TURNS),
        expected_turn_visible_output_token_limits=EXPECTED_TURN_VISIBLE_OUTPUT_TOKEN_LIMITS,
        expected_replica_count=EXPECTED_REPLICA_COUNT,
        public_session_prefix=cell.session_prefix,
        expected_provider=EXPECTED_PROVIDER,
        expected_model=EXPECTED_MODEL,
        expected_context_schema_version=cell.manifest_schema_version,
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
    show_replies: bool,
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
        raise Checkpoint143ABConfigurationError("execution sources changed after preflight")
    root = repository_root()
    var_root = root / "var"
    try:
        metadata = var_root.lstat()
    except FileNotFoundError as error:
        raise Checkpoint143ABConfigurationError("repository var directory is missing") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise Checkpoint143ABConfigurationError("repository var path is unsafe")
    writer = DurableReportWriter(var_root, REPORT_NAME, evaluation_label="Checkpoint 14.3 A/B")
    writer.prepare()
    blind_writer = DurableReportWriter(
        var_root,
        BLIND_REVIEW_TEMPLATE_NAME,
        evaluation_label="Checkpoint 14.3 blind review template",
    )
    blind_writer.prepare()
    review_path = root / REVIEW_RELATIVE_PATH
    if review_path.parent != writer.path.parent or review_path.exists() or review_path.is_symlink():
        raise Checkpoint143ABConfigurationError("fixed review path is unsafe or already exists")
    ledgers = {cell.cell_id: CellLedger(cell=cell) for cell in CELLS}
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "recorded_at": datetime.now(UTC).isoformat(),
        "checkpoint": "14.3",
        "purpose": "paired_v27_v28_character_agency_production_ab",
        "status": "authorized_preflight",
        "artifact_id": f"satori-checkpoint143-openai-v27-v28-ab1:{plan.digest}",
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
                "Checkpoint 14.3 A/B report contains forbidden private keys: " + ", ".join(unsafe)
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
        evaluation_label="Checkpoint 14.3 A/B",
    )
    try:
        checkpoint()
        if _execution_source_fingerprint() != plan.source_fingerprint:
            raise Checkpoint143ABConfigurationError(
                "execution sources changed after authorization claim"
            )
        settings = Settings()
        _validate_settings(settings)
        report["status"] = "running"
        checkpoint()
        policies = {CONTROL.cell_id: BEHAVIOR_POLICY_V27, TREATMENT.cell_id: BEHAVIOR_POLICY_V28}
        with tempfile.TemporaryDirectory(prefix="satori-checkpoint143-openai-ab-") as temporary:
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
        report["sample_digest"] = content_digest(
            {key: report[key] for key in report if key not in {"recorded_at", "completed_at"}}
        )
        blind_template = _review_template(report)
        blind_writer.write(blind_template)
        report["blind_review_template_path"] = BLIND_REVIEW_TEMPLATE_RELATIVE_PATH
        report["blind_review_template_digest"] = content_digest(blind_template)
        checkpoint()
        if show_replies:
            for cell in CELLS:
                sessions = cast(dict[str, Any], report["cells"])[cell.cell_id]["sessions"]
                for session in sessions:
                    for turn in session["turns"]:
                        print(
                            f"[{cell.cell_id}/{session['session_id']}/turn {turn['turn']}] "
                            f"{turn['reply']}",
                            flush=True,
                        )
        return report
    except BaseException as error:
        report["status"] = "failed"
        report["failed_at"] = datetime.now(UTC).isoformat()
        report["failure"] = {"error_type": type(error).__name__}
        checkpoint()
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect or execute Checkpoint 14.3 V27/V28 A/B.")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--authorization-id")
    parser.add_argument("--max-provider-calls", type=int)
    parser.add_argument("--max-cost-usd", type=float)
    parser.add_argument("--authorized-plan-digest")
    parser.add_argument("--show-replies", action="store_true")
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
            show_replies=arguments.show_replies,
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
