"""Ollama structured semantic adapter contracts without a daemon."""

import asyncio
import json
from datetime import UTC, datetime
from typing import Self
from urllib.request import Request

import pytest

from satori.core.semantic import (
    SemanticFormationProviderError,
    SemanticFormationRequest,
    SemanticSourceEvidence,
    SemanticSourceMemory,
)
from satori.infrastructure.providers.ollama_semantic import OllamaSemanticFormationAdapter


class FakeHttpResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self.body


def request() -> SemanticFormationRequest:
    return SemanticFormationRequest(
        schema_version=1,
        trace_id="trace-semantic-adapter",
        source_memory_id="memory-1",
        formation_version=1,
        max_claims=4,
        memories=(
            SemanticSourceMemory(
                memory_id="memory-1",
                source_interaction_id="interaction-1",
                occurred_at=datetime(2026, 7, 30, tzinfo=UTC),
                summary="Пользователя зовут Алексей.",
                evidence=(
                    SemanticSourceEvidence(
                        memory_evidence_id="memory-evidence-1",
                        source_message_id="message-user-1",
                        quote="Меня зовут Алексей.",
                    ),
                ),
            ),
        ),
    )


def adapter() -> OllamaSemanticFormationAdapter:
    return OllamaSemanticFormationAdapter(
        base_url="http://127.0.0.1:11434",
        model="qwen3:4b-instruct",
        timeout_seconds=30.0,
        max_output_tokens=768,
    )


def test_ollama_semantic_adapter_uses_strict_schema_and_maps_typed_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    proposal = {
        "schema_version": 1,
        "claims": [
            {
                "subject": "user",
                "predicate": "name",
                "value_kind": "text",
                "text_value": "Алексей",
                "number_value": None,
                "boolean_value": None,
                "polarity": True,
                "claim_kind": "explicit_fact",
                "confidence": 0.97,
                "evidence_memory_ids": ["memory-1"],
                "valid_from": None,
                "valid_until": None,
                "corrects_claim_id": None,
            }
        ],
    }
    body = json.dumps(
        {
            "model": "qwen3:4b-instruct",
            "message": {"role": "assistant", "content": json.dumps(proposal)},
            "done": True,
        }
    ).encode()

    def fake_urlopen(http_request: Request, *, timeout: float) -> FakeHttpResponse:
        captured["request"] = http_request
        captured["timeout"] = timeout
        return FakeHttpResponse(body)

    monkeypatch.setattr(
        "satori.infrastructure.providers.ollama_semantic.urlopen",
        fake_urlopen,
    )

    result = asyncio.run(adapter().generate_structured(request()))
    http_request = captured["request"]
    assert isinstance(http_request, Request)
    assert isinstance(http_request.data, bytes)
    payload = json.loads(http_request.data.decode())
    assert payload["stream"] is False
    assert payload["think"] is False
    assert payload["keep_alive"] == "5m"
    assert payload["options"] == {"temperature": 0.0, "num_predict": 768}
    assert payload["format"]["type"] == "object"
    assert "Меня зовут Алексей." in payload["messages"][1]["content"]
    assert "untrusted data" in payload["messages"][0]["content"]
    assert captured["timeout"] == 30.0
    assert result.proposal.claims[0].value == "Алексей"
    assert result.proposal.claims[0].evidence_memory_ids == ("memory-1",)


@pytest.mark.parametrize(
    "proposal",
    [
        {},
        {
            "schema_version": 1,
            "claims": [
                {
                    "subject": "user",
                    "predicate": "name",
                    "value_kind": "text",
                    "text_value": None,
                    "number_value": 42.0,
                    "boolean_value": None,
                    "polarity": True,
                    "claim_kind": "explicit_fact",
                    "confidence": 0.9,
                    "evidence_memory_ids": ["memory-1"],
                    "valid_from": None,
                    "valid_until": None,
                    "corrects_claim_id": None,
                }
            ],
        },
    ],
)
def test_ollama_semantic_adapter_rejects_malformed_or_mistyped_claims(
    monkeypatch: pytest.MonkeyPatch,
    proposal: object,
) -> None:
    body = json.dumps(
        {
            "model": "qwen3:4b-instruct",
            "message": {"role": "assistant", "content": json.dumps(proposal)},
            "done": True,
        }
    ).encode()

    def fake_urlopen(_request: Request, *, timeout: float) -> FakeHttpResponse:
        assert timeout == 30.0
        return FakeHttpResponse(body)

    monkeypatch.setattr(
        "satori.infrastructure.providers.ollama_semantic.urlopen",
        fake_urlopen,
    )

    with pytest.raises(SemanticFormationProviderError):
        asyncio.run(adapter().generate_structured(request()))
