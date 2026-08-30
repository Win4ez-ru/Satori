"""Offline arithmetic and fail-closed tests for the independent V26 ledger."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from satori.core.conversation import (
    ConversationGenerationParameters,
    ConversationMessage,
    ConversationMessageRole,
    ConversationProviderRequest,
    ConversationProviderResponse,
    ConversationUsage,
)
from satori.core.provider_metrics import ProviderExecutionMetrics
from tests.checkpoint142_openai_v26_ledger import (
    OPENAI_CACHED_INPUT_NANO_USD_PER_TOKEN,
    OPENAI_OUTPUT_NANO_USD_PER_TOKEN,
    OPENAI_UNCACHED_INPUT_NANO_USD_PER_TOKEN,
    ProviderCallBudgetExhausted,
    PublicTurnScope,
    V26AtomicOpenAICallLedger,
)


def _request(trace_id: str = "trace-1") -> ConversationProviderRequest:
    return ConversationProviderRequest(
        schema_version=1,
        trace_id=trace_id,
        context_schema_version=16,
        messages=(ConversationMessage(ConversationMessageRole.USER, "public fixture"),),
        parameters=ConversationGenerationParameters(
            schema_version=1,
            temperature=0.3,
            max_output_tokens=64,
        ),
    )


def _response(
    *,
    input_tokens: int = 100,
    output_tokens: int = 20,
    cached: int | None = 0,
    written: int | None = 0,
    finish_status: str = "completed",
    metrics: ProviderExecutionMetrics | None = None,
) -> ConversationProviderResponse:
    return ConversationProviderResponse(
        text="public reply",
        provider="openai",
        model="gpt-5.6-terra",
        finish_status=finish_status,
        usage=ConversationUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached,
            cache_write_input_tokens=written,
        ),
        metrics=metrics,
    )


def _ledger(
    *, required: int = 1, maximum: int = 2, cost: float = 0.15
) -> V26AtomicOpenAICallLedger:
    return V26AtomicOpenAICallLedger(
        maximum_calls=maximum,
        maximum_cost_usd=cost,
        required_base_calls=required,
        reasoning_token_allowance=1024,
    )


def test_zero_cache_success_has_exact_integer_cost_and_valid_terminal_gate() -> None:
    ledger = _ledger(maximum=1)
    call = ledger.reserve(_request(), PublicTurnScope("session-1", 1, "turn-1"))
    ledger.settle_success(call, _response())

    snapshot = ledger.snapshot()
    expected = (
        100 * OPENAI_UNCACHED_INPUT_NANO_USD_PER_TOKEN + 20 * OPENAI_OUTPUT_NANO_USD_PER_TOKEN
    )
    assert snapshot["actual_usage_cost_nano_usd"] == expected
    assert snapshot["cached_input_tokens"] == 0
    assert snapshot["cache_write_input_tokens"] == 0
    assert snapshot["usage_complete"] is True
    assert snapshot["gate_valid"] is True
    exact = ledger.require_completed_scope(PublicTurnScope("session-1", 1, "turn-1"), 1)
    assert len(exact) == 1
    assert exact[0].input_tokens == 100
    assert exact[0].output_tokens == 20
    assert exact[0].cached_input_tokens == 0
    assert exact[0].cache_write_input_tokens == 0
    with pytest.raises(FrozenInstanceError):
        exact[0].input_tokens = 999  # type: ignore[misc]
    snapshot["calls"][0]["input_tokens"] = 999
    assert exact[0].input_tokens == 100


@pytest.mark.parametrize(("cached", "written"), [(5, 0), (0, 5), (None, None)])
def test_cache_or_missing_cache_breakdown_fails_closed(
    cached: int | None,
    written: int | None,
) -> None:
    ledger = _ledger(maximum=1)
    call = ledger.reserve(_request(), PublicTurnScope("session-1", 1, "turn-1"))
    ledger.settle_success(call, _response(cached=cached, written=written))

    snapshot = ledger.snapshot()
    assert snapshot["usage_complete"] is False
    assert snapshot["zero_prompt_cache_verified"] is False
    assert snapshot["within_cost_limit"] is False
    assert snapshot["gate_valid"] is False
    if cached == 5:
        exact = (
            95 * OPENAI_UNCACHED_INPUT_NANO_USD_PER_TOKEN
            + 5 * OPENAI_CACHED_INPUT_NANO_USD_PER_TOKEN
            + 20 * OPENAI_OUTPUT_NANO_USD_PER_TOKEN
        )
        assert snapshot["calls"][0]["actual_cost_nano_usd"] == exact


def test_incomplete_finish_status_and_failed_call_are_not_terminal_evidence() -> None:
    incomplete = _ledger(maximum=1)
    first = incomplete.reserve(_request(), PublicTurnScope("session-1", 1, "turn-1"))
    incomplete.settle_success(first, _response(finish_status="incomplete"))
    assert incomplete.snapshot()["gate_valid"] is False

    failed = _ledger(maximum=1)
    second = failed.reserve(_request(), PublicTurnScope("session-1", 1, "turn-1"))
    projected = failed.snapshot()["guarded_cost_nano_usd"]
    failed.settle_failure(second, RuntimeError("content must not enter ledger"))
    snapshot = failed.snapshot()
    assert snapshot["guarded_cost_nano_usd"] == projected
    assert snapshot["calls"][0]["error_type"] == "RuntimeError"
    assert "content must not enter ledger" not in str(snapshot)
    assert snapshot["gate_valid"] is False
    with pytest.raises(RuntimeError, match="invalid paid provider attempt"):
        failed.require_completed_scope(PublicTurnScope("session-1", 1, "turn-1"), 1)
    with pytest.raises(ProviderCallBudgetExhausted, match="irreversibly invalid"):
        failed.reserve(_request("trace-2"), PublicTurnScope("session-1", 2, "turn-2"))


def test_retry_cannot_consume_a_call_reserved_for_mandatory_base() -> None:
    ledger = _ledger(required=2, maximum=2)
    scope = PublicTurnScope("session-1", 1, "turn-1")
    first = ledger.reserve(_request(), scope)
    ledger.settle_success(first, _response())

    with pytest.raises(ProviderCallBudgetExhausted, match="reserved"):
        ledger.reserve(_request(), scope)

    second = ledger.reserve(_request("trace-2"), PublicTurnScope("session-1", 2, "turn-2"))
    ledger.settle_success(second, _response())
    assert ledger.snapshot()["mandatory_base_calls_complete"] is True


def test_scope_trace_rebinding_third_attempt_and_cost_ceiling_fail_before_io() -> None:
    ledger = _ledger(required=1, maximum=2)
    scope = PublicTurnScope("session-1", 1, "turn-1")
    first = ledger.reserve(_request(), scope)
    ledger.settle_success(first, _response())
    with pytest.raises(ProviderCallBudgetExhausted, match="another trace"):
        ledger.reserve(_request("other-trace"), scope)

    second = ledger.reserve(_request(), scope)
    ledger.settle_success(second, _response())
    with pytest.raises(ProviderCallBudgetExhausted):
        ledger.reserve(_request(), scope)

    too_small = _ledger(required=1, maximum=1, cost=0.000001)
    with pytest.raises(ProviderCallBudgetExhausted, match="USD ceiling"):
        too_small.reserve(_request(), PublicTurnScope("session-2", 1, "turn-1"))


def test_empty_or_in_flight_ledger_is_never_usage_complete() -> None:
    ledger = _ledger(maximum=1)
    assert ledger.snapshot()["usage_complete"] is False
    ledger.reserve(_request(), PublicTurnScope("session-1", 1, "turn-1"))
    snapshot = ledger.snapshot()
    assert snapshot["usage_complete"] is False
    assert snapshot["gate_valid"] is False


def test_request_schema_drift_is_rejected_before_reservation() -> None:
    ledger = _ledger(maximum=1)
    drifted = replace(_request(), context_schema_version=15)

    with pytest.raises(ProviderCallBudgetExhausted, match="wire contract"):
        ledger.reserve(drifted, PublicTurnScope("session-1", 1, "turn-1"))
    assert ledger.snapshot()["provider_call_count"] == 0


def test_completed_scope_evidence_is_ordered_and_exactly_scope_bound() -> None:
    ledger = _ledger(required=1, maximum=2)
    scope = PublicTurnScope("session-1", 1, "turn-1")
    first = ledger.reserve(_request(), scope)
    ledger.settle_success(first, _response(input_tokens=100, output_tokens=20))
    second = ledger.reserve(_request(), scope)
    ledger.settle_success(second, _response(input_tokens=120, output_tokens=21))

    evidence = ledger.require_completed_scope(scope, 2)

    assert [(item.call_number, item.attempt_number) for item in evidence] == [(1, 1), (2, 2)]
    assert [item.scope for item in evidence] == [scope, scope]
    assert [(item.input_tokens, item.output_tokens) for item in evidence] == [
        (100, 20),
        (120, 21),
    ]
    for wrong_scope in (
        PublicTurnScope("other-session", 1, "turn-1"),
        PublicTurnScope("session-1", 2, "turn-1"),
        PublicTurnScope("session-1", 1, "other-turn"),
    ):
        with pytest.raises(RuntimeError, match="invalid paid provider attempt"):
            ledger.require_completed_scope(wrong_scope, 2)
    with pytest.raises(RuntimeError, match="invalid paid provider attempt"):
        ledger.require_completed_scope(scope, 1)


@pytest.mark.parametrize(("cached", "written"), [(1, 0), (0, 1), (None, None)])
def test_completed_scope_rejects_non_exact_cache_evidence(
    cached: int | None,
    written: int | None,
) -> None:
    ledger = _ledger(maximum=1)
    scope = PublicTurnScope("session-1", 1, "turn-1")
    call = ledger.reserve(_request(), scope)
    ledger.settle_success(call, _response(cached=cached, written=written))

    with pytest.raises(RuntimeError, match="invalid paid provider attempt"):
        ledger.require_completed_scope(scope, 1)


def test_snapshot_deep_copies_nested_provider_metrics() -> None:
    ledger = _ledger(maximum=1)
    call = ledger.reserve(_request(), PublicTurnScope("session-1", 1, "turn-1"))
    ledger.settle_success(
        call,
        _response(metrics=ProviderExecutionMetrics(total_duration_ns=1_000_000)),
    )

    first = ledger.snapshot()
    first["calls"][0]["provider_metrics"]["provider_total_ms"] = 999.0

    assert ledger.snapshot()["calls"][0]["provider_metrics"]["provider_total_ms"] == 1.0
