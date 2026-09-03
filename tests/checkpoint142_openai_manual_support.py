"""Version-neutral safety support for bounded OpenAI manual evaluators."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import platform
import stat
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, cast

import satori
from satori.application.affect.contracts import (
    EMOTIONAL_EXPRESSION_CONTEXT_SCHEMA_VERSION,
    EmotionAppraisalStatus,
    PreparedAffectiveContext,
)
from satori.application.affect.use_cases import PrepareAffectiveContext
from satori.application.conversation.contracts import BehaviorPolicy, SatoriReply, TalkInput
from satori.application.retrieval.contracts import RetrievedMemoryContext
from satori.application.semantic.contracts import RetrievedSemanticContext
from satori.config import (
    ConversationProviderKind,
    EmbeddingProviderKind,
    OpenAIReasoningEffort,
    Settings,
)
from satori.core.ids import Uuid4Generator
from satori.domain.conversation_history import ConversationInteraction
from satori.domain.initial_self import InitialSelfSnapshot
from satori.infrastructure.providers.ollama import OLLAMA_PROVIDER_NAME
from satori.infrastructure.providers.ollama_affect import APPRAISAL_METHOD
from tests.checkpoint142_openai_v26_ledger import (
    BudgetedOpenAIProvider,
    ExactProviderUsage,
    PublicTurnScope,
    TurnScopeBinding,
    V26AtomicOpenAICallLedger,
    safe_provider_metrics,
)
from tests.stage81_real_eval import (
    _build_runtime,
    _public_sampled_reply,
    _sanitized_manifest,
)

_TREE_FILE_SUFFIXES = frozenset({".py", ".json", ".typed"})
_TIMING_KEYS = (
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
)
_PUBLIC_TURN_EVIDENCE_KEYS = {
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
}
_PUBLIC_ATTEMPT_EVIDENCE_KEYS = {
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


class EvaluationArtifactSafetyError(RuntimeError):
    """Reject an unsafe claim/report path or private artifact payload."""


class AffectAppraisalGateError(RuntimeError):
    """Stop a manual-evaluation turn when appraisal was not provider-successful."""


APPLIED_AFFECT_REASON_CODE = "bounded_appraisal_applied"
NEUTRAL_AFFECT_REASON_CODE = "neutral_appraisal_no_delta"


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def content_digest(value: Mapping[str, Any]) -> str:
    """Hash a public JSON contract with stable Unicode-preserving serialization."""

    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return "sha256:" + hashlib.sha256(payload).hexdigest()


MANUAL_EVALUATION_ARTIFACT_CONTRACT = {
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


def manual_affect_contract() -> dict[str, Any]:
    """Return the exact pre-foreground local affect evidence contract."""

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


def manual_selected_usage_contract() -> dict[str, Any]:
    """Describe which exact atomic-ledger attempt supplies committed usage."""

    return {
        "source": "atomic_paid_call_ledger",
        "all_paid_attempts_require_exact_cache_aware_usage": True,
        "committed_reply_input_output_parity_required": True,
        "committed_reply_cache_breakdown_may_be_absent": True,
        "one_attempt_selects": 1,
        "successful_regeneration_selects": 2,
        "rejected_regeneration_selects": 1,
    }


def openai_manual_evaluation_settings(
    *,
    model: str,
    reasoning_effort: OpenAIReasoningEffort,
    reasoning_token_allowance: int,
    visible_output_token_ceiling: int,
) -> dict[str, object]:
    """Return the exact version-neutral runtime settings bound into an OpenAI sample plan."""

    return {
        "conversation_provider": ConversationProviderKind.OPENAI,
        "conversation_model": model,
        "conversation_provider_base_url": "http://127.0.0.1:11434",
        "conversation_timeout_seconds": 120.0,
        "conversation_temperature": 0.3,
        "conversation_max_output_tokens": visible_output_token_ceiling,
        "conversation_max_input_chars": 8000,
        "conversation_max_context_chars": 12_000,
        "conversation_max_response_chars": 12_000,
        "openai_base_url": "https://api.openai.com/v1",
        "openai_reasoning_effort": reasoning_effort,
        "openai_reasoning_token_allowance": reasoning_token_allowance,
        "recent_conversation_max_turns": 8,
        "recent_conversation_max_chars": 6000,
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


def public_settings_contract(settings: Mapping[str, object]) -> dict[str, object]:
    return {
        key: value.value if hasattr(value, "value") else value for key, value in settings.items()
    }


def strict_json_equal(actual: object, expected: object) -> bool:
    """Compare JSON-shaped evidence without Python bool/integer coercion."""

    if isinstance(expected, Mapping):
        return (
            isinstance(actual, Mapping)
            and set(actual) == set(expected)
            and all(strict_json_equal(actual[key], value) for key, value in expected.items())
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(
                strict_json_equal(left, right) for left, right in zip(actual, expected, strict=True)
            )
        )
    return type(actual) is type(expected) and actual == expected


def human_review_content_digest(review: Mapping[str, Any]) -> str:
    return content_digest({key: value for key, value in review.items() if key != "content_digest"})


def validate_human_review_artifact(
    review: Mapping[str, Any],
    completed_report: Mapping[str, Any],
    *,
    per_turn_dimensions: Sequence[str],
    cross_session_dimensions: Sequence[str],
    review_schema_version: int = 1,
) -> bool:
    """Validate one human-only review and return its explicit all-dimensions decision."""

    expected_top = {
        "schema_version",
        "artifact_id",
        "sample_digest",
        "execution_plan_digest",
        "session_reviews",
        "cross_session_dimensions",
        "reviewer_attestation",
        "accepted",
        "content_digest",
    }
    if set(review) != expected_top:
        raise ValueError("human-review artifact schema drift")
    if (
        type(review.get("schema_version")) is not int
        or review.get("schema_version") != review_schema_version
        or review.get("artifact_id") != completed_report.get("artifact_id")
        or review.get("sample_digest") != completed_report.get("sample_digest")
        or review.get("execution_plan_digest") != completed_report.get("execution_plan_digest")
        or review.get("content_digest") != human_review_content_digest(review)
    ):
        raise ValueError("human-review artifact is not exactly bound to this sample")
    expected_sessions = completed_report.get("sessions")
    session_reviews = review.get("session_reviews")
    if (
        not isinstance(expected_sessions, list)
        or not isinstance(session_reviews, list)
        or len(session_reviews) != len(expected_sessions)
    ):
        raise ValueError("human-review session cardinality drift")
    per_turn_set = set(per_turn_dimensions)
    if not per_turn_set or len(per_turn_set) != len(per_turn_dimensions):
        raise ValueError("human-review dimensions must be unique and non-empty")
    decisions: list[bool] = []
    for actual_session, expected_session in zip(session_reviews, expected_sessions, strict=True):
        if (
            not isinstance(actual_session, dict)
            or set(actual_session) != {"session_id", "turns"}
            or not isinstance(expected_session, dict)
            or actual_session.get("session_id") != expected_session.get("session_id")
        ):
            raise ValueError("human-review session identity drift")
        actual_turns = actual_session.get("turns")
        expected_turns = expected_session.get("turns")
        if (
            not isinstance(actual_turns, list)
            or not isinstance(expected_turns, list)
            or len(actual_turns) != len(expected_turns)
        ):
            raise ValueError("human-review turn cardinality drift")
        for actual_turn, expected_turn in zip(actual_turns, expected_turns, strict=True):
            if (
                not isinstance(actual_turn, dict)
                or set(actual_turn) != {"turn", "turn_id", "dimensions"}
                or not isinstance(expected_turn, dict)
                or type(actual_turn.get("turn")) is not int
                or actual_turn.get("turn") != expected_turn.get("turn")
                or actual_turn.get("turn_id") != expected_turn.get("turn_id")
            ):
                raise ValueError("human-review turn identity drift")
            dimensions = actual_turn.get("dimensions")
            if not isinstance(dimensions, dict) or set(dimensions) != per_turn_set:
                raise ValueError("human-review turn dimensions drift")
            if any(type(value) is not bool for value in dimensions.values()):
                raise ValueError("every per-turn review dimension must be an explicit boolean")
            decisions.extend(cast(dict[str, bool], dimensions).values())
    cross = review.get("cross_session_dimensions")
    cross_set = set(cross_session_dimensions)
    if (
        not cross_set
        or len(cross_set) != len(cross_session_dimensions)
        or not isinstance(cross, dict)
        or set(cross) != cross_set
        or any(type(value) is not bool for value in cross.values())
    ):
        raise ValueError("human-review cross-session dimensions drift")
    decisions.extend(cast(dict[str, bool], cross).values())
    attestation = review.get("reviewer_attestation")
    expected_attestation = {
        "exact_public_sample_reviewed",
        "no_automated_text_judge_used",
        "no_response_rewriting_performed",
    }
    if (
        not isinstance(attestation, dict)
        or set(attestation) != expected_attestation
        or any(type(value) is not bool for value in attestation.values())
    ):
        raise ValueError("human-review attestation schema drift")
    decisions.extend(cast(dict[str, bool], attestation).values())
    accepted = review.get("accepted")
    if type(accepted) is not bool or accepted is not all(decisions):
        raise ValueError("human-review accepted flag contradicts explicit decisions")
    return accepted


def validate_manual_evaluation_sessions(
    sessions: object,
    *,
    public_turns: Sequence[Mapping[str, Any]],
    expected_turn_temperatures: Sequence[float],
    expected_turn_visible_output_token_limits: Sequence[int],
    expected_replica_count: int,
    public_session_prefix: str,
    expected_provider: ConversationProviderKind,
    expected_model: str,
    expected_context_schema_version: int,
    visible_output_token_ceiling: int,
    maximum_response_chars: int,
    safe_manifest: Callable[[Mapping[str, Any]], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate exact public session, attempt, retry, usage and manifest evidence."""

    frozen_turn_temperatures = tuple(expected_turn_temperatures)
    frozen_turn_visible_output_token_limits = tuple(expected_turn_visible_output_token_limits)
    if len(frozen_turn_temperatures) != len(public_turns) or any(
        type(value) not in {int, float} or not math.isfinite(value) or not 0.0 <= value <= 2.0
        for value in frozen_turn_temperatures
    ):
        raise ValueError("expected per-turn temperature vector is invalid")
    if len(frozen_turn_visible_output_token_limits) != len(public_turns) or any(
        type(value) is not int or not 1 <= value <= visible_output_token_ceiling
        for value in frozen_turn_visible_output_token_limits
    ):
        raise ValueError("expected per-turn visible-output-token vector is invalid")
    if not isinstance(sessions, list) or len(sessions) != expected_replica_count:
        raise ValueError("completed report has invalid session cardinality")
    validated = cast(list[dict[str, Any]], sessions)
    for replica, session in enumerate(validated, start=1):
        if set(session) != {"session_id", "fresh_database", "completed", "turns"} or (
            session.get("session_id") != f"{public_session_prefix}-{replica}"
            or session.get("fresh_database") is not True
            or session.get("completed") is not True
        ):
            raise ValueError("completed session identity/status drift")
        turns = session.get("turns")
        if not isinstance(turns, list) or len(turns) != len(public_turns):
            raise ValueError("completed session turn cardinality drift")
        for turn_index, (actual, expected) in enumerate(zip(turns, public_turns, strict=True)):
            if not isinstance(actual, dict) or set(actual) != _PUBLIC_TURN_EVIDENCE_KEYS:
                raise ValueError("completed turn schema drift")
            if (
                type(actual.get("turn")) is not int
                or actual.get("turn") != expected.get("turn")
                or actual.get("turn_id") != expected.get("id")
                or actual.get("user") != expected.get("user_text")
                or actual.get("status") != "completed"
                or actual.get("provider_call_observed") is not True
            ):
                raise ValueError("completed turn identity/status drift")
            reply = actual.get("reply")
            if (
                not isinstance(reply, str)
                or not reply.strip()
                or len(reply) > maximum_response_chars
            ):
                raise ValueError("completed reply is missing or over the character limit")
            if not strict_json_equal(
                actual.get("generation"),
                {
                    "provider": expected_provider.value,
                    "model": expected_model,
                    "finish_status": "completed",
                    "replayed": False,
                },
            ):
                raise ValueError("completed generation metadata drift")
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
                or actual.get("usage_source") != "atomic_paid_call_ledger"
            ):
                raise ValueError("completed turn usage is incomplete or cached")
            attempts = actual.get("provider_attempts")
            if (
                not isinstance(attempts, list)
                or len(attempts) not in {1, 2}
                or type(actual.get("provider_attempt_count")) is not int
                or actual.get("provider_attempt_count") != len(attempts)
            ):
                raise ValueError("completed provider-attempt evidence is invalid")
            for index, attempt in enumerate(attempts, start=1):
                if not isinstance(attempt, dict) or set(attempt) != _PUBLIC_ATTEMPT_EVIDENCE_KEYS:
                    raise ValueError("completed provider-attempt schema drift")
                role_counts = attempt.get("message_role_counts")
                if (
                    type(attempt.get("attempt_number")) is not int
                    or attempt.get("attempt_number") != index
                    or type(attempt.get("wall_ms")) not in {int, float}
                    or not math.isfinite(cast(float, attempt["wall_ms"]))
                    or cast(float, attempt["wall_ms"]) < 0
                    or type(attempt.get("request_schema_version")) is not int
                    or attempt.get("request_schema_version") != 1
                    or type(attempt.get("context_schema_version")) is not int
                    or attempt.get("context_schema_version") != expected_context_schema_version
                    or type(attempt.get("message_count")) is not int
                    or cast(int, attempt["message_count"]) < 1
                    or not isinstance(role_counts, dict)
                    or not set(role_counts) <= {"system", "developer", "user", "assistant"}
                    or any(type(count) is not int or count < 0 for count in role_counts.values())
                    or sum(cast(dict[str, int], role_counts).values()) != attempt["message_count"]
                    or type(attempt.get("request_content_chars")) is not int
                    or cast(int, attempt["request_content_chars"]) < 1
                    or attempt.get("temperature") != frozen_turn_temperatures[turn_index]
                    or type(attempt.get("max_output_tokens")) is not int
                    or attempt.get("max_output_tokens")
                    != frozen_turn_visible_output_token_limits[turn_index]
                    or type(attempt.get("input_tokens")) is not int
                    or type(attempt.get("output_tokens")) is not int
                    or cast(int, attempt["input_tokens"]) < 0
                    or cast(int, attempt["output_tokens"]) < 0
                    or attempt.get("finish_status") != "completed"
                    or attempt.get("succeeded") is not True
                    or attempt.get("error_type") is not None
                    or safe_provider_metrics(attempt.get("provider_metrics"))
                    != attempt.get("provider_metrics")
                ):
                    raise ValueError("completed provider-attempt evidence drift")
            timings = actual.get("timings_ms")
            if (
                not isinstance(timings, dict)
                or set(timings) != set(_TIMING_KEYS)
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or value < 0
                    for value in timings.values()
                )
            ):
                raise ValueError("completed turn timings schema drift")
            manifest = actual.get("manifest")
            if not isinstance(manifest, dict) or safe_manifest(manifest) != manifest:
                raise ValueError("completed manifest contract drift")
            selected_attempt = actual.get("selected_provider_attempt")
            response_regenerated = manifest.get("response_regenerated")
            regeneration_reason = manifest.get("regeneration_reason")
            expected_selected = 2 if response_regenerated is True else 1
            if (
                type(selected_attempt) is not int
                or selected_attempt != expected_selected
                or not 1 <= selected_attempt <= len(attempts)
                or (regeneration_reason is not None) is not (len(attempts) == 2)
                or usage.get("input_tokens") != attempts[selected_attempt - 1].get("input_tokens")
                or usage.get("output_tokens") != attempts[selected_attempt - 1].get("output_tokens")
            ):
                raise ValueError("selected reply usage disagrees with its exact provider attempt")
    return validated


def _bytes_digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _regular_file_bytes(path: Path, label: str) -> bytes:
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise EvaluationArtifactSafetyError(f"{label} is missing") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise EvaluationArtifactSafetyError(f"{label} must be a regular non-symlink file")
    return path.read_bytes()


def _tree_fingerprint(
    path: Path,
    label: str,
    *,
    expected_relative_files: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise EvaluationArtifactSafetyError(f"{label} is missing") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise EvaluationArtifactSafetyError(f"{label} must be a non-symlink directory")
    if expected_relative_files is None:
        relative_files = tuple(
            candidate.relative_to(path).as_posix()
            for candidate in sorted(path.rglob("*"), key=lambda item: item.as_posix())
            if "__pycache__" not in candidate.relative_to(path).parts
            and candidate.suffix in _TREE_FILE_SUFFIXES
        )
        unexpected_count = 0
    else:
        relative_files = expected_relative_files
        expected = set(expected_relative_files)
        # Enumerate names but never read unexpected/conflict-copy files.  Besides being irrelevant
        # to canonical imports, macOS cloud conflict copies can be remote placeholders and must not
        # make offline plan inspection block on file hydration.
        actual = {
            candidate.relative_to(path).as_posix()
            for candidate in path.rglob("*")
            if "__pycache__" not in candidate.relative_to(path).parts
            and candidate.suffix in _TREE_FILE_SUFFIXES
        }
        unexpected_count = len(actual - expected)
    digest = hashlib.sha256()
    file_count = 0
    for relative_name in relative_files:
        candidate = path / relative_name
        candidate_metadata = candidate.lstat()
        if stat.S_ISLNK(candidate_metadata.st_mode) or not stat.S_ISREG(candidate_metadata.st_mode):
            raise EvaluationArtifactSafetyError(f"{label} contains a non-regular source file")
        name = relative_name.encode("utf-8")
        payload = candidate.read_bytes()
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
        file_count += 1
    if file_count == 0:
        raise EvaluationArtifactSafetyError(f"{label} contains no fingerprinted files")
    return {
        "sha256": "sha256:" + digest.hexdigest(),
        "file_count": file_count,
        "unexpected_source_file_count": unexpected_count,
    }


_V26_EVALUATOR_BUNDLE = (
    "checkpoint142_openai_v26_manual_eval.py",
    "checkpoint142_openai_v26_ledger.py",
    "checkpoint142_openai_manual_support.py",
    "stage81_real_eval.py",
)


def execution_source_fingerprint(
    *,
    evaluator_names: Sequence[str] = _V26_EVALUATOR_BUNDLE,
) -> dict[str, Any]:
    """Fingerprint runtime plus an explicit immutable evaluator source bundle.

    The default remains the historical V26 bundle so retained V26 validation and diagnostics keep
    their original dependency boundary.  A successor evaluator must name every helper it executes;
    this prevents a V27 authorization from silently binding only the retired V26 entry point.
    """

    root = repository_root()
    source_package = root / "src" / "satori"
    imported_package = Path(satori.__file__).resolve().parent
    source = _tree_fingerprint(source_package, "source package")
    source_files = tuple(
        candidate.relative_to(source_package).as_posix()
        for candidate in sorted(source_package.rglob("*"), key=lambda item: item.as_posix())
        if "__pycache__" not in candidate.relative_to(source_package).parts
        and candidate.suffix in _TREE_FILE_SUFFIXES
    )
    installed = _tree_fingerprint(
        imported_package,
        "installed package",
        expected_relative_files=source_files,
    )
    evaluator_names = tuple(evaluator_names)
    if (
        not evaluator_names
        or len(evaluator_names) != len(set(evaluator_names))
        or any(
            not isinstance(name, str)
            or not name
            or Path(name).name != name
            or Path(name).suffix != ".py"
            for name in evaluator_names
        )
    ):
        raise EvaluationArtifactSafetyError(
            "evaluator source bundle must contain unique local Python filenames"
        )
    evaluator_digest = hashlib.sha256()
    for name in evaluator_names:
        payload = _regular_file_bytes(root / "tests" / name, f"evaluator source {name}")
        encoded_name = name.encode("utf-8")
        evaluator_digest.update(len(encoded_name).to_bytes(8, "big"))
        evaluator_digest.update(encoded_name)
        evaluator_digest.update(len(payload).to_bytes(8, "big"))
        evaluator_digest.update(payload)
    seed_path = root / "src" / "satori" / "resources" / "seeds" / "satori-v1.json"
    migration = _tree_fingerprint(root / "migrations", "migration tree")
    migration_config = _regular_file_bytes(root / "alembic.ini", "alembic configuration")
    uv_lock = _regular_file_bytes(root / "uv.lock", "uv lockfile")
    pyproject = _regular_file_bytes(root / "pyproject.toml", "project configuration")
    installed_is_separate = imported_package != source_package.resolve()
    fingerprint: dict[str, Any] = {
        "schema_version": 1,
        "source_package": source,
        "installed_package": installed,
        "installed_wheel_parity": (
            installed_is_separate
            and source["sha256"] == installed["sha256"]
            and source["file_count"] == installed["file_count"]
            and installed["unexpected_source_file_count"] == 0
        ),
        "installed_runtime_is_separate": installed_is_separate,
        "distribution_version": importlib.metadata.version("satori-core"),
        "runtime_distributions": {
            name: importlib.metadata.version(name)
            for name in (
                "alembic",
                "pydantic",
                "pydantic-settings",
                "sqlalchemy",
                "satori-core",
            )
        },
        "seed_sha256": _bytes_digest(_regular_file_bytes(seed_path, "canonical seed")),
        "uv_lock_sha256": _bytes_digest(uv_lock),
        "pyproject_sha256": _bytes_digest(pyproject),
        "migration_tree": migration,
        "alembic_ini_sha256": _bytes_digest(migration_config),
        "evaluator_bundle_sha256": "sha256:" + evaluator_digest.hexdigest(),
        "runtime": {
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "python_cache_tag": sys.implementation.cache_tag,
            "platform_system": platform.system(),
            "platform_machine": platform.machine(),
        },
    }
    fingerprint["fingerprint_digest"] = content_digest(fingerprint)
    return fingerprint


def unsafe_artifact_paths(value: object, path: str = "$") -> tuple[str, ...]:
    unsafe: list[str] = []
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}"
            if key.casefold().replace("-", "_") in _UNSAFE_ARTIFACT_KEYS:
                unsafe.append(child_path)
            unsafe.extend(unsafe_artifact_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            unsafe.extend(unsafe_artifact_paths(child, f"{path}[{index}]"))
    return tuple(unsafe)


def _safe_directory(path: Path, *, create: bool, mode: int = 0o700) -> None:
    if create:
        with suppress(FileExistsError):
            path.mkdir(mode=mode)
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise EvaluationArtifactSafetyError(
            f"required directory is missing: {path.name}"
        ) from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise EvaluationArtifactSafetyError(f"unsafe evaluation directory: {path.name}")
    if metadata.st_mode & 0o022:
        raise EvaluationArtifactSafetyError(
            f"evaluation directory must not be group/world writable: {path.name}"
        )


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write while persisting evaluation artifact")
        view = view[written:]


def acquire_one_shot_authorization_claim(
    *,
    root: Path,
    authorization_id: str,
    expected_authorization_id: str,
    plan_digest: str,
    expected_claim_name: str,
    evaluation_label: str = "V26",
) -> Path:
    """Durably consume exactly one fixed authorization before Settings or provider I/O."""

    if authorization_id != expected_authorization_id:
        raise EvaluationArtifactSafetyError(
            f"authorization ID does not match the fixed {evaluation_label} grant"
        )
    root = root.resolve()
    _safe_directory(root, create=False)
    claims = root / "evaluation-authorizations"
    _safe_directory(claims, create=True)
    _fsync_directory(root)
    target = claims / expected_claim_name
    if target.parent != claims or target.name != expected_claim_name:
        raise EvaluationArtifactSafetyError("authorization claim path is not fixed")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(target, flags, 0o600)
    except FileExistsError as error:
        raise EvaluationArtifactSafetyError(
            f"{evaluation_label} authorization has already been consumed"
        ) from error
    try:
        receipt = {
            "schema_version": 1,
            "authorization_id": authorization_id,
            "execution_plan_digest": plan_digest,
            "one_shot": True,
        }
        payload = json.dumps(
            receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        _write_all(descriptor, payload)
        os.fsync(descriptor)
    except BaseException:
        # Intentionally retain the claim even if the receipt write fails: an uncertain grant must
        # never become replayable.
        raise
    finally:
        os.close(descriptor)
    os.chmod(target, 0o600, follow_symlinks=False)
    _fsync_directory(claims)
    return target


@dataclass(slots=True)
class DurableReportWriter:
    """Create one fixed 0600 report once, then atomically checkpoint only that file."""

    root: Path
    expected_report_name: str
    evaluation_label: str = "V26"
    _created: bool = False

    def __post_init__(self) -> None:
        self.root = self.root.resolve()
        _safe_directory(self.root, create=False)

    @property
    def path(self) -> Path:
        return self.root / "evaluations" / self.expected_report_name

    def prepare(self) -> None:
        """Validate/create the fixed report directory and reject an occupied target."""

        reports = self.root / "evaluations"
        _safe_directory(reports, create=True)
        _fsync_directory(self.root)
        target = self.path
        if target.parent != reports or target.name != self.expected_report_name:
            raise EvaluationArtifactSafetyError("report path is not fixed")
        if self._created:
            try:
                current = target.lstat()
            except FileNotFoundError as error:
                raise EvaluationArtifactSafetyError("owned report disappeared") from error
            if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode):
                raise EvaluationArtifactSafetyError("owned report target became unsafe")
        elif target.exists() or target.is_symlink():
            raise EvaluationArtifactSafetyError(
                f"fixed {self.evaluation_label} report already exists"
            )

    def write(self, report: Mapping[str, Any]) -> None:
        unsafe = unsafe_artifact_paths(report)
        if unsafe:
            raise EvaluationArtifactSafetyError(
                "report contains forbidden private keys: " + ", ".join(unsafe)
            )
        self.prepare()
        reports = self.root / "evaluations"
        target = self.path

        payload = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        temporary = reports / f".{self.expected_report_name}.{uuid.uuid4().hex}.tmp"
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        descriptor = os.open(temporary, flags, 0o600)
        try:
            _write_all(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            if self._created:
                os.replace(temporary, target)
            else:
                os.link(temporary, target, follow_symlinks=False)
                os.unlink(temporary)
                self._created = True
            os.chmod(target, 0o600, follow_symlinks=False)
            _fsync_directory(reports)
        except BaseException:
            with suppress(FileNotFoundError):
                temporary.unlink()
            raise


def _validate_successful_affect(
    prepared: PreparedAffectiveContext,
    *,
    expected_model: str,
) -> None:
    """Accept a provider-backed transition or the owner's exact neutral no-op."""

    if (
        prepared.provider != OLLAMA_PROVIDER_NAME
        or prepared.model != expected_model
        or prepared.appraisal_method != APPRAISAL_METHOD
        or prepared.provider_metrics is None
        or prepared.expression.appraisal_status is not prepared.appraisal_status
    ):
        raise AffectAppraisalGateError(
            "manual evaluation requires successful local affect evidence before "
            "foreground generation"
        )
    if prepared.appraisal_status is EmotionAppraisalStatus.APPLIED:
        if (
            prepared.reason_code != APPLIED_AFFECT_REASON_CODE
            or prepared.transition is None
            or prepared.transition.before != prepared.materialized_pre_event
            or not _expression_matches_state(prepared, prepared.transition.after)
        ):
            raise AffectAppraisalGateError(
                "applied appraisal requires the owner's exact bounded transition"
            )
        return
    if prepared.appraisal_status is EmotionAppraisalStatus.SKIPPED:
        if (
            prepared.reason_code != NEUTRAL_AFFECT_REASON_CODE
            or prepared.transition is not None
            or not _expression_matches_state(prepared, prepared.materialized_pre_event)
        ):
            raise AffectAppraisalGateError(
                "skipped appraisal must be the owner's exact neutral no-delta decision"
            )
        return
    raise AffectAppraisalGateError(
        "manual evaluation requires an applied or owner-approved neutral local affect appraisal"
    )


def _expression_matches_state(prepared: PreparedAffectiveContext, state: object) -> bool:
    """Verify that expression is the exact owner-selected affect snapshot projection."""

    expression = prepared.expression
    return expression.schema_version == EMOTIONAL_EXPRESSION_CONTEXT_SCHEMA_VERSION and all(
        getattr(expression, name) == getattr(state, name)
        for name in ("state_version", "mood_version", "as_of", "fast", "mood")
    )


def _safe_affect_evidence(prepared: PreparedAffectiveContext) -> dict[str, str | bool]:
    """Project content-free provider/owner evidence after strict appraisal validation."""

    return {
        "emotion_appraisal_status": prepared.appraisal_status.value,
        "emotion_appraisal_reason_code": prepared.reason_code,
        "emotion_appraisal_provider": cast(str, prepared.provider),
        "emotion_appraisal_model": cast(str, prepared.model),
        "emotion_appraisal_method": cast(str, prepared.appraisal_method),
        "emotion_appraisal_transition_prepared": prepared.transition is not None,
        "emotion_appraisal_provider_metrics_present": prepared.provider_metrics is not None,
    }


@dataclass(slots=True)
class RequiredSuccessfulAffect:
    """Delegate local appraisal but reject unavailable/rejected degradation before paid work."""

    delegate: PrepareAffectiveContext
    expected_model: str
    _pending_evidence: dict[str, str | bool] | None = field(init=False, default=None)
    _evidence_sink: Callable[[Mapping[str, str | bool]], None] | None = field(
        init=False,
        default=None,
    )

    def bind_evidence_sink(
        self,
        sink: Callable[[Mapping[str, str | bool]], None] | None,
    ) -> None:
        """Bind one report-owned sink so valid local evidence is durable before paid work."""

        self._evidence_sink = sink

    async def execute(
        self,
        snapshot: InitialSelfSnapshot,
        interaction: ConversationInteraction,
        *,
        user_text: str,
        trace_id: str,
        memory_context: RetrievedMemoryContext | None,
        semantic_context: RetrievedSemanticContext | None,
    ) -> PreparedAffectiveContext:
        self._pending_evidence = None
        prepared = await self.delegate.execute(
            snapshot,
            interaction,
            user_text=user_text,
            trace_id=trace_id,
            memory_context=memory_context,
            semantic_context=semantic_context,
        )
        _validate_successful_affect(prepared, expected_model=self.expected_model)
        self._pending_evidence = _safe_affect_evidence(prepared)
        if self._evidence_sink is not None:
            self._evidence_sink(self._pending_evidence)
        return prepared

    def consume_evidence(self, *, production_status: object) -> dict[str, str | bool]:
        """Return one validated turn's evidence and prove manifest/status parity."""

        evidence = self._pending_evidence
        self._pending_evidence = None
        if evidence is None:
            raise AffectAppraisalGateError(
                "affect evidence is missing for the completed evaluation turn"
            )
        if production_status != evidence["emotion_appraisal_status"]:
            raise AffectAppraisalGateError(
                "production manifest disagrees with validated affect evidence"
            )
        return evidence


def new_replica_record(*, session_id: str) -> dict[str, Any]:
    return {"session_id": session_id, "fresh_database": True, "completed": False, "turns": []}


def _safe_attempt(value: object, attempt_number: int) -> dict[str, Any]:
    raw = asdict(cast(Any, value)) if hasattr(value, "__dataclass_fields__") else {}
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
        "provider_metrics": safe_provider_metrics(raw.get("provider_metrics")),
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
        "cached_input_tokens": reply.usage.cached_input_tokens,
        "cache_write_input_tokens": reply.usage.cache_write_input_tokens,
    }


def _reconcile_committed_usage(
    *,
    committed_usage: Mapping[str, int | None] | None,
    exact_attempt_usages: tuple[ExactProviderUsage, ...],
    provider_attempts: Sequence[Mapping[str, Any]],
    regeneration_attempted: bool,
    response_regenerated: bool,
) -> tuple[dict[str, int], int]:
    """Bind lossy committed totals to the exact cache-aware selected provider attempt."""

    if (
        committed_usage is None
        or len(exact_attempt_usages) not in {1, 2}
        or len(provider_attempts) != len(exact_attempt_usages)
        or type(regeneration_attempted) is not bool
        or type(response_regenerated) is not bool
        or regeneration_attempted is not (len(exact_attempt_usages) == 2)
        or (response_regenerated and not regeneration_attempted)
    ):
        raise RuntimeError("committed usage cannot be bound to the exact provider attempts")
    for attempt_number, (attempt, exact) in enumerate(
        zip(provider_attempts, exact_attempt_usages, strict=True),
        start=1,
    ):
        if (
            exact.attempt_number != attempt_number
            or type(attempt.get("attempt_number")) is not int
            or attempt.get("attempt_number") != attempt_number
            or exact.scope != exact_attempt_usages[0].scope
            or type(attempt.get("input_tokens")) is not int
            or attempt.get("input_tokens") != exact.input_tokens
            or type(attempt.get("output_tokens")) is not int
            or attempt.get("output_tokens") != exact.output_tokens
        ):
            raise RuntimeError("provider-attempt usage disagrees with the atomic ledger")
    selected_attempt_number = 2 if response_regenerated else 1
    selected = exact_attempt_usages[selected_attempt_number - 1]
    if (
        type(committed_usage.get("input_tokens")) is not int
        or committed_usage.get("input_tokens") != selected.input_tokens
        or type(committed_usage.get("output_tokens")) is not int
        or committed_usage.get("output_tokens") != selected.output_tokens
    ):
        raise RuntimeError("committed selected usage disagrees with the atomic ledger")
    committed_cached = committed_usage.get("cached_input_tokens")
    committed_written = committed_usage.get("cache_write_input_tokens")
    if (committed_cached is None) is not (committed_written is None):
        raise RuntimeError("committed cache detail must be either complete or absent")
    for key, expected_cache_tokens in (
        ("cached_input_tokens", selected.cached_input_tokens),
        ("cache_write_input_tokens", selected.cache_write_input_tokens),
    ):
        value = committed_usage.get(key)
        if value is not None and (type(value) is not int or value != expected_cache_tokens):
            raise RuntimeError("committed cache detail contradicts the atomic ledger")
    return (
        {
            "input_tokens": selected.input_tokens,
            "output_tokens": selected.output_tokens,
            "cached_input_tokens": selected.cached_input_tokens,
            "cache_write_input_tokens": selected.cache_write_input_tokens,
        },
        selected_attempt_number,
    )


def _safe_timings(reply: SatoriReply) -> dict[str, int | float | None]:
    raw = asdict(reply.timings)
    return {
        key: (
            raw.get(key)
            if raw.get(key) is None
            or (
                isinstance(raw.get(key), (int, float))
                and not isinstance(raw.get(key), bool)
                and math.isfinite(cast(float, raw.get(key)))
                and cast(float, raw.get(key)) >= 0
            )
            else None
        )
        for key in _TIMING_KEYS
    }


async def run_replica(
    *,
    settings: Settings,
    database_path: Path,
    alembic_config: Path,
    replica_number: int,
    ledger: V26AtomicOpenAICallLedger,
    checkpoint: Callable[[], None],
    behavior_policy: BehaviorPolicy,
    public_turns: tuple[dict[str, Any], ...],
    public_session_prefix: str,
    expected_provider: ConversationProviderKind,
    expected_model: str,
    safe_manifest: Callable[[Mapping[str, Any]], dict[str, Any]],
    record: dict[str, Any],
    manifest_projector: Callable[[SatoriReply], dict[str, Any]] = _sanitized_manifest,
) -> dict[str, Any]:
    """Run one production session with durable evidence before and after each paid attempt."""

    public_session_id = f"{public_session_prefix}-{replica_number}"
    if record != new_replica_record(session_id=public_session_id):
        raise ValueError("replica record must be fresh, empty and report-owned")
    runtime, _ = await _build_runtime(
        settings,
        database_path,
        alembic_config=alembic_config,
        behavior_policy=behavior_policy,
    )
    binding = TurnScopeBinding()
    runtime.conversation_provider.delegate = BudgetedOpenAIProvider(
        delegate=runtime.conversation_provider.delegate,
        ledger=ledger,
        scope_binding=binding,
    )
    original_affect = runtime.services.talk.prepare_affect
    if original_affect is None:
        runtime.close()
        raise AffectAppraisalGateError("production runtime has no affect appraisal path")
    required_affect = RequiredSuccessfulAffect(
        original_affect,
        expected_model=settings.affective_appraisal_model,
    )
    runtime.services.talk.prepare_affect = cast(
        PrepareAffectiveContext,
        required_affect,
    )
    application_session_id = runtime.services.start_session.execute().session_id
    ids = Uuid4Generator()

    def evidence_sink_for(
        current_turn: dict[str, Any],
    ) -> Callable[[Mapping[str, str | bool]], None]:
        def capture(evidence: Mapping[str, str | bool]) -> None:
            current_turn.update(
                {
                    "status": "affect_appraisal_validated",
                    "affect_evidence": dict(evidence),
                }
            )
            checkpoint()

        return capture

    try:
        for fixture_turn in public_turns:
            turn_number = cast(int, fixture_turn["turn"])
            turn_record: dict[str, Any] = {
                "turn": turn_number,
                "turn_id": fixture_turn["id"],
                "user": fixture_turn["user_text"],
                "status": "pending_provider_call",
                "provider_call_observed": False,
            }
            cast(list[dict[str, Any]], record["turns"]).append(turn_record)
            checkpoint()
            scope = PublicTurnScope(
                session_id=public_session_id,
                turn=turn_number,
                turn_id=cast(str, fixture_turn["id"]),
            )
            first_attempt = len(runtime.conversation_provider.attempts)
            required_affect.bind_evidence_sink(evidence_sink_for(turn_record))
            binding.set(scope)
            try:
                reply = await runtime.services.talk.execute(
                    TalkInput(
                        user_text=cast(str, fixture_turn["user_text"]),
                        trace_id=ids.new(),
                        client_request_id=ids.new(),
                        session_id=application_session_id,
                    )
                )
            except BaseException as error:
                failed_attempts = runtime.conversation_provider.attempts[first_attempt:]
                turn_record.update(
                    {
                        "status": "failed",
                        "error_type": type(error).__name__,
                        "provider_call_observed": ledger.provider_call_observed(scope),
                        "provider_attempt_count": len(failed_attempts),
                        "provider_attempts": [
                            _safe_attempt(attempt, index)
                            for index, attempt in enumerate(failed_attempts, start=1)
                        ],
                    }
                )
                checkpoint()
                raise
            finally:
                binding.clear()
                required_affect.bind_evidence_sink(None)
            attempts = runtime.conversation_provider.attempts[first_attempt:]
            safe_attempts = [
                _safe_attempt(attempt, index) for index, attempt in enumerate(attempts, start=1)
            ]
            committed_usage = _safe_usage(reply)
            turn_record.update(
                {
                    "status": "provider_reply_received",
                    "provider_call_observed": True,
                    "reply": _public_sampled_reply(reply),
                    "generation": {
                        "provider": reply.provider,
                        "model": reply.model,
                        "finish_status": reply.finish_status,
                        "replayed": reply.replayed,
                    },
                    "usage": committed_usage,
                    "timings_ms": _safe_timings(reply),
                    "provider_attempt_count": len(safe_attempts),
                    "provider_attempts": safe_attempts,
                }
            )
            # Preserve evidence of an already-paid reply even if a later metadata gate fails.
            checkpoint()
            try:
                exact_attempt_usages = ledger.require_completed_scope(scope, len(safe_attempts))
            except RuntimeError as error:
                turn_record.update({"status": "failed", "error_type": "InvalidPaidProviderAttempt"})
                checkpoint()
                raise RuntimeError(
                    "turn contains a failed, incomplete or usage-invalid paid attempt"
                ) from error
            if (
                reply.provider != expected_provider.value
                or reply.model != expected_model
                or reply.finish_status != "completed"
                or reply.replayed
                or len(safe_attempts) not in {1, 2}
                or any(
                    attempt["succeeded"] is not True or attempt["finish_status"] != "completed"
                    for attempt in safe_attempts
                )
            ):
                turn_record.update({"status": "failed", "error_type": "NonComparableProviderReply"})
                checkpoint()
                raise RuntimeError("turn did not produce one comparable committed OpenAI reply")
            try:
                usage, selected_attempt_number = _reconcile_committed_usage(
                    committed_usage=committed_usage,
                    exact_attempt_usages=exact_attempt_usages,
                    provider_attempts=safe_attempts,
                    regeneration_attempted=reply.context_manifest.regeneration_attempted,
                    response_regenerated=reply.context_manifest.response_regenerated,
                )
            except RuntimeError as error:
                turn_record.update({"status": "failed", "error_type": "NonComparableProviderReply"})
                checkpoint()
                raise RuntimeError("turn did not preserve comparable selected usage") from error
            turn_record.update(
                {
                    "usage": usage,
                    "usage_source": "atomic_paid_call_ledger",
                    "selected_provider_attempt": selected_attempt_number,
                }
            )
            checkpoint()
            raw_manifest = manifest_projector(reply)
            raw_manifest["character_presence_memory_use_licensed"] = (
                reply.context_manifest.character_presence_memory_use_licensed
            )
            affect_evidence = required_affect.consume_evidence(
                production_status=raw_manifest.get("emotion_appraisal_status")
            )
            if turn_record.get("affect_evidence") != affect_evidence:
                raise AffectAppraisalGateError(
                    "durable affect evidence disagrees with the completed evaluation turn"
                )
            raw_manifest.update(affect_evidence)
            safe = safe_manifest(raw_manifest)
            turn_record.pop("affect_evidence")
            turn_record.update({"status": "completed", "manifest": safe})
            checkpoint()
        record["completed"] = True
        checkpoint()
        return record
    finally:
        runtime.services.close_session.execute(application_session_id)
        runtime.close()
