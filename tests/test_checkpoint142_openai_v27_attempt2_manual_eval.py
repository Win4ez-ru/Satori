"""Offline authority, cap-vector and lifecycle tests for V27 attempt 2."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any, cast

import pytest

import tests.checkpoint142_openai_v27_attempt2_manual_eval as attempt2
import tests.checkpoint142_openai_v27_manual_eval as attempt1
from satori.core.conversation import (
    ConversationGenerationParameters,
    ConversationMessage,
    ConversationMessageRole,
    ConversationProviderRequest,
    ConversationProviderResponse,
    ConversationUsage,
)
from tests.checkpoint142_openai_manual_support import (
    _TIMING_KEYS,
    EvaluationArtifactSafetyError,
    acquire_one_shot_authorization_claim,
    content_digest,
    human_review_content_digest,
)
from tests.checkpoint142_openai_v26_ledger import ProviderCallBudgetExhausted, PublicTurnScope
from tests.test_checkpoint142_character_movement_v27 import _observe_v27
from tests.test_checkpoint142_openai_v26_manual_eval import _valid_manifest


def _synthetic_fingerprint(
    *, parity: bool = True, source: str = "sha256:source"
) -> dict[str, object]:
    fingerprint: dict[str, object] = {
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


def _request(*, trace_id: str, visible_cap: int) -> ConversationProviderRequest:
    return ConversationProviderRequest(
        schema_version=1,
        trace_id=trace_id,
        context_schema_version=16,
        messages=(ConversationMessage(ConversationMessageRole.USER, "public attempt-2 fixture"),),
        parameters=ConversationGenerationParameters(
            schema_version=1,
            temperature=0.3,
            max_output_tokens=visible_cap,
        ),
    )


def _response() -> ConversationProviderResponse:
    return ConversationProviderResponse(
        text="public reply",
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


def _ledger() -> attempt2.Attempt2AtomicOpenAICallLedger:
    return attempt2.Attempt2AtomicOpenAICallLedger(
        maximum_calls=attempt2.MAXIMUM_PROVIDER_CALLS,
        maximum_cost_usd=attempt2.MAXIMUM_COST_USD,
        required_base_calls=attempt2.REQUIRED_BASE_CALLS,
        reasoning_token_allowance=attempt2.EXPECTED_REASONING_ALLOWANCE,
    )


def _completed_report(*, retry_selected: bool | None = None) -> dict[str, Any]:
    plan = attempt2.ExecutionPlan(_synthetic_fingerprint())
    ledger = _ledger()
    sessions: list[dict[str, Any]] = []
    for replica in range(1, attempt2.EXPECTED_REPLICA_COUNT + 1):
        session_id = f"v27-character-attempt2-replica-{replica}"
        turns: list[dict[str, Any]] = []
        for fixture, visible_cap in zip(
            attempt2.PUBLIC_TURNS,
            attempt2.EXPECTED_TURN_VISIBLE_OUTPUT_TOKEN_LIMITS,
            strict=True,
        ):
            request = _request(
                trace_id=f"attempt2-{replica}-{fixture['turn']}",
                visible_cap=visible_cap,
            )
            scope = PublicTurnScope(session_id, fixture["turn"], fixture["id"])
            call = ledger.reserve(request, scope)
            ledger.settle_success(call, _response())
            attempt = {
                "attempt_number": 1,
                "wall_ms": 10.0,
                "request_schema_version": 1,
                "context_schema_version": 16,
                "message_count": 1,
                "message_role_counts": {"user": 1},
                "request_content_chars": len("public attempt-2 fixture"),
                "temperature": 0.3,
                "max_output_tokens": visible_cap,
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
            manifest.update(
                {
                    "policy_id": attempt2.EXPECTED_POLICY_ID,
                    "policy_schema_version": 27,
                    "character_delivery_decision_schema_version": 4,
                    "character_presence_projection_schema_version": 2,
                    "character_presence_value_signals": ["connection:defining"],
                }
            )
            if retry_selected is not None and replica == 1 and fixture["turn"] == 1:
                retry_call = ledger.reserve(request, scope)
                retry_response = ConversationProviderResponse(
                    text="public retry reply",
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
        "schema_version": attempt2.REPORT_SCHEMA_VERSION,
        "recorded_at": "2026-08-30T10:00:00+00:00",
        "completed_at": "2026-08-30T10:10:00+00:00",
        "checkpoint": "14.2",
        "purpose": "v27_live_state_selected_character_movement_production_gate_attempt2",
        "status": "completed_awaiting_human_review",
        "artifact_id": f"satori-checkpoint142-openai-v27-attempt2:{plan.digest}",
        "authorization_id": attempt2.AUTHORIZATION_ID,
        "execution_plan_digest": plan.digest,
        "execution_plan": plan.public_mapping(),
        "source_fingerprint": copy.deepcopy(dict(plan.source_fingerprint)),
        "artifact_contract": dict(attempt2._ARTIFACT_CONTRACT),
        "configuration": attempt2._configuration(),
        "human_review_contract": attempt2._human_review_contract(),
        "budget": ledger.snapshot(),
        "sessions": sessions,
    }
    report["sample_digest"] = content_digest(attempt2._sample_payload(report))
    report["human_review_artifact_template"] = attempt2._human_review_template(report)
    return report


def test_attempt2_plan_is_distinct_exact_and_inspect_only(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fingerprint = _synthetic_fingerprint()
    plan = attempt2.ExecutionPlan(fingerprint)
    mapping = plan.public_mapping()

    assert mapping["policy_id"] == "satori.conversation.behavior.v27"
    assert mapping["provider"] == "openai"
    assert mapping["model"] == "gpt-5.6-terra"
    assert mapping["reasoning_effort"] == "medium"
    assert mapping["reasoning_token_allowance"] == 1024
    assert mapping["fresh_replica_count"] == 3
    assert mapping["turns_per_replica"] == 8
    assert mapping["required_base_calls"] == 24
    assert mapping["maximum_provider_calls"] == 30
    assert mapping["maximum_cost_usd"] == 0.15
    assert mapping["application_limits"]["expected_turn_visible_output_token_limits"] == [
        48,
        48,
        200,
        96,
        96,
        384,
        112,
        96,
    ]
    assert mapping["authorization_contract"]["authorization_id"] == attempt2.AUTHORIZATION_ID
    assert plan.digest != attempt1.ARCHIVED_EXECUTION_PLAN_DIGEST

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("inspect crossed a stateful or provider boundary")

    monkeypatch.setattr(attempt2, "ExecutionPlan", lambda: plan)
    monkeypatch.setattr(attempt2, "Settings", forbidden)
    monkeypatch.setattr(attempt2, "repository_root", forbidden)
    monkeypatch.setattr(attempt2, "acquire_one_shot_authorization_claim", forbidden)
    monkeypatch.setattr(attempt2, "run_replica", forbidden)

    assert attempt2.main([]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["mode"] == "inspect_only"
    assert inspected["network_attempted"] is False
    assert inspected["execution_plan_digest"] == plan.digest


def test_attempt2_authority_and_paths_cannot_reuse_attempt1() -> None:
    assert attempt2.AUTHORIZATION_ID != attempt1.AUTHORIZATION_ID
    assert attempt2.AUTHORIZATION_CLAIM_NAME != attempt1.AUTHORIZATION_CLAIM_NAME
    assert attempt2.CLAIM_RELATIVE_PATH != attempt1.CLAIM_RELATIVE_PATH
    assert attempt2.REPORT_RELATIVE_PATH != attempt1.REPORT_RELATIVE_PATH
    assert attempt2.REVIEW_RELATIVE_PATH != attempt1.REVIEW_RELATIVE_PATH
    assert "attempt2" in attempt2.AUTHORIZATION_ID
    assert "attempt2" in attempt2.CLAIM_RELATIVE_PATH
    assert "attempt2" in attempt2.REPORT_RELATIVE_PATH
    assert "attempt2" in attempt2.REVIEW_RELATIVE_PATH


def test_attempt1_authorization_fails_before_attempt2_fingerprint_or_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("old authority crossed attempt-2 offline preflight")

    monkeypatch.setattr(attempt2, "ExecutionPlan", forbidden)
    monkeypatch.setattr(attempt2, "_execution_source_fingerprint", forbidden)
    monkeypatch.setattr(attempt2, "repository_root", forbidden)
    monkeypatch.setattr(attempt2, "Settings", forbidden)
    monkeypatch.setattr(attempt2, "acquire_one_shot_authorization_claim", forbidden)
    monkeypatch.setattr(attempt2, "DurableReportWriter", forbidden)
    monkeypatch.setattr(attempt2, "run_replica", forbidden)

    with pytest.raises(
        attempt2.V27Attempt2ManualEvaluationConfigurationError,
        match="attempt-2 grant",
    ):
        asyncio.run(
            attempt2.run(
                execute=True,
                authorization_id=attempt1.AUTHORIZATION_ID,
                maximum_provider_calls=30,
                maximum_cost_usd=0.15,
                authorized_plan_digest=attempt1.ARCHIVED_EXECUTION_PLAN_DIGEST,
                show_replies=False,
            )
        )


def test_attempt1_digest_with_attempt2_id_fails_before_fingerprint_or_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("retired digest crossed attempt-2 shape preflight")

    monkeypatch.setattr(attempt2, "ExecutionPlan", forbidden)
    monkeypatch.setattr(attempt2, "repository_root", forbidden)
    monkeypatch.setattr(attempt2, "Settings", forbidden)
    monkeypatch.setattr(attempt2, "acquire_one_shot_authorization_claim", forbidden)

    with pytest.raises(
        attempt2.V27Attempt2ManualEvaluationConfigurationError,
        match="attempt-1 digest",
    ):
        asyncio.run(
            attempt2.run(
                execute=True,
                authorization_id=attempt2.AUTHORIZATION_ID,
                maximum_provider_calls=attempt2.MAXIMUM_PROVIDER_CALLS,
                maximum_cost_usd=attempt2.MAXIMUM_COST_USD,
                authorized_plan_digest=attempt1.ARCHIVED_EXECUTION_PLAN_DIGEST,
                show_replies=False,
            )
        )


def test_attempt2_preflight_accepts_only_its_exact_digest_and_source_parity() -> None:
    plan = attempt2.ExecutionPlan(_synthetic_fingerprint())
    attempt2._preflight(
        execute=True,
        authorization_id=attempt2.AUTHORIZATION_ID,
        maximum_provider_calls=attempt2.MAXIMUM_PROVIDER_CALLS,
        maximum_cost_usd=attempt2.MAXIMUM_COST_USD,
        authorized_plan_digest=plan.digest,
        plan=plan,
    )

    with pytest.raises(attempt2.V27Attempt2ManualEvaluationConfigurationError, match="digest"):
        attempt2._preflight(
            execute=True,
            authorization_id=attempt2.AUTHORIZATION_ID,
            maximum_provider_calls=attempt2.MAXIMUM_PROVIDER_CALLS,
            maximum_cost_usd=attempt2.MAXIMUM_COST_USD,
            authorized_plan_digest="sha256:" + "0" * 64,
            plan=plan,
        )
    stale = attempt2.ExecutionPlan(_synthetic_fingerprint(parity=False))
    with pytest.raises(attempt2.V27Attempt2ManualEvaluationConfigurationError, match="parity"):
        attempt2._preflight(
            execute=True,
            authorization_id=attempt2.AUTHORIZATION_ID,
            maximum_provider_calls=attempt2.MAXIMUM_PROVIDER_CALLS,
            maximum_cost_usd=attempt2.MAXIMUM_COST_USD,
            authorized_plan_digest=stale.digest,
            plan=stale,
        )


def test_claimed_source_drift_leaves_a_sanitized_failure_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan = attempt2.ExecutionPlan(_synthetic_fingerprint())
    var_root = tmp_path / "var"
    var_root.mkdir(mode=0o700)
    events: list[str] = []
    real_claim = acquire_one_shot_authorization_claim

    monkeypatch.setattr(attempt2, "ExecutionPlan", lambda: plan)
    monkeypatch.setattr(attempt2, "repository_root", lambda: tmp_path)

    def claim(**kwargs: Any) -> Path:
        events.append("claim")
        return real_claim(**kwargs)

    def drifted() -> dict[str, object]:
        events.append("fingerprint")
        return _synthetic_fingerprint(source="sha256:changed")

    class ForbiddenSettings:
        def __init__(self) -> None:
            events.append("settings")
            raise AssertionError("source drift reached Settings")

    monkeypatch.setattr(attempt2, "acquire_one_shot_authorization_claim", claim)
    monkeypatch.setattr(attempt2, "_execution_source_fingerprint", drifted)
    monkeypatch.setattr(attempt2, "Settings", ForbiddenSettings)
    monkeypatch.setattr(
        attempt2,
        "run_replica",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("provider reached")),
    )

    with pytest.raises(
        attempt2.V27Attempt2ManualEvaluationConfigurationError,
        match="sources changed",
    ):
        asyncio.run(
            attempt2.run(
                execute=True,
                authorization_id=attempt2.AUTHORIZATION_ID,
                maximum_provider_calls=attempt2.MAXIMUM_PROVIDER_CALLS,
                maximum_cost_usd=attempt2.MAXIMUM_COST_USD,
                authorized_plan_digest=plan.digest,
                show_replies=False,
            )
        )

    assert events == ["claim", "fingerprint"]
    claim_path = tmp_path / attempt2.CLAIM_RELATIVE_PATH
    report_path = tmp_path / attempt2.REPORT_RELATIVE_PATH
    assert claim_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["failure"] == {"error_type": "V27Attempt2ManualEvaluationConfigurationError"}
    assert report["budget"]["provider_call_count"] == 0
    attempt2.validate_artifact_privacy(report)


def test_claimed_settings_failure_leaves_a_sanitized_failure_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan = attempt2.ExecutionPlan(_synthetic_fingerprint())
    var_root = tmp_path / "var"
    var_root.mkdir(mode=0o700)

    class FailingSettings:
        def __init__(self) -> None:
            raise RuntimeError("offline settings fixture failed")

    monkeypatch.setattr(attempt2, "ExecutionPlan", lambda: plan)
    monkeypatch.setattr(attempt2, "repository_root", lambda: tmp_path)
    monkeypatch.setattr(
        attempt2,
        "_execution_source_fingerprint",
        lambda: copy.deepcopy(dict(plan.source_fingerprint)),
    )
    monkeypatch.setattr(attempt2, "Settings", FailingSettings)
    monkeypatch.setattr(
        attempt2,
        "run_replica",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("provider reached")),
    )

    with pytest.raises(RuntimeError, match="settings fixture"):
        asyncio.run(
            attempt2.run(
                execute=True,
                authorization_id=attempt2.AUTHORIZATION_ID,
                maximum_provider_calls=attempt2.MAXIMUM_PROVIDER_CALLS,
                maximum_cost_usd=attempt2.MAXIMUM_COST_USD,
                authorized_plan_digest=plan.digest,
                show_replies=False,
            )
        )

    report = json.loads((tmp_path / attempt2.REPORT_RELATIVE_PATH).read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["failure"] == {"error_type": "RuntimeError"}
    assert report["budget"]["provider_call_count"] == 0
    attempt2.validate_artifact_privacy(report)


def test_preexisting_review_path_rejects_before_claim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan = attempt2.ExecutionPlan(_synthetic_fingerprint())
    var_root = tmp_path / "var"
    evaluations = var_root / "evaluations"
    evaluations.mkdir(parents=True, mode=0o700)
    review = tmp_path / attempt2.REVIEW_RELATIVE_PATH
    review.write_text("{}", encoding="utf-8")
    review.chmod(0o600)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("unsafe review path consumed the authorization")

    monkeypatch.setattr(attempt2, "ExecutionPlan", lambda: plan)
    monkeypatch.setattr(attempt2, "repository_root", lambda: tmp_path)
    monkeypatch.setattr(attempt2, "acquire_one_shot_authorization_claim", forbidden)
    monkeypatch.setattr(attempt2, "Settings", forbidden)

    with pytest.raises(
        attempt2.V27Attempt2ManualEvaluationConfigurationError,
        match="review path",
    ):
        asyncio.run(
            attempt2.run(
                execute=True,
                authorization_id=attempt2.AUTHORIZATION_ID,
                maximum_provider_calls=attempt2.MAXIMUM_PROVIDER_CALLS,
                maximum_cost_usd=attempt2.MAXIMUM_COST_USD,
                authorized_plan_digest=plan.digest,
                show_replies=False,
            )
        )

    assert not (tmp_path / attempt2.CLAIM_RELATIVE_PATH).exists()


def test_attempt2_cap_ledger_accepts_exact_full_fresh_three_by_eight_vector() -> None:
    ledger = _ledger()

    for replica in range(1, 4):
        for fixture, visible_cap in zip(
            attempt2.PUBLIC_TURNS,
            attempt2.EXPECTED_TURN_VISIBLE_OUTPUT_TOKEN_LIMITS,
            strict=True,
        ):
            scope = PublicTurnScope(
                session_id=f"v27-character-attempt2-replica-{replica}",
                turn=fixture["turn"],
                turn_id=fixture["id"],
            )
            call = ledger.reserve(
                _request(
                    trace_id=f"attempt2-{replica}-{fixture['turn']}",
                    visible_cap=visible_cap,
                ),
                scope,
            )
            ledger.settle_success(call, _response())

    snapshot = ledger.snapshot()
    assert snapshot["provider_call_count"] == 24
    assert snapshot["base_call_count"] == 24
    assert snapshot["mandatory_base_calls_complete"] is True
    assert snapshot["gate_valid"] is True
    assert [call["requested_visible_output_token_limit"] for call in snapshot["calls"]] == (
        list(attempt2.EXPECTED_TURN_VISIBLE_OUTPUT_TOKEN_LIMITS) * 3
    )


def test_attempt2_cap_ledger_rejects_old_broad_cap_before_reservation() -> None:
    ledger = _ledger()
    fixture = attempt2.PUBLIC_TURNS[2]
    scope = PublicTurnScope(
        session_id="v27-character-attempt2-replica-1",
        turn=fixture["turn"],
        turn_id=fixture["id"],
    )

    with pytest.raises(ProviderCallBudgetExhausted, match="cap vector"):
        ledger.reserve(_request(trace_id="old-cap", visible_cap=160), scope)
    assert ledger.snapshot()["provider_call_count"] == 0
    assert ledger.snapshot()["base_call_count"] == 0


@pytest.mark.parametrize(
    ("session_id", "turn", "turn_id", "visible_cap"),
    [
        ("v27-character-attempt2-replica-4", 1, "warm-greeting", 48),
        ("v27-character-attempt2-replica-1", 9, "warm-greeting", 48),
        ("v27-character-attempt2-replica-1", 1, "wrong-turn", 48),
        ("v27-character-attempt2-replica-1", 1, "warm-greeting", 49),
    ],
)
def test_attempt2_cap_ledger_rejects_scope_or_cap_drift_before_reservation(
    session_id: str,
    turn: int,
    turn_id: str,
    visible_cap: int,
) -> None:
    ledger = _ledger()
    scope = PublicTurnScope(session_id, turn, turn_id)

    with pytest.raises(ProviderCallBudgetExhausted, match="cap vector"):
        ledger.reserve(_request(trace_id="scope-drift", visible_cap=visible_cap), scope)

    assert ledger.snapshot()["provider_call_count"] == 0
    assert ledger.snapshot()["base_call_count"] == 0


def test_attempt2_broad_cap_matches_current_v27_production_composition() -> None:
    observation = _observe_v27(attempt2.PUBLIC_TURNS[2]["user_text"])
    assert observation.request.parameters.max_output_tokens == 200


def test_attempt2_completed_cap_evidence_requires_every_exact_attempt() -> None:
    sessions: list[dict[str, Any]] = [
        {
            "session_id": f"v27-character-attempt2-replica-{replica}",
            "turns": [
                {
                    "turn": fixture["turn"],
                    "turn_id": fixture["id"],
                    "provider_attempts": [{"max_output_tokens": cap}],
                }
                for fixture, cap in zip(
                    attempt2.PUBLIC_TURNS,
                    attempt2.EXPECTED_TURN_VISIBLE_OUTPUT_TOKEN_LIMITS,
                    strict=True,
                )
            ],
        }
        for replica in range(1, 4)
    ]
    attempt2._validate_expected_turn_caps(sessions)

    tampered = copy.deepcopy(sessions)
    tampered[2]["turns"][2]["provider_attempts"][0]["max_output_tokens"] = 160
    with pytest.raises(ValueError, match="cap drift"):
        attempt2._validate_expected_turn_caps(tampered)


def test_attempt2_claim_is_one_shot_at_its_distinct_fixed_path(tmp_path: Path) -> None:
    var_root = tmp_path / "var"
    var_root.mkdir(mode=0o700)
    claim = acquire_one_shot_authorization_claim(
        root=var_root,
        authorization_id=attempt2.AUTHORIZATION_ID,
        expected_authorization_id=attempt2.AUTHORIZATION_ID,
        plan_digest="sha256:" + "1" * 64,
        expected_claim_name=attempt2.AUTHORIZATION_CLAIM_NAME,
        evaluation_label="V27 attempt 2",
    )
    assert claim.name == attempt2.AUTHORIZATION_CLAIM_NAME
    assert json.loads(claim.read_text(encoding="utf-8"))["one_shot"] is True
    with pytest.raises(EvaluationArtifactSafetyError, match="already been consumed"):
        acquire_one_shot_authorization_claim(
            root=var_root,
            authorization_id=attempt2.AUTHORIZATION_ID,
            expected_authorization_id=attempt2.AUTHORIZATION_ID,
            plan_digest="sha256:" + "1" * 64,
            expected_claim_name=attempt2.AUTHORIZATION_CLAIM_NAME,
            evaluation_label="V27 attempt 2",
        )


def test_attempt2_manifest_requires_exact_v27_schemas_and_one_value_guard() -> None:
    report = _completed_report()
    manifest = cast(dict[str, Any], report["sessions"][0]["turns"][0]["manifest"])
    assert attempt2._safe_manifest(manifest) == manifest

    for key, old in (
        ("character_delivery_decision_schema_version", 3),
        ("character_presence_projection_schema_version", 1),
    ):
        with pytest.raises(RuntimeError, match="schemas 4/2"):
            attempt2._safe_manifest({**manifest, key: old})
    with pytest.raises(RuntimeError, match="exactly one value"):
        attempt2._safe_manifest(
            {
                **manifest,
                "character_presence_value_signals": [
                    "connection:defining",
                    "autonomy:defining",
                ],
            }
        )


def test_attempt2_completed_report_accepts_exact_fixture_and_rejects_tampering() -> None:
    plan = attempt2.ExecutionPlan(_synthetic_fingerprint())
    report = _completed_report(retry_selected=True)
    attempt2._validate_completed_report(report, plan)

    boolean = copy.deepcopy(report)
    boolean["schema_version"] = True
    with pytest.raises(ValueError, match="identity/configuration"):
        attempt2._validate_completed_report(boolean, plan)

    cap = copy.deepcopy(report)
    cap["sessions"][2]["turns"][2]["provider_attempts"][0]["max_output_tokens"] = 160
    with pytest.raises(ValueError, match="provider-attempt evidence drift"):
        attempt2._validate_completed_report(cap, plan)

    source = copy.deepcopy(report)
    source["source_fingerprint"]["installed_wheel_parity"] = False
    with pytest.raises(ValueError, match="fingerprint"):
        attempt2._validate_completed_report(source, plan)

    repriced = copy.deepcopy(report)
    repriced["budget"]["calls"][0]["actual_cost_nano_usd"] += 1
    with pytest.raises(ValueError, match="cost arithmetic"):
        attempt2._validate_completed_report(repriced, plan)

    private = copy.deepcopy(report)
    private["sessions"][0]["turns"][0]["prompt"] = "private"
    with pytest.raises(ValueError, match="private"):
        attempt2._validate_completed_report(private, plan)


def test_attempt2_human_review_requires_external_plan_and_exact_boolean_binding() -> None:
    report = _completed_report()
    authorized_plan = attempt2.ExecutionPlan(_synthetic_fingerprint())
    plan_digest = authorized_plan.digest
    assert report["execution_plan_digest"] == plan_digest
    review = attempt2._human_review_template(report)
    for session in review["session_reviews"]:
        for turn in session["turns"]:
            turn["dimensions"] = {
                dimension: True for dimension in attempt2.PER_TURN_HUMAN_REVIEW_DIMENSIONS
            }
    review["cross_session_dimensions"] = {
        dimension: True for dimension in attempt2.CROSS_SESSION_HUMAN_REVIEW_DIMENSIONS
    }
    review["reviewer_attestation"] = {
        "exact_public_sample_reviewed": True,
        "no_automated_text_judge_used": True,
        "no_response_rewriting_performed": True,
    }
    review["accepted"] = True
    review["content_digest"] = human_review_content_digest(review)

    assert (
        attempt2.validate_v27_attempt2_human_review_artifact(
            review,
            report,
            authorized_plan_digest=plan_digest,
        )
        is True
    )

    one_false = copy.deepcopy(review)
    first_dimension = attempt2.PER_TURN_HUMAN_REVIEW_DIMENSIONS[0]
    one_false["session_reviews"][0]["turns"][0]["dimensions"][first_dimension] = False
    one_false["accepted"] = False
    one_false["content_digest"] = human_review_content_digest(one_false)
    assert (
        attempt2.validate_v27_attempt2_human_review_artifact(
            one_false,
            report,
            authorized_plan_digest=plan_digest,
        )
        is False
    )

    non_boolean = copy.deepcopy(review)
    non_boolean["session_reviews"][0]["turns"][0]["dimensions"][first_dimension] = 1
    non_boolean["content_digest"] = human_review_content_digest(non_boolean)
    with pytest.raises(ValueError, match="explicit boolean"):
        attempt2.validate_v27_attempt2_human_review_artifact(
            non_boolean,
            report,
            authorized_plan_digest=plan_digest,
        )

    with pytest.raises(ValueError, match="externally authorized"):
        attempt2.validate_v27_attempt2_human_review_artifact(
            review,
            report,
            authorized_plan_digest="sha256:" + "0" * 64,
        )

    stale = copy.deepcopy(review)
    stale["sample_digest"] = "sha256:" + "0" * 64
    stale["content_digest"] = human_review_content_digest(stale)
    with pytest.raises(ValueError, match="bound"):
        attempt2.validate_v27_attempt2_human_review_artifact(
            stale,
            report,
            authorized_plan_digest=plan_digest,
        )
