"""Inspect-first, source-bound V27 OpenAI production evaluation attempt 2.

Attempt 1 is immutable historical evidence.  This entry point has a distinct one-shot
authorization identity and artifact paths, binds the corrected V27 per-turn visible-output cap
vector, and otherwise preserves the same fresh 3 x 8 production and human-review contract.
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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from satori.application.conversation.context import (
    CONTEXT_MANIFEST_SCHEMA_VERSION,
    RUNTIME_CHARACTER_CONTEXT_SCHEMA_VERSION,
)
from satori.application.conversation.contracts import CONVERSATION_INCLUDED_SECTIONS
from satori.application.conversation.policy import BEHAVIOR_POLICY_V27
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
    validate_human_review_artifact,
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

REPORT_SCHEMA_VERSION = 1
REVIEW_SCHEMA_VERSION = 1
EXPECTED_POLICY_ID = "satori.conversation.behavior.v27"
EXPECTED_PROVIDER = ConversationProviderKind.OPENAI
EXPECTED_MODEL = "gpt-5.6-terra"
EXPECTED_REASONING_EFFORT = OpenAIReasoningEffort.MEDIUM
EXPECTED_REASONING_ALLOWANCE = 1024
EXPECTED_REPLICA_COUNT = 3
EXPECTED_DELIVERY_SCHEMA_VERSION = 4
EXPECTED_PRESENCE_SCHEMA_VERSION = 2
EXPECTED_TURN_VISIBLE_OUTPUT_TOKEN_LIMITS: tuple[int, ...] = (
    48,
    48,
    200,
    96,
    96,
    384,
    112,
    96,
)
MAXIMUM_PROVIDER_CALLS = 30
MAXIMUM_COST_USD = 0.15
RETIRED_ATTEMPT1_EXECUTION_PLAN_DIGEST = (
    "sha256:5e6bcc1fc53100e66990feb25d9448465a1a6bb1364e7b98eb6f14ddb4d94feb"
)
AUTHORIZATION_ID = "satori.checkpoint142.openai.v27.phase1.attempt2.2026-08-30.one-shot"
AUTHORIZATION_CLAIM_NAME = "checkpoint142-openai-v27-phase1-attempt2-2026-08-30.claim.json"
REPORT_NAME = "checkpoint142-openai-v27-phase1-attempt2-2026-08-30.json"
REPORT_RELATIVE_PATH = f"var/evaluations/{REPORT_NAME}"
REVIEW_RELATIVE_PATH = (
    "var/evaluations/checkpoint142-openai-v27-phase1-attempt2-2026-08-30.review.json"
)
CLAIM_RELATIVE_PATH = f"var/evaluation-authorizations/{AUTHORIZATION_CLAIM_NAME}"
REQUIRED_BASE_CALLS = EXPECTED_REPLICA_COUNT * len(PUBLIC_TURNS)
_ARTIFACT_CONTRACT = MANUAL_EVALUATION_ARTIFACT_CONTRACT

_EVALUATOR_SOURCE_BUNDLE = (
    "checkpoint142_openai_v27_attempt2_manual_eval.py",
    "checkpoint142_openai_v26_ledger.py",
    "checkpoint142_openai_manual_support.py",
    "stage81_real_eval.py",
)

_EXPECTED_SETTINGS = openai_manual_evaluation_settings(
    model=EXPECTED_MODEL,
    reasoning_effort=EXPECTED_REASONING_EFFORT,
    reasoning_token_allowance=EXPECTED_REASONING_ALLOWANCE,
    visible_output_token_ceiling=768,
)


class V27Attempt2ManualEvaluationConfigurationError(RuntimeError):
    """Reject unsafe or non-comparable attempt-2 execution before provider I/O."""


def _execution_source_fingerprint() -> dict[str, Any]:
    return execution_source_fingerprint(evaluator_names=_EVALUATOR_SOURCE_BUNDLE)


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
    source_fingerprint: Mapping[str, Any] = field(default_factory=_execution_source_fingerprint)

    def public_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "checkpoint": "14.2",
            "purpose": "v27_live_state_selected_character_movement_production_gate_attempt2",
            "policy_id": EXPECTED_POLICY_ID,
            "character_delivery_decision_schema_version": EXPECTED_DELIVERY_SCHEMA_VERSION,
            "character_presence_projection_schema_version": EXPECTED_PRESENCE_SCHEMA_VERSION,
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
            "source_fingerprint": copy.deepcopy(dict(self.source_fingerprint)),
            "turns": [dict(turn) for turn in PUBLIC_TURNS],
            "human_review_contract": _human_review_contract(),
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
            "available": False,
            "authorization_must_repeat_exact_id_digest_calls_and_cost": True,
        },
    }


def _preflight_shape(
    *,
    execute: bool,
    authorization_id: str | None,
    maximum_provider_calls: int | None,
    maximum_cost_usd: float | None,
    authorized_plan_digest: str | None,
) -> None:
    """Reject malformed or attempt-1 authority before fingerprinting or any I/O."""

    if not execute:
        raise V27Attempt2ManualEvaluationConfigurationError("paid execution requires --execute")
    if authorization_id != AUTHORIZATION_ID:
        raise V27Attempt2ManualEvaluationConfigurationError(
            "authorization ID does not match the fixed one-shot V27 attempt-2 grant"
        )
    if type(maximum_provider_calls) is not int or maximum_provider_calls != MAXIMUM_PROVIDER_CALLS:
        raise V27Attempt2ManualEvaluationConfigurationError(
            f"provider-call ceiling must equal {MAXIMUM_PROVIDER_CALLS}"
        )
    if (
        isinstance(maximum_cost_usd, bool)
        or not isinstance(maximum_cost_usd, (int, float))
        or not math.isfinite(maximum_cost_usd)
        or maximum_cost_usd != MAXIMUM_COST_USD
    ):
        raise V27Attempt2ManualEvaluationConfigurationError(
            f"USD ceiling must equal ${MAXIMUM_COST_USD:.2f}"
        )
    if (
        not isinstance(authorized_plan_digest, str)
        or len(authorized_plan_digest) != 71
        or not authorized_plan_digest.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in authorized_plan_digest[7:])
    ):
        raise V27Attempt2ManualEvaluationConfigurationError(
            "authorized plan digest must be one exact lowercase SHA-256 digest"
        )
    if authorized_plan_digest == RETIRED_ATTEMPT1_EXECUTION_PLAN_DIGEST:
        raise V27Attempt2ManualEvaluationConfigurationError(
            "the consumed V27 attempt-1 digest cannot authorize attempt 2"
        )


def _preflight(
    *,
    execute: bool,
    authorization_id: str | None,
    maximum_provider_calls: int | None,
    maximum_cost_usd: float | None,
    authorized_plan_digest: str | None,
    plan: ExecutionPlan,
) -> None:
    _preflight_shape(
        execute=execute,
        authorization_id=authorization_id,
        maximum_provider_calls=maximum_provider_calls,
        maximum_cost_usd=maximum_cost_usd,
        authorized_plan_digest=authorized_plan_digest,
    )
    if authorized_plan_digest != plan.digest:
        raise V27Attempt2ManualEvaluationConfigurationError(
            "authorized digest does not match the exact V27 attempt-2 execution plan"
        )
    source = plan.source_fingerprint
    expected_source_digest = content_digest(
        {key: value for key, value in source.items() if key != "fingerprint_digest"}
    )
    if (
        source.get("installed_wheel_parity") is not True
        or source.get("installed_runtime_is_separate") is not True
        or source.get("fingerprint_digest") != expected_source_digest
    ):
        raise V27Attempt2ManualEvaluationConfigurationError(
            "installed wheel/source parity and source fingerprint integrity are required"
        )


class Attempt2AtomicOpenAICallLedger(AtomicOpenAICallLedger):
    """Reject any request-local cap drift before the paid adapter can be called."""

    __slots__ = ()

    def reserve(self, request: ConversationProviderRequest, scope: PublicTurnScope) -> int:
        expected_sessions = {
            f"v27-character-attempt2-replica-{replica}"
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
                "provider request drifted from the digest-bound V27 attempt-2 cap vector"
            )
        return super().reserve(request, scope)


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


def _safe_manifest(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only public metadata and prove the exact V27 decision/presence envelope."""

    safe = {key: raw.get(key) for key in _SAFE_MANIFEST_KEYS}
    if (
        safe["schema_version"] != CONTEXT_MANIFEST_SCHEMA_VERSION
        or safe["policy_id"] != EXPECTED_POLICY_ID
        or safe["policy_schema_version"] != 27
        or safe["character_context_schema_version"] != RUNTIME_CHARACTER_CONTEXT_SCHEMA_VERSION
        or safe["character_delivery_decision_schema_version"] != EXPECTED_DELIVERY_SCHEMA_VERSION
        or safe["character_presence_projection_schema_version"] != EXPECTED_PRESENCE_SCHEMA_VERSION
    ):
        raise RuntimeError("production composition did not use exact V27 schemas 4/2")
    for key in (
        "schema_version",
        "policy_schema_version",
        "character_context_schema_version",
        "character_delivery_decision_schema_version",
        "character_presence_projection_schema_version",
    ):
        if type(safe[key]) is not int:
            raise RuntimeError("V27 manifest version fields must be exact integers")
    included = safe["included_sections"]
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
        or included
        != [section for section in CONVERSATION_INCLUDED_SECTIONS if section in included]
    ):
        raise RuntimeError("production composition emitted invalid canonical included sections")
    value_signals = safe["character_presence_value_signals"]
    if not isinstance(value_signals, list) or len(value_signals) != 1:
        raise RuntimeError("character presence V2 requires exactly one value guard")
    for key in (
        "character_presence_personality_signals",
        "character_presence_affect_signals",
        "character_presence_relationship_signals",
    ):
        signals = safe[key]
        if (
            not isinstance(signals, list)
            or not signals
            or len(signals) > 3
            or any(not isinstance(signal, str) or not signal for signal in signals)
            or len(signals) != len(set(signals))
        ):
            raise RuntimeError(f"production composition emitted invalid {key}")
    if (
        safe["emotion_appraisal_provider"] != "ollama"
        or safe["emotion_appraisal_model"] != "qwen3:4b-instruct"
        or safe["emotion_appraisal_method"] != "ollama.categorical_affective_appraisal.v2"
        or safe["emotion_appraisal_provider_metrics_present"] is not True
    ):
        raise RuntimeError("production turn omitted exact local affect-provider evidence")
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
    response_regenerated = safe["response_regenerated"]
    regeneration_reason = safe["regeneration_reason"]
    if (
        type(response_regenerated) is not bool
        or (
            regeneration_reason is not None
            and regeneration_reason not in {reason.value for reason in ResponseRegenerationReason}
        )
        or (response_regenerated and regeneration_reason is None)
    ):
        raise RuntimeError("production turn emitted invalid regeneration metadata")
    retrieval_status = safe["retrieval_status"]
    retrieved_count = safe["retrieved_memory_count"]
    licensed = safe["character_presence_memory_use_licensed"]
    expected_license = (
        retrieval_status == "retrieved"
        and safe["character_delivery_grounding"] == "trusted_context"
    )
    if (
        retrieval_status not in {"not_requested", "retrieved", "no_relevant_memory", "unavailable"}
        or type(retrieved_count) is not int
        or retrieved_count < 0
        or (retrieval_status == "retrieved") is not (retrieved_count > 0)
        or type(licensed) is not bool
        or licensed is not expected_license
    ):
        raise RuntimeError("production turn emitted invalid memory scope")
    return safe


def validate_artifact_privacy(value: Mapping[str, Any]) -> None:
    unsafe = unsafe_artifact_paths(value)
    if unsafe:
        raise ValueError("V27 attempt-2 artifact contains private keys: " + ", ".join(unsafe))


def _validate_settings(settings: Settings) -> None:
    drift = [
        key for key, expected in _EXPECTED_SETTINGS.items() if getattr(settings, key) != expected
    ]
    if drift:
        raise V27Attempt2ManualEvaluationConfigurationError(
            "runtime settings drift from the digest-bound plan: " + ", ".join(drift)
        )
    if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value().strip():
        raise V27Attempt2ManualEvaluationConfigurationError("OpenAI API key is not configured")
    if BEHAVIOR_POLICY_V27.policy_id != EXPECTED_POLICY_ID:
        raise V27Attempt2ManualEvaluationConfigurationError("behavior policy V27 is unavailable")


def _configuration() -> dict[str, Any]:
    return {
        "provider": EXPECTED_PROVIDER.value,
        "model": EXPECTED_MODEL,
        "service_tier": "default",
        "reasoning_effort": EXPECTED_REASONING_EFFORT.value,
        "reasoning_token_allowance": EXPECTED_REASONING_ALLOWANCE,
        "policy_id": EXPECTED_POLICY_ID,
        "character_delivery_decision_schema_version": EXPECTED_DELIVERY_SCHEMA_VERSION,
        "character_presence_projection_schema_version": EXPECTED_PRESENCE_SCHEMA_VERSION,
        "turn_visible_output_token_limits": list(EXPECTED_TURN_VISIBLE_OUTPUT_TOKEN_LIMITS),
        "application_state_scope": "fresh_disposable_database_per_replica",
        "derived_processing": "none",
        "store": False,
        "tools": "none",
        "provider_conversation_state": "none",
        "prompt_cache_mode": "explicit",
        "expected_cache_reads": 0,
        "expected_cache_writes": 0,
        "affect_contract": manual_affect_contract(),
        "selected_usage_contract": manual_selected_usage_contract(),
        "settings": public_settings_contract(_EXPECTED_SETTINGS),
    }


def _sample_payload(report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: report[key]
        for key in (
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
        )
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


def _validate_expected_turn_caps(sessions: Sequence[Mapping[str, Any]]) -> None:
    if len(sessions) != EXPECTED_REPLICA_COUNT:
        raise ValueError("V27 attempt-2 cap evidence requires all fresh replicas")
    for session in sessions:
        turns = session.get("turns")
        if not isinstance(turns, list) or len(turns) != len(PUBLIC_TURNS):
            raise ValueError("V27 attempt-2 cap evidence requires all fixed turns")
        for fixture, expected_cap, turn in zip(
            PUBLIC_TURNS,
            EXPECTED_TURN_VISIBLE_OUTPUT_TOKEN_LIMITS,
            turns,
            strict=True,
        ):
            if (
                not isinstance(turn, Mapping)
                or turn.get("turn") != fixture["turn"]
                or turn.get("turn_id") != fixture["id"]
            ):
                raise ValueError("V27 attempt-2 cap evidence turn identity drift")
            attempts = turn.get("provider_attempts")
            if (
                not isinstance(attempts, list)
                or not attempts
                or any(
                    not isinstance(attempt, Mapping)
                    or attempt.get("max_output_tokens") != expected_cap
                    for attempt in attempts
                )
            ):
                raise ValueError("V27 attempt-2 provider attempt cap drift")


def _validate_completed_report(report: Mapping[str, Any], plan: ExecutionPlan) -> None:
    validate_artifact_privacy(report)
    embedded_source = report.get("source_fingerprint")
    if (
        not isinstance(embedded_source, Mapping)
        or embedded_source.get("installed_wheel_parity") is not True
        or embedded_source.get("installed_runtime_is_separate") is not True
        or embedded_source.get("fingerprint_digest")
        != content_digest(
            {key: value for key, value in embedded_source.items() if key != "fingerprint_digest"}
        )
    ):
        raise ValueError("completed V27 attempt-2 source fingerprint integrity drift")
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
    if (
        set(report) != expected_top
        or type(report.get("schema_version")) is not int
        or report.get("schema_version") != REPORT_SCHEMA_VERSION
        or report.get("checkpoint") != "14.2"
        or report.get("purpose")
        != "v27_live_state_selected_character_movement_production_gate_attempt2"
        or report.get("status") != "completed_awaiting_human_review"
        or report.get("artifact_id") != f"satori-checkpoint142-openai-v27-attempt2:{plan.digest}"
        or report.get("authorization_id") != AUTHORIZATION_ID
        or report.get("execution_plan_digest") != plan.digest
        or not strict_json_equal(report.get("execution_plan"), plan.public_mapping())
        or not strict_json_equal(report.get("source_fingerprint"), plan.source_fingerprint)
        or not strict_json_equal(report.get("artifact_contract"), _ARTIFACT_CONTRACT)
        or not strict_json_equal(report.get("configuration"), _configuration())
        or not strict_json_equal(report.get("human_review_contract"), _human_review_contract())
    ):
        raise ValueError("completed V27 attempt-2 report identity/configuration drift")
    for key in ("recorded_at", "completed_at"):
        timestamp = report.get(key)
        if not isinstance(timestamp, str):
            raise ValueError("V27 attempt-2 report timestamps must be strings")
        parsed = datetime.fromisoformat(timestamp)
        if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
            raise ValueError("V27 attempt-2 report timestamps must be timezone-aware UTC")
    sessions = validate_manual_evaluation_sessions(
        report.get("sessions"),
        public_turns=PUBLIC_TURNS,
        expected_turn_temperatures=(0.3,) * len(PUBLIC_TURNS),
        expected_turn_visible_output_token_limits=EXPECTED_TURN_VISIBLE_OUTPUT_TOKEN_LIMITS,
        expected_replica_count=EXPECTED_REPLICA_COUNT,
        public_session_prefix="v27-character-attempt2-replica",
        expected_provider=EXPECTED_PROVIDER,
        expected_model=EXPECTED_MODEL,
        expected_context_schema_version=RUNTIME_CHARACTER_CONTEXT_SCHEMA_VERSION,
        visible_output_token_ceiling=768,
        maximum_response_chars=12_000,
        safe_manifest=_safe_manifest,
    )
    _validate_expected_turn_caps(sessions)
    validate_exact_openai_ledger(
        cast(Mapping[str, Any], report["budget"]),
        sessions,
        required_base_calls=REQUIRED_BASE_CALLS,
        maximum_provider_calls=MAXIMUM_PROVIDER_CALLS,
        maximum_cost_usd=MAXIMUM_COST_USD,
        reasoning_token_allowance=EXPECTED_REASONING_ALLOWANCE,
        visible_output_token_ceiling=768,
    )
    if report.get("sample_digest") != content_digest(_sample_payload(report)):
        raise ValueError("completed V27 attempt-2 sample digest mismatch")
    if not strict_json_equal(
        report.get("human_review_artifact_template"), _human_review_template(report)
    ):
        raise ValueError("completed V27 attempt-2 human-review template drift")


def validate_v27_attempt2_human_review_artifact(
    review: Mapping[str, Any],
    completed_report: Mapping[str, Any],
    *,
    authorized_plan_digest: str,
) -> bool:
    if (
        not isinstance(authorized_plan_digest, str)
        or authorized_plan_digest == RETIRED_ATTEMPT1_EXECUTION_PLAN_DIGEST
        or completed_report.get("execution_plan_digest") != authorized_plan_digest
    ):
        raise ValueError("human review is not bound to the externally authorized attempt-2 plan")
    embedded_source = completed_report.get("source_fingerprint")
    if not isinstance(embedded_source, Mapping):
        raise ValueError("completed V27 attempt-2 report is missing its source fingerprint")
    _validate_completed_report(
        completed_report,
        ExecutionPlan(source_fingerprint=copy.deepcopy(dict(embedded_source))),
    )
    return validate_human_review_artifact(
        review,
        completed_report,
        per_turn_dimensions=PER_TURN_HUMAN_REVIEW_DIMENSIONS,
        cross_session_dimensions=CROSS_SESSION_HUMAN_REVIEW_DIMENSIONS,
        review_schema_version=REVIEW_SCHEMA_VERSION,
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
        metadata = var_root.lstat()
    except FileNotFoundError as error:
        raise V27Attempt2ManualEvaluationConfigurationError(
            "repository var directory is missing"
        ) from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise V27Attempt2ManualEvaluationConfigurationError("repository var path is unsafe")
    writer = DurableReportWriter(var_root, REPORT_NAME, evaluation_label="V27 attempt 2")
    try:
        writer.prepare()
    except EvaluationArtifactSafetyError as error:
        raise V27Attempt2ManualEvaluationConfigurationError(str(error)) from error
    review_path = root / REVIEW_RELATIVE_PATH
    if review_path.parent != writer.path.parent or review_path.exists() or review_path.is_symlink():
        raise V27Attempt2ManualEvaluationConfigurationError(
            "fixed V27 attempt-2 review path is unsafe or already exists"
        )
    ledger = Attempt2AtomicOpenAICallLedger(
        maximum_calls=MAXIMUM_PROVIDER_CALLS,
        maximum_cost_usd=MAXIMUM_COST_USD,
        required_base_calls=REQUIRED_BASE_CALLS,
        reasoning_token_allowance=EXPECTED_REASONING_ALLOWANCE,
    )
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "recorded_at": datetime.now(UTC).isoformat(),
        "checkpoint": "14.2",
        "purpose": "v27_live_state_selected_character_movement_production_gate_attempt2",
        "status": "authorized_preflight",
        "artifact_id": f"satori-checkpoint142-openai-v27-attempt2:{plan.digest}",
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
        validate_artifact_privacy(report)
        writer.write(report)

    ledger.on_change = checkpoint
    try:
        acquire_one_shot_authorization_claim(
            root=var_root,
            authorization_id=cast(str, authorization_id),
            expected_authorization_id=AUTHORIZATION_ID,
            plan_digest=plan.digest,
            expected_claim_name=AUTHORIZATION_CLAIM_NAME,
            evaluation_label="V27 attempt 2",
        )
    except EvaluationArtifactSafetyError as error:
        raise V27Attempt2ManualEvaluationConfigurationError(str(error)) from error
    try:
        # Once the claim is consumed, every later failure must have a durable public-metadata
        # report. Persist the preflight state before reading Settings or constructing providers.
        checkpoint()
        if _execution_source_fingerprint() != plan.source_fingerprint:
            raise V27Attempt2ManualEvaluationConfigurationError(
                "execution sources changed after authorization preflight"
            )
        settings = Settings()
        _validate_settings(settings)
        report["status"] = "running"
        checkpoint()
        with tempfile.TemporaryDirectory(
            prefix="satori-checkpoint142-openai-v27-attempt2-"
        ) as temporary:
            for replica in range(1, EXPECTED_REPLICA_COUNT + 1):
                record = new_replica_record(session_id=f"v27-character-attempt2-replica-{replica}")
                cast(list[dict[str, Any]], report["sessions"]).append(record)
                checkpoint()
                await run_replica(
                    settings=settings,
                    database_path=Path(temporary) / f"replica-{replica}.db",
                    alembic_config=root / "alembic.ini",
                    replica_number=replica,
                    ledger=ledger,
                    checkpoint=checkpoint,
                    behavior_policy=BEHAVIOR_POLICY_V27,
                    public_turns=PUBLIC_TURNS,
                    public_session_prefix="v27-character-attempt2-replica",
                    expected_provider=EXPECTED_PROVIDER,
                    expected_model=EXPECTED_MODEL,
                    safe_manifest=_safe_manifest,
                    record=record,
                )
        if _execution_source_fingerprint() != plan.source_fingerprint:
            raise RuntimeError("execution sources changed during the paid V27 attempt-2 run")
        if ledger.snapshot().get("gate_valid") is not True:
            raise RuntimeError(
                "V27 attempt-2 paid-call ledger did not reach an exact valid terminal state"
            )
        report["budget"] = ledger.snapshot()
        report["status"] = "completed_awaiting_human_review"
        report["completed_at"] = datetime.now(UTC).isoformat()
        report["sample_digest"] = content_digest(_sample_payload(report))
        report["human_review_artifact_template"] = _human_review_template(report)
        _validate_completed_report(report, plan)
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
    parser = argparse.ArgumentParser(description="Inspect or execute the one-shot V27 attempt 2.")
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
                "sample_digest": completed["sample_digest"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
