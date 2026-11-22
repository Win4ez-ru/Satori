"""Cache-aware, fail-closed cost ledger for the one-shot V26 OpenAI gate.

This module is deliberately independent from the retired V24/V25 runners.  All monetary
arithmetic is performed in integer nano-dollars so a completed report can be validated without
float rounding or a mutable pricing dependency.
"""

from __future__ import annotations

import copy
import math
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar, cast

from satori.application.conversation.use_cases import ConversationProvider
from satori.core.conversation import (
    ConversationProviderError,
    ConversationProviderRequest,
    ConversationProviderResponse,
)

MAX_ATTEMPTS_PER_TURN = 2
HISTORICAL_LEDGER_SCHEMA_VERSION = 2
LEDGER_SCHEMA_VERSION = 3
EXPECTED_REASONING_TOKEN_ALLOWANCE = 1024
EXPECTED_PROVIDER_REQUEST_SCHEMA_VERSION = 1
EXPECTED_CONTEXT_SCHEMA_VERSION = 16
EXPECTED_GENERATION_PARAMETERS_SCHEMA_VERSION = 1
EXPECTED_TEMPERATURE = 0.3
MAXIMUM_VISIBLE_OUTPUT_TOKENS = 768
ABSOLUTE_MAX_COST_USD = 1.0
NANO_USD_PER_USD = 1_000_000_000
OPENAI_UNCACHED_INPUT_NANO_USD_PER_TOKEN = 2_000
OPENAI_CACHED_INPUT_NANO_USD_PER_TOKEN = 200
OPENAI_CACHE_WRITE_INPUT_NANO_USD_PER_TOKEN = 2_500
OPENAI_OUTPUT_NANO_USD_PER_TOKEN = 12_000
OPENAI_LONG_CONTEXT_INPUT_TOKEN_THRESHOLD = 272_000
PRICING_SNAPSHOT = "openai-gpt-5.6-terra-standard-2026-08-29"
INPUT_TOKEN_BYTE_GUARD_OVERHEAD = 4096
INPUT_TOKEN_MESSAGE_GUARD_OVERHEAD = 128

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

_TERMINAL_LEDGER_KEYS = {
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
_HISTORICAL_TERMINAL_LEDGER_CALL_KEYS = {
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
_TERMINAL_LEDGER_CALL_KEYS = _HISTORICAL_TERMINAL_LEDGER_CALL_KEYS | {"provider_call_observed"}


class ProviderCallBudgetExhausted(RuntimeError):
    """Reject a provider attempt before it can reach the paid foreground adapter."""


def _strict_positive_int(value: object, label: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _strict_non_negative_int(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _non_blank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-blank string")
    return value.strip()


def _usd_from_nano(value: int) -> float:
    return value / NANO_USD_PER_USD


def safe_provider_metrics(value: object) -> dict[str, int | float | None] | None:
    """Retain only content-free numeric provider timings/token metadata."""

    if not isinstance(value, dict):
        return None
    raw = cast(dict[str, Any], value)
    sanitized: dict[str, int | float | None] = {}
    for key in _SAFE_PROVIDER_METRIC_KEYS:
        metric = raw.get(key)
        if metric is None or (
            isinstance(metric, (int, float))
            and not isinstance(metric, bool)
            and math.isfinite(metric)
            and metric >= 0
        ):
            sanitized[key] = metric
    return sanitized or None


@dataclass(frozen=True, slots=True)
class PublicTurnScope:
    """Public report identity for one base turn and its optional validator retry."""

    session_id: str
    turn: int
    turn_id: str

    def __post_init__(self) -> None:
        _non_blank(self.session_id, "scope session_id")
        _strict_positive_int(self.turn, "scope turn")
        _non_blank(self.turn_id, "scope turn_id")


@dataclass(frozen=True, slots=True)
class ExactProviderUsage:
    """Exact cache-aware usage captured before production persistence drops cache detail."""

    scope: PublicTurnScope
    call_number: int
    attempt_number: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cache_write_input_tokens: int

    def __post_init__(self) -> None:
        if not isinstance(self.scope, PublicTurnScope):
            raise ValueError("usage scope must be a PublicTurnScope")
        _strict_positive_int(self.call_number, "usage call_number")
        attempt_number = _strict_positive_int(self.attempt_number, "usage attempt_number")
        if attempt_number > MAX_ATTEMPTS_PER_TURN:
            raise ValueError("usage attempt_number exceeds the V26 turn contract")
        input_tokens = _strict_non_negative_int(self.input_tokens, "usage input_tokens")
        _strict_non_negative_int(self.output_tokens, "usage output_tokens")
        cached_tokens = _strict_non_negative_int(
            self.cached_input_tokens,
            "usage cached_input_tokens",
        )
        cache_write_tokens = _strict_non_negative_int(
            self.cache_write_input_tokens,
            "usage cache_write_input_tokens",
        )
        if cached_tokens + cache_write_tokens > input_tokens:
            raise ValueError("cache-detail input tokens exceed total input tokens")


@dataclass(slots=True)
class TurnScopeBinding:
    """Bind provider calls to public evaluator IDs, never application trace IDs."""

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
class V26AtomicOpenAICallLedger:
    """Atomically reserve and settle every possible V26 paid attempt."""

    ledger_schema_version: ClassVar[int] = HISTORICAL_LEDGER_SCHEMA_VERSION

    maximum_calls: int
    maximum_cost_usd: float
    required_base_calls: int
    reasoning_token_allowance: int
    expected_context_schema_version: int = EXPECTED_CONTEXT_SCHEMA_VERSION
    on_change: Callable[[], None] | None = field(default=None, repr=False)
    _calls: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _attempts_by_scope: dict[PublicTurnScope, tuple[str, int]] = field(
        default_factory=dict,
        repr=False,
    )
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _maximum_cost_nano_usd: int = field(init=False, repr=False)

    def __post_init__(self) -> None:
        maximum_calls = _strict_positive_int(self.maximum_calls, "maximum_calls")
        required_base_calls = _strict_positive_int(self.required_base_calls, "required_base_calls")
        if required_base_calls > maximum_calls:
            raise ValueError("required base calls exceed the provider-call ceiling")
        if (
            isinstance(self.maximum_cost_usd, bool)
            or not isinstance(self.maximum_cost_usd, (int, float))
            or not math.isfinite(self.maximum_cost_usd)
            or not 0 < self.maximum_cost_usd <= ABSOLUTE_MAX_COST_USD
        ):
            raise ValueError("maximum_cost_usd must be finite, positive and at most $1")
        exact_nano = self.maximum_cost_usd * NANO_USD_PER_USD
        rounded_nano = round(exact_nano)
        if abs(exact_nano - rounded_nano) > 1e-6:
            raise ValueError("maximum_cost_usd must be exactly representable in nano-dollars")
        if self.reasoning_token_allowance != EXPECTED_REASONING_TOKEN_ALLOWANCE:
            raise ValueError("reasoning_token_allowance must equal the V26 plan")
        _strict_positive_int(
            self.expected_context_schema_version,
            "expected_context_schema_version",
        )
        self._maximum_cost_nano_usd = rounded_nano

    @staticmethod
    def _guarded_input_token_limit(request: ConversationProviderRequest) -> int:
        # UTF-8 bytes are a conservative upper bound for provider input tokens.  Fixed per-call
        # and per-message margins cover wire framing and provider-side special tokens.
        return (
            sum(len(message.content.encode("utf-8")) for message in request.messages)
            + INPUT_TOKEN_BYTE_GUARD_OVERHEAD
            + len(request.messages) * INPUT_TOKEN_MESSAGE_GUARD_OVERHEAD
        )

    def _projected_guard(self, request: ConversationProviderRequest) -> tuple[int, int, int]:
        guarded_input_tokens = self._guarded_input_token_limit(request)
        guarded_output_tokens = (
            request.parameters.max_output_tokens + self.reasoning_token_allowance
        )
        projected_nano_usd = (
            guarded_input_tokens * OPENAI_CACHE_WRITE_INPUT_NANO_USD_PER_TOKEN
            + guarded_output_tokens * OPENAI_OUTPUT_NANO_USD_PER_TOKEN
        )
        return guarded_input_tokens, guarded_output_tokens, projected_nano_usd

    def _temperature_is_valid(self, temperature: float) -> bool:
        """Preserve the exact historical V26 temperature contract."""

        return temperature == EXPECTED_TEMPERATURE

    def reserve(self, request: ConversationProviderRequest, scope: PublicTurnScope) -> int:
        if (
            request.schema_version != EXPECTED_PROVIDER_REQUEST_SCHEMA_VERSION
            or request.context_schema_version != self.expected_context_schema_version
            or request.parameters.schema_version != EXPECTED_GENERATION_PARAMETERS_SCHEMA_VERSION
            or not self._temperature_is_valid(request.parameters.temperature)
            or not 1 <= request.parameters.max_output_tokens <= MAXIMUM_VISIBLE_OUTPUT_TOKENS
        ):
            raise ProviderCallBudgetExhausted(
                "provider request drifted from the digest-bound V26 wire contract"
            )
        guarded_input, guarded_output, projected_nano = self._projected_guard(request)
        if guarded_input > OPENAI_LONG_CONTEXT_INPUT_TOKEN_THRESHOLD:
            raise ProviderCallBudgetExhausted(
                "guarded input exceeds the standard-context pricing range"
            )
        with self._lock:
            if any(
                call.get("status") == "failed"
                or (
                    call.get("status") == "succeeded"
                    and (
                        call.get("usage_complete") is not True
                        or call.get("zero_prompt_cache_verified") is not True
                    )
                )
                for call in self._calls
            ):
                raise ProviderCallBudgetExhausted(
                    "a prior provider attempt made the V26 sample irreversibly invalid"
                )
            trace_binding = self._attempts_by_scope.get(scope)
            if trace_binding is None:
                prior_attempts = 0
            else:
                bound_trace_id, prior_attempts = trace_binding
                if request.trace_id != bound_trace_id:
                    raise ProviderCallBudgetExhausted(
                        "public turn scope is already bound to another trace"
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
            charged_so_far = sum(
                cast(int, call["charged_guard_cost_nano_usd"]) for call in self._calls
            )
            if charged_so_far + projected_nano > self._maximum_cost_nano_usd:
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
                    "provider_call_observed": False,
                    "requested_visible_output_token_limit": request.parameters.max_output_tokens,
                    "guarded_input_token_limit": guarded_input,
                    "guarded_output_token_limit": guarded_output,
                    "projected_guard_cost_nano_usd": projected_nano,
                    "charged_guard_cost_nano_usd": projected_nano,
                    "projected_guard_cost_usd": _usd_from_nano(projected_nano),
                    "charged_guard_cost_usd": _usd_from_nano(projected_nano),
                }
            )
        # A durable report checkpoint must succeed before the caller may start network I/O.
        self._notify()
        return call_number

    def settle_success(self, call_number: int, response: ConversationProviderResponse) -> None:
        usage = response.usage
        input_tokens = usage.input_tokens if usage is not None else None
        output_tokens = usage.output_tokens if usage is not None else None
        cached_tokens = usage.cached_input_tokens if usage is not None else None
        cache_write_tokens = usage.cache_write_input_tokens if usage is not None else None
        details_complete = all(
            type(value) is int
            for value in (input_tokens, output_tokens, cached_tokens, cache_write_tokens)
        )
        actual_nano: int | None = None
        if details_complete:
            assert input_tokens is not None
            assert output_tokens is not None
            assert cached_tokens is not None
            assert cache_write_tokens is not None
            uncached_tokens = input_tokens - cached_tokens - cache_write_tokens
            if uncached_tokens >= 0:
                actual_nano = (
                    uncached_tokens * OPENAI_UNCACHED_INPUT_NANO_USD_PER_TOKEN
                    + cached_tokens * OPENAI_CACHED_INPUT_NANO_USD_PER_TOKEN
                    + cache_write_tokens * OPENAI_CACHE_WRITE_INPUT_NANO_USD_PER_TOKEN
                    + output_tokens * OPENAI_OUTPUT_NANO_USD_PER_TOKEN
                )
        with self._lock:
            record = self._in_flight_record(call_number)
            guarded_input = cast(int, record["guarded_input_token_limit"])
            guarded_output = cast(int, record["guarded_output_token_limit"])
            projected_nano = cast(int, record["projected_guard_cost_nano_usd"])
            zero_cache = details_complete and cached_tokens == 0 and cache_write_tokens == 0
            standard_context = (
                type(input_tokens) is int
                and input_tokens <= OPENAI_LONG_CONTEXT_INPUT_TOKEN_THRESHOLD
                and input_tokens <= guarded_input
            )
            output_guard_valid = type(output_tokens) is int and output_tokens <= guarded_output
            finish_completed = response.finish_status == "completed"
            exact_pricing = (
                details_complete
                and actual_nano is not None
                and standard_context
                and output_guard_valid
                and finish_completed
            )
            guard_projection_valid = (
                exact_pricing and actual_nano is not None and actual_nano <= projected_nano
            )
            # Unknown or invalid usage stays charged at the prior conservative reservation.
            charged_nano = (
                actual_nano
                if guard_projection_valid and actual_nano is not None
                else projected_nano
            )
            record.update(
                {
                    "status": "succeeded",
                    "provider_call_observed": True,
                    "finish_status": response.finish_status,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cached_input_tokens": cached_tokens,
                    "cache_write_input_tokens": cache_write_tokens,
                    "actual_cost_nano_usd": actual_nano,
                    "actual_cost_usd": (
                        _usd_from_nano(actual_nano) if actual_nano is not None else None
                    ),
                    "input_token_details_complete": details_complete,
                    "zero_prompt_cache_verified": zero_cache,
                    "standard_context_pricing_verified": standard_context,
                    "output_token_guard_verified": output_guard_valid,
                    "finish_status_completed": finish_completed,
                    "usage_complete": exact_pricing,
                    "guard_projection_valid": guard_projection_valid,
                    "charged_guard_cost_nano_usd": charged_nano,
                    "charged_guard_cost_usd": _usd_from_nano(charged_nano),
                    "service_tier_verified_by_adapter": finish_completed,
                    "provider_metrics": (
                        safe_provider_metrics(response.metrics.as_log_fields())
                        if response.metrics is not None
                        else None
                    ),
                }
            )
        self._notify()

    def settle_failure(self, call_number: int, error: BaseException) -> None:
        typed_error = error if isinstance(error, ConversationProviderError) else None
        metrics = (
            safe_provider_metrics(typed_error.metrics.as_log_fields())
            if typed_error is not None and typed_error.metrics is not None
            else None
        )
        usage = typed_error.usage if typed_error is not None else None
        input_tokens = usage.input_tokens if usage is not None else None
        output_tokens = usage.output_tokens if usage is not None else None
        cached_tokens = usage.cached_input_tokens if usage is not None else None
        cache_write_tokens = usage.cache_write_input_tokens if usage is not None else None
        details_complete = all(
            type(value) is int
            for value in (input_tokens, output_tokens, cached_tokens, cache_write_tokens)
        )
        actual_nano: int | None = None
        if details_complete:
            assert input_tokens is not None
            assert output_tokens is not None
            assert cached_tokens is not None
            assert cache_write_tokens is not None
            uncached_tokens = input_tokens - cached_tokens - cache_write_tokens
            if uncached_tokens >= 0:
                actual_nano = (
                    uncached_tokens * OPENAI_UNCACHED_INPUT_NANO_USD_PER_TOKEN
                    + cached_tokens * OPENAI_CACHED_INPUT_NANO_USD_PER_TOKEN
                    + cache_write_tokens * OPENAI_CACHE_WRITE_INPUT_NANO_USD_PER_TOKEN
                    + output_tokens * OPENAI_OUTPUT_NANO_USD_PER_TOKEN
                )
        with self._lock:
            record = self._in_flight_record(call_number)
            guarded_input = cast(int, record["guarded_input_token_limit"])
            guarded_output = cast(int, record["guarded_output_token_limit"])
            projected_nano = cast(int, record["projected_guard_cost_nano_usd"])
            zero_cache = details_complete and cached_tokens == 0 and cache_write_tokens == 0
            standard_context = (
                type(input_tokens) is int
                and input_tokens <= OPENAI_LONG_CONTEXT_INPUT_TOKEN_THRESHOLD
                and input_tokens <= guarded_input
            )
            output_guard_valid = type(output_tokens) is int and output_tokens <= guarded_output
            exact_pricing = (
                details_complete
                and actual_nano is not None
                and standard_context
                and output_guard_valid
                and typed_error is not None
                and typed_error.provider_response_observed
                and typed_error.service_tier_verified
            )
            guard_projection_valid = (
                exact_pricing and actual_nano is not None and actual_nano <= projected_nano
            )
            charged_nano = (
                actual_nano
                if guard_projection_valid and actual_nano is not None
                else projected_nano
            )
            record.update(
                {
                    "status": "failed",
                    "provider_call_observed": True,
                    "error_type": type(error).__name__,
                    "failure_reason": (
                        typed_error.reason.value if typed_error is not None else None
                    ),
                    "finish_status": (
                        "completed"
                        if typed_error is not None and typed_error.response_completed
                        else None
                    ),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cached_input_tokens": cached_tokens,
                    "cache_write_input_tokens": cache_write_tokens,
                    "actual_cost_nano_usd": actual_nano,
                    "actual_cost_usd": (
                        _usd_from_nano(actual_nano) if actual_nano is not None else None
                    ),
                    "input_token_details_complete": details_complete,
                    "zero_prompt_cache_verified": zero_cache,
                    "standard_context_pricing_verified": standard_context,
                    "output_token_guard_verified": output_guard_valid,
                    "finish_status_completed": (
                        typed_error.response_completed if typed_error is not None else False
                    ),
                    "usage_complete": exact_pricing,
                    "guard_projection_valid": guard_projection_valid,
                    "service_tier_verified_by_adapter": (
                        typed_error.service_tier_verified if typed_error is not None else False
                    ),
                    "charged_guard_cost_nano_usd": charged_nano,
                    "charged_guard_cost_usd": _usd_from_nano(charged_nano),
                    "provider_metrics": metrics,
                }
            )
        self._notify()

    def provider_call_observed(self, scope: PublicTurnScope) -> bool:
        """Return whether one reserved call for the public scope reached a terminal settlement."""

        if not isinstance(scope, PublicTurnScope):
            raise ValueError("scope must be a PublicTurnScope")
        with self._lock:
            return any(
                call.get("session_id") == scope.session_id
                and call.get("turn") == scope.turn
                and call.get("turn_id") == scope.turn_id
                and call.get("provider_call_observed") is True
                for call in self._calls
            )

    def _in_flight_record(self, call_number: int) -> dict[str, Any]:
        if type(call_number) is not int or not 1 <= call_number <= len(self._calls):
            raise RuntimeError("unknown provider call number")
        record = self._calls[call_number - 1]
        if record.get("status") != "in_flight":
            raise RuntimeError("provider call has already been settled")
        return record

    def require_completed_scope(
        self,
        scope: PublicTurnScope,
        expected_attempts: int,
    ) -> tuple[ExactProviderUsage, ...]:
        """Return immutable exact usage only after every paid attempt passes the ledger gate."""

        if type(expected_attempts) is not int or expected_attempts not in {
            1,
            MAX_ATTEMPTS_PER_TURN,
        }:
            raise RuntimeError("provider attempt count is outside the V26 turn contract")
        with self._lock:
            calls = [
                call
                for call in self._calls
                if call.get("session_id") == scope.session_id
                and call.get("turn") == scope.turn
                and call.get("turn_id") == scope.turn_id
            ]
            valid = len(calls) == expected_attempts and all(
                call.get("status") == "succeeded"
                and call.get("finish_status") == "completed"
                and call.get("usage_complete") is True
                and call.get("zero_prompt_cache_verified") is True
                and call.get("guard_projection_valid") is True
                and all(
                    type(call.get(key)) is int
                    for key in (
                        "input_tokens",
                        "output_tokens",
                        "cached_input_tokens",
                        "cache_write_input_tokens",
                    )
                )
                for call in calls
            )
            if not valid:
                raise RuntimeError("current V26 turn contains an invalid paid provider attempt")
            evidence = tuple(
                ExactProviderUsage(
                    scope=scope,
                    call_number=cast(int, call["call_number"]),
                    attempt_number=attempt_number,
                    input_tokens=cast(int, call["input_tokens"]),
                    output_tokens=cast(int, call["output_tokens"]),
                    cached_input_tokens=cast(int, call["cached_input_tokens"]),
                    cache_write_input_tokens=cast(int, call["cache_write_input_tokens"]),
                )
                for attempt_number, call in enumerate(calls, start=1)
            )
        return evidence

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            calls = copy.deepcopy(self._calls)
            base_count = len(self._attempts_by_scope)
        if self.ledger_schema_version == HISTORICAL_LEDGER_SCHEMA_VERSION:
            for call in calls:
                call.pop("provider_call_observed", None)
        successful = [call for call in calls if call.get("status") == "succeeded"]
        charged_nano = sum(cast(int, call["charged_guard_cost_nano_usd"]) for call in calls)
        actual_nano = sum(
            cast(int, call["actual_cost_nano_usd"])
            for call in calls
            if type(call.get("actual_cost_nano_usd")) is int
        )
        exact_pricing_complete = bool(calls) and all(
            call.get("usage_complete") is True
            and call.get("standard_context_pricing_verified") is True
            and call.get("output_token_guard_verified") is True
            and call.get("guard_projection_valid") is True
            and call.get("service_tier_verified_by_adapter") is True
            for call in calls
        )
        zero_prompt_cache_verified = bool(calls) and all(
            call.get("zero_prompt_cache_verified") is True for call in calls
        )
        service_tier_verified = bool(calls) and all(
            call.get("service_tier_verified_by_adapter") is True for call in calls
        )
        guard_projection_valid = bool(calls) and all(
            call.get("guard_projection_valid") is True for call in calls
        )
        all_calls_succeeded = len(successful) == len(calls)
        within_cost = charged_nano <= self._maximum_cost_nano_usd
        within_calls = len(calls) <= self.maximum_calls
        mandatory_complete = base_count == self.required_base_calls
        return {
            "schema_version": self.ledger_schema_version,
            "required_base_calls": self.required_base_calls,
            "maximum_provider_calls": self.maximum_calls,
            "maximum_attempts_per_turn": MAX_ATTEMPTS_PER_TURN,
            "base_call_count": base_count,
            "provider_call_count": len(calls),
            "successful_provider_call_count": len(successful),
            "input_tokens": sum(cast(int, call.get("input_tokens") or 0) for call in calls),
            "output_tokens": sum(cast(int, call.get("output_tokens") or 0) for call in calls),
            "cached_input_tokens": sum(
                cast(int, call.get("cached_input_tokens") or 0) for call in calls
            ),
            "cache_write_input_tokens": sum(
                cast(int, call.get("cache_write_input_tokens") or 0) for call in calls
            ),
            "maximum_cost_nano_usd": self._maximum_cost_nano_usd,
            "maximum_cost_usd": _usd_from_nano(self._maximum_cost_nano_usd),
            "actual_usage_cost_nano_usd": actual_nano,
            "actual_usage_cost_usd": _usd_from_nano(actual_nano),
            "guarded_cost_nano_usd": charged_nano,
            "guarded_cost_usd": _usd_from_nano(charged_nano),
            "usage_complete": exact_pricing_complete,
            "exact_pricing_evidence_complete": exact_pricing_complete,
            "zero_prompt_cache_verified": zero_prompt_cache_verified,
            "service_tier_verified": service_tier_verified,
            "guard_projection_valid": guard_projection_valid,
            "pricing": {
                "currency": "USD",
                "uncached_input_nano_usd_per_token": (OPENAI_UNCACHED_INPUT_NANO_USD_PER_TOKEN),
                "cached_input_nano_usd_per_token": OPENAI_CACHED_INPUT_NANO_USD_PER_TOKEN,
                "cache_write_input_nano_usd_per_token": (
                    OPENAI_CACHE_WRITE_INPUT_NANO_USD_PER_TOKEN
                ),
                "output_nano_usd_per_token": OPENAI_OUTPUT_NANO_USD_PER_TOKEN,
                "long_context_threshold_input_tokens": (OPENAI_LONG_CONTEXT_INPUT_TOKEN_THRESHOLD),
                "service_tier": "default",
                "prompt_cache_mode": "explicit",
                "expected_cache_behavior": "no_cache_reads_or_writes",
                "snapshot": PRICING_SNAPSHOT,
                "fx_conversion_used": False,
            },
            "within_call_limit": within_calls,
            "within_cost_limit": within_cost and exact_pricing_complete,
            "mandatory_base_calls_complete": mandatory_complete,
            "gate_valid": (
                exact_pricing_complete
                and all_calls_succeeded
                and all(call.get("finish_status_completed") is True for call in calls)
                and zero_prompt_cache_verified
                and within_calls
                and within_cost
                and mandatory_complete
            ),
            "calls": calls,
        }

    def _notify(self) -> None:
        callback = self.on_change
        if callback is not None:
            callback()


@dataclass(slots=True)
class BudgetedOpenAIProvider:
    """Wrap the production foreground provider with the V26 atomic ledger."""

    delegate: ConversationProvider
    ledger: V26AtomicOpenAICallLedger
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


def validate_exact_openai_ledger(
    budget: Mapping[str, Any],
    sessions: Sequence[Mapping[str, Any]],
    *,
    required_base_calls: int,
    maximum_provider_calls: int,
    maximum_cost_usd: float,
    reasoning_token_allowance: int,
    visible_output_token_ceiling: int,
) -> None:
    """Revalidate a serialized terminal ledger against public turn-attempt evidence."""

    if set(budget) != _TERMINAL_LEDGER_KEYS:
        raise ValueError("budget schema drift")
    schema_version = budget.get("schema_version")
    if type(schema_version) is not int or schema_version not in {
        HISTORICAL_LEDGER_SCHEMA_VERSION,
        LEDGER_SCHEMA_VERSION,
    }:
        raise ValueError("budget schema version is unsupported")
    calls = budget.get("calls")
    if not isinstance(calls, list):
        raise ValueError("budget calls must be an array")
    attempt_scopes: list[tuple[str, int, str, int, Mapping[str, Any]]] = []
    for session in sessions:
        session_id = session.get("session_id")
        turns = session.get("turns")
        if not isinstance(session_id, str) or not isinstance(turns, list):
            raise ValueError("ledger session evidence is invalid")
        for turn in turns:
            if not isinstance(turn, Mapping):
                raise ValueError("ledger turn evidence is invalid")
            attempts = turn.get("provider_attempts")
            if not isinstance(attempts, list):
                raise ValueError("ledger provider-attempt evidence is invalid")
            for index, attempt in enumerate(attempts, start=1):
                if not isinstance(attempt, Mapping):
                    raise ValueError("ledger provider-attempt evidence is invalid")
                attempt_scopes.append(
                    (
                        session_id,
                        cast(int, turn.get("turn")),
                        cast(str, turn.get("turn_id")),
                        index,
                        attempt,
                    )
                )
    if (
        len(calls) != len(attempt_scopes)
        or not required_base_calls <= len(calls) <= maximum_provider_calls
    ):
        raise ValueError("ledger/attempt call cardinality mismatch")

    total_actual = 0
    total_input = 0
    total_output = 0
    maximum_cost_nano = round(maximum_cost_usd * NANO_USD_PER_USD)
    for number, (raw_call, scope) in enumerate(zip(calls, attempt_scopes, strict=True), start=1):
        expected_call_keys = (
            _HISTORICAL_TERMINAL_LEDGER_CALL_KEYS
            if schema_version == HISTORICAL_LEDGER_SCHEMA_VERSION
            else _TERMINAL_LEDGER_CALL_KEYS
        )
        if not isinstance(raw_call, dict) or set(raw_call) != expected_call_keys:
            raise ValueError("ledger call must be an exact object")
        call = cast(dict[str, Any], raw_call)
        session_id, turn_number, turn_id, attempt_number, attempt = scope
        if (
            type(call.get("call_number")) is not int
            or call.get("call_number") != number
            or call.get("session_id") != session_id
            or type(call.get("turn")) is not int
            or call.get("turn") != turn_number
            or call.get("turn_id") != turn_id
            or call.get("attempt_kind") != ("base" if attempt_number == 1 else "validator_retry")
            or (
                schema_version == LEDGER_SCHEMA_VERSION
                and call.get("provider_call_observed") is not True
            )
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
            attempt.get("input_tokens") != input_tokens
            or attempt.get("output_tokens") != output_tokens
        ):
            raise ValueError("ledger usage disagrees with provider-attempt evidence")
        guarded_input = call.get("guarded_input_token_limit")
        guarded_output = call.get("guarded_output_token_limit")
        visible_limit = attempt.get("max_output_tokens")
        if (
            type(guarded_input) is not int
            or type(guarded_output) is not int
            or type(visible_limit) is not int
            or not 1 <= visible_limit <= visible_output_token_ceiling
            or guarded_output != visible_limit + reasoning_token_allowance
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
        if total_actual + projected > maximum_cost_nano:
            raise ValueError("ledger reservation guard exceeds the cumulative cost ceiling")
        exact_costs: dict[str, int | float] = {
            "projected_guard_cost_nano_usd": projected,
            "charged_guard_cost_nano_usd": actual,
            "actual_cost_nano_usd": actual,
            "projected_guard_cost_usd": _usd_from_nano(projected),
            "charged_guard_cost_usd": _usd_from_nano(actual),
            "actual_cost_usd": _usd_from_nano(actual),
            "requested_visible_output_token_limit": visible_limit,
        }
        if (
            any(
                type(call.get(key)) is not type(value) or call.get(key) != value
                for key, value in exact_costs.items()
            )
            or actual > projected
        ):
            raise ValueError("ledger cost arithmetic mismatch")
        provider_metrics = call.get("provider_metrics")
        if (
            provider_metrics is not None
            and safe_provider_metrics(provider_metrics) != provider_metrics
        ):
            raise ValueError("ledger provider metrics schema drift")
        total_actual += actual
        if total_actual > maximum_cost_nano:
            raise ValueError("ledger actual usage exceeds the cumulative cost ceiling")
        total_input += input_tokens
        total_output += output_tokens

    pricing = {
        "currency": "USD",
        "uncached_input_nano_usd_per_token": OPENAI_UNCACHED_INPUT_NANO_USD_PER_TOKEN,
        "cached_input_nano_usd_per_token": OPENAI_CACHED_INPUT_NANO_USD_PER_TOKEN,
        "cache_write_input_nano_usd_per_token": OPENAI_CACHE_WRITE_INPUT_NANO_USD_PER_TOKEN,
        "output_nano_usd_per_token": OPENAI_OUTPUT_NANO_USD_PER_TOKEN,
        "long_context_threshold_input_tokens": OPENAI_LONG_CONTEXT_INPUT_TOKEN_THRESHOLD,
        "service_tier": "default",
        "prompt_cache_mode": "explicit",
        "expected_cache_behavior": "no_cache_reads_or_writes",
        "snapshot": PRICING_SNAPSHOT,
        "fx_conversion_used": False,
    }
    scalars: dict[str, object] = {
        "schema_version": schema_version,
        "required_base_calls": required_base_calls,
        "maximum_provider_calls": maximum_provider_calls,
        "maximum_attempts_per_turn": MAX_ATTEMPTS_PER_TURN,
        "base_call_count": required_base_calls,
        "provider_call_count": len(calls),
        "successful_provider_call_count": len(calls),
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "maximum_cost_nano_usd": maximum_cost_nano,
        "maximum_cost_usd": maximum_cost_usd,
        "actual_usage_cost_nano_usd": total_actual,
        "actual_usage_cost_usd": _usd_from_nano(total_actual),
        "guarded_cost_nano_usd": total_actual,
        "guarded_cost_usd": _usd_from_nano(total_actual),
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
    base_count = sum(1 for call in calls if call.get("attempt_kind") == "base")
    if base_count != required_base_calls or budget.get("pricing") != pricing:
        raise ValueError("ledger terminal pricing/base evidence drift")
    if any(
        type(budget.get(key)) is not type(value) or budget.get(key) != value
        for key, value in scalars.items()
    ):
        raise ValueError("ledger terminal scalar evidence drift")


class AtomicOpenAICallLedger(V26AtomicOpenAICallLedger):
    """Current exact-accounting ledger used by successor evaluators."""

    __slots__ = ()

    ledger_schema_version = LEDGER_SCHEMA_VERSION
