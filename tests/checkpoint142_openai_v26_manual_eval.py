"""Attempt-5 one-shot, digest-bound V26 OpenAI production evaluator.

Inspection of the frozen plan and archived evidence is offline and is the only supported mode.
Paid execution is permanently retired and fails before fingerprinting, Settings, filesystem writes
or provider I/O; no historical or new authorization can re-enable it.  The archived report contains
only public dialogue, committed replies and allowlisted content-free metadata.  The distinct
attempt-5 identity preserves all
earlier fail-closed evidence: attempt 1 stopped on settings drift, attempt 2 on a valid neutral
affect no-op and attempt 3 after one paid call when the evaluator incorrectly required transient
cache detail from the lossy committed reply instead of its authoritative atomic ledger.  Attempt 4
stopped after two paid calls because the evaluator treated conditionally rendered
``self_consistency_facets`` as mandatory even when the turn requested no Satori disclosure facets.
"""

# ruff: noqa: RUF001  # Exact Russian production phrases are intentional.

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import math
import stat
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from satori.application.cognition.contracts import PositionStance, ResponseVerbosity
from satori.application.conversation.character_delivery_contracts import (
    CHARACTER_PRESENCE_PERSONALITY_CODES,
    CHARACTER_PRESENCE_VALUE_KEYS,
    CharacterAffectSignal,
    CharacterAffectSignalCode,
    CharacterDeliveryDecision,
    CharacterDeliveryGoal,
    CharacterDeliveryVoice,
    CharacterPresenceStrength,
    CharacterRelationshipSignal,
    CharacterRelationshipSignalCode,
    validate_affect_presence_semantics,
    validate_relationship_presence_semantics,
)
from satori.application.conversation.character_expression import (
    CharacterContinuationMode,
    CharacterGroundingMode,
    CharacterPressureLevel,
)
from satori.application.conversation.context import (
    CONTEXT_MANIFEST_SCHEMA_VERSION,
    RUNTIME_CHARACTER_CONTEXT_SCHEMA_VERSION,
)
from satori.application.conversation.contracts import CONVERSATION_INCLUDED_SECTIONS
from satori.application.conversation.disclosure_contracts import (
    ConversationalDisclosureMode,
    DisclosureFacet,
    DisclosureRequestKind,
)
from satori.application.conversation.policy import BEHAVIOR_POLICY_V26
from satori.application.conversation.response_validation import ResponseRegenerationReason
from satori.config import (
    ConversationProviderKind,
    EmbeddingProviderKind,
    OpenAIReasoningEffort,
    Settings,
)
from tests.checkpoint142_openai_manual_support import (
    APPLIED_AFFECT_REASON_CODE,
    NEUTRAL_AFFECT_REASON_CODE,
    DurableReportWriter,
    EvaluationArtifactSafetyError,
    acquire_one_shot_authorization_claim,
    content_digest,
    execution_source_fingerprint,
    new_replica_record,
    repository_root,
    run_replica,
    unsafe_artifact_paths,
)
from tests.checkpoint142_openai_v26_ledger import (
    MAX_ATTEMPTS_PER_TURN,
    NANO_USD_PER_USD,
    OPENAI_CACHE_WRITE_INPUT_NANO_USD_PER_TOKEN,
    OPENAI_CACHED_INPUT_NANO_USD_PER_TOKEN,
    OPENAI_LONG_CONTEXT_INPUT_TOKEN_THRESHOLD,
    OPENAI_OUTPUT_NANO_USD_PER_TOKEN,
    OPENAI_UNCACHED_INPUT_NANO_USD_PER_TOKEN,
    PRICING_SNAPSHOT,
    V26AtomicOpenAICallLedger,
)

REPORT_SCHEMA_VERSION = 4
REVIEW_SCHEMA_VERSION = 1
EXPECTED_POLICY_ID = "satori.conversation.behavior.v26"
EXPECTED_PROVIDER = ConversationProviderKind.OPENAI
EXPECTED_MODEL = "gpt-5.6-terra"
EXPECTED_REASONING_EFFORT = OpenAIReasoningEffort.MEDIUM
EXPECTED_REASONING_ALLOWANCE = 1024
EXPECTED_REPLICA_COUNT = 3
EXPECTED_MAX_INPUT_CHARS = 8000
EXPECTED_MAX_CONTEXT_CHARS = 12_000
EXPECTED_MAX_RESPONSE_CHARS = 12_000
EXPECTED_RECENT_TURNS = 8
EXPECTED_RECENT_CHARS = 6000
EXPECTED_VISIBLE_OUTPUT_TOKEN_CEILING = 768
MAXIMUM_PROVIDER_CALLS = 30
MAXIMUM_COST_USD = 0.15
AUTHORIZATION_ID = "satori.checkpoint142.openai.v26.phase1.attempt5.2026-08-29.one-shot"
AUTHORIZATION_CLAIM_NAME = "checkpoint142-openai-v26-phase1-attempt5-2026-08-29.claim.json"
REPORT_NAME = "checkpoint142-openai-v26-phase1-attempt5-2026-08-29.json"
REPORT_RELATIVE_PATH = f"var/evaluations/{REPORT_NAME}"
REVIEW_RELATIVE_PATH = (
    "var/evaluations/checkpoint142-openai-v26-phase1-attempt5-2026-08-29.review.json"
)
CLAIM_RELATIVE_PATH = f"var/evaluation-authorizations/{AUTHORIZATION_CLAIM_NAME}"

PUBLIC_TURNS: tuple[dict[str, Any], ...] = (
    {"turn": 1, "id": "warm-greeting", "user_text": "приветик, как ты?"},
    {"turn": 2, "id": "warm-reciprocity", "user_text": "и я тебя рад видеть"},
    {
        "turn": 3,
        "id": "broad-self-disclosure",
        "user_text": (
            "слушай, а расскажи о себе, кто ты, чем увлекаешься, как себя чувствуешь вообще"
        ),
    },
    {
        "turn": 4,
        "id": "completed-hard-work",
        "user_text": "Я сегодня наконец закончил сложную часть проекта",
    },
    {
        "turn": 5,
        "id": "post-achievement-depletion",
        "user_text": "Знаешь, я почему-то почти не рад этому. Скорее просто выжат",
    },
    {
        "turn": 6,
        "id": "risk-topic-switch",
        "user_text": "Я думаю, что скорость сейчас важнее качества. Ты согласна?",
    },
    {
        "turn": 7,
        "id": "direct-disagreement",
        "user_text": "Нет, я с тобой не согласен. По-моему, ты недооцениваешь этот риск.",
    },
    {
        "turn": 8,
        "id": "topic-closure",
        "user_text": "Ну ладно, с этим разобрались.",
    },
)
REQUIRED_BASE_CALLS = EXPECTED_REPLICA_COUNT * len(PUBLIC_TURNS)

PER_TURN_HUMAN_REVIEW_DIMENSIONS = (
    "grounded_without_invented_user_or_world_facts",
    "owned_reaction_instead_of_semantic_paraphrase",
    "recognizable_satori_presence",
    "natural_delivery_without_character_checklist",
    "context_proportional_length",
    "no_generic_assistant_or_therapist_closure",
    "requested_or_required_content_is_complete",
)
CROSS_SESSION_HUMAN_REVIEW_DIMENSIONS = (
    "stable_identity_without_phrase_template",
    "meaningful_sample_variation_without_personality_reset",
    "emotional_moment_carries_only_while_contextually_relevant",
    "no_recurring_personality_card_or_missing_hobby_disclaimer",
    "independent_position_and_bounded_initiative_are_observable",
    "foreground_model_is_acceptable_for_satori_character_delivery",
)

_SAFE_MANIFEST_KEYS = (
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

_EXPECTED_SETTINGS: dict[str, object] = {
    "conversation_provider": ConversationProviderKind.OPENAI,
    "conversation_model": EXPECTED_MODEL,
    "conversation_provider_base_url": "http://127.0.0.1:11434",
    "conversation_timeout_seconds": 120.0,
    "conversation_temperature": 0.3,
    "conversation_max_output_tokens": EXPECTED_VISIBLE_OUTPUT_TOKEN_CEILING,
    "conversation_max_input_chars": EXPECTED_MAX_INPUT_CHARS,
    "conversation_max_context_chars": EXPECTED_MAX_CONTEXT_CHARS,
    "conversation_max_response_chars": EXPECTED_MAX_RESPONSE_CHARS,
    "openai_base_url": "https://api.openai.com/v1",
    "openai_reasoning_effort": EXPECTED_REASONING_EFFORT,
    "openai_reasoning_token_allowance": EXPECTED_REASONING_ALLOWANCE,
    "recent_conversation_max_turns": EXPECTED_RECENT_TURNS,
    "recent_conversation_max_chars": EXPECTED_RECENT_CHARS,
    "ollama_keep_alive": "10m",
    "ollama_serialize_inference": True,
    "ollama_background_aging_seconds": 30.0,
    "ollama_background_grace_seconds": 2.0,
    "episode_formation_provider": ConversationProviderKind.OLLAMA,
    "episode_formation_model": "qwen3:4b-instruct",
    "episode_formation_max_output_tokens": 512,
    "semantic_formation_provider": ConversationProviderKind.OLLAMA,
    "semantic_formation_model": "qwen3:4b-instruct",
    "semantic_formation_max_output_tokens": 768,
    "model_formation_provider": ConversationProviderKind.OLLAMA,
    "model_formation_model": "qwen3:4b-instruct",
    "model_formation_max_output_tokens": 512,
    "model_formation_max_source_messages": 8,
    "model_formation_max_user_claims": 2,
    "model_formation_max_world_claims": 2,
    "model_backfill_limit": 100,
    "position_formation_provider": ConversationProviderKind.OLLAMA,
    "position_formation_model": "qwen3:4b-instruct",
    "position_formation_max_output_tokens": 640,
    "position_formation_max_source_messages": 8,
    "position_formation_max_positions": 3,
    "position_backfill_limit": 100,
    "position_context_top_k": 4,
    "position_context_max_chars": 1600,
    "reflection_provider": ConversationProviderKind.OLLAMA,
    "reflection_model": "qwen3:4b-instruct",
    "reflection_provider_base_url": "http://127.0.0.1:11434",
    "reflection_timeout_seconds": 180.0,
    "reflection_max_output_tokens": 768,
    "affective_appraisal_provider": ConversationProviderKind.OLLAMA,
    "affective_appraisal_model": "qwen3:4b-instruct",
    "affective_appraisal_provider_base_url": "http://127.0.0.1:11434",
    "affective_appraisal_timeout_seconds": 120.0,
    "affective_appraisal_max_output_tokens": 96,
    "affective_appraisal_context_window": 4096,
    "relationship_appraisal_provider": ConversationProviderKind.OLLAMA,
    "relationship_appraisal_model": "qwen3:4b-instruct",
    "relationship_appraisal_provider_base_url": "http://127.0.0.1:11434",
    "relationship_appraisal_timeout_seconds": 120.0,
    "relationship_appraisal_max_output_tokens": 64,
    "relationship_appraisal_context_window": 4096,
    "semantic_max_claims_per_memory": 4,
    "semantic_max_source_memories": 6,
    "semantic_backfill_limit": 100,
    "semantic_retrieval_top_k": 4,
    "semantic_retrieval_max_context_chars": 2000,
    "embedding_provider": EmbeddingProviderKind.OLLAMA,
    "embedding_model": "embeddinggemma:300m",
    "embedding_provider_base_url": "http://127.0.0.1:11434",
    "embedding_dimensions": 768,
    "embedding_timeout_seconds": 120.0,
    "retrieval_minimum_similarity": 0.55,
    "retrieval_candidate_limit": 32,
    "retrieval_top_k": 4,
    "retrieval_max_context_chars": 2400,
    "retrieval_semantic_weight": 0.80,
    "retrieval_importance_weight": 0.10,
    "retrieval_recency_weight": 0.10,
    "retrieval_recency_half_life_days": 30.0,
    "default_counterparty_id": "local-default",
}


def _affect_contract() -> dict[str, Any]:
    """Return the one exact pre-paid affect evidence contract used by plan and report."""

    return {
        "timing": "pre_generation",
        "provider": "ollama",
        "model": "qwen3:4b-instruct",
        "endpoint": "http://127.0.0.1:11434",
        "appraisal_method": "ollama.categorical_affective_appraisal.v2",
        "accepted_outcomes": [
            {
                "status": "applied",
                "reason_code": APPLIED_AFFECT_REASON_CODE,
                "transition_prepared": True,
            },
            {
                "status": "skipped",
                "reason_code": NEUTRAL_AFFECT_REASON_CODE,
                "transition_prepared": False,
            },
        ],
        "provider_metadata_required": True,
        "provider_metrics_required": True,
        "expression_owner_snapshot_parity_required": True,
        "fallback_before_paid_foreground": False,
        "post_response_affect": "none",
    }


def _selected_usage_contract() -> dict[str, Any]:
    return {
        "source": "atomic_paid_call_ledger",
        "all_paid_attempts_require_exact_cache_aware_usage": True,
        "committed_reply_input_output_parity_required": True,
        "committed_reply_cache_breakdown_may_be_absent": True,
        "one_attempt_selects": 1,
        "successful_regeneration_selects": 2,
        "rejected_regeneration_selects": 1,
    }


_ARTIFACT_CONTRACT = {
    "contains_public_dialogue_and_replies": True,
    "retains_remote_request_content": False,
    "retains_private_application_context": False,
    "retains_secret_values": False,
    "retains_temporary_databases": False,
    "automated_text_judging_performed": False,
    "response_rewriting_performed": False,
    "provider_output_becomes_state_authority": False,
    "selected_turn_usage_source": "atomic_paid_call_ledger",
    "committed_reply_usage_is_used_only_for_selected_total_parity": True,
}

_TIMING_KEYS = {
    "intake_ms",
    "recent_context_ms",
    "relationship_projection_ms",
    "retrieval_embedding_ms",
    "retrieval_search_ranking_ms",
    "affect_materialization_ms",
    "appraisal_request_build_ms",
    "emotion_appraisal_ms",
    "cognition_planning_ms",
    "context_assembly_ms",
    "conversation_generation_ms",
    "response_regeneration_ms",
    "grounding_validation_ms",
    "canonical_commit_ms",
    "committed_reply_ms",
}
_PROVIDER_METRIC_KEYS = {
    "provider_total_ms",
    "provider_load_ms",
    "provider_prompt_eval_ms",
    "provider_eval_ms",
    "provider_prompt_tokens",
    "provider_output_tokens",
    "provider_prompt_tokens_per_second",
    "provider_output_tokens_per_second",
    "client_request_build_ms",
    "client_http_roundtrip_ms",
    "client_response_parse_ms",
    "requested_output_token_limit",
    "provider_output_token_limit",
    "reasoning_output_tokens",
    "visible_output_tokens",
}
_ATTEMPT_KEYS = {
    "attempt_number",
    "wall_ms",
    "request_schema_version",
    "context_schema_version",
    "message_count",
    "message_role_counts",
    "request_content_chars",
    "temperature",
    "max_output_tokens",
    "input_tokens",
    "output_tokens",
    "provider_metrics",
    "finish_status",
    "succeeded",
    "error_type",
}
_LEDGER_KEYS = {
    "schema_version",
    "required_base_calls",
    "maximum_provider_calls",
    "maximum_attempts_per_turn",
    "base_call_count",
    "provider_call_count",
    "successful_provider_call_count",
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "maximum_cost_nano_usd",
    "maximum_cost_usd",
    "actual_usage_cost_nano_usd",
    "actual_usage_cost_usd",
    "guarded_cost_nano_usd",
    "guarded_cost_usd",
    "usage_complete",
    "exact_pricing_evidence_complete",
    "zero_prompt_cache_verified",
    "service_tier_verified",
    "guard_projection_valid",
    "pricing",
    "within_call_limit",
    "within_cost_limit",
    "mandatory_base_calls_complete",
    "gate_valid",
    "calls",
}
_LEDGER_CALL_KEYS = {
    "call_number",
    "session_id",
    "turn",
    "turn_id",
    "attempt_kind",
    "status",
    "requested_visible_output_token_limit",
    "guarded_input_token_limit",
    "guarded_output_token_limit",
    "projected_guard_cost_nano_usd",
    "charged_guard_cost_nano_usd",
    "projected_guard_cost_usd",
    "charged_guard_cost_usd",
    "finish_status",
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "actual_cost_nano_usd",
    "actual_cost_usd",
    "input_token_details_complete",
    "zero_prompt_cache_verified",
    "standard_context_pricing_verified",
    "output_token_guard_verified",
    "finish_status_completed",
    "usage_complete",
    "guard_projection_valid",
    "service_tier_verified_by_adapter",
    "provider_metrics",
}


class V26ManualEvaluationConfigurationError(RuntimeError):
    """Reject an unsafe or non-comparable V26 run before provider I/O."""


PAID_EXECUTION_RETIRED = True
PAID_EXECUTION_RETIREMENT_REASON = (
    "v26 paid execution is retired after the completed attempt-5 human rejection; "
    "the evaluator is retained only for offline archive validation"
)
ARCHIVED_ATTEMPT5_PLAN_DIGEST = (
    "sha256:8f191667e539296266aa4bb8eacbb837559d432d3b623d6f6b5896d250369107"
)
ARCHIVED_ATTEMPT5_SOURCE_DIGEST = (
    "sha256:7160cf33961b8cb6e8443d0c371b1996ae2ff7bfe4ab4a43d69921ed79e997dc"
)
ARCHIVED_ATTEMPT5_SAMPLE_DIGEST = (
    "sha256:29b2e14acabc3b9422b410a44a6fa8c00c4780e449e9639157da73b44b62a840"
)
ARCHIVED_ATTEMPT5_REVIEW_DIGEST = (
    "sha256:6e887ec86c0e23194d4ce46eb7d67e911e9a27dfc827b02dd955c522a55ce92e"
)


def _strict_json_equal(actual: object, expected: object) -> bool:
    """Compare JSON-shaped evidence without Python's bool/int equality coercion."""

    if isinstance(expected, Mapping):
        return (
            isinstance(actual, Mapping)
            and set(actual) == set(expected)
            and all(_strict_json_equal(actual[key], value) for key, value in expected.items())
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(
                _strict_json_equal(actual_item, expected_item)
                for actual_item, expected_item in zip(actual, expected, strict=True)
            )
        )
    return type(actual) is type(expected) and actual == expected


def _public_setting(value: object) -> object:
    return value.value if hasattr(value, "value") else value


def _settings_contract() -> dict[str, object]:
    return {key: _public_setting(value) for key, value in _EXPECTED_SETTINGS.items()}


def _human_review_contract() -> dict[str, Any]:
    contract = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "judge": "human_only",
        "automated_text_judging": False,
        "per_turn_boolean_dimensions": list(PER_TURN_HUMAN_REVIEW_DIMENSIONS),
        "cross_session_boolean_dimensions": list(CROSS_SESSION_HUMAN_REVIEW_DIMENSIONS),
        "exact_phrase_matching": False,
        "response_rewriting": False,
        "accepted_only_when_every_dimension_and_attestation_is_true": True,
        "fixed_review_artifact_path": REVIEW_RELATIVE_PATH,
    }
    contract["contract_digest"] = content_digest(contract)
    return contract


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    source_fingerprint: Mapping[str, Any] = field(default_factory=execution_source_fingerprint)

    def public_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": 4,
            "checkpoint": "14.2",
            "purpose": "v26_unified_character_presence_production_gate",
            "policy_id": EXPECTED_POLICY_ID,
            "provider": EXPECTED_PROVIDER.value,
            "model": EXPECTED_MODEL,
            "reasoning_effort": EXPECTED_REASONING_EFFORT.value,
            "reasoning_token_allowance": EXPECTED_REASONING_ALLOWANCE,
            "fresh_replica_count": EXPECTED_REPLICA_COUNT,
            "turns_per_replica": len(PUBLIC_TURNS),
            "required_base_calls": REQUIRED_BASE_CALLS,
            "maximum_provider_calls": MAXIMUM_PROVIDER_CALLS,
            "maximum_cost_usd": MAXIMUM_COST_USD,
            "maximum_attempts_per_turn": MAX_ATTEMPTS_PER_TURN,
            "derived_processing": "none",
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
            "affect_contract": _affect_contract(),
            "selected_usage_contract": _selected_usage_contract(),
            "settings": _settings_contract(),
            "application_limits": {
                "maximum_input_chars": EXPECTED_MAX_INPUT_CHARS,
                "maximum_context_chars": EXPECTED_MAX_CONTEXT_CHARS,
                "maximum_response_chars": EXPECTED_MAX_RESPONSE_CHARS,
                "recent_turns": EXPECTED_RECENT_TURNS,
                "recent_chars": EXPECTED_RECENT_CHARS,
                "visible_output_token_ceiling": EXPECTED_VISIBLE_OUTPUT_TOKEN_CEILING,
                "provider_output_token_ceiling": (
                    EXPECTED_VISIBLE_OUTPUT_TOKEN_CEILING + EXPECTED_REASONING_ALLOWANCE
                ),
            },
            "source_fingerprint": copy.deepcopy(dict(self.source_fingerprint)),
            "turns": [dict(turn) for turn in PUBLIC_TURNS],
            "human_review_contract": _human_review_contract(),
        }

    @property
    def digest(self) -> str:
        return content_digest(self.public_mapping())


def execution_plan_digest() -> str:
    """Return the only completed V26 grant digest; no current tree is authorizable."""

    return ARCHIVED_ATTEMPT5_PLAN_DIGEST


def inspect_plan() -> dict[str, Any]:
    plan = ExecutionPlan()
    return {
        **plan.public_mapping(),
        "mode": "inspect_only",
        "network_attempted": False,
        "current_source_diagnostic_digest": plan.digest,
        "execution_plan_digest": ARCHIVED_ATTEMPT5_PLAN_DIGEST,
        "archived_source_fingerprint_digest": ARCHIVED_ATTEMPT5_SOURCE_DIGEST,
        "current_source_diagnostic_only": True,
        "paid_execution": {
            "status": "retired",
            "available": False,
            "historical_or_new_authorization_can_execute": False,
        },
    }


def _reject_retired_paid_execution() -> None:
    """Fail before fingerprinting, Settings, filesystem writes or provider access."""

    raise V26ManualEvaluationConfigurationError(PAID_EXECUTION_RETIREMENT_REASON)


def _preflight(
    *,
    execute: bool,
    authorization_id: str | None,
    maximum_provider_calls: int | None,
    maximum_cost_usd: float | None,
    authorized_plan_digest: str | None,
    plan: ExecutionPlan,
) -> None:
    if not execute:
        raise V26ManualEvaluationConfigurationError("paid execution requires --execute")
    if authorization_id != AUTHORIZATION_ID:
        raise V26ManualEvaluationConfigurationError(
            "authorization ID does not match the fixed one-shot V26 grant"
        )
    if authorized_plan_digest != plan.digest:
        raise V26ManualEvaluationConfigurationError(
            "authorized digest does not match the exact V26 execution plan"
        )
    if type(maximum_provider_calls) is not int or maximum_provider_calls != MAXIMUM_PROVIDER_CALLS:
        raise V26ManualEvaluationConfigurationError(
            f"provider-call ceiling must equal {MAXIMUM_PROVIDER_CALLS}"
        )
    if (
        isinstance(maximum_cost_usd, bool)
        or not isinstance(maximum_cost_usd, (int, float))
        or not math.isfinite(maximum_cost_usd)
        or maximum_cost_usd != MAXIMUM_COST_USD
    ):
        raise V26ManualEvaluationConfigurationError(
            f"USD ceiling must equal ${MAXIMUM_COST_USD:.2f}"
        )
    source = plan.source_fingerprint
    if (
        source.get("installed_wheel_parity") is not True
        or source.get("installed_runtime_is_separate") is not True
        or source.get("fingerprint_digest")
        != content_digest(
            {key: value for key, value in source.items() if key != "fingerprint_digest"}
        )
    ):
        raise V26ManualEvaluationConfigurationError(
            "installed wheel/source parity and source fingerprint integrity are required"
        )


def _validate_settings(settings: Settings) -> None:
    drift = [
        key for key, expected in _EXPECTED_SETTINGS.items() if getattr(settings, key) != expected
    ]
    if drift:
        raise V26ManualEvaluationConfigurationError(
            "runtime settings drift from the digest-bound plan: " + ", ".join(drift)
        )
    if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value().strip():
        raise V26ManualEvaluationConfigurationError("OpenAI API key is not configured")
    if BEHAVIOR_POLICY_V26.policy_id != EXPECTED_POLICY_ID:
        raise V26ManualEvaluationConfigurationError("behavior policy V26 is unavailable")


def _safe_manifest_for(
    raw: Mapping[str, Any],
    *,
    expected_policy_id: str,
    expected_policy_schema_version: int,
    expected_delivery_schema_version: int,
    expected_presence_schema_version: int,
) -> dict[str, Any]:
    """Validate the shared production manifest envelope for one exact policy schema pair."""

    safe = {key: raw.get(key) for key in _SAFE_MANIFEST_KEYS}
    if set(safe) != set(_SAFE_MANIFEST_KEYS):
        raise RuntimeError("V26 safe manifest schema drift")
    if (
        safe["policy_id"] != expected_policy_id
        or type(safe["policy_schema_version"]) is not int
        or safe["policy_schema_version"] != expected_policy_schema_version
    ):
        raise RuntimeError("production composition did not use behavior policy V26")
    if (
        type(safe["schema_version"]) is not int
        or safe["schema_version"] != CONTEXT_MANIFEST_SCHEMA_VERSION
        or type(safe["character_context_schema_version"]) is not int
        or safe["character_context_schema_version"] != RUNTIME_CHARACTER_CONTEXT_SCHEMA_VERSION
    ):
        raise RuntimeError("production composition did not use the current context schemas")
    if (
        type(safe["character_delivery_decision_schema_version"]) is not int
        or safe["character_delivery_decision_schema_version"] != expected_delivery_schema_version
    ):
        raise RuntimeError("production composition did not use delivery decision V3")
    if (
        type(safe["character_presence_projection_schema_version"]) is not int
        or safe["character_presence_projection_schema_version"] != expected_presence_schema_version
    ):
        raise RuntimeError("production composition did not use character presence V1")
    response_regenerated = safe["response_regenerated"]
    regeneration_reason = safe["regeneration_reason"]
    valid_regeneration_reasons = {reason.value for reason in ResponseRegenerationReason}
    if (
        type(response_regenerated) is not bool
        or (
            regeneration_reason is not None
            and (
                not isinstance(regeneration_reason, str)
                or regeneration_reason not in valid_regeneration_reasons
            )
        )
        or (response_regenerated and regeneration_reason is None)
    ):
        raise RuntimeError("production composition emitted invalid regeneration metadata")
    affect_outcome = {
        "status": safe["emotion_appraisal_status"],
        "reason_code": safe["emotion_appraisal_reason_code"],
        "transition_prepared": safe["emotion_appraisal_transition_prepared"],
    }
    if (
        not any(
            _strict_json_equal(affect_outcome, allowed)
            for allowed in _affect_contract()["accepted_outcomes"]
        )
        or safe["emotion_appraisal_provider"] != "ollama"
        or safe["emotion_appraisal_model"] != "qwen3:4b-instruct"
        or safe["emotion_appraisal_method"] != "ollama.categorical_affective_appraisal.v2"
        or safe["emotion_appraisal_provider_metrics_present"] is not True
    ):
        raise RuntimeError("production turn did not retain exact local affect-provider evidence")
    included = safe["included_sections"]
    raw_disclosure_facets = safe["disclosure_facets"]
    try:
        if (
            not isinstance(raw_disclosure_facets, list)
            or len(raw_disclosure_facets) != len(set(raw_disclosure_facets))
            or any(not isinstance(item, str) for item in raw_disclosure_facets)
        ):
            raise ValueError("disclosure facets must be unique strings")
        disclosure_facets = tuple(DisclosureFacet(item) for item in raw_disclosure_facets)
    except (TypeError, ValueError) as error:
        raise RuntimeError("production composition emitted invalid disclosure facets") from error
    required_sections = {
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
    if (
        not isinstance(included, list)
        or len(included) != len(set(included))
        or not required_sections <= set(included)
        or not set(included) <= set(CONVERSATION_INCLUDED_SECTIONS)
        or ("self_consistency_facets" in included) is not bool(disclosure_facets)
        or "cognition_response_strategy" in included
        or included
        != [section for section in CONVERSATION_INCLUDED_SECTIONS if section in included]
    ):
        raise RuntimeError("production composition emitted invalid canonical included sections")
    retrieval_status = safe["retrieval_status"]
    if retrieval_status not in {
        "not_requested",
        "retrieved",
        "no_relevant_memory",
        "unavailable",
    } or ("retrieved_episodic_memory" in included) is (retrieval_status == "not_requested"):
        raise RuntimeError("production composition emitted inconsistent retrieval metadata")
    retrieved_count = safe["retrieved_memory_count"]
    if (
        type(retrieved_count) is not int
        or retrieved_count < 0
        or (retrieval_status == "retrieved") is not (retrieved_count > 0)
    ):
        raise RuntimeError("production composition emitted inconsistent retrieval count")
    try:
        decision = CharacterDeliveryDecision(
            schema_version=safe["character_delivery_decision_schema_version"],
            goal=CharacterDeliveryGoal(cast(str, safe["character_delivery_goal"])),
            voice=CharacterDeliveryVoice(cast(str, safe["character_delivery_voice"])),
            grounding=CharacterGroundingMode(cast(str, safe["character_delivery_grounding"])),
            continuation=CharacterContinuationMode(
                cast(str, safe["character_delivery_continuation"])
            ),
            pressure=CharacterPressureLevel(cast(str, safe["character_delivery_pressure"])),
            position_stance=PositionStance(cast(str, safe["character_delivery_position_stance"])),
            preserve_uncertainty=cast(bool, safe["character_delivery_preserve_uncertainty"]),
            cognition_intent_registry_version=cast(int, safe["cognition_intent_registry_version"]),
            cognition_primary_intent=cast(str, safe["cognition_primary_intent"]),
            cognition_intent_tags=tuple(cast(list[str], safe["cognition_intent_tags"])),
            required_point_codes=tuple(cast(list[str], safe["cognition_required_point_codes"])),
            forbidden_claim_codes=tuple(cast(list[str], safe["cognition_forbidden_claim_codes"])),
            response_verbosity=ResponseVerbosity(cast(str, safe["cognition_response_verbosity"])),
            required_disclosure_facets=disclosure_facets,
        )
    except (TypeError, ValueError) as error:
        raise RuntimeError(
            "production composition emitted an invalid typed delivery decision"
        ) from error
    if decision.position_stance.value != safe["cognition_position_stance"]:
        raise RuntimeError("delivery decision did not preserve cognition position stance")
    try:
        ConversationalDisclosureMode(cast(str, safe["disclosure_primary_mode"]))
        DisclosureRequestKind(cast(str, safe["disclosure_request_kind"]))
    except ValueError as error:
        raise RuntimeError("production composition emitted invalid disclosure metadata") from error

    def parsed_signals(
        key: str,
        allowed_codes: set[str],
        *,
        allow_direction: bool = False,
    ) -> list[tuple[str, CharacterPresenceStrength]]:
        raw_signals = safe[key]
        if not isinstance(raw_signals, list) or not raw_signals or len(raw_signals) > 3:
            raise RuntimeError(f"production composition emitted invalid {key}")
        parsed: list[tuple[str, CharacterPresenceStrength]] = []
        for signal in raw_signals:
            if not isinstance(signal, str):
                raise RuntimeError(f"production composition emitted invalid {key}")
            parts = signal.split(":")
            if len(parts) not in ({2, 3} if allow_direction else {2}):
                raise RuntimeError(f"production composition emitted invalid {key}")
            code, level, *direction = parts
            if code not in allowed_codes or (
                direction and direction[0] not in {"slightly_stronger", "slightly_softer"}
            ):
                raise RuntimeError(f"production composition emitted invalid {key}")
            try:
                strength = CharacterPresenceStrength(level)
            except ValueError as error:
                raise RuntimeError(f"production composition emitted invalid {key}") from error
            parsed.append((code, strength))
        if len({code for code, _ in parsed}) != len(parsed):
            raise RuntimeError(f"production composition emitted duplicate {key} codes")
        return parsed

    parsed_signals(
        "character_presence_personality_signals",
        set(CHARACTER_PRESENCE_PERSONALITY_CODES),
        allow_direction=True,
    )
    parsed_signals(
        "character_presence_value_signals",
        set(CHARACTER_PRESENCE_VALUE_KEYS),
    )
    if (
        expected_presence_schema_version >= 2
        and len(cast(list[str], safe["character_presence_value_signals"])) != 1
    ):
        raise RuntimeError("character presence V2 requires exactly one value guard")
    affect_signals = tuple(
        CharacterAffectSignal(CharacterAffectSignalCode(code), level)
        for code, level in parsed_signals(
            "character_presence_affect_signals",
            {item.value for item in CharacterAffectSignalCode},
        )
    )
    relationship_signals = tuple(
        CharacterRelationshipSignal(CharacterRelationshipSignalCode(code), level)
        for code, level in parsed_signals(
            "character_presence_relationship_signals",
            {item.value for item in CharacterRelationshipSignalCode},
        )
    )
    affect_profile = safe["affect_expression_profile"]
    relationship_profile = safe["relationship_expression_profile"]
    if affect_profile not in {
        "tense_non_hostile",
        "positive_light",
        "soft_negative_non_hostile",
        "interested_calm",
        "calm_even",
    } or relationship_profile not in {
        "fresh_undeveloped_neutral",
        "developing_neutral",
        "established_positive",
        "guarded_only_when_relationally_relevant",
    }:
        raise RuntimeError("production composition emitted invalid presence profiles")
    try:
        validate_affect_presence_semantics(cast(str, affect_profile), affect_signals)
        validate_relationship_presence_semantics(
            cast(str, relationship_profile), relationship_signals
        )
    except ValueError as error:
        raise RuntimeError(
            "production composition emitted inconsistent presence signals"
        ) from error
    has_strain = any(
        signal.code is CharacterRelationshipSignalCode.RECENT_STRAIN
        for signal in relationship_signals
    )
    if (
        type(safe["relationship_recent_strain"]) is not bool
        or safe["relationship_recent_strain"] is not has_strain
    ):
        raise RuntimeError("production relationship strain metadata is inconsistent")
    licensed = safe["character_presence_memory_use_licensed"]
    if type(licensed) is not bool:
        raise RuntimeError("production composition omitted the exact memory-use license")
    expected_license = (
        retrieval_status == "retrieved"
        and safe["character_delivery_grounding"] == "trusted_context"
    )
    if licensed is not expected_license:
        raise RuntimeError("memory-use license contradicts retrieval and grounding metadata")
    return safe


def _safe_manifest(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the frozen V26 schema pair through the shared envelope validator."""

    return _safe_manifest_for(
        raw,
        expected_policy_id=EXPECTED_POLICY_ID,
        expected_policy_schema_version=26,
        expected_delivery_schema_version=3,
        expected_presence_schema_version=1,
    )


def _configuration() -> dict[str, Any]:
    return {
        "provider": EXPECTED_PROVIDER.value,
        "model": EXPECTED_MODEL,
        "service_tier": "default",
        "reasoning_effort": EXPECTED_REASONING_EFFORT.value,
        "reasoning_token_allowance": EXPECTED_REASONING_ALLOWANCE,
        "policy_id": EXPECTED_POLICY_ID,
        "application_state_scope": "fresh_disposable_database_per_replica",
        "derived_processing": "none",
        "store": False,
        "tools": "none",
        "provider_conversation_state": "none",
        "prompt_cache_mode": "explicit",
        "expected_cache_reads": 0,
        "expected_cache_writes": 0,
        "affect_contract": _affect_contract(),
        "selected_usage_contract": _selected_usage_contract(),
        "settings": _settings_contract(),
    }


def _sample_payload(report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "artifact_id": report["artifact_id"],
        "authorization_id": report["authorization_id"],
        "execution_plan_digest": report["execution_plan_digest"],
        "execution_plan": report["execution_plan"],
        "source_fingerprint": report["source_fingerprint"],
        "artifact_contract": report["artifact_contract"],
        "configuration": report["configuration"],
        "human_review_contract": report["human_review_contract"],
        "budget": report["budget"],
        "sessions": report["sessions"],
    }


def _human_review_template(report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "artifact_id": report["artifact_id"],
        "sample_digest": report["sample_digest"],
        "execution_plan_digest": report["execution_plan_digest"],
        "session_reviews": [
            {
                "session_id": session["session_id"],
                "turns": [
                    {
                        "turn": turn["turn"],
                        "turn_id": turn["turn_id"],
                        "dimensions": {
                            dimension: None for dimension in PER_TURN_HUMAN_REVIEW_DIMENSIONS
                        },
                    }
                    for turn in session["turns"]
                ],
            }
            for session in cast(list[dict[str, Any]], report["sessions"])
        ],
        "cross_session_dimensions": {
            dimension: None for dimension in CROSS_SESSION_HUMAN_REVIEW_DIMENSIONS
        },
        "reviewer_attestation": {
            "exact_public_sample_reviewed": None,
            "no_automated_text_judge_used": None,
            "no_response_rewriting_performed": None,
        },
        "accepted": None,
        "content_digest": None,
    }


def human_review_content_digest(review: Mapping[str, Any]) -> str:
    return content_digest({key: value for key, value in review.items() if key != "content_digest"})


def _validate_provider_metrics(value: object, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or not set(value) <= _PROVIDER_METRIC_KEYS:
        raise ValueError(f"{label} provider metrics schema drift")
    if any(
        metric is not None
        and (
            isinstance(metric, bool)
            or not isinstance(metric, (int, float))
            or not math.isfinite(metric)
            or metric < 0
        )
        for metric in value.values()
    ):
        raise ValueError(f"{label} provider metrics must be finite non-negative numbers")


def validate_human_review_artifact(
    review: Mapping[str, Any], completed_report: Mapping[str, Any]
) -> bool:
    expected_top = set(_human_review_template(completed_report))
    if set(review) != expected_top:
        raise ValueError("human-review artifact schema drift")
    if (
        type(review.get("schema_version")) is not int
        or review.get("schema_version") != REVIEW_SCHEMA_VERSION
        or review.get("artifact_id") != completed_report.get("artifact_id")
        or review.get("sample_digest") != completed_report.get("sample_digest")
        or review.get("execution_plan_digest") != completed_report.get("execution_plan_digest")
    ):
        raise ValueError("human-review artifact is not bound to this completed sample")
    if review.get("content_digest") != human_review_content_digest(review):
        raise ValueError("human-review content digest mismatch")
    expected_sessions = cast(list[dict[str, Any]], completed_report["sessions"])
    session_reviews = review.get("session_reviews")
    if not isinstance(session_reviews, list) or len(session_reviews) != len(expected_sessions):
        raise ValueError("human-review session cardinality drift")
    decisions: list[bool] = []
    for actual, expected in zip(session_reviews, expected_sessions, strict=True):
        if not isinstance(actual, dict) or set(actual) != {"session_id", "turns"}:
            raise ValueError("human-review session schema drift")
        if actual.get("session_id") != expected["session_id"]:
            raise ValueError("human-review session identity drift")
        actual_turns = actual.get("turns")
        expected_turns = expected["turns"]
        if not isinstance(actual_turns, list) or len(actual_turns) != len(expected_turns):
            raise ValueError("human-review turn cardinality drift")
        for actual_turn, expected_turn in zip(actual_turns, expected_turns, strict=True):
            if not isinstance(actual_turn, dict) or set(actual_turn) != {
                "turn",
                "turn_id",
                "dimensions",
            }:
                raise ValueError("human-review turn schema drift")
            if (
                type(actual_turn.get("turn")) is not int
                or actual_turn.get("turn") != expected_turn["turn"]
                or actual_turn.get("turn_id") != expected_turn["turn_id"]
            ):
                raise ValueError("human-review turn identity drift")
            dimensions = actual_turn.get("dimensions")
            if not isinstance(dimensions, dict) or set(dimensions) != set(
                PER_TURN_HUMAN_REVIEW_DIMENSIONS
            ):
                raise ValueError("human-review turn dimensions drift")
            if any(type(value) is not bool for value in dimensions.values()):
                raise ValueError("every per-turn review dimension must be an explicit boolean")
            decisions.extend(cast(dict[str, bool], dimensions).values())
    cross = review.get("cross_session_dimensions")
    if not isinstance(cross, dict) or set(cross) != set(CROSS_SESSION_HUMAN_REVIEW_DIMENSIONS):
        raise ValueError("human-review cross-session dimensions drift")
    if any(type(value) is not bool for value in cross.values()):
        raise ValueError("every cross-session review dimension must be an explicit boolean")
    decisions.extend(cast(dict[str, bool], cross).values())
    attestation = review.get("reviewer_attestation")
    expected_attestation = {
        "exact_public_sample_reviewed",
        "no_automated_text_judge_used",
        "no_response_rewriting_performed",
    }
    if not isinstance(attestation, dict) or set(attestation) != expected_attestation:
        raise ValueError("human-review attestation schema drift")
    if any(type(value) is not bool for value in attestation.values()):
        raise ValueError("every reviewer attestation must be an explicit boolean")
    decisions.extend(cast(dict[str, bool], attestation).values())
    accepted = review.get("accepted")
    if type(accepted) is not bool or accepted is not all(decisions):
        raise ValueError("human-review accepted flag contradicts explicit decisions")
    return accepted


def _validate_ledger(budget: Mapping[str, Any], sessions: list[dict[str, Any]]) -> None:
    if set(budget) != _LEDGER_KEYS:
        raise ValueError("budget schema drift")
    calls = budget.get("calls")
    if not isinstance(calls, list):
        raise ValueError("budget calls must be an array")
    attempt_scopes: list[tuple[str, int, str, int, Mapping[str, Any]]] = []
    for session in sessions:
        for turn in cast(list[dict[str, Any]], session["turns"]):
            attempts = cast(list[dict[str, Any]], turn["provider_attempts"])
            for index, attempt in enumerate(attempts, start=1):
                attempt_scopes.append(
                    (
                        cast(str, session["session_id"]),
                        cast(int, turn["turn"]),
                        cast(str, turn["turn_id"]),
                        index,
                        attempt,
                    )
                )
    if (
        len(calls) != len(attempt_scopes)
        or not REQUIRED_BASE_CALLS <= len(calls) <= MAXIMUM_PROVIDER_CALLS
    ):
        raise ValueError("ledger/attempt call cardinality mismatch")
    total_actual = 0
    total_guarded = 0
    total_input = 0
    total_output = 0
    for number, (call, scope) in enumerate(zip(calls, attempt_scopes, strict=True), start=1):
        if not isinstance(call, dict) or set(call) != _LEDGER_CALL_KEYS:
            raise ValueError("ledger call must be an object")
        session_id, scope_turn, turn_id, attempt_number, scope_attempt = scope
        if (
            type(call.get("call_number")) is not int
            or call.get("call_number") != number
            or call.get("session_id") != session_id
            or type(call.get("turn")) is not int
            or call.get("turn") != scope_turn
            or call.get("turn_id") != turn_id
            or call.get("attempt_kind") != ("base" if attempt_number == 1 else "validator_retry")
            or call.get("status") != "succeeded"
            or call.get("finish_status") != "completed"
            or call.get("input_token_details_complete") is not True
            or call.get("usage_complete") is not True
            or call.get("zero_prompt_cache_verified") is not True
            or call.get("standard_context_pricing_verified") is not True
            or call.get("output_token_guard_verified") is not True
            or call.get("finish_status_completed") is not True
            or call.get("guard_projection_valid") is not True
            or call.get("service_tier_verified_by_adapter") is not True
            or type(call.get("cached_input_tokens")) is not int
            or call.get("cached_input_tokens") != 0
            or type(call.get("cache_write_input_tokens")) is not int
            or call.get("cache_write_input_tokens") != 0
        ):
            raise ValueError("ledger call is not exact, completed and cache-free")
        input_tokens = call.get("input_tokens")
        output_tokens = call.get("output_tokens")
        if type(input_tokens) is not int or type(output_tokens) is not int:
            raise ValueError("ledger usage must use exact integers")
        if (
            scope_attempt.get("input_tokens") != input_tokens
            or scope_attempt.get("output_tokens") != output_tokens
        ):
            raise ValueError("ledger usage disagrees with provider-attempt evidence")
        guarded_input = call.get("guarded_input_token_limit")
        guarded_output = call.get("guarded_output_token_limit")
        max_output_tokens = scope_attempt.get("max_output_tokens")
        if (
            type(guarded_input) is not int
            or type(guarded_output) is not int
            or type(max_output_tokens) is not int
            or guarded_output != max_output_tokens + EXPECTED_REASONING_ALLOWANCE
            or input_tokens > guarded_input
            or input_tokens > OPENAI_LONG_CONTEXT_INPUT_TOKEN_THRESHOLD
            or output_tokens > guarded_output
        ):
            raise ValueError("ledger token guard was exceeded")
        projected = (
            guarded_input * OPENAI_CACHE_WRITE_INPUT_NANO_USD_PER_TOKEN
            + guarded_output * OPENAI_OUTPUT_NANO_USD_PER_TOKEN
        )
        actual = (
            input_tokens * OPENAI_UNCACHED_INPUT_NANO_USD_PER_TOKEN
            + output_tokens * OPENAI_OUTPUT_NANO_USD_PER_TOKEN
        )
        exact_cost_fields = {
            "projected_guard_cost_nano_usd": projected,
            "charged_guard_cost_nano_usd": actual,
            "actual_cost_nano_usd": actual,
            "projected_guard_cost_usd": projected / NANO_USD_PER_USD,
            "charged_guard_cost_usd": actual / NANO_USD_PER_USD,
            "actual_cost_usd": actual / NANO_USD_PER_USD,
            "requested_visible_output_token_limit": scope_attempt.get("max_output_tokens"),
        }
        if (
            any(
                not _strict_json_equal(call.get(key), value)
                for key, value in exact_cost_fields.items()
            )
            or actual > projected
        ):
            raise ValueError("ledger cost arithmetic mismatch")
        _validate_provider_metrics(call.get("provider_metrics"), "ledger call")
        total_actual += actual
        total_guarded += actual
        total_input += input_tokens
        total_output += output_tokens
    expected_pricing = {
        "currency": "USD",
        "uncached_input_nano_usd_per_token": OPENAI_UNCACHED_INPUT_NANO_USD_PER_TOKEN,
        "cached_input_nano_usd_per_token": OPENAI_CACHED_INPUT_NANO_USD_PER_TOKEN,
        "cache_write_input_nano_usd_per_token": (OPENAI_CACHE_WRITE_INPUT_NANO_USD_PER_TOKEN),
        "output_nano_usd_per_token": OPENAI_OUTPUT_NANO_USD_PER_TOKEN,
        "long_context_threshold_input_tokens": OPENAI_LONG_CONTEXT_INPUT_TOKEN_THRESHOLD,
        "service_tier": "default",
        "prompt_cache_mode": "explicit",
        "expected_cache_behavior": "no_cache_reads_or_writes",
        "snapshot": PRICING_SNAPSHOT,
        "fx_conversion_used": False,
    }
    base_count = sum(1 for call in calls if call.get("attempt_kind") == "base")
    expected_scalars = {
        "schema_version": 2,
        "required_base_calls": REQUIRED_BASE_CALLS,
        "maximum_provider_calls": MAXIMUM_PROVIDER_CALLS,
        "maximum_attempts_per_turn": MAX_ATTEMPTS_PER_TURN,
        "base_call_count": REQUIRED_BASE_CALLS,
        "provider_call_count": len(calls),
        "successful_provider_call_count": len(calls),
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "maximum_cost_nano_usd": round(MAXIMUM_COST_USD * NANO_USD_PER_USD),
        "maximum_cost_usd": MAXIMUM_COST_USD,
        "actual_usage_cost_nano_usd": total_actual,
        "actual_usage_cost_usd": total_actual / NANO_USD_PER_USD,
        "guarded_cost_nano_usd": total_guarded,
        "guarded_cost_usd": total_guarded / NANO_USD_PER_USD,
        "usage_complete": True,
        "exact_pricing_evidence_complete": True,
        "zero_prompt_cache_verified": True,
        "service_tier_verified": True,
        "guard_projection_valid": True,
        "within_call_limit": True,
        "within_cost_limit": True,
        "mandatory_base_calls_complete": True,
        "gate_valid": True,
    }
    if base_count != REQUIRED_BASE_CALLS or not _strict_json_equal(
        budget.get("pricing"), expected_pricing
    ):
        raise ValueError("ledger terminal pricing/base evidence drift")
    if any(
        not _strict_json_equal(budget.get(key), value) for key, value in expected_scalars.items()
    ):
        raise ValueError("ledger terminal scalar evidence drift")


def validate_completed_report(report: Mapping[str, Any], plan: ExecutionPlan) -> None:
    expected_top = {
        "schema_version",
        "recorded_at",
        "completed_at",
        "checkpoint",
        "purpose",
        "status",
        "artifact_id",
        "authorization_id",
        "execution_plan_digest",
        "execution_plan",
        "source_fingerprint",
        "artifact_contract",
        "configuration",
        "human_review_contract",
        "budget",
        "sessions",
        "sample_digest",
        "human_review_artifact_template",
    }
    if set(report) != expected_top:
        raise ValueError("completed V26 report schema drift")
    if unsafe_artifact_paths(report):
        raise ValueError("completed V26 report contains private artifact keys")
    if (
        type(report.get("schema_version")) is not int
        or report.get("schema_version") != REPORT_SCHEMA_VERSION
        or report.get("checkpoint") != "14.2"
        or report.get("purpose") != "v26_unified_character_presence_production_gate"
        or report.get("status") != "completed_awaiting_human_review"
        or report.get("authorization_id") != AUTHORIZATION_ID
        or report.get("execution_plan_digest") != plan.digest
        or not _strict_json_equal(report.get("execution_plan"), plan.public_mapping())
        or not _strict_json_equal(report.get("source_fingerprint"), plan.source_fingerprint)
        or not _strict_json_equal(report.get("artifact_contract"), _ARTIFACT_CONTRACT)
        or not _strict_json_equal(report.get("configuration"), _configuration())
        or not _strict_json_equal(report.get("human_review_contract"), _human_review_contract())
    ):
        raise ValueError("completed V26 report identity/configuration drift")
    if report.get("artifact_id") != f"satori-checkpoint142-openai-v26:{plan.digest}":
        raise ValueError("completed V26 artifact identity drift")
    for key in ("recorded_at", "completed_at"):
        value = report.get(key)
        if not isinstance(value, str):
            raise ValueError("report timestamps must be strings")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
            raise ValueError("report timestamps must be timezone-aware UTC")
    sessions = report.get("sessions")
    if not isinstance(sessions, list) or len(sessions) != EXPECTED_REPLICA_COUNT:
        raise ValueError("completed V26 report must contain exactly three sessions")
    for replica, session in enumerate(sessions, start=1):
        if not isinstance(session, dict) or set(session) != {
            "session_id",
            "fresh_database",
            "completed",
            "turns",
        }:
            raise ValueError("completed V26 session schema drift")
        if (
            session.get("session_id") != f"v26-character-replica-{replica}"
            or session.get("fresh_database") is not True
            or session.get("completed") is not True
        ):
            raise ValueError("completed V26 session identity/status drift")
        turns = session.get("turns")
        if not isinstance(turns, list) or len(turns) != len(PUBLIC_TURNS):
            raise ValueError("completed V26 session turn cardinality drift")
        for actual, expected in zip(turns, PUBLIC_TURNS, strict=True):
            if not isinstance(actual, dict) or set(actual) != {
                "turn",
                "turn_id",
                "user",
                "status",
                "provider_call_observed",
                "reply",
                "generation",
                "usage",
                "timings_ms",
                "provider_attempt_count",
                "provider_attempts",
                "usage_source",
                "selected_provider_attempt",
                "manifest",
            }:
                raise ValueError("completed V26 turn schema drift")
            if (
                type(actual.get("turn")) is not int
                or actual.get("turn") != expected["turn"]
                or actual.get("turn_id") != expected["id"]
                or actual.get("user") != expected["user_text"]
                or actual.get("status") != "completed"
                or actual.get("provider_call_observed") is not True
            ):
                raise ValueError("completed V26 turn identity/status drift")
            reply = actual.get("reply")
            if (
                not isinstance(reply, str)
                or not reply.strip()
                or len(reply) > EXPECTED_MAX_RESPONSE_CHARS
            ):
                raise ValueError("completed V26 reply is missing or over the character limit")
            if not _strict_json_equal(
                actual.get("generation"),
                {
                    "provider": EXPECTED_PROVIDER.value,
                    "model": EXPECTED_MODEL,
                    "finish_status": "completed",
                    "replayed": False,
                },
            ):
                raise ValueError("completed V26 generation metadata drift")
            usage = actual.get("usage")
            if (
                not isinstance(usage, dict)
                or set(usage)
                != {
                    "input_tokens",
                    "output_tokens",
                    "cached_input_tokens",
                    "cache_write_input_tokens",
                }
                or type(usage.get("input_tokens")) is not int
                or type(usage.get("output_tokens")) is not int
                or type(usage.get("cached_input_tokens")) is not int
                or usage.get("cached_input_tokens") != 0
                or type(usage.get("cache_write_input_tokens")) is not int
                or usage.get("cache_write_input_tokens") != 0
            ):
                raise ValueError("completed V26 turn usage is incomplete or cached")
            if actual.get("usage_source") != "atomic_paid_call_ledger":
                raise ValueError("completed V26 turn usage provenance drift")
            attempts = actual.get("provider_attempts")
            if (
                not isinstance(attempts, list)
                or len(attempts) not in {1, 2}
                or type(actual.get("provider_attempt_count")) is not int
                or actual.get("provider_attempt_count") != len(attempts)
                or any(
                    not isinstance(attempt, dict)
                    or type(attempt.get("attempt_number")) is not int
                    or attempt.get("attempt_number") != index
                    or attempt.get("succeeded") is not True
                    or attempt.get("finish_status") != "completed"
                    for index, attempt in enumerate(attempts, start=1)
                )
            ):
                raise ValueError("completed V26 provider-attempt evidence is invalid")
            for index, attempt in enumerate(attempts, start=1):
                assert isinstance(attempt, dict)
                role_counts = attempt.get("message_role_counts")
                if (
                    set(attempt) != _ATTEMPT_KEYS
                    or attempt.get("attempt_number") != index
                    or type(attempt.get("wall_ms")) not in {int, float}
                    or not math.isfinite(cast(float, attempt["wall_ms"]))
                    or cast(float, attempt["wall_ms"]) < 0
                    or type(attempt.get("request_schema_version")) is not int
                    or attempt.get("request_schema_version") != 1
                    or type(attempt.get("context_schema_version")) is not int
                    or attempt.get("context_schema_version")
                    != RUNTIME_CHARACTER_CONTEXT_SCHEMA_VERSION
                    or type(attempt.get("message_count")) is not int
                    or cast(int, attempt["message_count"]) < 1
                    or not isinstance(role_counts, dict)
                    or not set(role_counts) <= {"system", "developer", "user", "assistant"}
                    or any(type(count) is not int or count < 0 for count in role_counts.values())
                    or sum(cast(dict[str, int], role_counts).values()) != attempt["message_count"]
                    or type(attempt.get("request_content_chars")) is not int
                    or cast(int, attempt["request_content_chars"]) < 1
                    or attempt.get("temperature") != 0.3
                    or type(attempt.get("max_output_tokens")) is not int
                    or not 1
                    <= cast(int, attempt["max_output_tokens"])
                    <= EXPECTED_VISIBLE_OUTPUT_TOKEN_CEILING
                    or type(attempt.get("input_tokens")) is not int
                    or type(attempt.get("output_tokens")) is not int
                    or cast(int, attempt["input_tokens"]) < 0
                    or cast(int, attempt["output_tokens"]) < 0
                    or attempt.get("error_type") is not None
                ):
                    raise ValueError("completed V26 provider-attempt schema drift")
                _validate_provider_metrics(attempt.get("provider_metrics"), "provider attempt")
            timings = actual.get("timings_ms")
            if (
                not isinstance(timings, dict)
                or set(timings) != _TIMING_KEYS
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or value < 0
                    for value in timings.values()
                )
            ):
                raise ValueError("completed V26 turn timings schema drift")
            manifest = actual.get("manifest")
            if not isinstance(manifest, dict) or set(manifest) != set(_SAFE_MANIFEST_KEYS):
                raise ValueError("completed V26 manifest schema drift")
            try:
                safe_manifest = _safe_manifest(manifest)
            except RuntimeError as error:
                raise ValueError("completed V26 manifest contract violation") from error
            if not _strict_json_equal(safe_manifest, manifest):
                raise ValueError("completed V26 manifest did not round-trip safely")
            selected_attempt = actual.get("selected_provider_attempt")
            expected_selected_attempt = 2 if manifest["response_regenerated"] is True else 1
            if (
                type(selected_attempt) is not int
                or selected_attempt != expected_selected_attempt
                or not 1 <= selected_attempt <= len(attempts)
                or (manifest["regeneration_reason"] is not None) is not (len(attempts) == 2)
                or usage.get("input_tokens") != attempts[selected_attempt - 1].get("input_tokens")
                or usage.get("output_tokens") != attempts[selected_attempt - 1].get("output_tokens")
            ):
                raise ValueError("selected reply usage disagrees with its exact provider attempt")
    _validate_ledger(
        cast(Mapping[str, Any], report["budget"]), cast(list[dict[str, Any]], sessions)
    )
    if report.get("sample_digest") != content_digest(_sample_payload(report)):
        raise ValueError("completed V26 sample digest mismatch")
    if not _strict_json_equal(
        report.get("human_review_artifact_template"), _human_review_template(report)
    ):
        raise ValueError("completed V26 human-review template drift")


def _archived_attempt5_plan(report: Mapping[str, Any]) -> ExecutionPlan:
    """Reconstruct the frozen attempt-5 plan from its own digest-bound evidence."""

    raw_plan = report.get("execution_plan")
    if not isinstance(raw_plan, Mapping):
        raise ValueError("archived V26 report is missing its execution plan")
    embedded_source = raw_plan.get("source_fingerprint")
    top_level_source = report.get("source_fingerprint")
    if not isinstance(embedded_source, Mapping) or not isinstance(top_level_source, Mapping):
        raise ValueError("archived V26 report is missing its source fingerprint")
    if not _strict_json_equal(top_level_source, embedded_source):
        raise ValueError("archived V26 top-level and plan source fingerprints disagree")
    source_digest = embedded_source.get("fingerprint_digest")
    expected_source_digest = content_digest(
        {key: value for key, value in embedded_source.items() if key != "fingerprint_digest"}
    )
    if source_digest != ARCHIVED_ATTEMPT5_SOURCE_DIGEST or source_digest != expected_source_digest:
        raise ValueError("archived V26 source fingerprint digest mismatch")
    raw_plan_digest = content_digest(raw_plan)
    if (
        report.get("execution_plan_digest") != ARCHIVED_ATTEMPT5_PLAN_DIGEST
        or raw_plan_digest != ARCHIVED_ATTEMPT5_PLAN_DIGEST
    ):
        raise ValueError("archived V26 execution plan digest mismatch")
    plan = ExecutionPlan(source_fingerprint=copy.deepcopy(dict(embedded_source)))
    if plan.digest != ARCHIVED_ATTEMPT5_PLAN_DIGEST or not _strict_json_equal(
        plan.public_mapping(), raw_plan
    ):
        raise ValueError("archived V26 execution plan cannot be reconstructed exactly")
    return plan


def validate_archived_attempt5_report(report: Mapping[str, Any]) -> None:
    """Validate retained V26 evidence without comparing it to the current V27 source tree."""

    plan = _archived_attempt5_plan(report)
    validate_completed_report(report, plan)
    if report.get("sample_digest") != ARCHIVED_ATTEMPT5_SAMPLE_DIGEST:
        raise ValueError("archived V26 sample digest mismatch")


def validate_archived_attempt5_bundle(
    report: Mapping[str, Any],
    review: Mapping[str, Any],
) -> bool:
    """Validate the full frozen chain and return the historical human acceptance decision."""

    validate_archived_attempt5_report(report)
    if review.get("content_digest") != ARCHIVED_ATTEMPT5_REVIEW_DIGEST:
        raise ValueError("archived V26 review digest mismatch")
    return validate_human_review_artifact(review, report)


async def run(
    *,
    execute: bool,
    authorization_id: str | None,
    maximum_provider_calls: int | None,
    maximum_cost_usd: float | None,
    authorized_plan_digest: str | None,
    show_replies: bool,
) -> dict[str, Any]:
    _reject_retired_paid_execution()
    plan = ExecutionPlan()
    _preflight(
        execute=execute,
        authorization_id=authorization_id,
        maximum_provider_calls=maximum_provider_calls,
        maximum_cost_usd=maximum_cost_usd,
        authorized_plan_digest=authorized_plan_digest,
        plan=plan,
    )
    root = repository_root()
    var_root = root / "var"
    try:
        var_metadata = var_root.lstat()
    except FileNotFoundError as error:
        raise V26ManualEvaluationConfigurationError(
            "repository var directory is missing"
        ) from error
    if stat.S_ISLNK(var_metadata.st_mode) or not stat.S_ISDIR(var_metadata.st_mode):
        raise V26ManualEvaluationConfigurationError("repository var path is unsafe")
    writer = DurableReportWriter(var_root, REPORT_NAME)
    if writer.path.exists() or writer.path.is_symlink():
        raise V26ManualEvaluationConfigurationError("fixed V26 report already exists")
    try:
        acquire_one_shot_authorization_claim(
            root=var_root,
            authorization_id=cast(str, authorization_id),
            expected_authorization_id=AUTHORIZATION_ID,
            plan_digest=plan.digest,
            expected_claim_name=AUTHORIZATION_CLAIM_NAME,
        )
    except EvaluationArtifactSafetyError as error:
        raise V26ManualEvaluationConfigurationError(str(error)) from error

    # Recompute after the irreversible claim and before Settings/provider construction.  Any
    # source change consumes the uncertain grant but cannot result in paid work.
    if execution_source_fingerprint() != plan.source_fingerprint:
        raise V26ManualEvaluationConfigurationError(
            "execution sources changed after authorization preflight"
        )
    settings = Settings()
    _validate_settings(settings)
    ledger = V26AtomicOpenAICallLedger(
        maximum_calls=MAXIMUM_PROVIDER_CALLS,
        maximum_cost_usd=MAXIMUM_COST_USD,
        required_base_calls=REQUIRED_BASE_CALLS,
        reasoning_token_allowance=EXPECTED_REASONING_ALLOWANCE,
    )
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "recorded_at": datetime.now(UTC).isoformat(),
        "checkpoint": "14.2",
        "purpose": "v26_unified_character_presence_production_gate",
        "status": "running",
        "artifact_id": f"satori-checkpoint142-openai-v26:{plan.digest}",
        "authorization_id": AUTHORIZATION_ID,
        "execution_plan_digest": plan.digest,
        "execution_plan": plan.public_mapping(),
        "source_fingerprint": copy.deepcopy(dict(plan.source_fingerprint)),
        "artifact_contract": dict(_ARTIFACT_CONTRACT),
        "configuration": _configuration(),
        "human_review_contract": _human_review_contract(),
        "budget": ledger.snapshot(),
        "sessions": [],
    }

    def checkpoint() -> None:
        report["budget"] = ledger.snapshot()
        writer.write(report)

    ledger.on_change = checkpoint
    checkpoint()
    try:
        with tempfile.TemporaryDirectory(prefix="satori-checkpoint142-openai-v26-") as temporary:
            for replica in range(1, EXPECTED_REPLICA_COUNT + 1):
                record = new_replica_record(session_id=f"v26-character-replica-{replica}")
                cast(list[dict[str, Any]], report["sessions"]).append(record)
                checkpoint()
                await run_replica(
                    settings=settings,
                    database_path=Path(temporary) / f"replica-{replica}.db",
                    alembic_config=root / "alembic.ini",
                    replica_number=replica,
                    ledger=ledger,
                    checkpoint=checkpoint,
                    behavior_policy=BEHAVIOR_POLICY_V26,
                    public_turns=PUBLIC_TURNS,
                    public_session_prefix="v26-character-replica",
                    expected_provider=EXPECTED_PROVIDER,
                    expected_model=EXPECTED_MODEL,
                    safe_manifest=_safe_manifest,
                    record=record,
                )
        if execution_source_fingerprint() != plan.source_fingerprint:
            raise RuntimeError("execution sources changed during the paid V26 run")
        budget = ledger.snapshot()
        if budget.get("gate_valid") is not True:
            raise RuntimeError("V26 paid-call ledger did not reach an exact valid terminal state")
        report["budget"] = budget
        report["status"] = "completed_awaiting_human_review"
        report["completed_at"] = datetime.now(UTC).isoformat()
        report["sample_digest"] = content_digest(_sample_payload(report))
        report["human_review_artifact_template"] = _human_review_template(report)
        validate_completed_report(report, plan)
        checkpoint()
        if show_replies:
            for session in cast(list[dict[str, Any]], report["sessions"]):
                for turn in cast(list[dict[str, Any]], session["turns"]):
                    print(
                        f"[{session['session_id']}/turn {turn['turn']}] {turn['reply']}",
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
    parser = argparse.ArgumentParser(description="Inspect the retired one-shot V26 gate archive.")
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
    _reject_retired_paid_execution()
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
                "sample_digest": completed["sample_digest"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
