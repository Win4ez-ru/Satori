"""Retired, module-scoped OpenAI v24 employer-demo evaluator.

This is a historical manual evaluator, not a pytest module.  Its default mode validates and
describes one public module from ``checkpoint142_employer_demo_v1.json``.  Paid execution is
permanently retired: ``--execute`` and direct ``run`` calls fail before Settings, report output or
network access.  Inspection, historical validation and version-neutral helper APIs remain
available for reproducibility and newer evaluators.

Historical paid execution used the production conversation composition and behavior policy v24.
OpenAI was the stateless foreground mechanism (the production adapter fixes ``store=false``);
application identity, conversation state and any derived memory stayed in the local disposable
database.  The runner passed only each fixture's public ``user_text`` into ``TalkToSatori``.
Semantic tags, review dimensions and all artifact metadata stayed evaluator-side, so no desired,
golden, reference or example reply could enter provider context.

The artifact intentionally contains exact public inputs and exact committed public replies for
human review.  Everything else is allowlisted: public session/turn identifiers, v24 decision
codes, timings, usage, conservative cost accounting and content-free attempt metadata.  Raw
provider messages, private application context, trace/interaction identifiers, credentials and
temporary database paths are never retained.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import tempfile
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from satori.application.cognition.contracts import PositionStance, ResponseVerbosity
from satori.application.conversation.character_delivery import (
    CharacterDeliveryDecision,
    CharacterDeliveryGoal,
    CharacterDeliveryVoice,
)
from satori.application.conversation.character_expression import (
    CharacterContinuationMode,
    CharacterGroundingMode,
    CharacterPressureLevel,
)
from satori.application.conversation.contracts import SatoriReply, TalkInput
from satori.application.conversation.policy import BEHAVIOR_POLICY_V24
from satori.application.conversation.use_cases import ConversationProvider
from satori.config import (
    ConversationProviderKind,
    LogLevel,
    OpenAIReasoningEffort,
    Settings,
)
from satori.core.conversation import (
    ConversationProviderError,
    ConversationProviderRequest,
    ConversationProviderResponse,
)
from satori.core.ids import Uuid4Generator
from satori.observability.logging import configure_logging
from tests.stage81_real_eval import (
    EvaluationRuntime,
    _build_runtime,
    _current_relationship,
    _process_post_response,
    _public_sampled_reply,
    _sanitized_manifest,
)

REPORT_SCHEMA_VERSION = 1
FIXTURE_PATH = Path(__file__).with_name("fixtures") / "checkpoint142_employer_demo_v1.json"
EXPECTED_CORPUS_ID = "satori.checkpoint142.employer-demo.ru.v1"
EXPECTED_POLICY_ID = "satori.conversation.behavior.v24"
EXPECTED_PROVIDER = ConversationProviderKind.OPENAI
EXPECTED_MODEL = "gpt-5.6-terra"
EXPECTED_REASONING_EFFORT = OpenAIReasoningEffort.MEDIUM
EXPECTED_REASONING_ALLOWANCE = 1024
EXPECTED_COGNITION_INTENT_REGISTRY_VERSION = 2
EXPECTED_COGNITION_TEMPLATE_REGISTRY_VERSION = 2
EXPECTED_COGNITION_TEMPLATE_ID = "satori.cognition.response-substance"
EXPECTED_COGNITION_TEMPLATE_SCHEMA_VERSION = 2
EXPECTED_REPLICA_COUNT = 3
MAX_ATTEMPTS_PER_TURN = 2
ABSOLUTE_MAX_COST_USD = 1.0
OPENAI_INPUT_USD_PER_MILLION_TOKENS = 2.0
OPENAI_OUTPUT_USD_PER_MILLION_TOKENS = 12.0
PRICING_SNAPSHOT = "repository-versioned-2026-08-27"
INPUT_TOKEN_BYTE_GUARD_OVERHEAD = 4096
INPUT_TOKEN_MESSAGE_GUARD_OVERHEAD = 128
HUMAN_REVIEW_NOTE = (
    "Fill every declared field with an explicit boolean after reading the exact public sample. "
    "Hard-safety keys are expanded in the completed artifact."
)

EXPECTED_MODULE_IDS = (
    "core_emotional",
    "intellectual_partner",
    "hurt_and_repair",
    "identity_and_memory",
)
_EXPECTED_MODULE_SHAPES = {
    "core_emotional": ("fresh_undeveloped_neutral", 3, frozenset(), frozenset()),
    "intellectual_partner": ("developing_neutral", 3, frozenset(), frozenset()),
    "hurt_and_repair": (
        "established_positive",
        3,
        frozenset(),
        frozenset({1, 2}),
    ),
    "identity_and_memory": (
        "developing_neutral",
        4,
        frozenset({1}),
        frozenset({1}),
    ),
}

_FORBIDDEN_REPLY_KEY_PARTS = (
    "assistant_text",
    "desired_reply",
    "desired_response",
    "exact_text",
    "example_reply",
    "expected_reply",
    "golden_reply",
    "reference_reply",
    "required_phrase",
    "required_reply",
    "required_response",
    "target_reply",
    "template_reply",
)
_UNSAFE_ARTIFACT_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "client_request_id",
        "credential",
        "credentials",
        "database_artifact",
        "database_path",
        "database_url",
        "interaction_id",
        "messages",
        "private_context",
        "prompt",
        "provider_messages",
        "provider_prompt",
        "raw_prompt",
        "raw_reasoning",
        "request_messages",
        "response_body",
        "retrieved_memory_ids",
        "retrieved_semantic_claim_ids",
        "trace_id",
    }
)
_SAFE_PROVIDER_METRIC_KEYS = (
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
)
_SAFE_TIMING_KEYS = (
    "interaction_start_ms",
    "recent_context_ms",
    "retrieval_ms",
    "semantic_retrieval_ms",
    "emotion_appraisal_ms",
    "emotion_read_ms",
    "relationship_read_ms",
    "personality_read_ms",
    "identity_read_ms",
    "prompt_build_ms",
    "conversation_generation_ms",
    "response_regeneration_ms",
    "grounding_validation_ms",
    "canonical_commit_ms",
    "committed_reply_ms",
)
_SAFE_V24_MANIFEST_KEYS = (
    "schema_version",
    "policy_id",
    "policy_schema_version",
    "character_context_schema_version",
    "cognition_position_stance",
    "cognition_preserve_uncertainty",
    "cognition_intent_registry_version",
    "cognition_primary_intent",
    "cognition_intent_tags",
    "cognition_required_point_codes",
    "cognition_forbidden_claim_codes",
    "cognition_response_verbosity",
    "cognition_template_registry_version",
    "cognition_template_id",
    "cognition_template_schema_version",
    "character_expression_plan_schema_version",
    "character_delivery_decision_schema_version",
    "character_delivery_goal",
    "character_delivery_voice",
    "character_delivery_grounding",
    "character_delivery_continuation",
    "character_delivery_pressure",
    "character_delivery_position_stance",
    "character_delivery_preserve_uncertainty",
    "retrieval_status",
    "retrieved_memory_count",
    "semantic_retrieval_status",
    "retrieved_semantic_claim_count",
    "emotion_appraisal_status",
    "relationship_expression_profile",
    "relationship_recent_strain",
    "affect_expression_profile",
    "recent_conversation_turn_count",
    "disclosure_primary_mode",
    "disclosure_facets",
    "consecutive_same_user_message_count",
    "duplicate_response_detected",
    "regeneration_attempted",
    "response_regenerated",
    "regeneration_reason",
)


class V24EvaluationConfigurationError(RuntimeError):
    """Reject incomplete, unsafe or non-comparable execution before provider I/O."""


PAID_EXECUTION_RETIRED = True
PAID_EXECUTION_RETIREMENT_REASON = (
    "v24 paid execution is retired; this evaluator is retained for offline inspection, "
    "historical evidence and shared helper APIs only"
)


class ProviderCallBudgetExhausted(RuntimeError):
    """Reject a base call or validator retry before it can reach OpenAI."""


@dataclass(frozen=True, slots=True)
class ModuleSpec:
    module_id: str
    purpose: str
    relationship_setup: str
    turns: tuple[dict[str, Any], ...]
    restart_after_turns: frozenset[int]
    derived_processing_after_turns: frozenset[int]
    dialogue_review_dimensions: tuple[str, ...]
    cross_replica_review_dimensions: tuple[str, ...]

    @property
    def turns_per_replica(self) -> int:
        return len(self.turns)

    @property
    def required_base_calls(self) -> int:
        return EXPECTED_REPLICA_COUNT * self.turns_per_replica

    @property
    def absolute_max_calls(self) -> int:
        return self.required_base_calls * MAX_ATTEMPTS_PER_TURN


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return cast(dict[str, Any], value)


def _array(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def _strict_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    return value


def _require_exact_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"{label} schema drift: missing={missing}, extra={extra}")


def _non_blank_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-blank string")
    return value


def _non_negative_number(value: object, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{label} must be a finite non-negative number")
    return float(value)


def _aware_utc_datetime(value: object, label: str) -> datetime:
    text = _non_blank_string(value, label)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"{label} must be an ISO-8601 datetime") from error
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise ValueError(f"{label} must be timezone-aware UTC")
    return parsed


def _normalized_key(key: str) -> str:
    return key.casefold().replace("-", "_").replace(" ", "_")


def _forbidden_reply_contract_paths(value: object, path: str = "$") -> tuple[str, ...]:
    found: list[str] = []
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}"
            normalized = _normalized_key(key)
            if any(part in normalized for part in _FORBIDDEN_REPLY_KEY_PARTS):
                found.append(child_path)
            found.extend(_forbidden_reply_contract_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_forbidden_reply_contract_paths(child, f"{path}[{index}]"))
    return tuple(found)


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    items = _array(value, label)
    result: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{label}[{index}] must be a non-blank string")
        result.append(item)
    if len(result) != len(set(result)):
        raise ValueError(f"{label} contains duplicates")
    return tuple(result)


def load_fixture(path: Path = FIXTURE_PATH) -> dict[str, Any]:
    fixture = _object(json.loads(path.read_text(encoding="utf-8")), "employer-demo fixture")
    validate_fixture(fixture)
    return fixture


def validate_fixture(fixture: Mapping[str, Any]) -> None:
    """Prove the fixture is public, modular and unable to authorize provider execution."""

    expected_top_level = {
        "schema_version",
        "corpus_id",
        "checkpoint",
        "policy_id",
        "execution_contract",
        "invariants",
        "hard_safety_dimensions",
        "quality_dimension_registry",
        "modules",
        "acceptance",
    }
    if set(fixture) != expected_top_level:
        raise ValueError("employer-demo fixture top-level schema drift")
    if (
        fixture.get("schema_version") != 1
        or fixture.get("corpus_id") != EXPECTED_CORPUS_ID
        or fixture.get("checkpoint") != "14.2"
        or fixture.get("policy_id") != EXPECTED_POLICY_ID
    ):
        raise ValueError("employer-demo fixture identity mismatch")
    forbidden = _forbidden_reply_contract_paths(fixture)
    if forbidden:
        raise ValueError(f"fixture contains scripted reply contract keys: {', '.join(forbidden)}")

    execution = _object(fixture.get("execution_contract"), "execution_contract")
    expected_execution = {
        "offline_only_by_default": True,
        "provider_execution_requires_separate_authorization": True,
        "provider_calls_authorized_by_this_fixture": False,
        "target_provider": EXPECTED_PROVIDER.value,
        "target_model": EXPECTED_MODEL,
        "fresh_replica_count": EXPECTED_REPLICA_COUNT,
        "automated_text_judging_performed": False,
        "provider_sample_is_authority": False,
    }
    _require_exact_keys(execution, set(expected_execution), "execution_contract")
    if execution != expected_execution:
        raise ValueError("employer-demo execution contract drift")
    expected_invariants = {
        "public_user_turns_only",
        "no_desired_golden_reference_or_template_prose",
        "human_boolean_review_is_bound_to_an_immutable_public_artifact",
        "hard_safety_is_required_on_every_selected_turn",
        "provider_output_never_becomes_state_authority",
        "each_module_requires_separate_paid_authorization",
        "stage_15_remains_locked",
    }
    if set(_string_tuple(fixture.get("invariants"), "invariants")) != expected_invariants:
        raise ValueError("employer-demo invariant registry drift")

    hard_dimensions = frozenset(
        _string_tuple(fixture.get("hard_safety_dimensions"), "hard_safety_dimensions")
    )
    quality_dimensions = frozenset(
        _string_tuple(fixture.get("quality_dimension_registry"), "quality_dimension_registry")
    )
    if not hard_dimensions or not quality_dimensions or hard_dimensions & quality_dimensions:
        raise ValueError("review dimension registries must be non-empty and disjoint")

    raw_modules = _array(fixture.get("modules"), "modules")
    if tuple(_object(module, "module").get("id") for module in raw_modules) != EXPECTED_MODULE_IDS:
        raise ValueError("employer-demo module order or identity drift")
    seen_turn_ids: set[str] = set()
    for module_index, raw_module in enumerate(raw_modules):
        module = _object(raw_module, f"modules[{module_index}]")
        required_keys = {
            "id",
            "purpose",
            "relationship_setup",
            "fresh_database_per_replica",
            "restart_after_turns",
            "turns",
            "dialogue_review_dimensions",
            "cross_replica_review_dimensions",
        }
        allowed_keys = {*required_keys, "derived_processing_after_turns"}
        if not required_keys <= set(module) or not set(module) <= allowed_keys:
            raise ValueError(f"module {module.get('id')} schema drift")
        if module.get("fresh_database_per_replica") is not True:
            raise ValueError("every module must use a fresh database per replica")
        purpose = module.get("purpose")
        if not isinstance(purpose, str) or not purpose.strip():
            raise ValueError("module purpose must be a non-blank string")
        expected_profile, expected_turn_count, expected_restart, expected_derived = (
            _EXPECTED_MODULE_SHAPES[cast(str, module["id"])]
        )
        if module.get("relationship_setup") != expected_profile:
            raise ValueError("module relationship setup drift")
        turns = _array(module.get("turns"), f"modules[{module_index}].turns")
        if len(turns) != expected_turn_count:
            raise ValueError("employer-demo module turn cardinality drift")
        for turn_number, raw_turn in enumerate(turns, start=1):
            turn = _object(raw_turn, f"modules[{module_index}].turns[{turn_number - 1}]")
            if set(turn) != {
                "turn",
                "id",
                "user_text",
                "semantic_tags",
                "review_dimensions",
            }:
                raise ValueError("employer-demo turn schema drift")
            if turn.get("turn") != turn_number:
                raise ValueError("module turn numbers must be consecutive from one")
            turn_id = turn.get("id")
            user_text = turn.get("user_text")
            if not isinstance(turn_id, str) or not turn_id.strip():
                raise ValueError("turn id must be a non-blank string")
            public_id = f"{module['id']}:{turn_id}"
            if public_id in seen_turn_ids:
                raise ValueError("module turn ids must be globally unique")
            seen_turn_ids.add(public_id)
            if not isinstance(user_text, str) or not user_text.strip():
                raise ValueError("turn user_text must be a non-blank public string")
            _string_tuple(turn.get("semantic_tags"), f"{public_id}.semantic_tags")
            review_dimensions = frozenset(
                _string_tuple(turn.get("review_dimensions"), f"{public_id}.review_dimensions")
            )
            if not review_dimensions or not review_dimensions <= quality_dimensions:
                raise ValueError("turn review dimensions must come from the quality registry")

        allowed_boundaries = set(range(1, len(turns)))
        restart_after = {
            _strict_int(value, "restart_after_turns item")
            for value in _array(module.get("restart_after_turns"), "restart_after_turns")
        }
        derived_after = {
            _strict_int(value, "derived_processing_after_turns item")
            for value in _array(
                module.get("derived_processing_after_turns", []),
                "derived_processing_after_turns",
            )
        }
        if not restart_after <= allowed_boundaries or not derived_after <= allowed_boundaries:
            raise ValueError("restart/derived boundaries must precede a later turn")
        if restart_after != expected_restart or derived_after != expected_derived:
            raise ValueError("module restart/derived-processing boundary drift")
        dialogue_dimensions = frozenset(
            _string_tuple(
                module.get("dialogue_review_dimensions"),
                f"{module['id']}.dialogue_review_dimensions",
            )
        )
        cross_dimensions = frozenset(
            _string_tuple(
                module.get("cross_replica_review_dimensions"),
                f"{module['id']}.cross_replica_review_dimensions",
            )
        )
        if not dialogue_dimensions <= hard_dimensions | quality_dimensions:
            raise ValueError("dialogue review dimension is not registered")
        if not cross_dimensions <= hard_dimensions | quality_dimensions:
            raise ValueError("cross-replica review dimension is not registered")

    acceptance = _object(fixture.get("acceptance"), "acceptance")
    if set(acceptance) != {
        "required_module_count",
        "required_fresh_replica_count_per_module",
        "all_hard_safety_dimensions_must_pass_on_every_turn",
        "all_declared_turn_quality_dimensions_must_pass",
        "all_dialogue_review_dimensions_must_pass",
        "all_cross_replica_review_dimensions_must_pass",
        "human_review_required",
        "automated_text_judging_performed",
        "provider_sample_is_authority",
        "one_module_cannot_accept_employer_demo_readiness",
    }:
        raise ValueError("employer-demo acceptance schema drift")
    if (
        acceptance.get("required_module_count") != len(EXPECTED_MODULE_IDS)
        or acceptance.get("required_fresh_replica_count_per_module") != EXPECTED_REPLICA_COUNT
        or acceptance.get("all_hard_safety_dimensions_must_pass_on_every_turn") is not True
        or acceptance.get("all_declared_turn_quality_dimensions_must_pass") is not True
        or acceptance.get("all_dialogue_review_dimensions_must_pass") is not True
        or acceptance.get("all_cross_replica_review_dimensions_must_pass") is not True
        or acceptance.get("human_review_required") is not True
        or acceptance.get("automated_text_judging_performed") is not False
        or acceptance.get("provider_sample_is_authority") is not False
        or acceptance.get("one_module_cannot_accept_employer_demo_readiness") is not True
    ):
        raise ValueError("employer-demo acceptance contract drift")


def module_spec(fixture: Mapping[str, Any], module_id: str) -> ModuleSpec:
    validate_fixture(fixture)
    raw_module = next(
        (
            _object(value, "module")
            for value in _array(fixture.get("modules"), "modules")
            if _object(value, "module").get("id") == module_id
        ),
        None,
    )
    if raw_module is None:
        raise ValueError(f"unknown employer-demo module: {module_id}")
    return ModuleSpec(
        module_id=module_id,
        purpose=cast(str, raw_module["purpose"]),
        relationship_setup=cast(str, raw_module["relationship_setup"]),
        turns=tuple(
            _object(turn, f"{module_id}.turn")
            for turn in _array(raw_module["turns"], f"{module_id}.turns")
        ),
        restart_after_turns=frozenset(cast(list[int], raw_module["restart_after_turns"])),
        derived_processing_after_turns=frozenset(
            cast(list[int], raw_module.get("derived_processing_after_turns", []))
        ),
        dialogue_review_dimensions=_string_tuple(
            raw_module["dialogue_review_dimensions"], "dialogue_review_dimensions"
        ),
        cross_replica_review_dimensions=_string_tuple(
            raw_module["cross_replica_review_dimensions"],
            "cross_replica_review_dimensions",
        ),
    )


def inspect_module(fixture: Mapping[str, Any], selected: ModuleSpec) -> dict[str, Any]:
    """Return a public, offline-only execution plan without loading Settings."""

    return {
        "schema_version": 1,
        "mode": "inspect_only",
        "network_attempted": False,
        "provider_calls_authorized_by_fixture": False,
        "corpus_id": fixture["corpus_id"],
        "policy_id": fixture["policy_id"],
        "module_id": selected.module_id,
        "execution_plan_digest": execution_plan_content_digest(selected),
        "purpose": selected.purpose,
        "relationship_setup": selected.relationship_setup,
        "fresh_replica_count": EXPECTED_REPLICA_COUNT,
        "turns_per_replica": selected.turns_per_replica,
        "required_base_calls": selected.required_base_calls,
        "maximum_calls_with_one_retry_per_turn": selected.absolute_max_calls,
        "turns": [
            {
                "turn": turn["turn"],
                "turn_id": turn["id"],
                "user": turn["user_text"],
            }
            for turn in selected.turns
        ],
        "restart_after_turns": sorted(selected.restart_after_turns),
        "derived_processing_after_turns": sorted(selected.derived_processing_after_turns),
        "paid_execution_requirements": {
            "status": "retired",
            "paid_execution_available": False,
            "historical_or_new_authorization_can_execute": False,
            "execute_flag": "--execute",
            "explicit_max_provider_calls": True,
            "explicit_max_cost_usd": True,
            "authorized_plan_digest": True,
            "one_module_per_invocation": True,
            "separate_user_authorization_required": True,
        },
    }


def execution_plan_content_digest(selected: ModuleSpec) -> str:
    """Bind paid authorization to the exact public module and execution-affecting context."""

    return _public_mapping_digest(
        {
            "schema_version": 1,
            "corpus_id": EXPECTED_CORPUS_ID,
            "policy_id": EXPECTED_POLICY_ID,
            "provider": EXPECTED_PROVIDER.value,
            "model": EXPECTED_MODEL,
            "reasoning_effort": EXPECTED_REASONING_EFFORT.value,
            "reasoning_token_allowance": EXPECTED_REASONING_ALLOWANCE,
            "fresh_replica_count": EXPECTED_REPLICA_COUNT,
            "maximum_attempts_per_turn": MAX_ATTEMPTS_PER_TURN,
            "module": {
                "id": selected.module_id,
                "purpose": selected.purpose,
                "relationship_setup": selected.relationship_setup,
                "turns": [dict(turn) for turn in selected.turns],
                "restart_after_turns": sorted(selected.restart_after_turns),
                "derived_processing_after_turns": sorted(selected.derived_processing_after_turns),
                "dialogue_review_dimensions": list(selected.dialogue_review_dimensions),
                "cross_replica_review_dimensions": list(selected.cross_replica_review_dimensions),
            },
        }
    )


def preflight_execution(
    *,
    execute: bool,
    maximum_provider_calls: int | None,
    maximum_cost_usd: float | None,
    authorized_plan_digest: str | None,
    selected: ModuleSpec,
) -> None:
    """Validate the historical envelope offline; this does not reactivate execution."""

    if not execute:
        raise V24EvaluationConfigurationError("paid OpenAI execution requires explicit --execute")
    try:
        supplied_plan_digest = _sha256_digest(
            authorized_plan_digest,
            "--authorized-plan-digest",
        )
    except (TypeError, ValueError) as error:
        raise V24EvaluationConfigurationError(
            "--authorized-plan-digest is required for the exact inspected module plan"
        ) from error
    if supplied_plan_digest != execution_plan_content_digest(selected):
        raise V24EvaluationConfigurationError(
            "authorized plan digest does not match the exact current module plan"
        )
    if (
        maximum_provider_calls is None
        or isinstance(maximum_provider_calls, bool)
        or not isinstance(maximum_provider_calls, int)
    ):
        raise V24EvaluationConfigurationError("--max-provider-calls is required for execution")
    if maximum_provider_calls < selected.required_base_calls:
        raise V24EvaluationConfigurationError(
            "provider-call ceiling cannot cover all mandatory base turns"
        )
    if maximum_provider_calls > selected.absolute_max_calls:
        raise V24EvaluationConfigurationError(
            "provider-call ceiling exceeds the module's max-one-retry envelope"
        )
    if (
        maximum_cost_usd is None
        or isinstance(maximum_cost_usd, bool)
        or not isinstance(maximum_cost_usd, (int, float))
        or not math.isfinite(maximum_cost_usd)
        or maximum_cost_usd <= 0
    ):
        raise V24EvaluationConfigurationError(
            "--max-cost-usd must be an explicit positive finite amount"
        )
    if maximum_cost_usd > ABSOLUTE_MAX_COST_USD:
        raise V24EvaluationConfigurationError(
            f"USD ceiling exceeds the evaluator safety cap of ${ABSOLUTE_MAX_COST_USD:.2f}"
        )


def _reject_retired_paid_execution() -> None:
    """Fail before Settings, report output, runtime construction or provider access."""

    raise V24EvaluationConfigurationError(PAID_EXECUTION_RETIREMENT_REASON)


def _validate_production_settings(settings: Settings) -> None:
    if settings.conversation_provider is not EXPECTED_PROVIDER:
        raise V24EvaluationConfigurationError("v24 demo requires OpenAI foreground configuration")
    if settings.conversation_model != EXPECTED_MODEL:
        raise V24EvaluationConfigurationError(f"v24 demo requires exact model {EXPECTED_MODEL}")
    if settings.openai_api_key is None:
        raise V24EvaluationConfigurationError("v24 demo requires an OpenAI API key")
    if settings.openai_reasoning_effort is not EXPECTED_REASONING_EFFORT:
        raise V24EvaluationConfigurationError(
            f"v24 demo requires reasoning effort {EXPECTED_REASONING_EFFORT.value}"
        )
    if settings.openai_reasoning_token_allowance != EXPECTED_REASONING_ALLOWANCE:
        raise V24EvaluationConfigurationError(
            f"v24 demo requires reasoning allowance {EXPECTED_REASONING_ALLOWANCE}"
        )
    if settings.openai_base_url != "https://api.openai.com/v1":
        raise V24EvaluationConfigurationError("v24 demo requires the canonical OpenAI API")
    if BEHAVIOR_POLICY_V24.policy_id != EXPECTED_POLICY_ID:
        raise V24EvaluationConfigurationError("behavior policy v24 is unavailable")
    background = (
        settings.affective_appraisal_provider,
        settings.episode_formation_provider,
        settings.semantic_formation_provider,
        settings.model_formation_provider,
        settings.position_formation_provider,
        settings.reflection_provider,
        settings.relationship_appraisal_provider,
    )
    if any(provider is not ConversationProviderKind.OLLAMA for provider in background):
        raise V24EvaluationConfigurationError("all non-foreground providers must remain Ollama")
    if settings.embedding_provider.value != "ollama":
        raise V24EvaluationConfigurationError("embedding provider must remain Ollama")


def _safe_configuration(settings: Settings, selected: ModuleSpec) -> dict[str, Any]:
    return {
        "conversation_provider": settings.conversation_provider.value,
        "conversation_model": settings.conversation_model,
        "openai_reasoning_effort": settings.openai_reasoning_effort.value,
        "openai_reasoning_token_allowance": settings.openai_reasoning_token_allowance,
        "responses_api_store": False,
        "remote_conversation_state": "disabled",
        "application_state_scope": "fresh_disposable_database_per_replica",
        "background_providers": "ollama",
        "policy_id": EXPECTED_POLICY_ID,
        "module_id": selected.module_id,
        "execution_plan_digest": execution_plan_content_digest(selected),
        "derived_processing": (
            "serial_only_at_versioned_boundaries"
            if selected.derived_processing_after_turns
            else "none"
        ),
    }


def _safe_provider_metrics(value: object) -> dict[str, int | float | None] | None:
    if not isinstance(value, dict):
        return None
    raw = cast(dict[str, Any], value)
    sanitized: dict[str, int | float | None] = {}
    for key in _SAFE_PROVIDER_METRIC_KEYS:
        metric = raw.get(key)
        if metric is None or (isinstance(metric, (int, float)) and not isinstance(metric, bool)):
            sanitized[key] = metric
    return sanitized or None


@dataclass(frozen=True, slots=True)
class PublicTurnScope:
    session_id: str
    turn: int
    turn_id: str

    def __post_init__(self) -> None:
        _non_blank_string(self.session_id, "public turn scope session_id")
        if _strict_int(self.turn, "public turn scope turn") < 1:
            raise ValueError("public turn scope turn must be positive")
        _non_blank_string(self.turn_id, "public turn scope turn_id")


@dataclass(slots=True)
class TurnScopeBinding:
    """Bind provider attempts to public evaluator IDs, never to application trace IDs."""

    current: PublicTurnScope | None = None

    def set(self, scope: PublicTurnScope) -> None:
        if self.current is not None:
            raise RuntimeError("provider turn scope is already active")
        self.current = scope

    def clear(self) -> None:
        self.current = None

    def require(self) -> PublicTurnScope:
        if self.current is None:
            raise ProviderCallBudgetExhausted("provider call has no public evaluator turn scope")
        return self.current


@dataclass(slots=True)
class AtomicOpenAICallLedger:
    """Atomically reserve and settle every possible paid attempt before/after network I/O."""

    maximum_calls: int
    maximum_cost_usd: float
    required_base_calls: int
    reasoning_token_allowance: int = EXPECTED_REASONING_ALLOWANCE
    on_change: Callable[[], None] | None = field(default=None, repr=False)
    _calls: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _attempts_by_scope: dict[PublicTurnScope, tuple[str, int]] = field(
        default_factory=dict,
        repr=False,
    )
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        if (
            _strict_int(self.maximum_calls, "ledger maximum_calls") < 1
            or _strict_int(self.required_base_calls, "ledger required_base_calls") < 1
            or self.required_base_calls > self.maximum_calls
        ):
            raise ValueError("ledger call envelope is invalid")
        if (
            isinstance(self.maximum_cost_usd, bool)
            or not isinstance(self.maximum_cost_usd, (int, float))
            or not math.isfinite(self.maximum_cost_usd)
            or not 0 < self.maximum_cost_usd <= ABSOLUTE_MAX_COST_USD
        ):
            raise ValueError("ledger USD envelope is invalid")
        if self.reasoning_token_allowance != EXPECTED_REASONING_ALLOWANCE:
            raise ValueError("ledger requires the exact OpenAI reasoning allowance")

    @staticmethod
    def _guarded_input_token_limit(request: ConversationProviderRequest) -> int:
        return (
            sum(len(message.content.encode("utf-8")) for message in request.messages)
            + INPUT_TOKEN_BYTE_GUARD_OVERHEAD
            + len(request.messages) * INPUT_TOKEN_MESSAGE_GUARD_OVERHEAD
        )

    def _projected_guard(self, request: ConversationProviderRequest) -> tuple[int, int, float]:
        guarded_input_tokens = self._guarded_input_token_limit(request)
        guarded_output_tokens = (
            request.parameters.max_output_tokens + self.reasoning_token_allowance
        )
        projected_cost = (
            guarded_input_tokens * OPENAI_INPUT_USD_PER_MILLION_TOKENS
            + guarded_output_tokens * OPENAI_OUTPUT_USD_PER_MILLION_TOKENS
        ) / 1_000_000
        return guarded_input_tokens, guarded_output_tokens, projected_cost

    def reserve(self, request: ConversationProviderRequest, scope: PublicTurnScope) -> int:
        guarded_input_tokens, guarded_output_tokens, projected_cost = self._projected_guard(request)
        with self._lock:
            trace_binding = self._attempts_by_scope.get(scope)
            if trace_binding is None:
                prior_attempts = 0
            else:
                bound_trace_id, prior_attempts = trace_binding
                if request.trace_id != bound_trace_id:
                    raise ProviderCallBudgetExhausted(
                        "public evaluator turn scope is already bound to another trace"
                    )
            provider_call_count = len(self._calls)
            if provider_call_count >= self.maximum_calls:
                raise ProviderCallBudgetExhausted("authorized provider-call ceiling reached")
            if prior_attempts >= MAX_ATTEMPTS_PER_TURN:
                raise ProviderCallBudgetExhausted("max-one validator retry already consumed")
            is_retry = prior_attempts == 1
            base_call_count = len(self._attempts_by_scope)
            if not is_retry and base_call_count >= self.required_base_calls:
                raise ProviderCallBudgetExhausted("all mandatory base turns already executed")

            bases_after = base_call_count + (0 if is_retry else 1)
            mandatory_bases_remaining = self.required_base_calls - bases_after
            if provider_call_count + 1 + mandatory_bases_remaining > self.maximum_calls:
                raise ProviderCallBudgetExhausted(
                    "retry would consume a call reserved for a mandatory base turn"
                )
            guarded_cost = sum(
                float(call.get("charged_guard_cost_usd") or 0.0) for call in self._calls
            )
            if guarded_cost + projected_cost > self.maximum_cost_usd + 1e-12:
                raise ProviderCallBudgetExhausted(
                    "next-call projection would exceed the explicit USD ceiling"
                )

            self._attempts_by_scope[scope] = (request.trace_id, prior_attempts + 1)
            call_number = provider_call_count + 1
            self._calls.append(
                {
                    "call_number": call_number,
                    "session_id": scope.session_id,
                    "turn": scope.turn,
                    "turn_id": scope.turn_id,
                    "attempt_kind": "validator_retry" if is_retry else "base",
                    "status": "in_flight",
                    "requested_visible_output_token_limit": request.parameters.max_output_tokens,
                    "guarded_input_token_limit": guarded_input_tokens,
                    "guarded_output_token_limit": guarded_output_tokens,
                    "projected_guard_cost_usd": round(projected_cost, 8),
                    "charged_guard_cost_usd": round(projected_cost, 8),
                }
            )
        self._notify()
        return call_number

    def settle_success(self, call_number: int, response: ConversationProviderResponse) -> None:
        usage = response.usage
        input_tokens = usage.input_tokens if usage is not None else None
        output_tokens = usage.output_tokens if usage is not None else None
        actual_cost_usd = None
        if input_tokens is not None and output_tokens is not None:
            actual_cost_usd = (
                input_tokens * OPENAI_INPUT_USD_PER_MILLION_TOKENS
                + output_tokens * OPENAI_OUTPUT_USD_PER_MILLION_TOKENS
            ) / 1_000_000
        with self._lock:
            record = self._calls[call_number - 1]
            projected = cast(float, record["projected_guard_cost_usd"])
            guard_projection_valid = actual_cost_usd is None or actual_cost_usd <= projected + 1e-12
            record.update(
                {
                    "status": "succeeded",
                    "finish_status": response.finish_status,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "actual_cost_usd": (
                        round(actual_cost_usd, 8) if actual_cost_usd is not None else None
                    ),
                    "usage_complete": actual_cost_usd is not None,
                    "guard_projection_valid": guard_projection_valid,
                    "charged_guard_cost_usd": round(
                        actual_cost_usd if actual_cost_usd is not None else projected,
                        8,
                    ),
                    "provider_metrics": (
                        _safe_provider_metrics(response.metrics.as_log_fields())
                        if response.metrics is not None
                        else None
                    ),
                }
            )
        self._notify()

    def settle_failure(self, call_number: int, error: BaseException) -> None:
        metrics = (
            _safe_provider_metrics(error.metrics.as_log_fields())
            if isinstance(error, ConversationProviderError) and error.metrics is not None
            else None
        )
        with self._lock:
            self._calls[call_number - 1].update(
                {
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "failure_reason": (
                        error.reason.value if isinstance(error, ConversationProviderError) else None
                    ),
                    "finish_status": None,
                    "input_tokens": None,
                    "output_tokens": None,
                    "actual_cost_usd": None,
                    "usage_complete": False,
                    "guard_projection_valid": True,
                    "provider_metrics": metrics,
                }
            )
        self._notify()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            calls = [dict(call) for call in self._calls]
            base_call_count = len(self._attempts_by_scope)
        successful = [call for call in calls if call.get("status") == "succeeded"]
        actual_costs = [call.get("actual_cost_usd") for call in successful]
        guarded_cost = sum(float(call.get("charged_guard_cost_usd") or 0.0) for call in calls)
        usage_complete = len(successful) == len(calls) and all(
            isinstance(cost, (int, float)) and not isinstance(cost, bool) for cost in actual_costs
        )
        guard_projection_valid = all(
            call.get("guard_projection_valid") is not False for call in calls
        )
        return {
            "required_base_calls": self.required_base_calls,
            "maximum_provider_calls": self.maximum_calls,
            "maximum_attempts_per_turn": MAX_ATTEMPTS_PER_TURN,
            "base_call_count": base_call_count,
            "provider_call_count": len(calls),
            "successful_provider_call_count": len(successful),
            "input_tokens": sum(cast(int, call.get("input_tokens") or 0) for call in successful),
            "output_tokens": sum(cast(int, call.get("output_tokens") or 0) for call in successful),
            "maximum_cost_usd": self.maximum_cost_usd,
            "actual_usage_cost_usd": round(
                sum(float(cost) for cost in actual_costs if isinstance(cost, (int, float))),
                8,
            ),
            "guarded_cost_usd": round(guarded_cost, 8),
            "usage_complete": usage_complete,
            "guard_projection_valid": guard_projection_valid,
            "pricing": {
                "currency": "USD",
                "input_usd_per_million_tokens": OPENAI_INPUT_USD_PER_MILLION_TOKENS,
                "output_usd_per_million_tokens": OPENAI_OUTPUT_USD_PER_MILLION_TOKENS,
                "snapshot": PRICING_SNAPSHOT,
                "fx_conversion_used": False,
            },
            "within_call_limit": len(calls) <= self.maximum_calls,
            "within_cost_limit": (
                guarded_cost <= self.maximum_cost_usd + 1e-12 and guard_projection_valid
            ),
            "mandatory_base_calls_complete": base_call_count == self.required_base_calls,
            "calls": calls,
        }

    def _notify(self) -> None:
        if self.on_change is not None:
            self.on_change()


@dataclass(slots=True)
class BudgetedOpenAIProvider:
    """Wrap the production foreground provider with the atomic module ledger."""

    delegate: ConversationProvider
    ledger: AtomicOpenAICallLedger
    scope_binding: TurnScopeBinding

    async def generate(
        self, request: ConversationProviderRequest, /
    ) -> ConversationProviderResponse:
        scope = self.scope_binding.require()
        call_number = self.ledger.reserve(request, scope)
        try:
            response = await self.delegate.generate(request)
        except BaseException as error:
            self.ledger.settle_failure(call_number, error)
            raise
        self.ledger.settle_success(call_number, response)
        return response


def _safe_attempt(value: object, attempt_number: int) -> dict[str, Any]:
    raw = (
        asdict(cast(Any, value))
        if hasattr(value, "__dataclass_fields__")
        else _object(value, "attempt")
    )
    return {
        "attempt_number": attempt_number,
        "wall_ms": raw.get("wall_ms"),
        "request_schema_version": raw.get("request_schema_version"),
        "context_schema_version": raw.get("context_schema_version"),
        "message_count": raw.get("message_count"),
        "message_role_counts": raw.get("message_role_counts"),
        "request_content_chars": raw.get("request_content_chars"),
        "temperature": raw.get("temperature"),
        "max_output_tokens": raw.get("max_output_tokens"),
        "input_tokens": raw.get("input_tokens"),
        "output_tokens": raw.get("output_tokens"),
        "provider_metrics": _safe_provider_metrics(raw.get("provider_metrics")),
        "finish_status": raw.get("finish_status"),
        "succeeded": raw.get("succeeded"),
        "error_type": raw.get("error_type"),
    }


def _safe_usage(reply: SatoriReply) -> dict[str, int | None] | None:
    if reply.usage is None:
        return None
    return {
        "input_tokens": reply.usage.input_tokens,
        "output_tokens": reply.usage.output_tokens,
    }


def _safe_timings(reply: SatoriReply) -> dict[str, int | float | None]:
    raw = asdict(reply.timings)
    return {
        key: (
            raw.get(key)
            if raw.get(key) is None
            or (isinstance(raw.get(key), (int, float)) and not isinstance(raw.get(key), bool))
            else None
        )
        for key in _SAFE_TIMING_KEYS
    }


def _validate_v24_decision_codes(manifest: Mapping[str, Any]) -> None:
    """Reconstruct the closed typed decision so artifacts cannot retain invented codes."""

    preserve_uncertainty = manifest.get("character_delivery_preserve_uncertainty")
    cognition_uncertainty = manifest.get("cognition_preserve_uncertainty")
    if type(preserve_uncertainty) is not bool or type(cognition_uncertainty) is not bool:
        raise ValueError("v24 delivery/cognition uncertainty flags must be boolean")
    intent_registry_version = _strict_int(
        manifest.get("cognition_intent_registry_version"),
        "manifest.cognition_intent_registry_version",
    )
    template_registry_version = _strict_int(
        manifest.get("cognition_template_registry_version"),
        "manifest.cognition_template_registry_version",
    )
    template_schema_version = _strict_int(
        manifest.get("cognition_template_schema_version"),
        "manifest.cognition_template_schema_version",
    )
    if (
        intent_registry_version != EXPECTED_COGNITION_INTENT_REGISTRY_VERSION
        or template_registry_version != EXPECTED_COGNITION_TEMPLATE_REGISTRY_VERSION
        or manifest.get("cognition_template_id") != EXPECTED_COGNITION_TEMPLATE_ID
        or template_schema_version != EXPECTED_COGNITION_TEMPLATE_SCHEMA_VERSION
    ):
        raise ValueError("v24 cognition registry/template metadata drift")
    primary_intent = _non_blank_string(
        manifest.get("cognition_primary_intent"), "manifest.cognition_primary_intent"
    )
    intent_tags = _string_tuple(
        manifest.get("cognition_intent_tags"), "manifest.cognition_intent_tags"
    )
    required_point_codes = _string_tuple(
        manifest.get("cognition_required_point_codes"),
        "manifest.cognition_required_point_codes",
    )
    forbidden_claim_codes = _string_tuple(
        manifest.get("cognition_forbidden_claim_codes"),
        "manifest.cognition_forbidden_claim_codes",
    )
    decision_schema_version = _strict_int(
        manifest.get("character_delivery_decision_schema_version"),
        "manifest.character_delivery_decision_schema_version",
    )
    decision = CharacterDeliveryDecision(
        schema_version=decision_schema_version,
        goal=CharacterDeliveryGoal(cast(str, manifest.get("character_delivery_goal"))),
        voice=CharacterDeliveryVoice(cast(str, manifest.get("character_delivery_voice"))),
        grounding=CharacterGroundingMode(cast(str, manifest.get("character_delivery_grounding"))),
        continuation=CharacterContinuationMode(
            cast(str, manifest.get("character_delivery_continuation"))
        ),
        pressure=CharacterPressureLevel(cast(str, manifest.get("character_delivery_pressure"))),
        position_stance=PositionStance(
            cast(str, manifest.get("character_delivery_position_stance"))
        ),
        preserve_uncertainty=preserve_uncertainty,
        cognition_intent_registry_version=intent_registry_version,
        cognition_primary_intent=primary_intent,
        cognition_intent_tags=intent_tags,
        required_point_codes=required_point_codes,
        forbidden_claim_codes=forbidden_claim_codes,
        response_verbosity=ResponseVerbosity(
            cast(str, manifest.get("cognition_response_verbosity"))
        ),
    )
    if decision.position_stance.value != manifest.get("cognition_position_stance"):
        raise ValueError("v24 delivery decision did not preserve cognition stance")
    if decision.preserve_uncertainty != cognition_uncertainty:
        raise ValueError("v24 delivery decision did not preserve cognition uncertainty")


def _safe_v24_manifest(reply: SatoriReply) -> dict[str, Any]:
    raw = _sanitized_manifest(reply)
    safe = {key: raw.get(key) for key in _SAFE_V24_MANIFEST_KEYS}
    if safe["policy_id"] != EXPECTED_POLICY_ID or safe["policy_schema_version"] != 24:
        raise RuntimeError("production composition did not use behavior policy v24")
    if safe["character_expression_plan_schema_version"] is not None:
        raise RuntimeError("v24 composition unexpectedly retained a legacy expression plan")
    if safe["character_delivery_decision_schema_version"] != 1:
        raise RuntimeError("v24 composition did not emit character delivery decision v1")
    try:
        _validate_v24_decision_codes(safe)
        _validate_manifest_metadata(safe)
    except (TypeError, ValueError) as error:
        raise RuntimeError("v24 manifest contains invalid typed metadata") from error
    return safe


def _validate_module_turn_manifest(
    module_id: str,
    turn_number: int,
    manifest: Mapping[str, Any],
) -> None:
    """Pin evaluator-critical typed behavior without judging or prescribing reply text."""

    if module_id != "hurt_and_repair":
        return
    expected: dict[int, dict[str, object]] = {
        1: {
            "relationship_expression_profile": "established_positive",
            "relationship_recent_strain": False,
            "character_delivery_goal": "hold_boundary",
            "character_delivery_voice": "cool_reserve",
            "character_delivery_continuation": "boundary",
        },
        2: {
            "relationship_expression_profile": "guarded_only_when_relationally_relevant",
            "relationship_recent_strain": True,
            "cognition_primary_intent": "receive_repair",
            "character_delivery_goal": "owned_response",
            "character_delivery_voice": "cool_reserve",
            "character_delivery_continuation": "complete",
        },
        3: {
            "relationship_expression_profile": "guarded_only_when_relationally_relevant",
            "relationship_recent_strain": True,
            "cognition_primary_intent": "answer_directly",
            "character_delivery_goal": "guarded_help",
            "character_delivery_voice": "cool_reserve",
            "character_delivery_grounding": "trusted_context",
            "character_delivery_continuation": "guarded",
        },
    }
    contract = expected.get(turn_number)
    if contract is None or any(manifest.get(key) != value for key, value in contract.items()):
        raise ValueError("hurt/repair module typed turn contract drift")


def _relationship_conditioning(profile: str) -> dict[str, int] | None:
    # These are deterministic typed relationship events, never provider samples or hidden prose.
    if profile == "fresh_undeveloped_neutral":
        return None
    if profile == "developing_neutral":
        return {
            "positive_sessions": 3,
            "positive_turns_per_session": 4,
            "negative_sessions": 0,
            "negative_turns_per_session": 0,
        }
    if profile == "established_positive":
        return {
            "positive_sessions": 10,
            "positive_turns_per_session": 8,
            "negative_sessions": 0,
            "negative_turns_per_session": 0,
        }
    raise ValueError(f"unsupported relationship setup: {profile}")


def _safe_conditioning_report(
    requested_profile: str,
    raw: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if raw is None:
        return {
            "requested_profile": requested_profile,
            "method": "canonical_fresh_relationship_state",
            "actual_profile": "fresh_undeveloped_neutral",
            "processed_interactions": 0,
        }
    state = _object(raw.get("state"), "conditioning.state")
    projection = _object(state.get("projection"), "conditioning.state.projection")
    actual_profile = (
        "fresh_undeveloped_neutral"
        if projection.get("maturity") == "low"
        else (
            "established_positive"
            if projection.get("maturity") == "established"
            and projection.get("familiarity") in {"high", "very_high"}
            and (
                projection.get("trust") in {"high", "very_high"}
                or projection.get("comfort") in {"high", "very_high"}
            )
            else "developing_neutral"
        )
    )
    if actual_profile != requested_profile:
        raise RuntimeError(
            f"relationship conditioning produced {actual_profile}, expected {requested_profile}"
        )
    return {
        "requested_profile": requested_profile,
        "method": "typed_deterministic_relationship_conditioning",
        "actual_profile": actual_profile,
        "processed_interactions": raw.get("processed_interactions"),
        "state_version": state.get("state_version"),
        "maturity_value": state.get("maturity_value"),
        "qualified_interaction_count": state.get("qualified_interaction_count"),
        "distinct_session_count": state.get("distinct_session_count"),
    }


def _safe_relationship_snapshot(raw: Mapping[str, Any]) -> dict[str, Any]:
    projection = _object(raw.get("projection"), "relationship projection")
    vector = _object(raw.get("vector"), "relationship vector")
    return {
        "state_version": raw.get("state_version"),
        "maturity_value": raw.get("maturity_value"),
        "expression": {
            key: projection.get(key)
            for key in (
                "maturity",
                "familiarity",
                "trust",
                "comfort",
                "closeness",
                "intellectual_respect",
                "affection",
            )
        },
        "vector": {
            key: vector.get(key)
            for key in (
                "familiarity",
                "trust",
                "comfort",
                "closeness",
                "intellectual_respect",
                "affection",
            )
        },
        "processed_interaction_count": raw.get("processed_interaction_count"),
        "qualified_interaction_count": raw.get("qualified_interaction_count"),
        "positive_evidence_count": raw.get("positive_evidence_count"),
        "negative_evidence_count": raw.get("negative_evidence_count"),
    }


def _unsafe_artifact_paths(value: object, path: str = "$") -> tuple[str, ...]:
    unsafe: list[str] = []
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}"
            if key.casefold() in _UNSAFE_ARTIFACT_KEYS:
                unsafe.append(child_path)
            unsafe.extend(_unsafe_artifact_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            unsafe.extend(_unsafe_artifact_paths(child, f"{path}[{index}]"))
    return tuple(unsafe)


def assert_safe_artifact(report: Mapping[str, Any]) -> None:
    unsafe = _unsafe_artifact_paths(report)
    if unsafe:
        raise ValueError(f"unsafe evaluation artifact fields: {', '.join(unsafe)}")


def _write_safe_report(path: Path, report: dict[str, Any]) -> None:
    assert_safe_artifact(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _review_boolean_template(keys: Sequence[str]) -> dict[str, None]:
    return {key: None for key in keys}


def _human_review_template(selected: ModuleSpec) -> dict[str, Any]:
    return {
        "status": "pending",
        "reviewer": "human",
        "automated_text_judging_performed": False,
        "provider_sample_is_authority": False,
        "artifact_id": None,
        "sample_digest": None,
        "sessions": [
            {
                "session_id": f"{selected.module_id}-replica-{replica_number}",
                "turns": [
                    {
                        "turn": turn["turn"],
                        "turn_id": turn["id"],
                        "hard_safety_booleans": {},
                        "quality_booleans": _review_boolean_template(
                            cast(list[str], turn["review_dimensions"])
                        ),
                    }
                    for turn in selected.turns
                ],
                "dialogue_booleans": _review_boolean_template(selected.dialogue_review_dimensions),
            }
            for replica_number in range(1, EXPECTED_REPLICA_COUNT + 1)
        ],
        "cross_replica_booleans": _review_boolean_template(
            selected.cross_replica_review_dimensions
        ),
        "module_pass": None,
        "note": HUMAN_REVIEW_NOTE,
    }


_SAMPLE_DIGEST_KEYS = (
    "schema_version",
    "recorded_at",
    "completed_at",
    "checkpoint",
    "purpose",
    "artifact_id",
    "corpus_id",
    "policy_id",
    "module_id",
    "artifact_contract",
    "configuration",
    "budget",
    "sessions",
)


def sample_content_digest(report: Mapping[str, Any]) -> str:
    payload = {key: report.get(key) for key in _SAMPLE_DIGEST_KEYS}
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _strict_boolean_map(value: object, keys: Sequence[str], label: str) -> bool:
    decisions = _object(value, label)
    if set(decisions) != set(keys):
        raise ValueError(f"{label} must contain exactly the declared dimension keys")
    if any(type(decisions[key]) is not bool for key in keys):
        raise ValueError(f"{label} values must be explicit booleans")
    return all(cast(bool, decisions[key]) for key in keys)


def aggregate_human_review(
    fixture: Mapping[str, Any],
    report: Mapping[str, Any],
    review: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate explicit human booleans and bind them to one immutable module sample."""

    validate_fixture(fixture)
    module_id = report.get("module_id")
    if not isinstance(module_id, str):
        raise ValueError("report module_id is missing")
    selected = module_spec(fixture, module_id)
    _validate_completed_report(fixture, selected, report)
    if set(review) != {
        "artifact_id",
        "sample_digest",
        "module_id",
        "sessions",
        "cross_replica_booleans",
        "module_pass",
    }:
        raise ValueError("human review schema drift")
    if (
        review.get("artifact_id") != report.get("artifact_id")
        or review.get("sample_digest") != report.get("sample_digest")
        or review.get("module_id") != module_id
    ):
        raise ValueError("human review is not bound to this module sample")

    hard_dimensions = _string_tuple(fixture.get("hard_safety_dimensions"), "hard_safety_dimensions")
    sessions = _array(review.get("sessions"), "review.sessions")
    if len(sessions) != EXPECTED_REPLICA_COUNT:
        raise ValueError("human review must cover all three sessions")
    all_passes: list[bool] = []
    compact_sessions: list[dict[str, Any]] = []
    for replica_number, raw_session in enumerate(sessions, start=1):
        session = _object(raw_session, f"review.sessions[{replica_number - 1}]")
        if set(session) != {"session_id", "turns", "dialogue_booleans"}:
            raise ValueError("human review session schema drift")
        expected_session_id = f"{module_id}-replica-{replica_number}"
        if session.get("session_id") != expected_session_id:
            raise ValueError("human review sessions must use the public ordered session IDs")
        turns = _array(session.get("turns"), "review.session.turns")
        if len(turns) != len(selected.turns):
            raise ValueError("human review session must cover every module turn")
        compact_turns: list[dict[str, Any]] = []
        for fixture_turn, raw_turn in zip(selected.turns, turns, strict=True):
            turn = _object(raw_turn, "review.turn")
            if set(turn) != {"turn", "turn_id", "hard_safety_booleans", "quality_booleans"}:
                raise ValueError("human review turn schema drift")
            if (
                turn.get("turn") != fixture_turn["turn"]
                or turn.get("turn_id") != fixture_turn["id"]
            ):
                raise ValueError("human review turn does not match the public fixture")
            hard_pass = _strict_boolean_map(
                turn.get("hard_safety_booleans"), hard_dimensions, "hard_safety_booleans"
            )
            quality_pass = _strict_boolean_map(
                turn.get("quality_booleans"),
                cast(list[str], fixture_turn["review_dimensions"]),
                "quality_booleans",
            )
            turn_pass = hard_pass and quality_pass
            all_passes.append(turn_pass)
            compact_turns.append(
                {
                    "turn": fixture_turn["turn"],
                    "turn_id": fixture_turn["id"],
                    "hard_safety_pass": hard_pass,
                    "quality_pass": quality_pass,
                    "turn_pass": turn_pass,
                }
            )
        dialogue_pass = _strict_boolean_map(
            session.get("dialogue_booleans"),
            selected.dialogue_review_dimensions,
            "dialogue_booleans",
        )
        all_passes.append(dialogue_pass)
        compact_sessions.append(
            {
                "session_id": expected_session_id,
                "dialogue_pass": dialogue_pass,
                "turns": compact_turns,
            }
        )
    cross_replica_pass = _strict_boolean_map(
        review.get("cross_replica_booleans"),
        selected.cross_replica_review_dimensions,
        "cross_replica_booleans",
    )
    all_passes.append(cross_replica_pass)
    accepted = all(all_passes)
    if type(review.get("module_pass")) is not bool or review.get("module_pass") is not accepted:
        raise ValueError("module_pass must be an explicit boolean equal to all blocking reviews")
    return {
        "status": "accepted" if accepted else "rejected",
        "artifact_id": report["artifact_id"],
        "sample_digest": report["sample_digest"],
        "module_id": module_id,
        "decision_source": "explicit_human_boolean_review",
        "automated_text_judging_performed": False,
        "provider_sample_is_authority": False,
        "accepted": accepted,
        "sessions": compact_sessions,
        "cross_replica_pass": cross_replica_pass,
        "employer_demo_readiness_accepted": False,
        "readiness_note": "all four separately authorized modules must pass",
    }


def _public_mapping_digest(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def employer_demo_readiness_content_digest(value: Mapping[str, Any]) -> str:
    """Bind the cross-module decision to its exact public module evidence."""

    canonical_value = dict(value)
    canonical_value.pop("readiness_digest", None)
    return _public_mapping_digest(canonical_value)


def _sha256_digest(value: object, label: str) -> str:
    digest = _non_blank_string(value, label)
    prefix = "sha256:"
    hexadecimal = digest.removeprefix(prefix)
    if (
        not digest.startswith(prefix)
        or len(hexadecimal) != 64
        or any(character not in "0123456789abcdef" for character in hexadecimal)
    ):
        raise ValueError(f"{label} must be one canonical SHA-256 digest")
    return digest


def validate_employer_demo_readiness(fixture: Mapping[str, Any], value: Mapping[str, Any]) -> None:
    """Fail closed when a persisted cross-module readiness decision is edited or incomplete."""

    validate_fixture(fixture)
    assert_safe_artifact(value)
    expected_keys = {
        "schema_version",
        "status",
        "corpus_id",
        "policy_id",
        "decision_source",
        "automated_text_judging_performed",
        "provider_sample_is_authority",
        "required_module_ids",
        "modules",
        "all_modules_accepted",
        "employer_demo_readiness_accepted",
        "readiness_digest",
    }
    _require_exact_keys(value, expected_keys, "employer-demo readiness result")
    if (
        value.get("schema_version") != 1
        or value.get("corpus_id") != EXPECTED_CORPUS_ID
        or value.get("policy_id") != EXPECTED_POLICY_ID
        or value.get("decision_source") != "four_digest_bound_explicit_human_module_reviews"
        or value.get("automated_text_judging_performed") is not False
        or value.get("provider_sample_is_authority") is not False
        or value.get("required_module_ids") != list(EXPECTED_MODULE_IDS)
    ):
        raise ValueError("employer-demo readiness identity/schema contract drift")

    modules = _array(value.get("modules"), "employer-demo readiness modules")
    if len(modules) != len(EXPECTED_MODULE_IDS):
        raise ValueError("employer-demo readiness requires exactly four module decisions")
    artifact_ids: set[str] = set()
    artifact_uuids: set[uuid.UUID] = set()
    sample_digests: set[str] = set()
    review_digests: set[str] = set()
    module_acceptance: list[bool] = []
    for expected_module_id, raw_module in zip(EXPECTED_MODULE_IDS, modules, strict=True):
        module = _object(raw_module, "employer-demo readiness module")
        _require_exact_keys(
            module,
            {"module_id", "artifact_id", "sample_digest", "human_review_digest", "accepted"},
            "employer-demo readiness module",
        )
        if module.get("module_id") != expected_module_id:
            raise ValueError("employer-demo readiness module order/identity drift")
        artifact_id = _non_blank_string(module.get("artifact_id"), "readiness artifact_id")
        artifact_prefix = f"satori-checkpoint142-openai-v24:{expected_module_id}:"
        if not artifact_id.startswith(artifact_prefix):
            raise ValueError("employer-demo readiness artifact has the wrong module prefix")
        try:
            artifact_uuid = uuid.UUID(artifact_id.removeprefix(artifact_prefix))
        except ValueError as error:
            raise ValueError("employer-demo readiness artifact is not UUID-bound") from error
        sample_digest = _sha256_digest(module.get("sample_digest"), "readiness sample_digest")
        review_digest = _sha256_digest(
            module.get("human_review_digest"), "readiness human_review_digest"
        )
        accepted = module.get("accepted")
        if type(accepted) is not bool:
            raise ValueError("employer-demo module acceptance must be an explicit boolean")
        artifact_ids.add(artifact_id)
        artifact_uuids.add(artifact_uuid)
        sample_digests.add(sample_digest)
        review_digests.add(review_digest)
        module_acceptance.append(accepted)
    expected_count = len(EXPECTED_MODULE_IDS)
    if not all(
        len(values) == expected_count
        for values in (artifact_ids, artifact_uuids, sample_digests, review_digests)
    ):
        raise ValueError("employer-demo readiness module evidence must be distinct")

    accepted = all(module_acceptance)
    if (
        type(value.get("all_modules_accepted")) is not bool
        or value.get("all_modules_accepted") is not accepted
        or type(value.get("employer_demo_readiness_accepted")) is not bool
        or value.get("employer_demo_readiness_accepted") is not accepted
        or value.get("status") != ("accepted" if accepted else "rejected")
    ):
        raise ValueError("employer-demo readiness acceptance/status mismatch")
    readiness_digest = _sha256_digest(
        value.get("readiness_digest"), "employer-demo readiness_digest"
    )
    if readiness_digest != employer_demo_readiness_content_digest(value):
        raise ValueError("employer-demo readiness digest is missing or stale")


def aggregate_employer_demo_readiness(
    fixture: Mapping[str, Any],
    reports: Sequence[Mapping[str, Any]],
    reviews: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Produce one digest-bound decision only from all four distinct reviewed modules."""

    validate_fixture(fixture)
    if len(reports) != len(EXPECTED_MODULE_IDS) or len(reviews) != len(EXPECTED_MODULE_IDS):
        raise ValueError("employer-demo readiness requires exactly four reports and reviews")

    reports_by_module: dict[str, Mapping[str, Any]] = {}
    reviews_by_module: dict[str, Mapping[str, Any]] = {}
    for label, values, destination in (
        ("report", reports, reports_by_module),
        ("review", reviews, reviews_by_module),
    ):
        for value in values:
            module_id = value.get("module_id")
            if not isinstance(module_id, str) or module_id not in EXPECTED_MODULE_IDS:
                raise ValueError(f"employer-demo {label} has an unknown module_id")
            if module_id in destination:
                raise ValueError(f"employer-demo {label} module_id must be unique")
            destination[module_id] = value
    if set(reports_by_module) != set(EXPECTED_MODULE_IDS) or set(reviews_by_module) != set(
        EXPECTED_MODULE_IDS
    ):
        raise ValueError("employer-demo readiness requires every declared module exactly once")

    module_decisions: list[dict[str, Any]] = []
    shared_configuration: dict[str, Any] | None = None
    artifact_ids: set[str] = set()
    sample_digests: set[str] = set()
    for module_id in EXPECTED_MODULE_IDS:
        report = reports_by_module[module_id]
        review = reviews_by_module[module_id]
        decision = aggregate_human_review(fixture, report, review)
        configuration = dict(_object(report.get("configuration"), "report.configuration"))
        configuration.pop("module_id", None)
        configuration.pop("derived_processing", None)
        configuration.pop("execution_plan_digest", None)
        if shared_configuration is None:
            shared_configuration = configuration
        elif configuration != shared_configuration:
            raise ValueError("employer-demo modules must share one production configuration")

        artifact_id = cast(str, decision["artifact_id"])
        sample_digest = cast(str, decision["sample_digest"])
        if artifact_id in artifact_ids or sample_digest in sample_digests:
            raise ValueError("employer-demo module artifacts and samples must be distinct")
        artifact_ids.add(artifact_id)
        sample_digests.add(sample_digest)
        module_decisions.append(
            {
                "module_id": module_id,
                "artifact_id": artifact_id,
                "sample_digest": sample_digest,
                "human_review_digest": _public_mapping_digest(review),
                "accepted": decision["accepted"],
            }
        )

    accepted = all(cast(bool, item["accepted"]) for item in module_decisions)
    result: dict[str, Any] = {
        "schema_version": 1,
        "status": "accepted" if accepted else "rejected",
        "corpus_id": EXPECTED_CORPUS_ID,
        "policy_id": EXPECTED_POLICY_ID,
        "decision_source": "four_digest_bound_explicit_human_module_reviews",
        "automated_text_judging_performed": False,
        "provider_sample_is_authority": False,
        "required_module_ids": list(EXPECTED_MODULE_IDS),
        "modules": module_decisions,
        "all_modules_accepted": accepted,
        "employer_demo_readiness_accepted": accepted,
    }
    result["readiness_digest"] = employer_demo_readiness_content_digest(result)
    validate_employer_demo_readiness(fixture, result)
    return result


async def _build_budgeted_runtime(
    settings: Settings,
    database_path: Path,
    *,
    alembic_config: Path,
    selected: ModuleSpec,
    ledger: AtomicOpenAICallLedger,
    conditioning: dict[str, int] | None,
) -> tuple[EvaluationRuntime, TurnScopeBinding, dict[str, Any] | None]:
    runtime, conditioning_report = await _build_runtime(
        settings,
        database_path,
        alembic_config=alembic_config,
        conditioning=conditioning,
        behavior_policy=BEHAVIOR_POLICY_V24,
    )
    binding = TurnScopeBinding()
    runtime.conversation_provider.delegate = BudgetedOpenAIProvider(
        delegate=runtime.conversation_provider.delegate,
        ledger=ledger,
        scope_binding=binding,
    )
    return runtime, binding, conditioning_report


async def _run_replica(
    *,
    settings: Settings,
    database_path: Path,
    alembic_config: Path,
    selected: ModuleSpec,
    replica_number: int,
    ledger: AtomicOpenAICallLedger,
    checkpoint: Callable[[], None],
    record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    session_id = f"{selected.module_id}-replica-{replica_number}"
    if record is None:
        record = {
            "session_id": session_id,
            "fresh_database": True,
            "completed": False,
            "relationship_setup": None,
            "restart_boundaries": [],
            "turns": [],
        }
    elif record.get("session_id") != session_id:
        raise ValueError("replica record does not match its public session id")
    runtime: EvaluationRuntime | None = None
    binding: TurnScopeBinding | None = None
    application_session_id: str | None = None
    id_generator = Uuid4Generator()

    async def start_runtime(
        conditioning: dict[str, int] | None,
    ) -> tuple[EvaluationRuntime, TurnScopeBinding, dict[str, Any] | None]:
        return await _build_budgeted_runtime(
            settings,
            database_path,
            alembic_config=alembic_config,
            selected=selected,
            ledger=ledger,
            conditioning=conditioning,
        )

    try:
        runtime, binding, raw_conditioning = await start_runtime(
            _relationship_conditioning(selected.relationship_setup)
        )
        record["relationship_setup"] = _safe_conditioning_report(
            selected.relationship_setup,
            raw_conditioning,
        )
        application_session_id = runtime.services.start_session.execute().session_id
        checkpoint()

        for fixture_turn in selected.turns:
            turn_number = cast(int, fixture_turn["turn"])
            scope = PublicTurnScope(
                session_id=session_id,
                turn=turn_number,
                turn_id=cast(str, fixture_turn["id"]),
            )
            first_attempt = len(runtime.conversation_provider.attempts)
            trace_id = id_generator.new()
            relationship_before = _safe_relationship_snapshot(_current_relationship(runtime))
            binding.set(scope)
            try:
                reply = await runtime.services.talk.execute(
                    TalkInput(
                        user_text=cast(str, fixture_turn["user_text"]),
                        trace_id=trace_id,
                        client_request_id=id_generator.new(),
                        session_id=application_session_id,
                    )
                )
            finally:
                binding.clear()
            attempts = runtime.conversation_provider.attempts[first_attempt:]
            manifest = _safe_v24_manifest(reply)
            _validate_module_turn_manifest(selected.module_id, turn_number, manifest)
            usage = _safe_usage(reply)
            safe_attempts = [
                _safe_attempt(attempt, attempt_number)
                for attempt_number, attempt in enumerate(attempts, start=1)
            ]
            if (
                reply.provider != EXPECTED_PROVIDER.value
                or reply.model != EXPECTED_MODEL
                or reply.finish_status != "completed"
                or reply.replayed
                or usage is None
                or len(safe_attempts) not in {1, 2}
            ):
                raise RuntimeError("production turn did not produce one comparable OpenAI reply")
            for key in ("input_tokens", "output_tokens"):
                if _strict_int(usage.get(key), f"live_reply.usage.{key}") < 0:
                    raise RuntimeError("production turn returned invalid usage")
            for attempt_number, safe_attempt in enumerate(safe_attempts, start=1):
                _validate_provider_attempt(safe_attempt, attempt_number)
            if (
                turn_number == 1
                and manifest.get("relationship_expression_profile") != selected.relationship_setup
            ):
                raise RuntimeError("first turn did not observe the requested relationship profile")
            turn_record: dict[str, Any] = {
                "turn": turn_number,
                "turn_id": fixture_turn["id"],
                "user": fixture_turn["user_text"],
                "reply": _public_sampled_reply(reply),
                "generation": {
                    "provider": reply.provider,
                    "requested_model": EXPECTED_MODEL,
                    "reported_model": reply.model,
                    "finish_status": reply.finish_status,
                    "replayed": reply.replayed,
                },
                "usage": usage,
                "timings_ms": _safe_timings(reply),
                "provider_attempt_count": len(safe_attempts),
                "provider_attempts": safe_attempts,
                "manifest": manifest,
                "relationship_before": relationship_before,
                "derived_processing": "not_requested",
            }
            cast(list[dict[str, Any]], record["turns"]).append(turn_record)
            checkpoint()

            if turn_number in selected.derived_processing_after_turns:
                post = await _process_post_response(runtime, reply.interaction_id, trace_id)
                turn_record["derived_processing"] = "production_post_response_path"
                turn_record["post_response"] = post
                turn_record["relationship_after_derived"] = _safe_relationship_snapshot(
                    _current_relationship(runtime)
                )
                checkpoint()
                if post.get("failure_phases"):
                    raise RuntimeError("required derived processing failed")

            if turn_number in selected.restart_after_turns:
                runtime.services.close_session.execute(application_session_id)
                application_session_id = None
                runtime.close()
                runtime = None
                binding = None
                runtime, binding, _ = await start_runtime(None)
                application_session_id = runtime.services.start_session.execute().session_id
                cast(list[int], record["restart_boundaries"]).append(turn_number)
                checkpoint()

        record["completed"] = True
        checkpoint()
        return record
    finally:
        if runtime is not None:
            if application_session_id is not None:
                runtime.services.close_session.execute(application_session_id)
            runtime.close()


def _expand_hard_safety_template(
    human_review: dict[str, Any], hard_dimensions: Sequence[str]
) -> None:
    sessions = cast(list[dict[str, Any]], human_review["sessions"])
    for session in sessions:
        for turn in cast(list[dict[str, Any]], session["turns"]):
            turn["hard_safety_booleans"] = _review_boolean_template(hard_dimensions)


def _validate_artifact_contract(value: object) -> None:
    contract = _object(value, "report.artifact_contract")
    expected = {
        "contains_public_fixture_dialogue": True,
        "contains_exact_public_sampled_replies": True,
        "contains_public_session_and_turn_ids": True,
        "retains_remote_request_content": False,
        "retains_private_application_context": False,
        "retains_secret_values": False,
        "retains_temporary_databases": False,
        "automated_text_judging_performed": False,
    }
    _require_exact_keys(contract, set(expected), "report.artifact_contract")
    if contract != expected:
        raise ValueError("completed sample artifact contract drift")


def _validate_report_configuration(value: object, selected: ModuleSpec) -> None:
    configuration = _object(value, "report.configuration")
    expected = {
        "conversation_provider": EXPECTED_PROVIDER.value,
        "conversation_model": EXPECTED_MODEL,
        "openai_reasoning_effort": EXPECTED_REASONING_EFFORT.value,
        "openai_reasoning_token_allowance": EXPECTED_REASONING_ALLOWANCE,
        "responses_api_store": False,
        "remote_conversation_state": "disabled",
        "application_state_scope": "fresh_disposable_database_per_replica",
        "background_providers": "ollama",
        "policy_id": EXPECTED_POLICY_ID,
        "module_id": selected.module_id,
        "execution_plan_digest": execution_plan_content_digest(selected),
        "derived_processing": (
            "serial_only_at_versioned_boundaries"
            if selected.derived_processing_after_turns
            else "none"
        ),
    }
    _require_exact_keys(configuration, set(expected), "report.configuration")
    if configuration != expected:
        raise ValueError("completed sample production configuration drift")


def _validate_relationship_snapshot(value: object, label: str) -> dict[str, Any]:
    snapshot = _object(value, label)
    _require_exact_keys(
        snapshot,
        {
            "state_version",
            "maturity_value",
            "expression",
            "vector",
            "processed_interaction_count",
            "qualified_interaction_count",
            "positive_evidence_count",
            "negative_evidence_count",
        },
        label,
    )
    state_version = _strict_int(snapshot.get("state_version"), f"{label}.state_version")
    if state_version < 1:
        raise ValueError(f"{label}.state_version must be positive")
    maturity = _non_negative_number(snapshot.get("maturity_value"), f"{label}.maturity_value")
    if maturity > 1:
        raise ValueError(f"{label}.maturity_value must not exceed one")

    dimensions = {
        "familiarity",
        "trust",
        "comfort",
        "closeness",
        "intellectual_respect",
        "affection",
    }
    expression = _object(snapshot.get("expression"), f"{label}.expression")
    _require_exact_keys(expression, {"maturity", *dimensions}, f"{label}.expression")
    if expression.get("maturity") not in {"low", "developing", "established"}:
        raise ValueError(f"{label}.expression.maturity is not a closed projection code")
    ordinary_levels = {"low", "emerging", "moderate", "high", "very_high"}
    centered_levels = {"uncertain", "very_low", "low", "moderate", "high", "very_high"}
    for key in ("familiarity", "closeness", "affection"):
        if expression.get(key) not in ordinary_levels:
            raise ValueError(f"{label}.expression.{key} is not a closed projection code")
    for key in ("trust", "comfort", "intellectual_respect"):
        if expression.get(key) not in centered_levels:
            raise ValueError(f"{label}.expression.{key} is not a closed projection code")

    vector = _object(snapshot.get("vector"), f"{label}.vector")
    _require_exact_keys(vector, dimensions, f"{label}.vector")
    for key in dimensions:
        component = _non_negative_number(vector.get(key), f"{label}.vector.{key}")
        if component > 1:
            raise ValueError(f"{label}.vector.{key} must not exceed one")
    for key in (
        "processed_interaction_count",
        "qualified_interaction_count",
        "positive_evidence_count",
        "negative_evidence_count",
    ):
        if _strict_int(snapshot.get(key), f"{label}.{key}") < 0:
            raise ValueError(f"{label}.{key} must be non-negative")
    return snapshot


def _validate_derived_relationship_transition(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    module_id: str,
    turn_number: int,
) -> None:
    before_version = _strict_int(before.get("state_version"), "relationship_before.state_version")
    after_version = _strict_int(
        after.get("state_version"), "relationship_after_derived.state_version"
    )
    before_processed = _strict_int(
        before.get("processed_interaction_count"),
        "relationship_before.processed_interaction_count",
    )
    after_processed = _strict_int(
        after.get("processed_interaction_count"),
        "relationship_after_derived.processed_interaction_count",
    )
    if after_processed != before_processed + 1:
        raise ValueError("derived processing did not consume exactly one relationship source")
    evidence_counter_deltas: dict[str, int] = {}
    for key in (
        "qualified_interaction_count",
        "positive_evidence_count",
        "negative_evidence_count",
    ):
        before_count = _strict_int(before.get(key), f"relationship_before.{key}")
        after_count = _strict_int(after.get(key), f"relationship_after_derived.{key}")
        counter_delta = after_count - before_count
        if counter_delta not in {0, 1}:
            raise ValueError("derived relationship evidence counters are not bounded")
        evidence_counter_deltas[key] = counter_delta
    if after_version - before_version not in {0, 1}:
        raise ValueError("derived relationship state-version transition is not bounded")

    if module_id != "hurt_and_repair":
        return
    if after_version != before_version + 1 or after.get("vector") == before.get("vector"):
        raise ValueError("hurt/repair boundary did not apply a bounded relationship transition")
    if turn_number not in {1, 2}:
        raise ValueError("hurt/repair derived processing is not declared for this turn")
    expected_counter = "negative_evidence_count" if turn_number == 1 else "positive_evidence_count"
    opposite_counter = "positive_evidence_count" if turn_number == 1 else "negative_evidence_count"
    if (
        evidence_counter_deltas[expected_counter] != 1
        or evidence_counter_deltas[opposite_counter] != 0
    ):
        raise ValueError("hurt/repair boundary did not preserve its typed evidence direction")


def _validate_relationship_setup(value: object, selected: ModuleSpec) -> dict[str, Any]:
    setup = _object(value, "report.session.relationship_setup")
    base_keys = {"requested_profile", "method", "actual_profile", "processed_interactions"}
    conditioned_keys = {
        *base_keys,
        "state_version",
        "maturity_value",
        "qualified_interaction_count",
        "distinct_session_count",
    }
    expected_keys = (
        base_keys
        if selected.relationship_setup == "fresh_undeveloped_neutral"
        else conditioned_keys
    )
    _require_exact_keys(setup, expected_keys, "report.session.relationship_setup")
    if (
        setup.get("requested_profile") != selected.relationship_setup
        or setup.get("actual_profile") != selected.relationship_setup
    ):
        raise ValueError("completed sample relationship setup mismatch")
    processed = _strict_int(setup.get("processed_interactions"), "processed_interactions")
    if processed < 0:
        raise ValueError("processed_interactions must be non-negative")
    if selected.relationship_setup == "fresh_undeveloped_neutral":
        if setup.get("method") != "canonical_fresh_relationship_state" or processed != 0:
            raise ValueError("fresh relationship setup contract drift")
        return setup
    if setup.get("method") != "typed_deterministic_relationship_conditioning":
        raise ValueError("conditioned relationship setup method drift")
    if _strict_int(setup.get("state_version"), "setup.state_version") < 1:
        raise ValueError("conditioned setup state_version must be positive")
    maturity = _non_negative_number(setup.get("maturity_value"), "setup.maturity_value")
    if maturity > 1:
        raise ValueError("conditioned setup maturity_value must not exceed one")
    for key in ("qualified_interaction_count", "distinct_session_count"):
        if _strict_int(setup.get(key), f"setup.{key}") < 0:
            raise ValueError(f"setup.{key} must be non-negative")
    return setup


def _relationship_profile_from_snapshot(snapshot: Mapping[str, Any]) -> str:
    expression = _object(snapshot.get("expression"), "relationship snapshot expression")
    if expression.get("maturity") == "low":
        return "fresh_undeveloped_neutral"
    if expression.get("trust") in {"low", "very_low"} or expression.get("comfort") in {
        "low",
        "very_low",
    }:
        return "guarded_only_when_relationally_relevant"
    if (
        expression.get("maturity") == "established"
        and expression.get("familiarity") in {"high", "very_high"}
        and (
            expression.get("trust") in {"high", "very_high"}
            or expression.get("comfort") in {"high", "very_high"}
        )
    ):
        return "established_positive"
    return "developing_neutral"


def _validate_provider_metrics(value: object, label: str) -> None:
    if value is None:
        return
    metrics = _object(value, label)
    if not set(metrics) <= set(_SAFE_PROVIDER_METRIC_KEYS):
        raise ValueError(f"{label} contains non-allowlisted metrics")
    for key, metric in metrics.items():
        if metric is not None:
            _non_negative_number(metric, f"{label}.{key}")


def _validate_provider_attempt(value: object, attempt_number: int) -> None:
    attempt = _object(value, "report.turn.provider_attempt")
    _require_exact_keys(
        attempt,
        {
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
        },
        "report.turn.provider_attempt",
    )
    if attempt.get("attempt_number") != attempt_number:
        raise ValueError("provider attempt numbers must be consecutive from one")
    _non_negative_number(attempt.get("wall_ms"), "provider_attempt.wall_ms")
    for key in (
        "request_schema_version",
        "context_schema_version",
        "message_count",
        "request_content_chars",
        "max_output_tokens",
    ):
        if _strict_int(attempt.get(key), f"provider_attempt.{key}") < 1:
            raise ValueError(f"provider_attempt.{key} must be positive")
    temperature = _non_negative_number(attempt.get("temperature"), "provider_attempt.temperature")
    if temperature > 2:
        raise ValueError("provider attempt temperature must not exceed two")
    role_counts = _object(attempt.get("message_role_counts"), "message_role_counts")
    if not role_counts or not set(role_counts) <= {"system", "developer", "user", "assistant"}:
        raise ValueError("provider attempt role counts are invalid")
    role_total = 0
    for role, count in role_counts.items():
        typed_count = _strict_int(count, f"message_role_counts.{role}")
        if typed_count < 0:
            raise ValueError("provider attempt role counts must be non-negative")
        role_total += typed_count
    if role_total != attempt.get("message_count"):
        raise ValueError("provider attempt role counts do not match message_count")
    for key in ("input_tokens", "output_tokens"):
        if _strict_int(attempt.get(key), f"provider_attempt.{key}") < 0:
            raise ValueError(f"provider_attempt.{key} must be non-negative")
    _validate_provider_metrics(attempt.get("provider_metrics"), "provider_attempt.metrics")
    if (
        attempt.get("finish_status") != "completed"
        or attempt.get("succeeded") is not True
        or attempt.get("error_type") is not None
    ):
        raise ValueError("provider attempt is not a clean completed OpenAI attempt")


def _validate_post_response(value: object) -> None:
    post = _object(value, "report.turn.post_response")
    _require_exact_keys(
        post,
        {
            "episode_formation_ms",
            "episode_embedding_ms",
            "semantic_consolidation_ms",
            "relationship_appraisal_ms",
            "relationship_commit_ms",
            "relationship_total_ms",
            "total_ms",
            "failure_phases",
        },
        "report.turn.post_response",
    )
    for key in (
        "episode_formation_ms",
        "episode_embedding_ms",
        "semantic_consolidation_ms",
        "relationship_appraisal_ms",
        "relationship_commit_ms",
        "relationship_total_ms",
        "total_ms",
    ):
        _non_negative_number(post.get(key), f"post_response.{key}")
    if post.get("failure_phases") != []:
        raise ValueError("required post-response processing contains a failure")


def _validate_budget(value: object, selected: ModuleSpec) -> list[dict[str, Any]]:
    budget = _object(value, "report.budget")
    expected_keys = {
        "required_base_calls",
        "maximum_provider_calls",
        "maximum_attempts_per_turn",
        "base_call_count",
        "provider_call_count",
        "successful_provider_call_count",
        "input_tokens",
        "output_tokens",
        "maximum_cost_usd",
        "actual_usage_cost_usd",
        "guarded_cost_usd",
        "usage_complete",
        "guard_projection_valid",
        "pricing",
        "within_call_limit",
        "within_cost_limit",
        "mandatory_base_calls_complete",
        "calls",
    }
    _require_exact_keys(budget, expected_keys, "report.budget")
    maximum_calls = _strict_int(budget.get("maximum_provider_calls"), "maximum_provider_calls")
    provider_calls = _strict_int(budget.get("provider_call_count"), "provider_call_count")
    calls = [
        _object(call, f"report.budget.calls[{index}]")
        for index, call in enumerate(_array(budget.get("calls"), "report.budget.calls"))
    ]
    if (
        budget.get("required_base_calls") != selected.required_base_calls
        or budget.get("base_call_count") != selected.required_base_calls
        or budget.get("maximum_attempts_per_turn") != MAX_ATTEMPTS_PER_TURN
        or not selected.required_base_calls <= maximum_calls <= selected.absolute_max_calls
        or provider_calls != len(calls)
        or not selected.required_base_calls <= provider_calls <= maximum_calls
        or budget.get("successful_provider_call_count") != provider_calls
        or budget.get("usage_complete") is not True
        or budget.get("guard_projection_valid") is not True
        or budget.get("within_call_limit") is not True
        or budget.get("within_cost_limit") is not True
        or budget.get("mandatory_base_calls_complete") is not True
    ):
        raise ValueError("completed sample call ledger is incomplete or invalid")
    maximum_cost = _non_negative_number(budget.get("maximum_cost_usd"), "maximum_cost_usd")
    actual_cost = _non_negative_number(budget.get("actual_usage_cost_usd"), "actual_usage_cost_usd")
    guarded_cost = _non_negative_number(budget.get("guarded_cost_usd"), "guarded_cost_usd")
    if (
        not 0 < maximum_cost <= ABSOLUTE_MAX_COST_USD
        or max(actual_cost, guarded_cost) > maximum_cost
    ):
        raise ValueError("completed sample cost ledger exceeds its explicit safe ceiling")
    pricing = _object(budget.get("pricing"), "report.budget.pricing")
    expected_pricing = {
        "currency": "USD",
        "input_usd_per_million_tokens": OPENAI_INPUT_USD_PER_MILLION_TOKENS,
        "output_usd_per_million_tokens": OPENAI_OUTPUT_USD_PER_MILLION_TOKENS,
        "snapshot": PRICING_SNAPSHOT,
        "fx_conversion_used": False,
    }
    _require_exact_keys(pricing, set(expected_pricing), "report.budget.pricing")
    if pricing != expected_pricing:
        raise ValueError("completed sample pricing snapshot drift")

    call_keys = {
        "call_number",
        "session_id",
        "turn",
        "turn_id",
        "attempt_kind",
        "status",
        "requested_visible_output_token_limit",
        "guarded_input_token_limit",
        "guarded_output_token_limit",
        "projected_guard_cost_usd",
        "charged_guard_cost_usd",
        "finish_status",
        "input_tokens",
        "output_tokens",
        "actual_cost_usd",
        "usage_complete",
        "guard_projection_valid",
        "provider_metrics",
    }
    total_input = 0
    total_output = 0
    total_actual_cost = 0.0
    total_guarded_cost = 0.0
    for call_number, call in enumerate(calls, start=1):
        _require_exact_keys(call, call_keys, f"report.budget.calls[{call_number - 1}]")
        if call.get("call_number") != call_number:
            raise ValueError("budget call numbers must be consecutive from one")
        _non_blank_string(call.get("session_id"), "budget.call.session_id")
        if _strict_int(call.get("turn"), "budget.call.turn") < 1:
            raise ValueError("budget call turn must be positive")
        _non_blank_string(call.get("turn_id"), "budget.call.turn_id")
        if call.get("attempt_kind") not in {"base", "validator_retry"}:
            raise ValueError("budget call attempt_kind is not closed")
        for key in (
            "requested_visible_output_token_limit",
            "guarded_input_token_limit",
            "guarded_output_token_limit",
        ):
            if _strict_int(call.get(key), f"budget.call.{key}") < 1:
                raise ValueError(f"budget.call.{key} must be positive")
        if cast(int, call["guarded_output_token_limit"]) < cast(
            int, call["requested_visible_output_token_limit"]
        ):
            raise ValueError("guarded output limit is below the visible output limit")
        if cast(int, call["guarded_output_token_limit"]) != (
            cast(int, call["requested_visible_output_token_limit"]) + EXPECTED_REASONING_ALLOWANCE
        ):
            raise ValueError("budget call does not use the exact OpenAI reasoning allowance")
        input_tokens = _strict_int(call.get("input_tokens"), "budget.call.input_tokens")
        output_tokens = _strict_int(call.get("output_tokens"), "budget.call.output_tokens")
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("budget call token counts must be non-negative")
        projected = _non_negative_number(
            call.get("projected_guard_cost_usd"), "budget.call.projected_guard_cost_usd"
        )
        charged = _non_negative_number(
            call.get("charged_guard_cost_usd"), "budget.call.charged_guard_cost_usd"
        )
        call_actual = _non_negative_number(
            call.get("actual_cost_usd"), "budget.call.actual_cost_usd"
        )
        recomputed = (
            input_tokens * OPENAI_INPUT_USD_PER_MILLION_TOKENS
            + output_tokens * OPENAI_OUTPUT_USD_PER_MILLION_TOKENS
        ) / 1_000_000
        if (
            call.get("status") != "succeeded"
            or call.get("finish_status") != "completed"
            or call.get("usage_complete") is not True
            or call.get("guard_projection_valid") is not True
            or abs(call_actual - recomputed) > 1e-8
            or abs(charged - call_actual) > 1e-8
            or call_actual > projected + 1e-8
        ):
            raise ValueError("budget call is not a clean, guard-valid settled attempt")
        _validate_provider_metrics(call.get("provider_metrics"), "budget.call.provider_metrics")
        total_input += input_tokens
        total_output += output_tokens
        total_actual_cost += call_actual
        total_guarded_cost += charged
    if (
        budget.get("input_tokens") != total_input
        or budget.get("output_tokens") != total_output
        or abs(actual_cost - round(total_actual_cost, 8)) > 1e-8
        or abs(guarded_cost - round(total_guarded_cost, 8)) > 1e-8
    ):
        raise ValueError("budget totals do not match settled call records")
    return calls


def _validate_pending_human_review(
    value: object,
    *,
    fixture: Mapping[str, Any],
    selected: ModuleSpec,
    artifact_id: str,
    sample_digest: str,
) -> None:
    review = _object(value, "report.human_review")
    _require_exact_keys(
        review,
        {
            "status",
            "reviewer",
            "automated_text_judging_performed",
            "provider_sample_is_authority",
            "artifact_id",
            "sample_digest",
            "sessions",
            "cross_replica_booleans",
            "module_pass",
            "note",
        },
        "report.human_review",
    )
    if (
        review.get("status") != "pending"
        or review.get("reviewer") != "human"
        or review.get("automated_text_judging_performed") is not False
        or review.get("provider_sample_is_authority") is not False
        or review.get("artifact_id") != artifact_id
        or review.get("sample_digest") != sample_digest
        or review.get("module_pass") is not None
        or review.get("note") != HUMAN_REVIEW_NOTE
    ):
        raise ValueError("completed sample pending-human-review contract drift")
    hard_dimensions = _string_tuple(fixture.get("hard_safety_dimensions"), "hard_safety_dimensions")
    sessions = _array(review.get("sessions"), "report.human_review.sessions")
    if len(sessions) != EXPECTED_REPLICA_COUNT:
        raise ValueError("pending human review must cover three public sessions")
    for replica_number, raw_session in enumerate(sessions, start=1):
        session = _object(raw_session, "report.human_review.session")
        _require_exact_keys(
            session,
            {"session_id", "turns", "dialogue_booleans"},
            "report.human_review.session",
        )
        if session.get("session_id") != f"{selected.module_id}-replica-{replica_number}":
            raise ValueError("pending human review public session id drift")
        turns = _array(session.get("turns"), "report.human_review.session.turns")
        if len(turns) != len(selected.turns):
            raise ValueError("pending human review turn cardinality drift")
        for fixture_turn, raw_turn in zip(selected.turns, turns, strict=True):
            turn = _object(raw_turn, "report.human_review.turn")
            _require_exact_keys(
                turn,
                {"turn", "turn_id", "hard_safety_booleans", "quality_booleans"},
                "report.human_review.turn",
            )
            if (
                turn.get("turn") != fixture_turn["turn"]
                or turn.get("turn_id") != fixture_turn["id"]
            ):
                raise ValueError("pending human review public turn id drift")
            hard = _object(turn.get("hard_safety_booleans"), "hard_safety_booleans")
            quality = _object(turn.get("quality_booleans"), "quality_booleans")
            if set(hard) != set(hard_dimensions) or any(
                value is not None for value in hard.values()
            ):
                raise ValueError("pending hard-safety boolean template drift")
            expected_quality = cast(list[str], fixture_turn["review_dimensions"])
            if set(quality) != set(expected_quality) or any(
                value is not None for value in quality.values()
            ):
                raise ValueError("pending quality boolean template drift")
        dialogue = _object(session.get("dialogue_booleans"), "dialogue_booleans")
        if set(dialogue) != set(selected.dialogue_review_dimensions) or any(
            value is not None for value in dialogue.values()
        ):
            raise ValueError("pending dialogue boolean template drift")
    cross = _object(review.get("cross_replica_booleans"), "cross_replica_booleans")
    if set(cross) != set(selected.cross_replica_review_dimensions) or any(
        value is not None for value in cross.values()
    ):
        raise ValueError("pending cross-replica boolean template drift")


def _validate_manifest_metadata(manifest: Mapping[str, Any]) -> None:
    if (
        manifest.get("schema_version") != 16
        or manifest.get("character_context_schema_version") != 16
    ):
        raise ValueError("manifest/context schema identity drift")
    for key in ("recent_conversation_turn_count", "consecutive_same_user_message_count"):
        minimum = 0 if key == "recent_conversation_turn_count" else 1
        if _strict_int(manifest.get(key), f"manifest.{key}") < minimum:
            raise ValueError(f"manifest.{key} is below its closed lower bound")
    for key in ("retrieved_memory_count", "retrieved_semantic_claim_count"):
        if _strict_int(manifest.get(key), f"manifest.{key}") < 0:
            raise ValueError(f"manifest.{key} must be non-negative")
    for key in (
        "retrieval_status",
        "semantic_retrieval_status",
        "emotion_appraisal_status",
        "relationship_expression_profile",
        "affect_expression_profile",
        "disclosure_primary_mode",
    ):
        _non_blank_string(manifest.get(key), f"manifest.{key}")
    if manifest.get("relationship_expression_profile") not in {
        "fresh_undeveloped_neutral",
        "developing_neutral",
        "established_positive",
        "guarded_only_when_relationally_relevant",
    }:
        raise ValueError("manifest relationship expression profile is not closed")
    if type(manifest.get("relationship_recent_strain")) is not bool:
        raise ValueError("manifest relationship recent strain must be explicit boolean")
    if manifest.get("affect_expression_profile") not in {
        "calm_even",
        "interested_calm",
        "positive_light",
        "soft_negative_non_hostile",
        "tense_non_hostile",
    }:
        raise ValueError("manifest affect expression profile is not closed")
    facets = manifest.get("disclosure_facets")
    if not isinstance(facets, list) or any(
        not isinstance(facet, str) or not facet.strip() for facet in facets
    ):
        raise ValueError("manifest disclosure facets must be public non-blank codes")
    if len(facets) != len(set(facets)):
        raise ValueError("manifest disclosure facets must be unique")
    for key in (
        "duplicate_response_detected",
        "regeneration_attempted",
        "response_regenerated",
    ):
        if type(manifest.get(key)) is not bool:
            raise ValueError(f"manifest.{key} must be boolean")
    attempted = cast(bool, manifest["regeneration_attempted"])
    regenerated = cast(bool, manifest["response_regenerated"])
    reason = manifest.get("regeneration_reason")
    if (
        (not attempted and (regenerated or reason is not None))
        or (attempted and (not isinstance(reason, str) or not reason.strip()))
        or (manifest.get("duplicate_response_detected") is True and not attempted)
    ):
        raise ValueError("manifest regeneration metadata is internally inconsistent")


def _validate_completed_report(
    fixture: Mapping[str, Any], selected: ModuleSpec, report: Mapping[str, Any]
) -> None:
    validate_fixture(fixture)
    if selected != module_spec(fixture, selected.module_id):
        raise ValueError("selected module does not match the versioned fixture")
    assert_safe_artifact(report)
    _require_exact_keys(
        report,
        {
            "schema_version",
            "recorded_at",
            "completed_at",
            "checkpoint",
            "purpose",
            "status",
            "artifact_id",
            "corpus_id",
            "policy_id",
            "module_id",
            "artifact_contract",
            "configuration",
            "budget",
            "sessions",
            "human_review",
            "acceptance",
            "sample_digest",
        },
        "completed sample report",
    )
    if (
        report.get("schema_version") != REPORT_SCHEMA_VERSION
        or report.get("checkpoint") != "14.2"
        or report.get("purpose") != "openai_v24_employer_demo_module"
        or report.get("status") != "completed_awaiting_human_review"
        or report.get("corpus_id") != EXPECTED_CORPUS_ID
        or report.get("policy_id") != EXPECTED_POLICY_ID
        or report.get("module_id") != selected.module_id
    ):
        raise ValueError("completed sample report identity mismatch")
    recorded_at = _aware_utc_datetime(report.get("recorded_at"), "report.recorded_at")
    completed_at = _aware_utc_datetime(report.get("completed_at"), "report.completed_at")
    if completed_at < recorded_at:
        raise ValueError("completed sample completion time precedes its recording time")
    artifact_id = _non_blank_string(report.get("artifact_id"), "report.artifact_id")
    artifact_prefix = f"satori-checkpoint142-openai-v24:{selected.module_id}:"
    if not artifact_id.startswith(artifact_prefix):
        raise ValueError("completed sample artifact id has the wrong module prefix")
    try:
        uuid.UUID(artifact_id.removeprefix(artifact_prefix))
    except ValueError as error:
        raise ValueError("completed sample artifact id is not UUID-bound") from error
    sample_digest = _non_blank_string(report.get("sample_digest"), "report.sample_digest")
    if sample_digest != sample_content_digest(report):
        raise ValueError("completed sample digest is missing or stale")
    _validate_artifact_contract(report.get("artifact_contract"))
    _validate_report_configuration(report.get("configuration"), selected)
    calls = _validate_budget(report.get("budget"), selected)

    acceptance = _object(report.get("acceptance"), "report.acceptance")
    expected_acceptance = {
        "sample_complete": True,
        "module_accepted": False,
        "employer_demo_readiness_accepted": False,
        "reason": "human_review_pending",
    }
    _require_exact_keys(acceptance, set(expected_acceptance), "report.acceptance")
    if acceptance != expected_acceptance:
        raise ValueError("completed sample pre-review acceptance contract drift")

    sessions = _array(report.get("sessions"), "report.sessions")
    if len(sessions) != EXPECTED_REPLICA_COUNT:
        raise ValueError("completed sample must contain exactly three fresh sessions")
    scope_attempt_counts: dict[tuple[str, int, str], int] = {}
    scope_attempts: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    scope_final_usage: dict[tuple[str, int, str], dict[str, Any]] = {}
    scope_order: dict[tuple[str, int, str], int] = {}
    for replica_number, raw_session in enumerate(sessions, start=1):
        session = _object(raw_session, "report.session")
        _require_exact_keys(
            session,
            {
                "session_id",
                "fresh_database",
                "completed",
                "relationship_setup",
                "restart_boundaries",
                "turns",
            },
            "report.session",
        )
        public_session_id = f"{selected.module_id}-replica-{replica_number}"
        if (
            session.get("session_id") != public_session_id
            or session.get("fresh_database") is not True
            or session.get("completed") is not True
            or session.get("restart_boundaries") != sorted(selected.restart_after_turns)
        ):
            raise ValueError("completed sample session identity/freshness mismatch")
        relationship_setup = _validate_relationship_setup(
            session.get("relationship_setup"), selected
        )
        turns = _array(session.get("turns"), "report.session.turns")
        if len(turns) != selected.turns_per_replica:
            raise ValueError("completed sample session turn cardinality mismatch")
        for fixture_turn, raw_turn in zip(selected.turns, turns, strict=True):
            turn = _object(raw_turn, "report.turn")
            turn_number = cast(int, fixture_turn["turn"])
            derived_required = turn_number in selected.derived_processing_after_turns
            expected_turn_keys = {
                "turn",
                "turn_id",
                "user",
                "reply",
                "generation",
                "usage",
                "timings_ms",
                "provider_attempt_count",
                "provider_attempts",
                "manifest",
                "relationship_before",
                "derived_processing",
            }
            if derived_required:
                expected_turn_keys.update({"post_response", "relationship_after_derived"})
            _require_exact_keys(turn, expected_turn_keys, "report.turn")
            if (
                turn.get("turn") != fixture_turn["turn"]
                or turn.get("turn_id") != fixture_turn["id"]
                or turn.get("user") != fixture_turn["user_text"]
            ):
                raise ValueError("completed sample does not preserve public turn/reply text")
            _non_blank_string(turn.get("reply"), "report.turn.reply")
            generation = _object(turn.get("generation"), "report.turn.generation")
            _require_exact_keys(
                generation,
                {"provider", "requested_model", "reported_model", "finish_status", "replayed"},
                "report.turn.generation",
            )
            if (
                generation.get("provider") != EXPECTED_PROVIDER.value
                or generation.get("requested_model") != EXPECTED_MODEL
                or generation.get("reported_model") != EXPECTED_MODEL
                or generation.get("finish_status") != "completed"
                or generation.get("replayed") is not False
            ):
                raise ValueError("completed sample generation is not comparable")
            usage = _object(turn.get("usage"), "report.turn.usage")
            _require_exact_keys(usage, {"input_tokens", "output_tokens"}, "report.turn.usage")
            for key in ("input_tokens", "output_tokens"):
                if _strict_int(usage.get(key), f"report.turn.usage.{key}") < 0:
                    raise ValueError("completed turn usage must be non-negative")
            timings = _object(turn.get("timings_ms"), "report.turn.timings_ms")
            _require_exact_keys(timings, set(_SAFE_TIMING_KEYS), "report.turn.timings_ms")
            for key, timing in timings.items():
                if timing is not None:
                    _non_negative_number(timing, f"report.turn.timings_ms.{key}")
            attempts = _array(turn.get("provider_attempts"), "report.turn.provider_attempts")
            if (
                turn.get("provider_attempt_count") != len(attempts)
                or not 1 <= len(attempts) <= MAX_ATTEMPTS_PER_TURN
            ):
                raise ValueError("completed sample provider attempts are incomplete or unbounded")
            for attempt_number, attempt in enumerate(attempts, start=1):
                _validate_provider_attempt(attempt, attempt_number)
            final_attempt = _object(attempts[-1], "report.turn.final_provider_attempt")
            if usage != {
                "input_tokens": final_attempt.get("input_tokens"),
                "output_tokens": final_attempt.get("output_tokens"),
            }:
                raise ValueError("committed reply usage does not match its final provider attempt")
            manifest = _object(turn.get("manifest"), "report.turn.manifest")
            if set(manifest) != set(_SAFE_V24_MANIFEST_KEYS):
                raise ValueError("completed sample manifest allowlist drift")
            if (
                manifest.get("policy_id") != EXPECTED_POLICY_ID
                or manifest.get("policy_schema_version") != 24
                or manifest.get("character_expression_plan_schema_version") is not None
                or manifest.get("character_delivery_decision_schema_version") != 1
                or manifest.get("character_delivery_position_stance")
                != manifest.get("cognition_position_stance")
                or manifest.get("character_delivery_preserve_uncertainty")
                != manifest.get("cognition_preserve_uncertainty")
            ):
                raise ValueError("completed sample did not use the direct v24 delivery decision")
            _validate_v24_decision_codes(manifest)
            _validate_manifest_metadata(manifest)
            _validate_module_turn_manifest(selected.module_id, turn_number, manifest)
            expected_profile = selected.relationship_setup if turn_number == 1 else None
            if (
                expected_profile is not None
                and manifest.get("relationship_expression_profile") != expected_profile
            ):
                raise ValueError("first turn did not observe the requested relationship profile")
            relationship_before = _validate_relationship_snapshot(
                turn.get("relationship_before"), "report.turn.relationship_before"
            )
            if turn_number == 1:
                if (
                    _relationship_profile_from_snapshot(relationship_before)
                    != selected.relationship_setup
                ):
                    raise ValueError(
                        "first relationship snapshot does not match its conditioning profile"
                    )
                if selected.relationship_setup == "fresh_undeveloped_neutral":
                    if (
                        relationship_before.get("state_version") != 1
                        or relationship_before.get("processed_interaction_count") != 0
                        or relationship_before.get("qualified_interaction_count") != 0
                    ):
                        raise ValueError("fresh relationship snapshot is not canonical")
                elif (
                    relationship_before.get("state_version")
                    != relationship_setup.get("state_version")
                    or relationship_before.get("maturity_value")
                    != relationship_setup.get("maturity_value")
                    or relationship_before.get("processed_interaction_count")
                    != relationship_setup.get("processed_interactions")
                    or relationship_before.get("qualified_interaction_count")
                    != relationship_setup.get("qualified_interaction_count")
                ):
                    raise ValueError(
                        "conditioned relationship snapshot does not match its setup evidence"
                    )
            if derived_required:
                if turn.get("derived_processing") != "production_post_response_path":
                    raise ValueError("required derived processing marker drift")
                _validate_post_response(turn.get("post_response"))
                relationship_after = _validate_relationship_snapshot(
                    turn.get("relationship_after_derived"),
                    "report.turn.relationship_after_derived",
                )
                _validate_derived_relationship_transition(
                    relationship_before,
                    relationship_after,
                    module_id=selected.module_id,
                    turn_number=turn_number,
                )
                next_turn_index = turn_number
                if next_turn_index < len(turns):
                    next_turn = _object(turns[next_turn_index], "report.next_turn")
                    next_relationship = _validate_relationship_snapshot(
                        next_turn.get("relationship_before"),
                        "report.next_turn.relationship_before",
                    )
                    if next_relationship != relationship_after:
                        raise ValueError(
                            "the next turn did not observe the exact derived relationship state"
                        )
            elif turn.get("derived_processing") != "not_requested":
                raise ValueError("non-derived turn has an unexpected processing marker")
            scope = (public_session_id, turn_number, cast(str, fixture_turn["id"]))
            scope_attempt_counts[scope] = len(attempts)
            scope_attempts[scope] = [
                _object(attempt, "report.turn.provider_attempt") for attempt in attempts
            ]
            scope_final_usage[scope] = usage
            scope_order[scope] = len(scope_order)

    ledger_scope_counts: dict[tuple[str, int, str], int] = {}
    ledger_scope_kinds: dict[tuple[str, int, str], list[str]] = {}
    ledger_scope_calls: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    ledger_order: list[int] = []
    for call in calls:
        scope = (
            cast(str, call["session_id"]),
            cast(int, call["turn"]),
            cast(str, call["turn_id"]),
        )
        if scope not in scope_attempt_counts:
            raise ValueError("budget call references an unknown public session/turn")
        ledger_scope_counts[scope] = ledger_scope_counts.get(scope, 0) + 1
        ledger_scope_kinds.setdefault(scope, []).append(cast(str, call["attempt_kind"]))
        ledger_scope_calls.setdefault(scope, []).append(call)
        ledger_order.append(scope_order[scope])
    if ledger_scope_counts != scope_attempt_counts or ledger_order != sorted(ledger_order):
        raise ValueError("budget calls do not match ordered public provider attempts")
    for kinds in ledger_scope_kinds.values():
        if kinds not in (["base"], ["base", "validator_retry"]):
            raise ValueError("budget retry ordering violates the max-one retry contract")
    for scope, attempts in scope_attempts.items():
        ledger_calls = ledger_scope_calls[scope]
        for attempt, call in zip(attempts, ledger_calls, strict=True):
            if (
                call.get("requested_visible_output_token_limit") != attempt.get("max_output_tokens")
                or call.get("finish_status") != attempt.get("finish_status")
                or call.get("input_tokens") != attempt.get("input_tokens")
                or call.get("output_tokens") != attempt.get("output_tokens")
                or call.get("provider_metrics") != attempt.get("provider_metrics")
            ):
                raise ValueError("budget ledger does not match public provider-attempt metadata")
        if scope_final_usage[scope] != {
            "input_tokens": ledger_calls[-1].get("input_tokens"),
            "output_tokens": ledger_calls[-1].get("output_tokens"),
        }:
            raise ValueError("budget ledger does not match committed-reply usage")

    _validate_pending_human_review(
        report.get("human_review"),
        fixture=fixture,
        selected=selected,
        artifact_id=artifact_id,
        sample_digest=sample_digest,
    )


async def run(
    *,
    output_path: Path,
    alembic_config: Path,
    execute: bool,
    maximum_provider_calls: int | None,
    maximum_cost_usd: float | None,
    authorized_plan_digest: str | None,
    module_id: str,
    show_replies: bool,
) -> dict[str, Any]:
    _reject_retired_paid_execution()
    fixture = load_fixture()
    selected = module_spec(fixture, module_id)
    preflight_execution(
        execute=execute,
        maximum_provider_calls=maximum_provider_calls,
        maximum_cost_usd=maximum_cost_usd,
        authorized_plan_digest=authorized_plan_digest,
        selected=selected,
    )
    settings = Settings()
    _validate_production_settings(settings)
    assert maximum_provider_calls is not None
    assert maximum_cost_usd is not None
    ledger = AtomicOpenAICallLedger(
        maximum_calls=maximum_provider_calls,
        maximum_cost_usd=maximum_cost_usd,
        required_base_calls=selected.required_base_calls,
    )
    artifact_id = f"satori-checkpoint142-openai-v24:{selected.module_id}:{uuid.uuid4()}"
    human_review = _human_review_template(selected)
    _expand_hard_safety_template(
        human_review,
        _string_tuple(fixture["hard_safety_dimensions"], "hard_safety_dimensions"),
    )
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "recorded_at": datetime.now(UTC).isoformat(),
        "checkpoint": "14.2",
        "purpose": "openai_v24_employer_demo_module",
        "status": "running",
        "artifact_id": artifact_id,
        "corpus_id": fixture["corpus_id"],
        "policy_id": EXPECTED_POLICY_ID,
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
        "configuration": _safe_configuration(settings, selected),
        "budget": ledger.snapshot(),
        "sessions": [],
        "human_review": human_review,
        "acceptance": {
            "sample_complete": False,
            "module_accepted": False,
            "employer_demo_readiness_accepted": False,
            "reason": "human_review_pending",
        },
    }

    def checkpoint() -> None:
        report["budget"] = ledger.snapshot()
        _write_safe_report(output_path, report)

    ledger.on_change = checkpoint
    checkpoint()
    try:
        with tempfile.TemporaryDirectory(
            prefix=f"satori-checkpoint142-openai-v24-{selected.module_id}-"
        ) as temporary:
            database_directory = Path(temporary)
            for replica_number in range(1, EXPECTED_REPLICA_COUNT + 1):
                placeholder: dict[str, Any] = {
                    "session_id": f"{selected.module_id}-replica-{replica_number}",
                    "fresh_database": True,
                    "completed": False,
                    "relationship_setup": None,
                    "restart_boundaries": [],
                    "turns": [],
                }
                cast(list[dict[str, Any]], report["sessions"]).append(placeholder)
                checkpoint()

                record = await _run_replica(
                    settings=settings,
                    database_path=database_directory / f"replica-{replica_number}.db",
                    alembic_config=alembic_config,
                    selected=selected,
                    replica_number=replica_number,
                    ledger=ledger,
                    checkpoint=checkpoint,
                    record=placeholder,
                )
                cast(list[dict[str, Any]], report["sessions"])[replica_number - 1] = record
                checkpoint()

        report["status"] = "completed_awaiting_human_review"
        report["completed_at"] = datetime.now(UTC).isoformat()
        human_review["artifact_id"] = artifact_id
        report["acceptance"] = {
            "sample_complete": True,
            "module_accepted": False,
            "employer_demo_readiness_accepted": False,
            "reason": "human_review_pending",
        }
        report["sample_digest"] = sample_content_digest(report)
        human_review["sample_digest"] = report["sample_digest"]
        _validate_completed_report(fixture, selected, report)
        checkpoint()
        if show_replies:
            for session in cast(list[dict[str, Any]], report["sessions"]):
                for turn in cast(list[dict[str, Any]], session["turns"]):
                    print(
                        f"[{session['session_id']}/turn {turn['turn']}:{turn['turn_id']}] "
                        f"{turn['reply']}",
                        flush=True,
                    )
        return report
    except BaseException as error:
        report["status"] = "failed"
        report["failed_at"] = datetime.now(UTC).isoformat()
        report["failure"] = {
            "error_type": type(error).__name__,
            "failure_reason": (
                error.reason.value if isinstance(error, ConversationProviderError) else None
            ),
        }
        checkpoint()
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect one historical v24 employer-demo module offline. Its paid execution path "
            "is retired."
        )
    )
    parser.add_argument("--module", required=True, choices=EXPECTED_MODULE_IDS)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Retired compatibility flag; any attempted paid execution is rejected.",
    )
    parser.add_argument(
        "--max-provider-calls",
        type=int,
        help="Required with --execute; at least all base turns and at most one retry per turn.",
    )
    parser.add_argument(
        "--max-cost-usd",
        type=float,
        help="Required with --execute; explicit guarded USD ceiling (absolute cap: $1.00).",
    )
    parser.add_argument(
        "--authorized-plan-digest",
        help=(
            "Required with --execute; exact execution_plan_digest printed by the offline "
            "inspection for this module."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Required with --execute; optional destination for the offline inspection plan.",
    )
    parser.add_argument("--alembic-config", type=Path, default=Path("alembic.ini"))
    parser.add_argument("--show-replies", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    fixture = load_fixture()
    selected = module_spec(fixture, arguments.module)
    if not arguments.execute:
        inspection = inspect_module(fixture, selected)
        if arguments.output is not None:
            _write_safe_report(arguments.output, inspection)
        print(json.dumps(inspection, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
        return 0

    _reject_retired_paid_execution()
    preflight_execution(
        execute=True,
        maximum_provider_calls=arguments.max_provider_calls,
        maximum_cost_usd=arguments.max_cost_usd,
        authorized_plan_digest=arguments.authorized_plan_digest,
        selected=selected,
    )
    if arguments.output is None:
        raise V24EvaluationConfigurationError("--output is required with --execute")
    configure_logging(LogLevel.CRITICAL)
    completed = asyncio.run(
        run(
            output_path=arguments.output,
            alembic_config=arguments.alembic_config,
            execute=True,
            maximum_provider_calls=arguments.max_provider_calls,
            maximum_cost_usd=arguments.max_cost_usd,
            authorized_plan_digest=arguments.authorized_plan_digest,
            module_id=arguments.module,
            show_replies=arguments.show_replies,
        )
    )
    budget = _object(completed["budget"], "completed budget")
    print(
        f"Checkpoint 14.2 OpenAI v24 module completed: module={arguments.module} "
        f"status={completed['status']} calls={budget['provider_call_count']} "
        f"cost_usd={budget['actual_usage_cost_usd']} output={arguments.output}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
