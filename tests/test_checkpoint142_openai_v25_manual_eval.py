"""Offline safety tests for the retired v25 manual evaluator."""

# ruff: noqa: RUF001  # Exact Russian production phrases are intentional.

import asyncio
from pathlib import Path
from typing import Any

import pytest

from tests import checkpoint142_openai_v25_manual_eval as evaluator
from tests.checkpoint142_openai_v25_manual_eval import (
    ABSOLUTE_MAX_CALLS,
    PAID_EXECUTION_RETIRED,
    REQUIRED_BASE_CALLS,
    V25ManualEvaluationConfigurationError,
    _preflight,
    execution_plan_digest,
    inspect_plan,
)


def test_v25_manual_plan_is_offline_exact_and_digest_bound() -> None:
    plan = inspect_plan()

    assert plan["network_attempted"] is False
    assert plan["policy_id"] == "satori.conversation.behavior.v25"
    assert plan["fresh_replica_count"] == 3
    assert plan["turns_per_replica"] == 3
    assert plan["required_base_calls"] == 9
    assert plan["maximum_calls_with_one_retry_per_turn"] == 18
    assert plan["execution_plan_digest"] == execution_plan_digest()
    assert PAID_EXECUTION_RETIRED is True
    assert plan["paid_execution"] == {
        "status": "retired",
        "available": False,
        "historical_or_new_authorization_can_execute": False,
    }
    assert [turn["user_text"] for turn in plan["turns"]] == [
        "приветик, как ты?",
        "и я тебя рад видеть",
        "слушай, а расскажи о себе, кто ты, чем увлекаешься, как себя чувствуешь вообще",
    ]


@pytest.mark.parametrize(
    ("execute", "calls", "cost", "digest"),
    [
        (False, REQUIRED_BASE_CALLS, 0.15, execution_plan_digest()),
        (True, REQUIRED_BASE_CALLS - 1, 0.15, execution_plan_digest()),
        (True, ABSOLUTE_MAX_CALLS + 1, 0.15, execution_plan_digest()),
        (True, REQUIRED_BASE_CALLS, 0.0, execution_plan_digest()),
        (True, REQUIRED_BASE_CALLS, 0.15, "sha256:wrong"),
    ],
)
def test_v25_manual_preflight_fails_closed(
    execute: bool,
    calls: int,
    cost: float,
    digest: str,
) -> None:
    with pytest.raises(V25ManualEvaluationConfigurationError):
        _preflight(
            execute=execute,
            maximum_provider_calls=calls,
            maximum_cost_usd=cost,
            authorized_plan_digest=digest,
        )


def test_v25_historical_preflight_keeps_exact_offline_envelope_validation() -> None:
    _preflight(
        execute=True,
        maximum_provider_calls=ABSOLUTE_MAX_CALLS,
        maximum_cost_usd=0.15,
        authorized_plan_digest=execution_plan_digest(),
    )


@pytest.mark.parametrize(
    "authorized_plan_digest",
    [execution_plan_digest(), "sha256:new-authorization-cannot-reactivate-v25"],
)
def test_v25_cli_and_run_reject_any_digest_before_settings_output_or_network(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    authorized_plan_digest: str,
) -> None:
    output_path = tmp_path / "v25-paid-report.json"

    def fail_if_settings_are_loaded() -> None:
        raise AssertionError("retired v25 execution must fail before Settings or .env")

    def fail_if_report_is_written(_path: Path, _report: dict[str, Any]) -> None:
        raise AssertionError("retired v25 execution must fail before report output")

    async def fail_if_replica_runs(**_arguments: Any) -> dict[str, Any]:
        raise AssertionError("retired v25 execution must fail before provider runtime")

    monkeypatch.setattr(evaluator, "Settings", fail_if_settings_are_loaded)
    monkeypatch.setattr(evaluator, "_write_report", fail_if_report_is_written)
    monkeypatch.setattr(evaluator, "_run_replica", fail_if_replica_runs)

    with pytest.raises(V25ManualEvaluationConfigurationError, match="paid execution is retired"):
        evaluator.main(
            [
                "--execute",
                "--max-provider-calls",
                str(ABSOLUTE_MAX_CALLS),
                "--max-cost-usd",
                "0.15",
                "--authorized-plan-digest",
                authorized_plan_digest,
                "--output",
                str(output_path),
            ]
        )
    with pytest.raises(V25ManualEvaluationConfigurationError, match="paid execution is retired"):
        asyncio.run(
            evaluator.run(
                output_path=output_path,
                alembic_config=Path("alembic.ini"),
                execute=True,
                maximum_provider_calls=ABSOLUTE_MAX_CALLS,
                maximum_cost_usd=0.15,
                authorized_plan_digest=authorized_plan_digest,
                show_replies=False,
            )
        )
    assert not output_path.exists()
