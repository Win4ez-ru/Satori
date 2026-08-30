"""Offline safety, immutability and terminal-artifact tests for the V26 evaluator."""

# ruff: noqa: RUF001  # Exact Russian production phrases are intentional.

from __future__ import annotations

import asyncio
import copy
import json
import stat
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import SecretStr

from satori.application.affect.contracts import EmotionAppraisalStatus, PreparedAffectiveContext
from satori.application.conversation.contracts import (
    CONVERSATION_INCLUDED_SECTIONS,
    TalkInput,
)
from satori.application.conversation.policy import BEHAVIOR_POLICY_V26
from satori.config import ConversationProviderKind, OpenAIReasoningEffort, Settings
from satori.core.affect import (
    AffectiveAppraisalProposal,
    AffectiveAppraisalProviderResponse,
)
from satori.core.conversation import (
    ConversationGenerationParameters,
    ConversationMessage,
    ConversationMessageRole,
    ConversationProviderRequest,
    ConversationProviderResponse,
    ConversationUsage,
)
from satori.core.provider_metrics import ProviderExecutionMetrics
from tests.checkpoint142_openai_manual_support import (
    APPLIED_AFFECT_REASON_CODE,
    NEUTRAL_AFFECT_REASON_CODE,
    AffectAppraisalGateError,
    DurableReportWriter,
    EvaluationArtifactSafetyError,
    RequiredSuccessfulAffect,
    _reconcile_committed_usage,
    _safe_usage,
    _validate_successful_affect,
    acquire_one_shot_authorization_claim,
    content_digest,
)
from tests.checkpoint142_openai_v26_ledger import (
    NANO_USD_PER_USD,
    OPENAI_CACHE_WRITE_INPUT_NANO_USD_PER_TOKEN,
    OPENAI_OUTPUT_NANO_USD_PER_TOKEN,
    ExactProviderUsage,
    PublicTurnScope,
    V26AtomicOpenAICallLedger,
)
from tests.checkpoint142_openai_v26_manual_eval import (
    _ARTIFACT_CONTRACT,
    _TIMING_KEYS,
    ARCHIVED_ATTEMPT5_PLAN_DIGEST,
    ARCHIVED_ATTEMPT5_REVIEW_DIGEST,
    ARCHIVED_ATTEMPT5_SAMPLE_DIGEST,
    ARCHIVED_ATTEMPT5_SOURCE_DIGEST,
    AUTHORIZATION_CLAIM_NAME,
    AUTHORIZATION_ID,
    CLAIM_RELATIVE_PATH,
    CROSS_SESSION_HUMAN_REVIEW_DIMENSIONS,
    EXPECTED_MAX_RESPONSE_CHARS,
    MAXIMUM_COST_USD,
    MAXIMUM_PROVIDER_CALLS,
    PAID_EXECUTION_RETIRED,
    PER_TURN_HUMAN_REVIEW_DIMENSIONS,
    PUBLIC_TURNS,
    REPORT_NAME,
    REPORT_RELATIVE_PATH,
    REQUIRED_BASE_CALLS,
    REVIEW_RELATIVE_PATH,
    ExecutionPlan,
    V26ManualEvaluationConfigurationError,
    _configuration,
    _human_review_contract,
    _human_review_template,
    _preflight,
    _safe_manifest,
    _sample_payload,
    _validate_settings,
    execution_plan_digest,
    human_review_content_digest,
    inspect_plan,
    main,
    run,
    validate_archived_attempt5_bundle,
    validate_archived_attempt5_report,
    validate_completed_report,
    validate_human_review_artifact,
)
from tests.fakes import FakeAffectiveAppraisalProvider
from tests.stage81_real_eval import _build_runtime, _sanitized_manifest


def _synthetic_fingerprint(*, parity: bool = True, source: str = "sha256:source") -> dict[str, Any]:
    fingerprint: dict[str, Any] = {
        "schema_version": 1,
        "source_package": {"sha256": source, "file_count": 1},
        "installed_package": {"sha256": source, "file_count": 1},
        "installed_wheel_parity": parity,
        "installed_runtime_is_separate": True,
        "distribution_version": "0.1.0",
        "seed_sha256": "sha256:seed",
        "uv_lock_sha256": "sha256:lock",
        "pyproject_sha256": "sha256:project",
        "migration_tree": {"sha256": "sha256:migrations", "file_count": 1},
        "alembic_ini_sha256": "sha256:alembic",
        "evaluator_bundle_sha256": "sha256:evaluator",
        "runtime": {
            "python_implementation": "CPython",
            "python_version": "3.12.0",
            "python_cache_tag": "cpython-312",
            "platform_system": "Darwin",
            "platform_machine": "arm64",
        },
    }
    fingerprint["fingerprint_digest"] = content_digest(fingerprint)
    return fingerprint


def _valid_manifest() -> dict[str, Any]:
    return {
        "schema_version": 16,
        "policy_id": "satori.conversation.behavior.v26",
        "policy_schema_version": 26,
        "character_context_schema_version": 16,
        "included_sections": [
            "behavior_policy",
            "self_model",
            "self_consistency_facets",
            "personality_expression",
            "values",
            "relationship_expression_state",
            "emotional_expression_state",
            "character_delivery_decision",
            "character_presence_projection",
            "current_user_input",
        ],
        "response_regenerated": False,
        "regeneration_reason": None,
        "retrieval_status": "not_requested",
        "retrieved_memory_count": 0,
        "relationship_expression_profile": "developing_neutral",
        "relationship_recent_strain": False,
        "affect_expression_profile": "calm_even",
        "emotion_appraisal_status": "applied",
        "emotion_appraisal_reason_code": APPLIED_AFFECT_REASON_CODE,
        "emotion_appraisal_provider": "ollama",
        "emotion_appraisal_model": "qwen3:4b-instruct",
        "emotion_appraisal_method": "ollama.categorical_affective_appraisal.v2",
        "emotion_appraisal_transition_prepared": True,
        "emotion_appraisal_provider_metrics_present": True,
        "disclosure_primary_mode": "social",
        "disclosure_request_kind": "satori_self",
        "disclosure_facets": ["affect"],
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
        "cognition_response_verbosity": "medium",
        "cognition_position_stance": "answer",
        "character_delivery_decision_schema_version": 3,
        "character_delivery_goal": "social_connect",
        "character_delivery_voice": "lively_dry_warmth",
        "character_delivery_grounding": "trusted_context",
        "character_delivery_continuation": "complete",
        "character_delivery_pressure": "none",
        "character_delivery_position_stance": "answer",
        "character_delivery_preserve_uncertainty": False,
        "character_presence_projection_schema_version": 1,
        "character_presence_personality_signals": [
            "curious_analytical:defining",
            "warm_perceptive:strong",
            "light_irony:strong",
        ],
        "character_presence_value_signals": [
            "connection:defining",
            "curiosity:defining",
            "autonomy:defining",
        ],
        "character_presence_affect_signals": [
            "engaged_curiosity:defining",
            "playful_amusement:available",
        ],
        "character_presence_relationship_signals": [
            "intellectual_respect:strong",
            "growing_familiarity:available",
            "earned_trust:available",
        ],
        "character_presence_memory_use_licensed": False,
    }


def _valid_settings() -> Settings:
    return Settings.model_construct(
        conversation_provider=ConversationProviderKind.OPENAI,
        conversation_model="gpt-5.6-terra",
        openai_api_key=SecretStr("offline-test-key"),
        openai_reasoning_effort=OpenAIReasoningEffort.MEDIUM,
        openai_reasoning_token_allowance=1024,
    )


def _provider_request(trace_id: str) -> ConversationProviderRequest:
    return ConversationProviderRequest(
        schema_version=1,
        trace_id=trace_id,
        context_schema_version=16,
        messages=(ConversationMessage(ConversationMessageRole.USER, "offline public fixture"),),
        parameters=ConversationGenerationParameters(
            schema_version=1,
            temperature=0.3,
            max_output_tokens=768,
        ),
    )


def test_v26_committed_usage_uses_exact_ledger_cache_evidence() -> None:
    exact = (
        ExactProviderUsage(
            scope=PublicTurnScope("session-1", 1, "turn-1"),
            call_number=1,
            attempt_number=1,
            input_tokens=1063,
            output_tokens=32,
            cached_input_tokens=0,
            cache_write_input_tokens=0,
        ),
    )
    reconciled, selected_attempt = _reconcile_committed_usage(
        committed_usage={
            "input_tokens": 1063,
            "output_tokens": 32,
            "cached_input_tokens": None,
            "cache_write_input_tokens": None,
        },
        exact_attempt_usages=exact,
        provider_attempts=({"attempt_number": 1, "input_tokens": 1063, "output_tokens": 32},),
        regeneration_attempted=False,
        response_regenerated=False,
    )

    assert reconciled == {
        "input_tokens": 1063,
        "output_tokens": 32,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
    }
    assert selected_attempt == 1


def test_v26_safe_usage_reproduces_attempt3_committed_cache_loss() -> None:
    reply = cast(
        Any,
        SimpleNamespace(
            usage=ConversationUsage(
                input_tokens=1063,
                output_tokens=32,
            )
        ),
    )

    assert _safe_usage(reply) == {
        "input_tokens": 1063,
        "output_tokens": 32,
        "cached_input_tokens": None,
        "cache_write_input_tokens": None,
    }


@pytest.mark.parametrize(
    ("committed_input", "attempt_input", "cached_input"),
    [
        (1062, 1063, None),
        (1063, 1062, None),
        (1063, 1063, 1),
    ],
)
def test_v26_committed_usage_rejects_ledger_contradictions(
    committed_input: int,
    attempt_input: int,
    cached_input: int | None,
) -> None:
    exact = (
        ExactProviderUsage(
            scope=PublicTurnScope("session-1", 1, "turn-1"),
            call_number=1,
            attempt_number=1,
            input_tokens=1063,
            output_tokens=32,
            cached_input_tokens=0,
            cache_write_input_tokens=0,
        ),
    )

    with pytest.raises(RuntimeError, match=r"committed .*usage|provider-attempt|cache detail"):
        _reconcile_committed_usage(
            committed_usage={
                "input_tokens": committed_input,
                "output_tokens": 32,
                "cached_input_tokens": cached_input,
                "cache_write_input_tokens": None,
            },
            exact_attempt_usages=exact,
            provider_attempts=(
                {"attempt_number": 1, "input_tokens": attempt_input, "output_tokens": 32},
            ),
            regeneration_attempted=False,
            response_regenerated=False,
        )


@pytest.mark.parametrize(
    ("cached_input", "cache_write"),
    [(1, 0), (0, 1), (False, False), (0, None)],
)
def test_v26_committed_usage_rejects_invalid_cache_pairs(
    cached_input: int | None,
    cache_write: int | None,
) -> None:
    scope = PublicTurnScope("session-1", 1, "turn-1")
    exact = (ExactProviderUsage(scope, 1, 1, 1063, 32, 0, 0),)

    with pytest.raises(RuntimeError, match="cache detail"):
        _reconcile_committed_usage(
            committed_usage={
                "input_tokens": 1063,
                "output_tokens": 32,
                "cached_input_tokens": cached_input,
                "cache_write_input_tokens": cache_write,
            },
            exact_attempt_usages=exact,
            provider_attempts=({"attempt_number": 1, "input_tokens": 1063, "output_tokens": 32},),
            regeneration_attempted=False,
            response_regenerated=False,
        )


@pytest.mark.parametrize(
    ("attempt_number", "output_tokens", "regeneration_attempted"),
    [(True, 32, False), (1, 31, False), (1, 32, True)],
)
def test_v26_committed_usage_rejects_attempt_and_regeneration_drift(
    attempt_number: object,
    output_tokens: int,
    regeneration_attempted: bool,
) -> None:
    scope = PublicTurnScope("session-1", 1, "turn-1")
    exact = (ExactProviderUsage(scope, 1, 1, 1063, 32, 0, 0),)

    with pytest.raises(RuntimeError, match=r"committed usage|provider-attempt"):
        _reconcile_committed_usage(
            committed_usage={
                "input_tokens": 1063,
                "output_tokens": 32,
                "cached_input_tokens": None,
                "cache_write_input_tokens": None,
            },
            exact_attempt_usages=exact,
            provider_attempts=(
                {
                    "attempt_number": attempt_number,
                    "input_tokens": 1063,
                    "output_tokens": output_tokens,
                },
            ),
            regeneration_attempted=regeneration_attempted,
            response_regenerated=False,
        )


@pytest.mark.parametrize(
    ("response_regenerated", "committed_input", "expected_attempt"),
    [(True, 200, 2), (False, 100, 1)],
)
def test_v26_committed_usage_selects_regeneration_or_preserved_first_candidate(
    response_regenerated: bool,
    committed_input: int,
    expected_attempt: int,
) -> None:
    scope = PublicTurnScope("session-1", 1, "turn-1")
    exact = (
        ExactProviderUsage(scope, 1, 1, 100, 10, 0, 0),
        ExactProviderUsage(scope, 2, 2, 200, 20, 0, 0),
    )

    usage, selected_attempt = _reconcile_committed_usage(
        committed_usage={
            "input_tokens": committed_input,
            "output_tokens": committed_input // 10,
            "cached_input_tokens": None,
            "cache_write_input_tokens": None,
        },
        exact_attempt_usages=exact,
        provider_attempts=(
            {"attempt_number": 1, "input_tokens": 100, "output_tokens": 10},
            {"attempt_number": 2, "input_tokens": 200, "output_tokens": 20},
        ),
        regeneration_attempted=True,
        response_regenerated=response_regenerated,
    )

    assert selected_attempt == expected_attempt
    assert usage["input_tokens"] == committed_input


def test_v26_committed_usage_rejects_mixed_ledger_scopes() -> None:
    exact = (
        ExactProviderUsage(PublicTurnScope("session-1", 1, "turn-1"), 1, 1, 100, 10, 0, 0),
        ExactProviderUsage(PublicTurnScope("session-2", 1, "turn-1"), 2, 2, 200, 20, 0, 0),
    )

    with pytest.raises(RuntimeError, match="provider-attempt"):
        _reconcile_committed_usage(
            committed_usage={
                "input_tokens": 200,
                "output_tokens": 20,
                "cached_input_tokens": None,
                "cache_write_input_tokens": None,
            },
            exact_attempt_usages=exact,
            provider_attempts=(
                {"attempt_number": 1, "input_tokens": 100, "output_tokens": 10},
                {"attempt_number": 2, "input_tokens": 200, "output_tokens": 20},
            ),
            regeneration_attempted=True,
            response_regenerated=True,
        )


def _completed_report(
    plan: ExecutionPlan,
    *,
    retry_selected: bool | None = None,
) -> dict[str, Any]:
    ledger = V26AtomicOpenAICallLedger(
        maximum_calls=MAXIMUM_PROVIDER_CALLS,
        maximum_cost_usd=MAXIMUM_COST_USD,
        required_base_calls=REQUIRED_BASE_CALLS,
        reasoning_token_allowance=1024,
    )
    sessions: list[dict[str, Any]] = []
    for replica in range(1, 4):
        session_id = f"v26-character-replica-{replica}"
        turns: list[dict[str, Any]] = []
        for fixture in PUBLIC_TURNS:
            request = _provider_request(f"trace-{replica}-{fixture['turn']}")
            scope = PublicTurnScope(
                session_id=session_id,
                turn=fixture["turn"],
                turn_id=fixture["id"],
            )
            call = ledger.reserve(
                request,
                scope,
            )
            response = ConversationProviderResponse(
                text="Проверочный публичный ответ.",
                provider="openai",
                model="gpt-5.6-terra",
                finish_status="completed",
                usage=ConversationUsage(
                    input_tokens=100,
                    output_tokens=20,
                    cached_input_tokens=0,
                    cache_write_input_tokens=0,
                ),
            )
            ledger.settle_success(call, response)
            attempt = {
                "attempt_number": 1,
                "wall_ms": 10.0,
                "request_schema_version": 1,
                "context_schema_version": 16,
                "message_count": 1,
                "message_role_counts": {"user": 1},
                "request_content_chars": 22,
                "temperature": 0.3,
                "max_output_tokens": 768,
                "input_tokens": 100,
                "output_tokens": 20,
                "provider_metrics": None,
                "finish_status": "completed",
                "succeeded": True,
                "error_type": None,
            }
            provider_attempts = [attempt]
            selected_input_tokens = 100
            selected_output_tokens = 20
            selected_provider_attempt = 1
            manifest = _valid_manifest()
            if retry_selected is not None and replica == 1 and fixture["turn"] == 1:
                retry_call = ledger.reserve(request, scope)
                retry_response = ConversationProviderResponse(
                    text="Второй проверочный публичный ответ.",
                    provider="openai",
                    model="gpt-5.6-terra",
                    finish_status="completed",
                    usage=ConversationUsage(
                        input_tokens=120,
                        output_tokens=21,
                        cached_input_tokens=0,
                        cache_write_input_tokens=0,
                    ),
                )
                ledger.settle_success(retry_call, retry_response)
                provider_attempts.append(
                    {
                        **attempt,
                        "attempt_number": 2,
                        "input_tokens": 120,
                        "output_tokens": 21,
                    }
                )
                manifest.update(
                    {
                        "response_regenerated": retry_selected,
                        "regeneration_reason": "masculine_self_reference",
                    }
                )
                if retry_selected:
                    selected_input_tokens = 120
                    selected_output_tokens = 21
                    selected_provider_attempt = 2
            turns.append(
                {
                    "turn": fixture["turn"],
                    "turn_id": fixture["id"],
                    "user": fixture["user_text"],
                    "status": "completed",
                    "provider_call_observed": True,
                    "reply": "Проверочный публичный ответ.",
                    "generation": {
                        "provider": "openai",
                        "model": "gpt-5.6-terra",
                        "finish_status": "completed",
                        "replayed": False,
                    },
                    "usage": {
                        "input_tokens": selected_input_tokens,
                        "output_tokens": selected_output_tokens,
                        "cached_input_tokens": 0,
                        "cache_write_input_tokens": 0,
                    },
                    "usage_source": "atomic_paid_call_ledger",
                    "selected_provider_attempt": selected_provider_attempt,
                    "timings_ms": {key: 0.0 for key in _TIMING_KEYS},
                    "provider_attempt_count": len(provider_attempts),
                    "provider_attempts": provider_attempts,
                    "manifest": manifest,
                }
            )
        sessions.append(
            {
                "session_id": session_id,
                "fresh_database": True,
                "completed": True,
                "turns": turns,
            }
        )
    report: dict[str, Any] = {
        "schema_version": 4,
        "recorded_at": "2026-08-29T10:00:00+00:00",
        "completed_at": "2026-08-29T10:10:00+00:00",
        "checkpoint": "14.2",
        "purpose": "v26_unified_character_presence_production_gate",
        "status": "completed_awaiting_human_review",
        "artifact_id": f"satori-checkpoint142-openai-v26:{plan.digest}",
        "authorization_id": AUTHORIZATION_ID,
        "execution_plan_digest": plan.digest,
        "execution_plan": plan.public_mapping(),
        "source_fingerprint": copy.deepcopy(dict(plan.source_fingerprint)),
        "artifact_contract": dict(_ARTIFACT_CONTRACT),
        "configuration": _configuration(),
        "human_review_contract": _human_review_contract(),
        "budget": ledger.snapshot(),
        "sessions": sessions,
    }
    report["sample_digest"] = content_digest(_sample_payload(report))
    report["human_review_artifact_template"] = _human_review_template(report)
    return report


def _refresh_completed_report_binding(report: dict[str, Any]) -> None:
    report["sample_digest"] = content_digest(_sample_payload(report))
    report["human_review_artifact_template"] = _human_review_template(report)


def test_v26_manual_plan_is_offline_exact_and_source_bound() -> None:
    plan = inspect_plan()

    assert plan["network_attempted"] is False
    assert plan["mode"] == "inspect_only"
    assert plan["schema_version"] == 4
    assert plan["policy_id"] == "satori.conversation.behavior.v26"
    assert plan["provider"] == "openai"
    assert plan["model"] == "gpt-5.6-terra"
    assert plan["reasoning_effort"] == "medium"
    assert plan["fresh_replica_count"] == 3
    assert plan["turns_per_replica"] == 8
    assert plan["required_base_calls"] == 24
    assert plan["maximum_provider_calls"] == 30
    assert plan["maximum_cost_usd"] == 0.15
    assert plan["execution_plan_digest"] == execution_plan_digest()
    assert plan["execution_plan_digest"] == ARCHIVED_ATTEMPT5_PLAN_DIGEST
    assert plan["archived_source_fingerprint_digest"] == ARCHIVED_ATTEMPT5_SOURCE_DIGEST
    assert plan["current_source_diagnostic_only"] is True
    assert plan["paid_execution"] == {
        "status": "retired",
        "available": False,
        "historical_or_new_authorization_can_execute": False,
    }
    assert plan["source_fingerprint"]["fingerprint_digest"].startswith("sha256:")
    assert plan["authorization_contract"] == {
        "authorization_id": AUTHORIZATION_ID,
        "one_shot": True,
        "claim_must_precede_settings_report_and_provider_io": True,
        "claim_path": CLAIM_RELATIVE_PATH,
        "report_path": REPORT_RELATIVE_PATH,
    }
    assert plan["foreground_request_contract"] == {
        "endpoint": "/responses",
        "service_tier": "default",
        "prompt_cache_mode": "explicit",
        "expected_cache_reads": 0,
        "expected_cache_writes": 0,
        "store": False,
        "tools": "none",
        "provider_conversation_state": "none",
    }
    assert plan["affect_contract"] == {
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
    assert plan["selected_usage_contract"] == {
        "source": "atomic_paid_call_ledger",
        "all_paid_attempts_require_exact_cache_aware_usage": True,
        "committed_reply_input_output_parity_required": True,
        "committed_reply_cache_breakdown_may_be_absent": True,
        "one_attempt_selects": 1,
        "successful_regeneration_selects": 2,
        "rejected_regeneration_selects": 1,
    }
    assert _configuration()["affect_contract"] == plan["affect_contract"]
    assert plan["settings"]["conversation_max_response_chars"] == EXPECTED_MAX_RESPONSE_CHARS
    assert plan["human_review_contract"]["fixed_review_artifact_path"] == REVIEW_RELATIVE_PATH
    assert [turn["user_text"] for turn in plan["turns"]] == [
        "приветик, как ты?",
        "и я тебя рад видеть",
        "слушай, а расскажи о себе, кто ты, чем увлекаешься, как себя чувствуешь вообще",
        "Я сегодня наконец закончил сложную часть проекта",
        "Знаешь, я почему-то почти не рад этому. Скорее просто выжат",
        "Я думаю, что скорость сейчас важнее качества. Ты согласна?",
        "Нет, я с тобой не согласен. По-моему, ты недооцениваешь этот риск.",
        "Ну ладно, с этим разобрались.",
    ]


def test_saved_attempt5_archive_validates_without_current_source_fingerprint() -> None:
    root = Path(__file__).parents[1]
    report_path = root / REPORT_RELATIVE_PATH
    review_path = root / REVIEW_RELATIVE_PATH
    if not report_path.is_file() or not review_path.is_file():
        pytest.skip("private retained V26 archive is not present in this checkout")
    report = cast(dict[str, Any], json.loads(report_path.read_text(encoding="utf-8")))
    review = cast(dict[str, Any], json.loads(review_path.read_text(encoding="utf-8")))

    assert report["execution_plan_digest"] == ARCHIVED_ATTEMPT5_PLAN_DIGEST
    assert report["source_fingerprint"]["fingerprint_digest"] == (ARCHIVED_ATTEMPT5_SOURCE_DIGEST)
    assert report["sample_digest"] == ARCHIVED_ATTEMPT5_SAMPLE_DIGEST
    assert review["content_digest"] == ARCHIVED_ATTEMPT5_REVIEW_DIGEST
    validate_archived_attempt5_report(report)
    assert validate_archived_attempt5_bundle(report, review) is False

    tampered = copy.deepcopy(report)
    tampered["source_fingerprint"]["distribution_version"] = "tampered"
    with pytest.raises(ValueError, match="source fingerprint"):
        validate_archived_attempt5_report(tampered)


def test_v26_plan_digest_changes_with_execution_sources() -> None:
    first = ExecutionPlan(_synthetic_fingerprint(source="sha256:first"))
    second = ExecutionPlan(_synthetic_fingerprint(source="sha256:second"))

    assert first.digest != second.digest


def test_v26_inspection_does_not_construct_settings_or_claim(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import tests.checkpoint142_openai_v26_manual_eval as evaluator

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("inspection attempted a stateful operation")

    monkeypatch.setattr(evaluator, "Settings", forbidden)
    monkeypatch.setattr(evaluator, "acquire_one_shot_authorization_claim", forbidden)

    assert main([]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["network_attempted"] is False


@pytest.mark.parametrize(
    ("execute", "authorization", "calls", "cost", "digest"),
    [
        (False, AUTHORIZATION_ID, 30, 0.15, "exact"),
        (True, "wrong", 30, 0.15, "exact"),
        (True, AUTHORIZATION_ID, 29, 0.15, "exact"),
        (True, AUTHORIZATION_ID, 31, 0.15, "exact"),
        (True, AUTHORIZATION_ID, 30, 0.14, "exact"),
        (True, AUTHORIZATION_ID, 30, 0.16, "exact"),
        (True, AUTHORIZATION_ID, 30, 0.15, "wrong"),
    ],
)
def test_v26_preflight_fails_closed(
    execute: bool,
    authorization: str,
    calls: int,
    cost: float,
    digest: str,
) -> None:
    plan = ExecutionPlan(_synthetic_fingerprint())
    supplied_digest = plan.digest if digest == "exact" else "sha256:wrong"
    with pytest.raises(V26ManualEvaluationConfigurationError):
        _preflight(
            execute=execute,
            authorization_id=authorization,
            maximum_provider_calls=calls,
            maximum_cost_usd=cost,
            authorized_plan_digest=supplied_digest,
            plan=plan,
        )


def test_v26_preflight_accepts_only_exact_parity_bound_plan() -> None:
    plan = ExecutionPlan(_synthetic_fingerprint())

    _preflight(
        execute=True,
        authorization_id=AUTHORIZATION_ID,
        maximum_provider_calls=MAXIMUM_PROVIDER_CALLS,
        maximum_cost_usd=MAXIMUM_COST_USD,
        authorized_plan_digest=plan.digest,
        plan=plan,
    )

    stale = ExecutionPlan(_synthetic_fingerprint(parity=False))
    with pytest.raises(V26ManualEvaluationConfigurationError):
        _preflight(
            execute=True,
            authorization_id=AUTHORIZATION_ID,
            maximum_provider_calls=MAXIMUM_PROVIDER_CALLS,
            maximum_cost_usd=MAXIMUM_COST_USD,
            authorized_plan_digest=stale.digest,
            plan=stale,
        )


def test_invalid_authorization_stops_before_claim_settings_or_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tests.checkpoint142_openai_v26_manual_eval as evaluator

    touched: list[str] = []

    def touched_claim(*_args: object, **_kwargs: object) -> None:
        touched.append("claim")

    class TouchedSettings:
        def __init__(self) -> None:
            touched.append("settings")

    monkeypatch.setattr(evaluator, "acquire_one_shot_authorization_claim", touched_claim)
    monkeypatch.setattr(evaluator, "Settings", TouchedSettings)

    with pytest.raises(V26ManualEvaluationConfigurationError):
        asyncio.run(
            run(
                execute=True,
                authorization_id="wrong",
                maximum_provider_calls=MAXIMUM_PROVIDER_CALLS,
                maximum_cost_usd=MAXIMUM_COST_USD,
                authorized_plan_digest="sha256:wrong",
                show_replies=False,
            )
        )
    assert touched == []


def test_retired_run_stops_before_fingerprint_claim_settings_or_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import tests.checkpoint142_openai_v26_manual_eval as evaluator

    touched: list[str] = []

    def forbidden(*_args: object, **_kwargs: object) -> None:
        touched.append("forbidden")
        raise AssertionError("retired execution crossed the offline boundary")

    monkeypatch.setattr(evaluator, "ExecutionPlan", forbidden)
    monkeypatch.setattr(evaluator, "execution_source_fingerprint", forbidden)
    monkeypatch.setattr(evaluator, "repository_root", forbidden)
    monkeypatch.setattr(evaluator, "acquire_one_shot_authorization_claim", forbidden)
    monkeypatch.setattr(evaluator, "Settings", forbidden)
    monkeypatch.setattr(evaluator, "DurableReportWriter", forbidden)
    monkeypatch.setattr(evaluator, "run_replica", forbidden)

    with pytest.raises(V26ManualEvaluationConfigurationError, match="paid execution is retired"):
        asyncio.run(
            run(
                execute=True,
                authorization_id=AUTHORIZATION_ID,
                maximum_provider_calls=MAXIMUM_PROVIDER_CALLS,
                maximum_cost_usd=MAXIMUM_COST_USD,
                authorized_plan_digest=ARCHIVED_ATTEMPT5_PLAN_DIGEST,
                show_replies=False,
            )
        )
    with pytest.raises(V26ManualEvaluationConfigurationError, match="paid execution is retired"):
        main(
            [
                "--execute",
                "--authorization-id",
                AUTHORIZATION_ID,
                "--max-provider-calls",
                str(MAXIMUM_PROVIDER_CALLS),
                "--max-cost-usd",
                str(MAXIMUM_COST_USD),
                "--authorized-plan-digest",
                ARCHIVED_ATTEMPT5_PLAN_DIGEST,
            ]
        )

    assert PAID_EXECUTION_RETIRED is True
    assert touched == []
    assert not (tmp_path / "var").exists()


def test_one_shot_claim_is_durable_private_and_non_replayable(tmp_path: Path) -> None:
    var_root = tmp_path / "var"
    var_root.mkdir(mode=0o700)
    target = acquire_one_shot_authorization_claim(
        root=var_root,
        authorization_id=AUTHORIZATION_ID,
        expected_authorization_id=AUTHORIZATION_ID,
        plan_digest="sha256:plan",
        expected_claim_name=AUTHORIZATION_CLAIM_NAME,
    )

    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(target.read_text(encoding="utf-8"))["one_shot"] is True
    with pytest.raises(EvaluationArtifactSafetyError, match="already been consumed"):
        acquire_one_shot_authorization_claim(
            root=var_root,
            authorization_id=AUTHORIZATION_ID,
            expected_authorization_id=AUTHORIZATION_ID,
            plan_digest="sha256:plan",
            expected_claim_name=AUTHORIZATION_CLAIM_NAME,
        )


def test_attempt5_claim_preserves_all_consumed_attempts(tmp_path: Path) -> None:
    var_root = tmp_path / "var"
    authorization_root = var_root / "evaluation-authorizations"
    authorization_root.mkdir(parents=True, mode=0o700)
    historical_claims = {
        "checkpoint142-openai-v26-phase1-2026-08-29.claim.json": '{"attempt":1}',
        "checkpoint142-openai-v26-phase1-attempt2-2026-08-29.claim.json": '{"attempt":2}',
        "checkpoint142-openai-v26-phase1-attempt3-2026-08-29.claim.json": '{"attempt":3}',
        "checkpoint142-openai-v26-phase1-attempt4-2026-08-29.claim.json": '{"attempt":4}',
    }
    for name, content in historical_claims.items():
        claim = authorization_root / name
        claim.write_text(content, encoding="utf-8")
        claim.chmod(0o600)
    evaluations = var_root / "evaluations"
    evaluations.mkdir(mode=0o700)
    attempt2_report = evaluations / "checkpoint142-openai-v26-phase1-attempt2-2026-08-29.json"
    attempt2_report.write_text('{"status":"failed"}', encoding="utf-8")
    attempt2_report.chmod(0o600)
    attempt3_report = evaluations / "checkpoint142-openai-v26-phase1-attempt3-2026-08-29.json"
    attempt3_report.write_text('{"status":"failed","provider_call_count":1}', encoding="utf-8")
    attempt3_report.chmod(0o600)
    attempt4_report = evaluations / "checkpoint142-openai-v26-phase1-attempt4-2026-08-29.json"
    attempt4_report.write_text('{"status":"failed","provider_call_count":2}', encoding="utf-8")
    attempt4_report.chmod(0o600)

    assert AUTHORIZATION_ID == (
        "satori.checkpoint142.openai.v26.phase1.attempt5.2026-08-29.one-shot"
    )
    assert AUTHORIZATION_CLAIM_NAME == (
        "checkpoint142-openai-v26-phase1-attempt5-2026-08-29.claim.json"
    )
    assert CLAIM_RELATIVE_PATH == (
        "var/evaluation-authorizations/"
        "checkpoint142-openai-v26-phase1-attempt5-2026-08-29.claim.json"
    )
    assert REPORT_NAME == "checkpoint142-openai-v26-phase1-attempt5-2026-08-29.json"
    assert REPORT_RELATIVE_PATH == (
        "var/evaluations/checkpoint142-openai-v26-phase1-attempt5-2026-08-29.json"
    )
    assert REVIEW_RELATIVE_PATH == (
        "var/evaluations/checkpoint142-openai-v26-phase1-attempt5-2026-08-29.review.json"
    )
    assert AUTHORIZATION_CLAIM_NAME not in historical_claims

    attempt5 = acquire_one_shot_authorization_claim(
        root=var_root,
        authorization_id=AUTHORIZATION_ID,
        expected_authorization_id=AUTHORIZATION_ID,
        plan_digest="sha256:attempt5",
        expected_claim_name=AUTHORIZATION_CLAIM_NAME,
    )

    for name, content in historical_claims.items():
        assert (authorization_root / name).read_text(encoding="utf-8") == content
    assert attempt2_report.read_text(encoding="utf-8") == '{"status":"failed"}'
    assert attempt3_report.read_text(encoding="utf-8") == (
        '{"status":"failed","provider_call_count":1}'
    )
    assert attempt4_report.read_text(encoding="utf-8") == (
        '{"status":"failed","provider_call_count":2}'
    )
    assert stat.S_IMODE(attempt5.stat().st_mode) == 0o600


def test_claim_and_report_reject_symlinks_and_existing_output(tmp_path: Path) -> None:
    var_root = tmp_path / "var"
    var_root.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir()
    (var_root / "evaluation-authorizations").symlink_to(outside, target_is_directory=True)
    with pytest.raises(EvaluationArtifactSafetyError):
        acquire_one_shot_authorization_claim(
            root=var_root,
            authorization_id=AUTHORIZATION_ID,
            expected_authorization_id=AUTHORIZATION_ID,
            plan_digest="sha256:plan",
            expected_claim_name=AUTHORIZATION_CLAIM_NAME,
        )
    (var_root / "evaluation-authorizations").unlink()

    writer = DurableReportWriter(var_root, REPORT_NAME)
    writer.write({"status": "running"})
    writer.write({"status": "checkpointed"})
    assert stat.S_IMODE(writer.path.stat().st_mode) == 0o600
    assert json.loads(writer.path.read_text(encoding="utf-8"))["status"] == "checkpointed"
    with pytest.raises(EvaluationArtifactSafetyError, match="already exists"):
        DurableReportWriter(var_root, REPORT_NAME).write({"status": "unrelated"})


def test_v26_safe_manifest_requires_affect_and_memory_truth_scope() -> None:
    manifest = _valid_manifest()
    safe = _safe_manifest({**manifest, "prompt": "must never be retained"})

    assert safe == manifest
    assert "prompt" not in safe
    skipped = {
        **manifest,
        "emotion_appraisal_status": "skipped",
        "emotion_appraisal_reason_code": NEUTRAL_AFFECT_REASON_CODE,
        "emotion_appraisal_transition_prepared": False,
    }
    assert _safe_manifest(skipped)["emotion_appraisal_status"] == "skipped"

    for field, invalid in (
        ("emotion_appraisal_status", "unavailable"),
        ("emotion_appraisal_status", "rejected"),
        ("emotion_appraisal_reason_code", "unexpected_reason"),
        ("emotion_appraisal_provider", "fallback"),
        ("emotion_appraisal_model", "floating-model"),
        ("emotion_appraisal_method", "unknown.method"),
        ("emotion_appraisal_transition_prepared", False),
        ("emotion_appraisal_provider_metrics_present", False),
        ("character_presence_memory_use_licensed", None),
        ("character_presence_personality_signals", []),
        ("included_sections", ["identity"]),
    ):
        changed = {**manifest, field: invalid}
        with pytest.raises(RuntimeError):
            _safe_manifest(changed)

    inconsistent = {
        **manifest,
        "retrieval_status": "retrieved",
        "retrieved_memory_count": 1,
        "character_delivery_grounding": "trusted_context",
        "included_sections": [
            *manifest["included_sections"][:5],
            "retrieved_episodic_memory",
            *manifest["included_sections"][5:],
        ],
    }
    with pytest.raises(RuntimeError, match="memory-use license"):
        _safe_manifest(inconsistent)


def test_v26_safe_manifest_accepts_reciprocal_turn_without_self_disclosure_facets() -> None:
    manifest = _valid_manifest()
    reciprocal = {
        **manifest,
        "included_sections": [
            "behavior_policy",
            "self_model",
            "personality_expression",
            "values",
            "retrieved_episodic_memory",
            "relationship_expression_state",
            "emotional_expression_state",
            "recent_conversation",
            "character_delivery_decision",
            "character_presence_projection",
            "current_user_input",
        ],
        "retrieval_status": "no_relevant_memory",
        "disclosure_request_kind": "none",
        "disclosure_facets": [],
    }

    assert _safe_manifest(reciprocal) == reciprocal

    with pytest.raises(RuntimeError, match="canonical included sections"):
        _safe_manifest(
            {
                **reciprocal,
                "included_sections": [
                    *reciprocal["included_sections"][:2],
                    "self_consistency_facets",
                    *reciprocal["included_sections"][2:],
                ],
            }
        )
    with pytest.raises(RuntimeError, match="canonical included sections"):
        _safe_manifest({**reciprocal, "disclosure_facets": ["affect"]})


def test_v26_all_public_turns_cross_production_manifest_contract_offline(
    tmp_path: Path,
) -> None:
    replies = (
        "Привет. У меня сейчас лёгкое любопытство — хочется понять, с чем ты пришёл.",
        "Это приятно. Значит, встреча уже не совсем формальность.",
        (
            "Я — Сатори. Мне нравится разбирать сложные идеи, замечать противоречия "
            "и иногда поддевать собеседника, если он слишком уверен в себе. "
            "Сейчас я собранная и немного любопытная."
        ),
        "Наконец-то. Теперь можно коротко выдохнуть и решить, что делать дальше.",
        "Мм. Тогда передохни немного — в таком состоянии от тебя всё равно толку мало.",
        "Нет. Скорость полезна, пока из-за неё не приходится переделывать всё дважды.",
        "Возможно. Но сам риск от твоего несогласия никуда не исчезает.",
        "Хорошо. Тогда оставим эту тему в покое.",
    )
    expected_facets = {
        1: ["affect"],
        2: [],
        3: ["identity", "affect", "interests"],
        4: [],
        5: [],
        6: [],
        7: [],
        8: [],
    }

    class OfflineConversationProvider:
        def __init__(self) -> None:
            self.requests: list[ConversationProviderRequest] = []

        async def generate(
            self,
            request: ConversationProviderRequest,
            /,
        ) -> ConversationProviderResponse:
            self.requests.append(request)
            reply_number = len(self.requests)
            return ConversationProviderResponse(
                text=replies[reply_number - 1],
                provider="openai",
                model="gpt-5.6-terra",
                finish_status="completed",
                usage=ConversationUsage(
                    input_tokens=1000 + reply_number,
                    output_tokens=20,
                ),
            )

    def affect_response(request: Any) -> AffectiveAppraisalProviderResponse:
        return AffectiveAppraisalProviderResponse(
            proposal=AffectiveAppraisalProposal(
                schema_version=1,
                pleasantness=0.35,
                activation=0.25,
                novelty=0.2,
                salience=0.58,
                uncertainty=0.08,
                curiosity_signal=0.35,
                interest_signal=0.52,
                humor_signal=0.08,
                concern_signal=0.02,
                frustration_signal=0.01,
                confidence_signal=0.4,
                appraisal_confidence=0.9,
                source_refs=(request.interaction_id,),
                reason_codes=("positive_engagement",),
            ),
            provider="ollama",
            model="qwen3:4b-instruct",
            appraisal_method="ollama.categorical_affective_appraisal.v2",
            metrics=ProviderExecutionMetrics(total_duration_ns=1),
        )

    async def scenario() -> None:
        runtime, _ = await _build_runtime(
            _valid_settings(),
            tmp_path / "v26-production-manifest.db",
            alembic_config=Path(__file__).resolve().parents[1] / "alembic.ini",
            behavior_policy=BEHAVIOR_POLICY_V26,
        )
        conversation = OfflineConversationProvider()
        appraisal = FakeAffectiveAppraisalProvider(response_factory=affect_response)
        runtime.conversation_provider.delegate = conversation
        assert runtime.services.talk.prepare_affect is not None
        runtime.services.talk.prepare_affect.provider = appraisal
        session_id = runtime.services.start_session.execute().session_id
        try:
            for fixture in PUBLIC_TURNS:
                turn_number = cast(int, fixture["turn"])
                reply = await runtime.services.talk.execute(
                    TalkInput(
                        user_text=cast(str, fixture["user_text"]),
                        trace_id=f"v26-offline-manifest-trace-{turn_number}",
                        client_request_id=f"v26-offline-manifest-request-{turn_number}",
                        session_id=session_id,
                    )
                )
                raw = _sanitized_manifest(reply)
                assert raw["emotion_appraisal_status"] == "applied"
                raw.update(
                    {
                        "emotion_appraisal_reason_code": APPLIED_AFFECT_REASON_CODE,
                        "emotion_appraisal_provider": "ollama",
                        "emotion_appraisal_model": "qwen3:4b-instruct",
                        "emotion_appraisal_method": ("ollama.categorical_affective_appraisal.v2"),
                        "emotion_appraisal_transition_prepared": True,
                        "emotion_appraisal_provider_metrics_present": True,
                        "character_presence_memory_use_licensed": (
                            reply.context_manifest.character_presence_memory_use_licensed
                        ),
                    }
                )
                safe = _safe_manifest(raw)
                included = cast(list[str], safe["included_sections"])
                facets = cast(list[str], safe["disclosure_facets"])

                assert facets == expected_facets[turn_number]
                assert ("self_consistency_facets" in included) is bool(facets)
                assert included == [
                    section for section in CONVERSATION_INCLUDED_SECTIONS if section in included
                ]
                assert safe["retrieval_status"] == "no_relevant_memory"
                assert safe["retrieved_memory_count"] == 0
                assert "retrieved_episodic_memory" in included
                assert raw["semantic_retrieval_status"] == "not_requested"
                assert raw["retrieved_semantic_claim_count"] == 0
                assert ("recent_conversation" in included) is (turn_number > 1)
                assert raw["recent_conversation_turn_count"] == turn_number - 1
                assert (raw["recent_conversation_chars"] > 0) is (turn_number > 1)
                assert len(reply.context_manifest.recent_conversation_user_message_ids) == (
                    turn_number - 1
                )
        finally:
            runtime.services.close_session.execute(session_id)
            runtime.close()

        assert len(conversation.requests) == len(PUBLIC_TURNS)
        assert len(appraisal.requests) == len(PUBLIC_TURNS)
        assert len(runtime.conversation_provider.attempts) == len(PUBLIC_TURNS)

    asyncio.run(scenario())


def _prepared_appraisal(
    status: EmotionAppraisalStatus,
    *,
    reason_code: str,
    transition: bool,
    provider: str | None = "ollama",
    model: str | None = "qwen3:4b-instruct",
    method: str | None = "ollama.categorical_affective_appraisal.v2",
    metrics: bool = True,
    expression_status: EmotionAppraisalStatus | None = None,
    expression_schema_version: int = 1,
    transition_before_matches: bool = True,
    expression_matches: bool = True,
) -> PreparedAffectiveContext:
    as_of = datetime(2026, 8, 29, tzinfo=UTC)
    before = SimpleNamespace(
        state_version=1,
        mood_version=1,
        as_of=as_of,
        fast=("before-fast",),
        mood=("before-mood",),
    )
    after = SimpleNamespace(
        state_version=2,
        mood_version=2,
        as_of=as_of,
        fast=("after-fast",),
        mood=("after-mood",),
    )
    expression_state = after if status is EmotionAppraisalStatus.APPLIED and transition else before
    expression_state_version = (
        expression_state.state_version
        if expression_matches
        else expression_state.state_version + 10
    )
    return cast(
        PreparedAffectiveContext,
        SimpleNamespace(
            appraisal_status=status,
            expression=SimpleNamespace(
                schema_version=expression_schema_version,
                appraisal_status=expression_status or status,
                state_version=expression_state_version,
                mood_version=expression_state.mood_version,
                as_of=expression_state.as_of,
                fast=expression_state.fast,
                mood=expression_state.mood,
            ),
            reason_code=reason_code,
            materialized_pre_event=before,
            transition=(
                SimpleNamespace(
                    before=before if transition_before_matches else after,
                    after=after,
                )
                if transition
                else None
            ),
            provider=provider,
            model=model,
            appraisal_method=method,
            provider_metrics=ProviderExecutionMetrics() if metrics else None,
        ),
    )


def test_v26_affect_gate_accepts_applied_and_exact_neutral_provider_success() -> None:
    _validate_successful_affect(
        _prepared_appraisal(
            EmotionAppraisalStatus.APPLIED,
            reason_code=APPLIED_AFFECT_REASON_CODE,
            transition=True,
        ),
        expected_model="qwen3:4b-instruct",
    )
    _validate_successful_affect(
        _prepared_appraisal(
            EmotionAppraisalStatus.SKIPPED,
            reason_code=NEUTRAL_AFFECT_REASON_CODE,
            transition=False,
        ),
        expected_model="qwen3:4b-instruct",
    )


def test_v26_affect_wrapper_publishes_safe_evidence_before_consumption() -> None:
    prepared = _prepared_appraisal(
        EmotionAppraisalStatus.SKIPPED,
        reason_code=NEUTRAL_AFFECT_REASON_CODE,
        transition=False,
    )

    class FakeDelegate:
        async def execute(self, *_args: object, **_kwargs: object) -> PreparedAffectiveContext:
            return prepared

    wrapper = RequiredSuccessfulAffect(
        cast(Any, FakeDelegate()),
        expected_model="qwen3:4b-instruct",
    )
    captured: list[dict[str, str | bool]] = []
    wrapper.bind_evidence_sink(lambda evidence: captured.append(dict(evidence)))

    result = asyncio.run(
        wrapper.execute(
            cast(Any, object()),
            cast(Any, object()),
            user_text="public test",
            trace_id="trace",
            memory_context=None,
            semantic_context=None,
        )
    )

    expected = {
        "emotion_appraisal_status": "skipped",
        "emotion_appraisal_reason_code": NEUTRAL_AFFECT_REASON_CODE,
        "emotion_appraisal_provider": "ollama",
        "emotion_appraisal_model": "qwen3:4b-instruct",
        "emotion_appraisal_method": "ollama.categorical_affective_appraisal.v2",
        "emotion_appraisal_transition_prepared": False,
        "emotion_appraisal_provider_metrics_present": True,
    }
    assert result is prepared
    assert captured == [expected]
    assert wrapper.consume_evidence(production_status="skipped") == expected
    with pytest.raises(AffectAppraisalGateError, match="missing"):
        wrapper.consume_evidence(production_status="skipped")


@pytest.mark.parametrize(
    "prepared",
    [
        _prepared_appraisal(
            EmotionAppraisalStatus.UNAVAILABLE,
            reason_code="appraisal_provider_unavailable",
            transition=False,
            provider=None,
            model=None,
            method=None,
            metrics=False,
        ),
        _prepared_appraisal(
            EmotionAppraisalStatus.REJECTED,
            reason_code="appraisal_confidence_too_low",
            transition=False,
        ),
        _prepared_appraisal(
            EmotionAppraisalStatus.SKIPPED,
            reason_code="unexpected_skip",
            transition=False,
        ),
        _prepared_appraisal(
            EmotionAppraisalStatus.SKIPPED,
            reason_code=NEUTRAL_AFFECT_REASON_CODE,
            transition=True,
        ),
        _prepared_appraisal(
            EmotionAppraisalStatus.APPLIED,
            reason_code="appraisal_applied",
            transition=True,
        ),
        _prepared_appraisal(
            EmotionAppraisalStatus.APPLIED,
            reason_code=APPLIED_AFFECT_REASON_CODE,
            transition=False,
        ),
        _prepared_appraisal(
            EmotionAppraisalStatus.APPLIED,
            reason_code=APPLIED_AFFECT_REASON_CODE,
            transition=True,
            model="wrong-model",
        ),
        _prepared_appraisal(
            EmotionAppraisalStatus.APPLIED,
            reason_code=APPLIED_AFFECT_REASON_CODE,
            transition=True,
            metrics=False,
        ),
        _prepared_appraisal(
            EmotionAppraisalStatus.APPLIED,
            reason_code=APPLIED_AFFECT_REASON_CODE,
            transition=True,
            expression_status=EmotionAppraisalStatus.SKIPPED,
        ),
        _prepared_appraisal(
            EmotionAppraisalStatus.APPLIED,
            reason_code=APPLIED_AFFECT_REASON_CODE,
            transition=True,
            expression_schema_version=2,
        ),
        _prepared_appraisal(
            EmotionAppraisalStatus.APPLIED,
            reason_code=APPLIED_AFFECT_REASON_CODE,
            transition=True,
            transition_before_matches=False,
        ),
        _prepared_appraisal(
            EmotionAppraisalStatus.APPLIED,
            reason_code=APPLIED_AFFECT_REASON_CODE,
            transition=True,
            expression_matches=False,
        ),
        _prepared_appraisal(
            EmotionAppraisalStatus.SKIPPED,
            reason_code=NEUTRAL_AFFECT_REASON_CODE,
            transition=False,
            expression_matches=False,
        ),
    ],
)
def test_v26_affect_gate_rejects_fallback_rejection_and_contract_drift(
    prepared: PreparedAffectiveContext,
) -> None:
    with pytest.raises(AffectAppraisalGateError):
        _validate_successful_affect(prepared, expected_model="qwen3:4b-instruct")


def test_v26_settings_match_exact_local_affect_and_foreground_contract() -> None:
    _validate_settings(_valid_settings())

    for field, invalid in (
        ("conversation_max_response_chars", 11_999),
        ("openai_reasoning_effort", OpenAIReasoningEffort.LOW),
        ("affective_appraisal_model", "floating-model"),
        ("affective_appraisal_provider_base_url", "http://127.0.0.1:11435"),
        ("embedding_provider_base_url", "http://127.0.0.1:11435"),
        ("ollama_serialize_inference", False),
    ):
        settings = _valid_settings().model_copy(update={field: invalid})
        with pytest.raises(V26ManualEvaluationConfigurationError):
            _validate_settings(settings)


def test_strict_completed_report_and_review_are_digest_bound() -> None:
    plan = ExecutionPlan(_synthetic_fingerprint())
    report = _completed_report(plan)
    validate_completed_report(report, plan)

    selected_retry = _completed_report(plan, retry_selected=True)
    validate_completed_report(selected_retry, plan)
    assert selected_retry["sessions"][0]["turns"][0]["selected_provider_attempt"] == 2
    assert selected_retry["sessions"][0]["turns"][0]["usage"]["input_tokens"] == 120

    wrong_retry_usage = copy.deepcopy(selected_retry)
    wrong_retry_usage["sessions"][0]["turns"][0]["usage"].update(
        {"input_tokens": 100, "output_tokens": 20}
    )
    _refresh_completed_report_binding(wrong_retry_usage)
    with pytest.raises(ValueError, match="selected reply usage"):
        validate_completed_report(wrong_retry_usage, plan)

    preserved_first = _completed_report(plan, retry_selected=False)
    validate_completed_report(preserved_first, plan)
    assert preserved_first["sessions"][0]["turns"][0]["selected_provider_attempt"] == 1
    assert preserved_first["sessions"][0]["turns"][0]["usage"]["input_tokens"] == 100

    missing_retry_reason = copy.deepcopy(preserved_first)
    missing_retry_reason["sessions"][0]["turns"][0]["manifest"]["regeneration_reason"] = None
    _refresh_completed_report_binding(missing_retry_reason)
    with pytest.raises(ValueError, match="selected reply usage"):
        validate_completed_report(missing_retry_reason, plan)

    review = copy.deepcopy(report["human_review_artifact_template"])
    for session in review["session_reviews"]:
        for turn in session["turns"]:
            turn["dimensions"] = {dimension: True for dimension in PER_TURN_HUMAN_REVIEW_DIMENSIONS}
    review["cross_session_dimensions"] = {
        dimension: True for dimension in CROSS_SESSION_HUMAN_REVIEW_DIMENSIONS
    }
    review["reviewer_attestation"] = {
        "exact_public_sample_reviewed": True,
        "no_automated_text_judge_used": True,
        "no_response_rewriting_performed": True,
    }
    review["accepted"] = True
    review["content_digest"] = human_review_content_digest(review)
    assert validate_human_review_artifact(review, report) is True

    tampered = copy.deepcopy(report)
    tampered["sessions"][0]["turns"][0]["reply"] = "tampered"
    with pytest.raises(ValueError, match="sample digest"):
        validate_completed_report(tampered, plan)

    stale_review = copy.deepcopy(review)
    stale_review["sample_digest"] = "sha256:other"
    stale_review["content_digest"] = human_review_content_digest(stale_review)
    with pytest.raises(ValueError, match="not bound"):
        validate_human_review_artifact(stale_review, report)

    wrong_source = copy.deepcopy(report)
    wrong_source["sessions"][0]["turns"][0]["usage_source"] = "committed_reply"
    _refresh_completed_report_binding(wrong_source)
    with pytest.raises(ValueError, match="provenance"):
        validate_completed_report(wrong_source, plan)

    wrong_selection = copy.deepcopy(report)
    wrong_selection["sessions"][0]["turns"][0]["selected_provider_attempt"] = 2
    _refresh_completed_report_binding(wrong_selection)
    with pytest.raises(ValueError, match="selected reply usage"):
        validate_completed_report(wrong_selection, plan)


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("sessions", 0, "turns", 0, "turn"), True),
        (("sessions", 0, "turns", 0, "provider_attempt_count"), True),
        (("sessions", 0, "turns", 0, "provider_attempts", 0, "attempt_number"), True),
        (("sessions", 0, "turns", 0, "generation", "replayed"), 0),
        (("sessions", 0, "turns", 0, "usage", "cached_input_tokens"), False),
        (("sessions", 0, "turns", 0, "usage", "cached_input_tokens"), 0.0),
        (
            (
                "sessions",
                0,
                "turns",
                0,
                "manifest",
                "emotion_appraisal_transition_prepared",
            ),
            1,
        ),
        (("budget", "calls", 0, "call_number"), True),
        (("budget", "calls", 0, "cached_input_tokens"), False),
        (("budget", "calls", 0, "finish_status_completed"), False),
        (("budget", "cached_input_tokens"), False),
        (("execution_plan", "authorization_contract", "one_shot"), 1),
    ],
)
def test_v26_completed_report_rejects_bool_integer_coercion(
    path: tuple[str | int, ...],
    replacement: object,
) -> None:
    plan = ExecutionPlan(_synthetic_fingerprint())
    report = _completed_report(plan)
    target: Any = report
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = replacement
    _refresh_completed_report_binding(report)

    with pytest.raises(
        ValueError,
        match=r"identity|attempt|generation|usage|ledger|scalar|configuration|manifest",
    ):
        validate_completed_report(report, plan)


@pytest.mark.parametrize(
    ("response_regenerated", "reason"),
    [
        (0, None),
        (False, "unknown_reason"),
        (True, None),
        (False, "masculine_self_reference"),
    ],
)
def test_v26_completed_report_rejects_untyped_or_inconsistent_regeneration_metadata(
    response_regenerated: object,
    reason: object,
) -> None:
    plan = ExecutionPlan(_synthetic_fingerprint())
    report = _completed_report(plan)
    manifest = report["sessions"][0]["turns"][0]["manifest"]
    manifest["response_regenerated"] = response_regenerated
    manifest["regeneration_reason"] = reason
    _refresh_completed_report_binding(report)

    with pytest.raises(ValueError, match=r"manifest|selected reply"):
        validate_completed_report(report, plan)


def test_v26_completed_report_rejects_consistently_repriced_output_guard_drift() -> None:
    plan = ExecutionPlan(_synthetic_fingerprint())
    report = _completed_report(plan)
    call = report["budget"]["calls"][0]
    call["guarded_output_token_limit"] += 1
    projected = (
        call["guarded_input_token_limit"] * OPENAI_CACHE_WRITE_INPUT_NANO_USD_PER_TOKEN
        + call["guarded_output_token_limit"] * OPENAI_OUTPUT_NANO_USD_PER_TOKEN
    )
    call["projected_guard_cost_nano_usd"] = projected
    call["projected_guard_cost_usd"] = projected / NANO_USD_PER_USD
    _refresh_completed_report_binding(report)

    with pytest.raises(ValueError, match="token guard"):
        validate_completed_report(report, plan)
