"""Budget admission at the shared real adapter boundary, without paid calls."""

import copy
import json
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from satori.infrastructure.providers.openai_structured import OpenAIStructuredClient
from satori.server.budget import BudgetedOpenAITransport, OpenAIBudgetError, PriceAssumptions
from satori.server.usage import Role, UsageLedger
from tests.test_openai_structured import SCHEMA, envelope

NOW = datetime(2026, 10, 31, 19, tzinfo=UTC)


def test_review_window_is_finite_and_never_exceeds_ninety_days() -> None:
    assert PriceAssumptions(Decimal("150"), NOW, NOW + timedelta(days=90))
    for duration in (timedelta(0), timedelta(days=-1), timedelta(days=90, microseconds=1)):
        with pytest.raises(ValueError, match="invalid_price_assumptions"):
            PriceAssumptions(Decimal("150"), NOW, NOW + duration)


def request() -> dict[str, Any]:
    return {
        "model": "gpt-6-luna",
        "input": [{"role": "user", "content": "Synthetic input"}],
        "max_output_tokens": 100,
        "reasoning": {"effort": "none"},
        "service_tier": "default",
        "store": False,
    }


def response() -> dict[str, Any]:
    result = envelope()
    result["usage"] = {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}
    return result


class Transport:
    def __init__(self, document: object = None, *, error: Exception | None = None) -> None:
        self.document = response() if document is None else document
        self.error = error
        self.calls = 0

    def post_json(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return json.dumps(self.document).encode()


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[UsageLedger]:
    owner = UsageLedger(tmp_path / "usage", clock=lambda: NOW)
    owner.configure_period("2026-10", 1_000_000)
    yield owner
    owner.close()


def budget(
    ledger: UsageLedger, transport: Transport, role: Role = Role.CONVERSATION
) -> BudgetedOpenAITransport:
    return BudgetedOpenAITransport(
        transport,
        ledger,
        role,
        PriceAssumptions(Decimal("130"), NOW, NOW + timedelta(days=1)),
        clock=lambda: NOW,
    )


def post(client: BudgetedOpenAITransport, payload: Mapping[str, Any] | None = None) -> bytes:
    return client.post_json(
        "/responses",
        request() if payload is None else payload,
        timeout_seconds=3,
        max_response_bytes=1_000_000,
    )


def test_each_attempt_is_charged_once_including_repeated_transport_calls(
    ledger: UsageLedger,
) -> None:
    raw = Transport()
    client = budget(ledger, raw)
    assert post(client)
    first = ledger.snapshot("2026-10")
    assert first.unsettled_attempts == 0
    assert first.settled_reported_micro_rub == 1300
    assert post(client)
    assert raw.calls == 2
    assert ledger.snapshot("2026-10").settled_reported_micro_rub == 2600
    assert ledger.snapshot("2026-10").provider_invoice_verified is False


def test_actual_structured_wire_passes_through_budget_owner(ledger: UsageLedger) -> None:
    raw = Transport()
    result = OpenAIStructuredClient(budget(ledger, raw, Role.EPISODE)).generate(
        model="gpt-6-luna",
        messages=[{"role": "user", "content": "Synthetic input"}],
        schema=SCHEMA,
        schema_name="episode_v1",
        max_output_tokens=128,
        timeout_seconds=3,
    )
    assert result.model == "gpt-6-luna"
    assert raw.calls == 1
    assert ledger.snapshot("2026-10").settled_reported_micro_rub > 0


@pytest.mark.parametrize(
    "mutation",
    [
        {"model": "expensive-other-model"},
        {"service_tier": "priority"},
        {"store": True},
        {"background": True},
        {"tools": []},
        {"previous_response_id": "r"},
        {"reasoning": {"effort": "high"}},
        {"stream": True},
        {"truncation": "auto"},
        {"input": [{"role": "user", "content": "x" * 200_000}]},
        {"max_output_tokens": True},
    ],
)
def test_unknown_cost_or_disclosure_configuration_never_dispatches(
    ledger: UsageLedger,
    mutation: dict[str, object],
) -> None:
    raw = Transport()
    document = request() | mutation
    with pytest.raises(OpenAIBudgetError, match="request_rejected"):
        post(budget(ledger, raw), document)
    assert raw.calls == 0
    assert ledger.snapshot("2026-10").liability_micro_rub == 0


@pytest.mark.parametrize(
    "mutation",
    [
        {"usage": None},
        {"usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 99}},
        {"usage": {"input_tokens": True, "output_tokens": 2, "total_tokens": 3}},
        {
            "usage": {
                "input_tokens": 20,
                "output_tokens": 10,
                "total_tokens": 30,
                "output_tokens_details": {"reasoning_tokens": 11},
            }
        },
        {"service_tier": "priority"},
        {"model": "other-model"},
    ],
)
def test_invalid_usage_retains_unknown_reservation(
    ledger: UsageLedger,
    mutation: dict[str, object],
) -> None:
    raw = Transport(response() | mutation)
    with pytest.raises(OpenAIBudgetError, match="usage_unknown"):
        post(budget(ledger, raw))
    snapshot = ledger.snapshot("2026-10")
    assert raw.calls == 1
    assert snapshot.unsettled_attempts == 1
    assert snapshot.liability_micro_rub > 1300
    assert snapshot.settled_reported_micro_rub == 0


def test_timeout_does_not_release_liability_or_replay(ledger: UsageLedger) -> None:
    raw = Transport(error=TimeoutError("private-provider-body"))
    with pytest.raises(OpenAIBudgetError) as error:
        post(budget(ledger, raw))
    assert str(error.value) == "dispatch_unknown"
    assert raw.calls == 1
    assert ledger.snapshot("2026-10").unsettled_attempts == 1


def test_cap_rejects_before_http(tmp_path: Path) -> None:
    owner = UsageLedger(tmp_path / "usage", clock=lambda: NOW)
    owner.configure_period("2026-10", 1)
    raw = Transport()
    try:
        with pytest.raises(OpenAIBudgetError, match="budget_rejected"):
            post(budget(owner, raw))
        assert raw.calls == 0
    finally:
        owner.close()


def test_expired_rate_assumption_rejects_before_http(ledger: UsageLedger) -> None:
    raw = Transport()
    client = BudgetedOpenAITransport(
        raw,
        ledger,
        Role.CONVERSATION,
        PriceAssumptions(Decimal("130"), NOW - timedelta(days=1), NOW),
        clock=lambda: NOW,
    )
    with pytest.raises(OpenAIBudgetError, match="price_expired"):
        post(client)
    assert raw.calls == 0


def test_embedding_cost_uses_its_own_rate_and_usage_shape(ledger: UsageLedger) -> None:
    raw = Transport(
        {"model": "text-embedding-3-small", "usage": {"prompt_tokens": 20, "total_tokens": 20}}
    )
    client = budget(ledger, raw, Role.EMBEDDING)
    assert client.post_json(
        "/embeddings",
        {
            "model": "text-embedding-3-small",
            "input": ["Synthetic input"],
            "dimensions": 1536,
            "encoding_format": "float",
        },
        timeout_seconds=3,
        max_response_bytes=1_000_000,
    )
    assert ledger.snapshot("2026-10").settled_reported_micro_rub == 52


def test_refused_proposal_with_valid_usage_still_costs_money(ledger: UsageLedger) -> None:
    document = copy.deepcopy(response())
    document["output"] = [
        {"type": "message", "role": "assistant", "content": [{"type": "refusal", "refusal": "No"}]}
    ]
    raw = Transport(document)
    assert post(budget(ledger, raw))
    assert ledger.snapshot("2026-10").settled_reported_micro_rub > 0
