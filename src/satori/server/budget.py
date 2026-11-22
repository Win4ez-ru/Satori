"""Per-attempt budget admission at the actual all-role OpenAI HTTP boundary.

RUB amounts are conservative tariff/FX estimates, never bank invoices. Every
transport attempt consumes its own durable reservation before network dispatch.
"""

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from satori.application.source_policy.dispatch import admit_provider_dispatch
from satori.infrastructure.providers.openai_http import OpenAIHttpStatusError, OpenAITransportError
from satori.infrastructure.providers.openai_structured import (
    OpenAIStructuredTransport,
    bounded_json,
)
from satori.server.usage import AttemptSpec, Model, Provider, Role, UsageError, UsageLedger

MAX_REQUEST_BYTES = 200_000
_INPUT_OVERHEAD = 4096


class OpenAIBudgetError(OpenAITransportError):
    """Closed metadata; no provider body, text, key or exception strings."""

    def __init__(
        self,
        code: Literal[
            "request_rejected",
            "price_expired",
            "budget_rejected",
            "dispatch_unknown",
            "usage_unknown",
            "accounting_unknown",
        ],
    ) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PriceAssumptions:
    """Explicit short-context Standard rates and a finite operator-selected FX bound.

    The FX bound is an assumption, not a current exchange quote. An expired tariff
    review stops dispatch. Only the closed Luna/Terra/embedding candidates are
    priced; no paid tool or long-context tier. Input covers the highest documented2.5x
    cache-write factor; no unproven cache discount is applied.
    """

    rub_per_usd_ceiling: Decimal
    reviewed_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        value = self.rub_per_usd_ceiling
        if (
            type(value) is not Decimal
            or not value.is_finite()
            or not 1 <= value <= 1000
            or self.reviewed_at.tzinfo is None
            or self.expires_at.tzinfo is None
            or self.reviewed_at.utcoffset() is None
            or self.expires_at.utcoffset() is None
            or not timedelta(0) < self.expires_at - self.reviewed_at <= timedelta(days=90)
        ):
            raise ValueError("invalid_price_assumptions")

    def estimate_micro_rub(
        self,
        *,
        embedding: bool,
        input_tokens: int,
        output_tokens: int,
        model: Model | None = None,
    ) -> int:
        if any(
            type(n) is not int or not 0 <= n <= 1_000_000_000 for n in (input_tokens, output_tokens)
        ):
            raise OpenAIBudgetError("usage_unknown")
        # None preserves the existing public Luna/embedding estimate calls. The
        # actual HTTP owner always supplies its exact, immutable role-bound model.
        selected = (
            (Model.TEXT_EMBEDDING_3_SMALL if embedding else Model.GPT_6_LUNA)
            if model is None
            else model
        )
        if type(embedding) is not bool or not isinstance(selected, Model):
            raise OpenAIBudgetError("usage_unknown")
        if embedding:
            if selected is not Model.TEXT_EMBEDDING_3_SMALL or output_tokens:
                raise OpenAIBudgetError("usage_unknown")
            input_price, output_price = Decimal("0.02"), Decimal(0)
        elif selected is Model.GPT_6_LUNA:
            input_price, output_price = Decimal("0.10") * Decimal("2.5"), Decimal("0.50")
        elif selected is Model.GPT_6_1_TERRA:
            input_price, output_price = Decimal("1.00") * Decimal("2.5"), Decimal("5.00")
        else:
            raise OpenAIBudgetError("usage_unknown")
        # Per-million USD * micro-RUB cancels the million denominator exactly.
        amount = (
            input_tokens * input_price + output_tokens * output_price
        ) * self.rub_per_usd_ceiling
        return max(1, int(amount.to_integral_value(rounding=ROUND_CEILING)))


class BudgetedOpenAITransport:
    """One exact role over the shared credential-safe client and usage owner."""

    def __init__(
        self,
        transport: OpenAIStructuredTransport,
        ledger: UsageLedger,
        role: Role,
        prices: PriceAssumptions,
        *,
        clock: Callable[[], datetime] | None = None,
        model: Model | None = None,
    ) -> None:
        if not isinstance(role, Role) or role in {Role.STT, Role.TTS}:
            raise ValueError("unsupported_budget_role")
        selected = (
            (Model.TEXT_EMBEDDING_3_SMALL if role is Role.EMBEDDING else Model.GPT_6_LUNA)
            if model is None
            else model
        )
        allowed = (
            {Model.TEXT_EMBEDDING_3_SMALL}
            if role is Role.EMBEDDING
            else {Model.GPT_6_LUNA, Model.GPT_6_1_TERRA}
            if role is Role.CONVERSATION
            else {Model.GPT_6_LUNA}
        )
        if not isinstance(selected, Model) or selected not in allowed:
            raise ValueError("unsupported_budget_model_for_role")
        self._transport = transport
        self._ledger = ledger
        self._role = role
        self._model = selected
        self._prices = prices
        self._clock = clock or (lambda: datetime.now(UTC))

    def _request(self, path: str, payload: Mapping[str, Any]) -> tuple[dict[str, Any], int, int]:
        try:
            body = json.dumps(dict(payload), ensure_ascii=False, allow_nan=False).encode("utf-8")
            document = bounded_json(body, MAX_REQUEST_BYTES)
            if not isinstance(document, dict):
                raise ValueError
            embedding = self._role == Role.EMBEDDING
            model = {
                Model.TEXT_EMBEDDING_3_SMALL: "text-embedding-3-small",
                Model.GPT_6_LUNA: "gpt-6-luna",
                Model.GPT_6_1_TERRA: "gpt-6.1-terra",
            }[self._model]
            if document.get("model") != model or path != (
                "/embeddings" if embedding else "/responses"
            ):
                raise ValueError
            if embedding:
                if set(document) - {"model", "input", "dimensions", "encoding_format"}:
                    raise ValueError
                texts = document.get("input")
                if (
                    type(texts) is not list
                    or not 1 <= len(texts) <= 32
                    or any(
                        type(t) is not str or not t.strip() or len(t.encode("utf-8")) > 8192
                        for t in texts
                    )
                    or document.get("encoding_format") != "float"
                    or type(document.get("dimensions")) is not int
                    or not 1 <= document["dimensions"] <= 1536
                ):
                    raise ValueError
                output: object = 0
            else:
                allowed = {
                    "model",
                    "input",
                    "max_output_tokens",
                    "reasoning",
                    "service_tier",
                    "store",
                    "background",
                    "text",
                    "temperature",
                    "prompt_cache_options",
                    "stream",
                    "truncation",
                }
                messages = document.get("input")
                output = document.get("max_output_tokens")
                if (
                    set(document) - allowed
                    or document.get("store") is not False
                    or document.get("background", False) is not False
                    or document.get("service_tier") != "default"
                    or document.get("stream", False) is not False
                    or document.get("truncation", "disabled") != "disabled"
                    or document.get("reasoning") != {"effort": "none"}
                    or type(output) is not int
                    or not 1 <= output <= 8192
                    or type(messages) is not list
                    or not 1 <= len(messages) <= 64
                ):
                    raise ValueError
                for message in messages:
                    if (
                        type(message) is not dict
                        or set(message) != {"role", "content"}
                        or message["role"] not in {"system", "developer", "user", "assistant"}
                        or type(message["content"]) is not str
                        or not message["content"].strip()
                    ):
                        raise ValueError
                if document.get("prompt_cache_options", {"mode": "explicit"}) != {
                    "mode": "explicit"
                }:
                    raise ValueError
            # The full UTF8 wire plus framing reserve deliberately overestimates
            # actual BPE input. This remains a tariff estimate, not invoice proof.
            reserved_input = len(body) + _INPUT_OVERHEAD
            if reserved_input > 272_000:
                raise ValueError
            return document, reserved_input, cast(int, output)
        except (TypeError, ValueError, UnicodeError, RecursionError, KeyError):
            raise OpenAIBudgetError("request_rejected") from None

    def post_json(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        if (
            type(timeout_seconds) not in {int, float}
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 600
            or type(max_response_bytes) is not int
            or not 1 <= max_response_bytes <= 16_000_000
        ):
            raise OpenAIBudgetError("request_rejected")
        now = self._clock()
        if (
            now.tzinfo is None
            or now.utcoffset() is None
            or not self._prices.reviewed_at <= now < self._prices.expires_at
        ):
            raise OpenAIBudgetError("price_expired")
        document, input_bound, output_bound = self._request(path, payload)
        embedding = self._role == Role.EMBEDDING
        amount = self._prices.estimate_micro_rub(
            embedding=embedding,
            input_tokens=input_bound,
            output_tokens=output_bound,
            model=self._model,
        )
        attempt = uuid4()
        try:
            self._ledger.reserve(
                AttemptSpec(
                    attempt_id=attempt,
                    provider=Provider.OPENAI,
                    model=self._model,
                    role=self._role,
                    reservation_micro_rub=amount,
                )
            )
        except UsageError:
            raise OpenAIBudgetError("budget_rejected") from None
        try:
            admit_provider_dispatch()
        except Exception:
            try:
                self._ledger.cancel_before_dispatch(attempt)
            except UsageError:
                raise OpenAIBudgetError("accounting_unknown") from None
            raise
        try:
            permit = self._ledger.begin_dispatch(attempt)
            if not permit.dispatch_permitted:
                raise UsageError("invalid_transition")
        except UsageError:
            raise OpenAIBudgetError("accounting_unknown") from None
        try:
            body = self._transport.post_json(
                path,
                document,
                timeout_seconds=timeout_seconds,
                max_response_bytes=max_response_bytes,
            )
        except OpenAIHttpStatusError:
            self._mark_unknown(attempt)
            raise
        except Exception:
            self._mark_unknown(attempt)
            raise OpenAIBudgetError("dispatch_unknown") from None
        try:
            response = bounded_json(body, max_response_bytes)
            if not isinstance(response, dict) or response.get("model") != document["model"]:
                raise ValueError
            if not embedding and response.get("service_tier") != "default":
                raise ValueError
            usage = response.get("usage")
            if type(usage) is not dict:
                raise ValueError
            input_tokens = usage.get("prompt_tokens" if embedding else "input_tokens")
            output_tokens = 0 if embedding else usage.get("output_tokens")
            if any(
                type(n) is not int or not 0 <= n <= 1_000_000_000
                for n in (input_tokens, output_tokens)
            ):
                raise ValueError
            input_count = cast(int, input_tokens)
            output_count = cast(int, output_tokens)
            total = usage.get("total_tokens")
            if type(total) is not int or total != input_count + output_count:
                raise ValueError
            for name, keys, maximum in (
                ("input_tokens_details", ("cached_tokens", "cache_write_tokens"), input_count),
                ("output_tokens_details", ("reasoning_tokens",), output_count),
            ):
                details = usage.get(name)
                if details is not None:
                    if type(details) is not dict:
                        raise ValueError
                    for key in keys:
                        count = details.get(key)
                        if count is not None and (
                            type(count) is not int or not 0 <= count <= maximum
                        ):
                            raise ValueError
            charge = self._prices.estimate_micro_rub(
                embedding=embedding,
                input_tokens=input_count,
                output_tokens=output_count,
                model=self._model,
            )
        except (TypeError, ValueError, KeyError, RecursionError):
            self._mark_unknown(attempt)
            raise OpenAIBudgetError("usage_unknown") from None
        try:
            self._ledger.settle_report(
                attempt, charge, input_tokens=input_count, output_tokens=output_count
            )
        except UsageError:
            raise OpenAIBudgetError("accounting_unknown") from None
        return body

    def _mark_unknown(self, attempt: UUID) -> None:
        try:
            self._ledger.mark_unknown(attempt)
        except UsageError:
            raise OpenAIBudgetError("accounting_unknown") from None
