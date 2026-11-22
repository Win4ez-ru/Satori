"""Offline retirement and immutable-evidence tests for V27 attempt 1."""

from __future__ import annotations

import asyncio
import copy
import json

import pytest

import tests.checkpoint142_openai_v27_manual_eval as archived
from tests.checkpoint142_openai_manual_support import content_digest


def test_attempt1_inspection_is_frozen_and_non_executable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    first = archived.inspect_plan()
    second = archived.inspect_plan()

    assert first == second
    assert first["mode"] == "archived_inspect_only"
    assert first["status"] == "failed_immutable"
    assert first["network_attempted"] is False
    assert first["execution_plan_digest"] == archived.ARCHIVED_EXECUTION_PLAN_DIGEST
    assert first["source_fingerprint_digest"] == archived.ARCHIVED_SOURCE_FINGERPRINT_DIGEST
    assert first["evaluator_bundle_digest"] == archived.ARCHIVED_EVALUATOR_BUNDLE_DIGEST
    assert first["paid_execution"]["available"] is False
    assert first["paid_execution"]["authorization_reusable"] is False
    assert first["failure_evidence"] == {
        "error_type": "InvalidProviderResponse",
        "provider_call_count": 19,
        "successful_provider_call_count": 18,
        "actual_successful_usage_cost_usd": 0.057856,
        "conservative_guarded_cost_usd": 0.100594,
        "failed_turn": 3,
        "failed_turn_id": "broad-self-disclosure",
        "requested_visible_output_token_limit": 160,
        "observed_visible_output_tokens": 164,
        "observed_reasoning_output_tokens": 63,
    }

    assert archived.main([]) == 0
    assert json.loads(capsys.readouterr().out) == first


def test_attempt1_execute_retires_before_current_source_settings_files_or_network() -> None:
    assert "ExecutionPlan" not in archived.__dict__
    assert "Settings" not in archived.__dict__
    assert "execution_source_fingerprint" not in archived.__dict__
    assert "run_replica" not in archived.__dict__
    assert "DurableReportWriter" not in archived.__dict__
    assert "acquire_one_shot_authorization_claim" not in archived.__dict__

    with pytest.raises(
        archived.V27ManualEvaluationConfigurationError,
        match="retired",
    ):
        archived.main(
            [
                "--execute",
                "--authorization-id",
                archived.AUTHORIZATION_ID,
                "--max-provider-calls",
                "30",
                "--max-cost-usd",
                "0.15",
                "--authorized-plan-digest",
                archived.ARCHIVED_EXECUTION_PLAN_DIGEST,
            ]
        )

    with pytest.raises(
        archived.V27ManualEvaluationConfigurationError,
        match="retired",
    ):
        asyncio.run(
            archived.run(
                execute=True,
                authorization_id=archived.AUTHORIZATION_ID,
                maximum_provider_calls=30,
                maximum_cost_usd=0.15,
                authorized_plan_digest=archived.ARCHIVED_EXECUTION_PLAN_DIGEST,
                show_replies=False,
            )
        )


def test_attempt1_exact_claim_content_remains_valid_and_tampering_fails() -> None:
    claim = {
        "authorization_id": archived.AUTHORIZATION_ID,
        "execution_plan_digest": archived.ARCHIVED_EXECUTION_PLAN_DIGEST,
        "one_shot": True,
        "schema_version": 1,
    }
    assert content_digest(claim) == archived.ARCHIVED_CLAIM_CONTENT_DIGEST
    archived.validate_archived_attempt1_claim(claim)

    tampered = {**claim, "one_shot": False}
    with pytest.raises(ValueError, match="content drift"):
        archived.validate_archived_attempt1_claim(tampered)


def test_attempt1_archived_report_validator_is_exact_and_private_key_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = {
        "status": "failed",
        "authorization_id": archived.AUTHORIZATION_ID,
        "execution_plan_digest": archived.ARCHIVED_EXECUTION_PLAN_DIGEST,
    }
    monkeypatch.setattr(archived, "ARCHIVED_REPORT_CONTENT_DIGEST", content_digest(report))
    archived.validate_archived_attempt1_report(report)

    changed = copy.deepcopy(report)
    changed["status"] = "completed"
    with pytest.raises(ValueError, match="content drift"):
        archived.validate_archived_attempt1_report(changed)
    with pytest.raises(ValueError, match="private"):
        archived.validate_archived_attempt1_report({**report, "api_key": "private"})
