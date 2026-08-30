"""Offline immutability, privacy and fail-closed tests for the V27 OpenAI gate."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any, cast

import pytest

from tests.checkpoint142_openai_manual_support import (
    EvaluationArtifactSafetyError,
    acquire_one_shot_authorization_claim,
    content_digest,
    human_review_content_digest,
)
from tests.checkpoint142_openai_v26_manual_eval import ExecutionPlan as V26ExecutionPlan
from tests.checkpoint142_openai_v27_manual_eval import (
    _ARTIFACT_CONTRACT,
    AUTHORIZATION_CLAIM_NAME,
    AUTHORIZATION_ID,
    CROSS_SESSION_HUMAN_REVIEW_DIMENSIONS,
    MAXIMUM_COST_USD,
    MAXIMUM_PROVIDER_CALLS,
    PER_TURN_HUMAN_REVIEW_DIMENSIONS,
    PUBLIC_TURNS,
    REQUIRED_BASE_CALLS,
    ExecutionPlan,
    V27ManualEvaluationConfigurationError,
    _configuration,
    _human_review_contract,
    _human_review_template,
    _preflight,
    _safe_manifest,
    _sample_payload,
    _validate_completed_report,
    main,
    run,
    validate_artifact_privacy,
    validate_v27_human_review_artifact,
)
from tests.test_checkpoint142_openai_v26_manual_eval import (
    _completed_report as _v26_completed_report,
)


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


def _v27_completed_report(*, retry_selected: bool | None = None) -> dict[str, Any]:
    historical = _v26_completed_report(
        V26ExecutionPlan(_synthetic_fingerprint()),
        retry_selected=retry_selected,
    )
    plan = ExecutionPlan(_synthetic_fingerprint())
    report = copy.deepcopy(historical)
    report.update(
        {
            "schema_version": 1,
            "purpose": "v27_live_state_selected_character_movement_production_gate",
            "artifact_id": f"satori-checkpoint142-openai-v27:{plan.digest}",
            "authorization_id": AUTHORIZATION_ID,
            "execution_plan_digest": plan.digest,
            "execution_plan": plan.public_mapping(),
            "source_fingerprint": copy.deepcopy(dict(plan.source_fingerprint)),
            "artifact_contract": dict(_ARTIFACT_CONTRACT),
            "configuration": _configuration(),
            "human_review_contract": _human_review_contract(),
        }
    )
    for replica, session in enumerate(cast(list[dict[str, Any]], report["sessions"]), start=1):
        session_id = f"v27-character-replica-{replica}"
        old_session_id = session["session_id"]
        session["session_id"] = session_id
        for turn in cast(list[dict[str, Any]], session["turns"]):
            manifest = cast(dict[str, Any], turn["manifest"])
            manifest.update(
                {
                    "policy_id": "satori.conversation.behavior.v27",
                    "policy_schema_version": 27,
                    "character_delivery_decision_schema_version": 4,
                    "character_presence_projection_schema_version": 2,
                    "character_presence_value_signals": ["connection:defining"],
                }
            )
        for call in cast(list[dict[str, Any]], report["budget"]["calls"]):
            if call["session_id"] == old_session_id:
                call["session_id"] = session_id
    report["sample_digest"] = content_digest(_sample_payload(report))
    report["human_review_artifact_template"] = _human_review_template(report)
    return report


def test_v27_plan_is_exact_deterministic_and_inspect_only() -> None:
    fingerprint = _synthetic_fingerprint()
    first = ExecutionPlan(fingerprint)
    second = ExecutionPlan(copy.deepcopy(fingerprint))
    plan = first.public_mapping()

    assert first.digest == second.digest
    assert plan["policy_id"] == "satori.conversation.behavior.v27"
    assert plan["character_delivery_decision_schema_version"] == 4
    assert plan["character_presence_projection_schema_version"] == 2
    assert plan["provider"] == "openai"
    assert plan["model"] == "gpt-5.6-terra"
    assert plan["reasoning_effort"] == "medium"
    assert plan["reasoning_token_allowance"] == 1024
    assert plan["fresh_replica_count"] == 3
    assert plan["turns_per_replica"] == 8
    assert plan["required_base_calls"] == REQUIRED_BASE_CALLS == 24
    assert plan["maximum_provider_calls"] == MAXIMUM_PROVIDER_CALLS == 30
    assert plan["maximum_cost_usd"] == MAXIMUM_COST_USD == 0.15
    assert plan["authorization_contract"]["authorization_id"] == AUTHORIZATION_ID
    assert plan["turns"] == [dict(turn) for turn in PUBLIC_TURNS]


def test_v27_inspection_never_constructs_state_or_provider(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import tests.checkpoint142_openai_v27_manual_eval as evaluator

    plan = ExecutionPlan(_synthetic_fingerprint())

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("inspect crossed a stateful or provider boundary")

    monkeypatch.setattr(evaluator, "ExecutionPlan", lambda: plan)
    monkeypatch.setattr(evaluator, "Settings", forbidden)
    monkeypatch.setattr(evaluator, "acquire_one_shot_authorization_claim", forbidden)
    monkeypatch.setattr(evaluator, "run_replica", forbidden)

    assert main([]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["mode"] == "inspect_only"
    assert inspected["network_attempted"] is False
    assert inspected["execution_plan_digest"] == plan.digest


@pytest.mark.parametrize(
    ("authorization", "calls", "cost", "digest"),
    [
        ("wrong", 30, 0.15, "sha256:" + "0" * 64),
        (AUTHORIZATION_ID, 29, 0.15, "sha256:" + "0" * 64),
        (AUTHORIZATION_ID, 30, 0.14, "sha256:" + "0" * 64),
        (AUTHORIZATION_ID, 30, 0.15, "invalid"),
    ],
)
def test_v27_malformed_authority_stops_before_fingerprint_or_io(
    monkeypatch: pytest.MonkeyPatch,
    authorization: str,
    calls: int,
    cost: float,
    digest: str,
) -> None:
    import tests.checkpoint142_openai_v27_manual_eval as evaluator

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("malformed authority crossed the offline preflight")

    monkeypatch.setattr(evaluator, "ExecutionPlan", forbidden)
    monkeypatch.setattr(evaluator, "_execution_source_fingerprint", forbidden)
    monkeypatch.setattr(evaluator, "repository_root", forbidden)
    monkeypatch.setattr(evaluator, "Settings", forbidden)
    monkeypatch.setattr(evaluator, "acquire_one_shot_authorization_claim", forbidden)
    monkeypatch.setattr(evaluator, "DurableReportWriter", forbidden)
    monkeypatch.setattr(evaluator, "run_replica", forbidden)

    with pytest.raises(V27ManualEvaluationConfigurationError):
        asyncio.run(
            run(
                execute=True,
                authorization_id=authorization,
                maximum_provider_calls=calls,
                maximum_cost_usd=cost,
                authorized_plan_digest=digest,
                show_replies=False,
            )
        )


def test_v27_preflight_accepts_only_exact_digest_and_source_parity() -> None:
    plan = ExecutionPlan(_synthetic_fingerprint())
    _preflight(
        execute=True,
        authorization_id=AUTHORIZATION_ID,
        maximum_provider_calls=MAXIMUM_PROVIDER_CALLS,
        maximum_cost_usd=MAXIMUM_COST_USD,
        authorized_plan_digest=plan.digest,
        plan=plan,
    )
    with pytest.raises(V27ManualEvaluationConfigurationError, match="digest"):
        _preflight(
            execute=True,
            authorization_id=AUTHORIZATION_ID,
            maximum_provider_calls=MAXIMUM_PROVIDER_CALLS,
            maximum_cost_usd=MAXIMUM_COST_USD,
            authorized_plan_digest="sha256:" + "0" * 64,
            plan=plan,
        )
    stale = ExecutionPlan(_synthetic_fingerprint(parity=False))
    with pytest.raises(V27ManualEvaluationConfigurationError, match="parity"):
        _preflight(
            execute=True,
            authorization_id=AUTHORIZATION_ID,
            maximum_provider_calls=MAXIMUM_PROVIDER_CALLS,
            maximum_cost_usd=MAXIMUM_COST_USD,
            authorized_plan_digest=stale.digest,
            plan=stale,
        )


def test_v27_claim_precedes_settings_and_source_drift_stops_before_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import tests.checkpoint142_openai_v27_manual_eval as evaluator

    plan = ExecutionPlan(_synthetic_fingerprint())
    var_root = tmp_path / "var"
    var_root.mkdir(mode=0o700)
    events: list[str] = []

    monkeypatch.setattr(evaluator, "ExecutionPlan", lambda: plan)
    monkeypatch.setattr(evaluator, "repository_root", lambda: tmp_path)

    def claimed(**_kwargs: object) -> Path:
        events.append("claim")
        return var_root / AUTHORIZATION_CLAIM_NAME

    def drifted() -> dict[str, Any]:
        events.append("fingerprint")
        return _synthetic_fingerprint(source="sha256:changed")

    class ForbiddenSettings:
        def __init__(self) -> None:
            events.append("settings")
            raise AssertionError("source drift reached Settings")

    monkeypatch.setattr(evaluator, "acquire_one_shot_authorization_claim", claimed)
    monkeypatch.setattr(evaluator, "_execution_source_fingerprint", drifted)
    monkeypatch.setattr(evaluator, "Settings", ForbiddenSettings)
    monkeypatch.setattr(
        evaluator,
        "run_replica",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("provider reached")),
    )

    with pytest.raises(V27ManualEvaluationConfigurationError, match="sources changed"):
        asyncio.run(
            run(
                execute=True,
                authorization_id=AUTHORIZATION_ID,
                maximum_provider_calls=MAXIMUM_PROVIDER_CALLS,
                maximum_cost_usd=MAXIMUM_COST_USD,
                authorized_plan_digest=plan.digest,
                show_replies=False,
            )
        )
    assert events == ["claim", "fingerprint"]


def test_v27_claim_is_one_shot_and_uses_a_distinct_fixed_path(tmp_path: Path) -> None:
    var_root = tmp_path / "var"
    var_root.mkdir(mode=0o700)
    claim = acquire_one_shot_authorization_claim(
        root=var_root,
        authorization_id=AUTHORIZATION_ID,
        expected_authorization_id=AUTHORIZATION_ID,
        plan_digest="sha256:" + "1" * 64,
        expected_claim_name=AUTHORIZATION_CLAIM_NAME,
        evaluation_label="V27",
    )
    assert claim.name == AUTHORIZATION_CLAIM_NAME
    assert json.loads(claim.read_text(encoding="utf-8"))["one_shot"] is True
    with pytest.raises(EvaluationArtifactSafetyError, match="already been consumed"):
        acquire_one_shot_authorization_claim(
            root=var_root,
            authorization_id=AUTHORIZATION_ID,
            expected_authorization_id=AUTHORIZATION_ID,
            plan_digest="sha256:" + "1" * 64,
            expected_claim_name=AUTHORIZATION_CLAIM_NAME,
            evaluation_label="V27",
        )


def test_v27_manifest_requires_exact_schema_pair_and_one_value_guard() -> None:
    report = _v27_completed_report()
    manifest = cast(dict[str, Any], report["sessions"][0]["turns"][0]["manifest"])
    assert _safe_manifest(manifest) == manifest

    for key, old in (
        ("character_delivery_decision_schema_version", 3),
        ("character_presence_projection_schema_version", 1),
    ):
        changed = {**manifest, key: old}
        with pytest.raises(RuntimeError, match="schemas 4/2"):
            _safe_manifest(changed)
    with pytest.raises(RuntimeError, match="exactly one value"):
        _safe_manifest(
            {
                **manifest,
                "character_presence_value_signals": [
                    "connection:defining",
                    "autonomy:defining",
                ],
            }
        )


def test_v27_completed_report_rejects_bool_retry_cost_and_private_tampering() -> None:
    plan = ExecutionPlan(_synthetic_fingerprint())
    report = _v27_completed_report(retry_selected=True)
    _validate_completed_report(report, plan)

    boolean = copy.deepcopy(report)
    boolean["schema_version"] = True
    with pytest.raises(ValueError, match="identity/configuration"):
        _validate_completed_report(boolean, plan)

    nested_boolean = copy.deepcopy(report)
    nested_boolean["execution_plan"]["schema_version"] = True
    nested_boolean["sample_digest"] = content_digest(_sample_payload(nested_boolean))
    with pytest.raises(ValueError, match="identity/configuration"):
        _validate_completed_report(nested_boolean, plan)

    retry = copy.deepcopy(report)
    retry["sessions"][0]["turns"][0]["selected_provider_attempt"] = 1
    with pytest.raises(ValueError, match="selected reply usage"):
        _validate_completed_report(retry, plan)

    repriced = copy.deepcopy(report)
    repriced["budget"]["calls"][0]["actual_cost_nano_usd"] += 1
    with pytest.raises(ValueError, match="cost arithmetic"):
        _validate_completed_report(repriced, plan)

    over_budget = _v27_completed_report()
    for session in over_budget["sessions"]:
        for turn in session["turns"]:
            turn["provider_attempts"][0]["output_tokens"] = 1792
            turn["usage"]["output_tokens"] = 1792
    for call in over_budget["budget"]["calls"]:
        actual_nano = call["input_tokens"] * 2000 + 1792 * 12_000
        call.update(
            {
                "output_tokens": 1792,
                "actual_cost_nano_usd": actual_nano,
                "charged_guard_cost_nano_usd": actual_nano,
                "actual_cost_usd": actual_nano / 1_000_000_000,
                "charged_guard_cost_usd": actual_nano / 1_000_000_000,
            }
        )
    with pytest.raises(ValueError, match="cumulative cost ceiling"):
        _validate_completed_report(over_budget, plan)

    private = copy.deepcopy(report)
    private["sessions"][0]["turns"][0]["prompt"] = "private"
    with pytest.raises(ValueError, match="private"):
        _validate_completed_report(private, plan)
    with pytest.raises(ValueError, match="private"):
        validate_artifact_privacy({"nested": {"api_key": "secret"}})


def test_v27_human_review_requires_bool_decisions_and_exact_digest_binding() -> None:
    report = _v27_completed_report()
    review = _human_review_template(report)
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
    assert validate_v27_human_review_artifact(review, report) is True

    integer = copy.deepcopy(review)
    integer["session_reviews"][0]["turns"][0]["dimensions"][PER_TURN_HUMAN_REVIEW_DIMENSIONS[0]] = 1
    integer["content_digest"] = human_review_content_digest(integer)
    with pytest.raises(ValueError, match="explicit boolean"):
        validate_v27_human_review_artifact(integer, report)

    rebound = copy.deepcopy(review)
    rebound["sample_digest"] = "sha256:" + "0" * 64
    rebound["content_digest"] = human_review_content_digest(rebound)
    with pytest.raises(ValueError, match="bound"):
        validate_v27_human_review_artifact(rebound, report)
