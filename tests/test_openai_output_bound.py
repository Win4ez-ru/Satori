"""Opt-in output resource floor, preserved local policy and actual budget boundary."""

import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from satori.config import Settings
from satori.core.conversation import ConversationProviderFailureReason, GenerationFailed
from satori.desktop.backend import DesktopBackend
from satori.server.budget import BudgetedOpenAITransport, PriceAssumptions
from satori.server.openai_runtime import (
    OPENAI_OUTPUT_TOKEN_LIMIT_FLOOR,
    OPENAI_ROLES,
    build_openai_providers,
    build_openai_settings,
)
from satori.server.usage import AttemptState, Role
from tests.test_openai_all_role_integration import (
    NOW,
    PERIOD,
    CanonicalFixtureHTTP,
    RecordingLedger,
)
from tests.test_stage81_conversation_context import _builder

REPOSITORY = Path(__file__).resolve().parents[1]


def correction() -> str:
    corpus = json.loads(
        (REPOSITORY / "tests/fixtures/stage81_dialogue_coherence_v2.json").read_text()
    )
    return str(corpus["turns"][8]["user_text"])


def test_default_local_56_preserved_and_opt_in_floor_changes_no_prompt_messages() -> None:
    builder, context = _builder()
    local, _ = builder.build(context, user_text=correction(), trace_id="local-56")
    with_floor, _ = replace(builder, output_token_limit_floor=128).build(
        context, user_text=correction(), trace_id="explicit-resource-floor"
    )
    assert builder.output_token_limit_floor == 0
    assert local.parameters.max_output_tokens == 56
    assert with_floor.parameters.max_output_tokens == 128
    assert with_floor.messages == local.messages
    assert with_floor.parameters.temperature == local.parameters.temperature
    assert with_floor.parameters.schema_version == local.parameters.schema_version


@pytest.mark.parametrize(
    ("limit", "expected"), [(32, 128), (56, 128), (384, 384), (768, 768), (900, 768)]
)
def test_floor_preserves_larger_caps_and_never_exceeds_global(limit: int, expected: int) -> None:
    builder, _ = _builder()
    bounded = replace(builder, output_token_limit_floor=128)
    assert bounded._apply_output_limit_floor(limit) == expected


@pytest.mark.parametrize("floor", [-1, True, 128.0, 769])
def test_builder_floor_must_be_an_integer_within_global(floor: Any) -> None:
    builder, _ = _builder()
    with pytest.raises(ValueError, match="output token limit floor must fit"):
        replace(builder, output_token_limit_floor=floor)


def test_floor_above_configured_global_is_rejected_instead_of_silently_clamped() -> None:
    builder, _ = _builder()
    with pytest.raises(ValueError, match="output token limit floor must fit"):
        replace(builder, max_output_tokens=64, output_token_limit_floor=128)


def test_configuration_defaults_local_to_zero_and_factory_explicitly_selects_128(
    tmp_path: Path,
) -> None:
    defaults = {
        name: field.get_default(call_default_factory=True)
        for name, field in Settings.model_fields.items()
    }
    local = Settings(_env_file=None, **defaults)  # type: ignore[call-arg]
    assert local.conversation_output_token_limit_floor == 0
    selected = build_openai_settings(tmp_path.resolve(), SecretStr("synthetic-key"))
    assert selected.conversation_output_token_limit_floor == OPENAI_OUTPUT_TOKEN_LIMIT_FLOOR == 128
    assert selected.conversation_max_output_tokens == 768
    defaults["conversation_output_token_limit_floor"] = 128
    with pytest.raises(ValidationError, match="requires_all_openai_mode"):
        Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "change",
    [
        {"conversation_max_output_tokens": 769},
        {"conversation_max_output_tokens": 64},
        {"conversation_output_token_limit_floor": 769},
        {"conversation_output_token_limit_floor": True},
        {"conversation_output_token_limit_floor": 128.0},
    ],
)
def test_all_openai_numeric_configuration_rejects_unapproved_global_or_invalid_floor(
    tmp_path: Path,
    change: dict[str, object],
) -> None:
    values = build_openai_settings(tmp_path.resolve(), SecretStr("synthetic-key")).model_dump()
    values.update(change)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)  # type: ignore[call-arg]


class OutputObservedHTTP(CanonicalFixtureHTTP):
    def __init__(
        self,
        role: Role,
        ledger: RecordingLedger,
        prices: PriceAssumptions,
        *,
        incomplete: bool,
        trigger_retry: bool = False,
    ) -> None:
        super().__init__(role, ledger)
        self.prices = prices
        self.incomplete = incomplete
        self.trigger_retry = trigger_retry
        self.observed_caps: list[int] = []

    def post_json(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        if self.role == Role.CONVERSATION:
            cap = payload["max_output_tokens"]
            self.observed_caps.append(cap)
            rows = [
                row
                for row in self.ledger.records()
                if row.spec.role == self.role and row.state == AttemptState.DISPATCHED
            ]
            assert len(rows) == 1
            request_bytes = json.dumps(dict(payload), ensure_ascii=False, allow_nan=False).encode()
            expected = self.prices.estimate_micro_rub(
                embedding=False,
                input_tokens=len(request_bytes) + 4096,
                output_tokens=cap,
            )
            assert rows[0].spec.reservation_micro_rub == expected
            assert expected > self.prices.estimate_micro_rub(
                embedding=False,
                input_tokens=len(request_bytes) + 4096,
                output_tokens=56,
            )
        body = super().post_json(
            path, payload, timeout_seconds=timeout_seconds, max_response_bytes=max_response_bytes
        )
        if self.role == Role.CONVERSATION and self.incomplete:
            response = json.loads(body)
            response["status"] = "incomplete"
            response["incomplete_details"] = {"reason": "max_output_tokens"}
            response["usage"]["output_tokens"] = payload["max_output_tokens"]
            response["usage"]["total_tokens"] = 100 + payload["max_output_tokens"]
            return json.dumps(response).encode()
        if self.role == Role.CONVERSATION and self.trigger_retry:
            response = json.loads(body)
            response["output"][0]["content"][0]["text"] = "Я готов."
            return json.dumps(response).encode()
        return body


@pytest.mark.parametrize(
    ("incomplete", "trigger_retry"), [(False, False), (True, False), (False, True)]
)
def test_actual_composition_budget_and_wire_use_128_without_accepting_partial_output_or_retry(
    tmp_path: Path,
    incomplete: bool,
    trigger_retry: bool,
) -> None:
    root = tmp_path.resolve() / "installation"
    root.mkdir(mode=0o700)
    selected = build_openai_settings(root, SecretStr("synthetic-key"))
    ledger = RecordingLedger(root.parent / "usage")
    ledger.configure_period(PERIOD, 800_000_000)
    prices = PriceAssumptions(Decimal(100), NOW - timedelta(hours=1), NOW + timedelta(days=1))
    raw = {
        role: OutputObservedHTTP(
            role, ledger, prices, incomplete=incomplete, trigger_retry=trigger_retry
        )
        for role in OPENAI_ROLES
    }
    transports = {
        role: BudgetedOpenAITransport(transport, ledger, role, prices, clock=lambda: NOW)
        for role, transport in raw.items()
    }
    providers = build_openai_providers(selected, transports)
    backend = DesktopBackend(root, REPOSITORY, providers=providers, runtime_settings=selected)
    backend.open()
    backend.activate()
    session = str(backend.new_session()["session_id"])

    async def scenario() -> None:
        try:
            if incomplete:
                with pytest.raises(GenerationFailed) as failed:
                    await backend.send_for_session(correction(), "floor-contract", session)
                assert failed.value.reason == ConversationProviderFailureReason.OUTPUT_TOKEN_LIMIT
                history = backend.history(session)["interactions"]
                assert isinstance(history, list)
                assert len(history) == 1
                assert history[0]["status"] == "failed"
                assert history[0]["assistant_text"] is None
            else:
                response = await backend.send_for_session(correction(), "floor-contract", session)
                # Exactly one existing validator retry; no rewrite or third call.
                assert response["text"] == ("Я готов." if trigger_retry else "Integration reply 1.")
                history = backend.history(session)["interactions"]
                assert isinstance(history, list)
                assert len(history) == 1
            calls = 2 if trigger_retry else 1
            assert raw[Role.CONVERSATION].observed_caps == [128] * calls
            assert raw[Role.CONVERSATION].calls == ["/responses"] * calls
        finally:
            await backend.close()

    try:
        asyncio.run(scenario())
        conversation_rows = [row for row in ledger.records() if row.spec.role == Role.CONVERSATION]
        assert len(conversation_rows) == (2 if trigger_retry else 1)
        assert all(row.state == AttemptState.SETTLED for row in conversation_rows)
    finally:
        ledger.close()
