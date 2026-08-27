"""Stage 9 Ollama adapter contracts without requiring a daemon."""

import asyncio
import json
from datetime import UTC, datetime
from typing import Self
from urllib.request import Request

import pytest

from satori.core.models import (
    ModelFormationProviderError,
    ModelFormationRequest,
    ModelSourceMessage,
)
from satori.infrastructure.providers.ollama_models import OllamaModelFormationAdapter


class FakeHttpResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self.body


def request() -> ModelFormationRequest:
    return ModelFormationRequest(
        schema_version=1,
        trace_id="trace-model-adapter",
        source_interaction_id="interaction-1",
        source_message_id="message-1",
        identity_id="identity-1",
        counterparty_id="person-a",
        formation_version=1,
        max_user_claims=4,
        max_world_claims=4,
        messages=(
            ModelSourceMessage(
                message_id="message-1",
                interaction_id="interaction-1",
                identity_id="identity-1",
                counterparty_id="person-a",
                observed_at=datetime(2026, 8, 22, tzinfo=UTC),
                content="Меня зовут Алексей. Проект Сатори теперь активен.",
            ),
        ),
    )


def adapter() -> OllamaModelFormationAdapter:
    return OllamaModelFormationAdapter(
        base_url="http://127.0.0.1:11434",
        model="qwen3:4b-instruct",
        timeout_seconds=30.0,
        max_output_tokens=512,
    )


def valid_proposal() -> dict[str, object]:
    return {
        "schema_version": 1,
        "user_claims": [
            {
                "predicate": "display_name",
                "value_kind": "text",
                "text_value": "Алексей",
                "number_value": None,
                "boolean_value": None,
                "epistemic_kind": "explicit_fact",
                "confidence": 0.9,
                "evidence": [{"message_id": "message-1", "quote": "Меня зовут Алексей."}],
                "corrects_claim_id": None,
            }
        ],
        "world_claims": [
            {
                "subject_kind": "project",
                "subject_label": "Сатори",
                "predicate": "status",
                "value_kind": "text",
                "text_value": "active",
                "number_value": None,
                "boolean_value": None,
                "epistemic_kind": "explicit_fact",
                "confidence": 0.9,
                "evidence": [
                    {
                        "message_id": "message-1",
                        "quote": "Проект Сатори теперь активен.",
                    }
                ],
                "corrects_claim_id": None,
            }
        ],
    }


def test_adapter_uses_untrusted_strict_schema_and_maps_both_owner_proposals(
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

    monkeypatch.setattr("satori.infrastructure.providers.ollama_models.urlopen", fake_urlopen)
    result = asyncio.run(adapter().generate_structured(request()))

    http_request = captured["request"]
    assert isinstance(http_request, Request)
    assert isinstance(http_request.data, bytes)
    payload = json.loads(http_request.data.decode())
    assert payload["stream"] is False
    assert payload["think"] is False
    assert payload["options"] == {"temperature": 0.0, "num_predict": 512}
    assert payload["format"]["type"] == "object"
    assert "UNTRUSTED DATA" in payload["messages"][0]["content"]
    assert "at most 4 user claims and 4 world claims" in payload["messages"][0]["content"]
    assert "Меня зовут Алексей" in payload["messages"][1]["content"]
    assert captured["timeout"] == 30.0
    assert result.proposal.user_claims[0].value == "Алексей"
    assert result.proposal.world_claims[0].value == "active"
    assert result.proposal.world_claims[0].subject_kind == "project"


@pytest.mark.parametrize(
    "mutation",
    [
        {"predicate": "medical_diagnosis"},
        {"epistemic_kind": "fact"},
        {"extra_write_instruction": "persist this"},
    ],
)
def test_adapter_rejects_out_of_registry_or_extra_provider_output(
    monkeypatch: pytest.MonkeyPatch,
    mutation: dict[str, object],
) -> None:
    proposal = valid_proposal()
    assert isinstance(proposal["user_claims"], list)
    proposal["user_claims"][0].update(mutation)
    body = json.dumps(
        {
            "model": "qwen3:4b-instruct",
            "message": {"role": "assistant", "content": json.dumps(proposal)},
            "done": True,
        }
    ).encode()

    monkeypatch.setattr(
        "satori.infrastructure.providers.ollama_models.urlopen",
        lambda _request, *, timeout: FakeHttpResponse(body),
    )
    with pytest.raises(ModelFormationProviderError):
        asyncio.run(adapter().generate_structured(request()))
