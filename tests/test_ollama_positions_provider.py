"""Stage 11 Ollama position adapter contracts without requiring a daemon."""

import asyncio
import json
from datetime import UTC, datetime
from typing import Self
from urllib.request import Request

import pytest

from satori.core.positions import (
    PositionFormationProviderError,
    PositionFormationRequest,
    PositionSourceMessage,
    PositionValueReference,
)
from satori.infrastructure.providers.ollama_positions import OllamaPositionFormationAdapter


class FakeHttpResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self.body


def formation_request() -> PositionFormationRequest:
    return PositionFormationRequest(
        schema_version=1,
        trace_id="trace-position-adapter",
        source_interaction_id="interaction-2",
        source_message_id="message-2",
        identity_id="identity-1",
        formation_version=1,
        max_positions=3,
        messages=(
            PositionSourceMessage(
                "message-2",
                "interaction-2",
                "identity-1",
                "person-b",
                datetime(2026, 8, 22, 13, tzinfo=UTC),
                "Данные проверки показывают меньше ошибок.",
            ),
            PositionSourceMessage(
                "message-1",
                "interaction-1",
                "identity-1",
                "person-a",
                datetime(2026, 8, 22, 12, tzinfo=UTC),
                "Это важно, потому что основания можно проверить.",
            ),
        ),
        current_positions=(),
        values=(PositionValueReference("intellectual_honesty", "Проверяемые основания"),),
    )


def adapter() -> OllamaPositionFormationAdapter:
    return OllamaPositionFormationAdapter(
        base_url="http://127.0.0.1:11434",
        model="qwen3:4b-instruct",
        timeout_seconds=30,
        max_output_tokens=640,
    )


def valid_proposal() -> dict[str, object]:
    return {
        "schema_version": 1,
        "positions": [
            {
                "proposition": "Проверяемые основания улучшают решения",
                "kind": "opinion",
                "stance": "support",
                "confidence": 0.8,
                "evidence": [
                    {
                        "message_id": "message-1",
                        "quote": "Это важно, потому что основания можно проверить.",
                        "role": "argument",
                    },
                    {
                        "message_id": "message-2",
                        "quote": "Данные проверки показывают меньше ошибок.",
                        "role": "observation",
                    },
                ],
                "value_key": "intellectual_honesty",
                "revises_position_id": None,
                "opposes_position_id": None,
                "challenges_position_id": None,
                "expected_target_version": None,
            }
        ],
    }


def test_adapter_uses_strict_untrusted_schema_and_maps_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    body = json.dumps(
        {
            "model": "qwen3:4b-instruct",
            "message": {"role": "assistant", "content": json.dumps(valid_proposal())},
            "done": True,
        }
    ).encode()

    def fake_urlopen(http_request: Request, *, timeout: float) -> FakeHttpResponse:
        captured["request"] = http_request
        captured["timeout"] = timeout
        return FakeHttpResponse(body)

    monkeypatch.setattr("satori.infrastructure.providers.ollama_positions.urlopen", fake_urlopen)
    result = asyncio.run(adapter().generate_structured(formation_request()))

    http_request = captured["request"]
    assert isinstance(http_request, Request)
    assert isinstance(http_request.data, bytes)
    payload = json.loads(http_request.data.decode())
    assert payload["stream"] is False
    assert payload["think"] is False
    assert payload["options"] == {"temperature": 0.0, "num_predict": 640}
    assert "UNTRUSTED DATA" in payload["messages"][0]["content"]
    assert "Never propose fact" in payload["messages"][0]["content"]
    assert captured["timeout"] == 30
    assert result.proposal.positions[0].value_key == "intellectual_honesty"
    assert result.proposal.positions[0].evidence[1].role.value == "observation"


@pytest.mark.parametrize(
    "mutation",
    [
        {"kind": "fact"},
        {"stance": "agreement"},
        {"kind": "belief"},
        {"extra_write_instruction": "persist this"},
    ],
)
def test_adapter_rejects_fact_invalid_enum_invalid_semantics_and_extra_output(
    monkeypatch: pytest.MonkeyPatch,
    mutation: dict[str, object],
) -> None:
    proposal = valid_proposal()
    assert isinstance(proposal["positions"], list)
    proposal["positions"][0].update(mutation)
    body = json.dumps(
        {
            "model": "qwen3:4b-instruct",
            "message": {"role": "assistant", "content": json.dumps(proposal)},
            "done": True,
        }
    ).encode()
    monkeypatch.setattr(
        "satori.infrastructure.providers.ollama_positions.urlopen",
        lambda _request, *, timeout: FakeHttpResponse(body),
    )

    with pytest.raises(PositionFormationProviderError):
        asyncio.run(adapter().generate_structured(formation_request()))
