"""Ollama structured episode adapter contracts without a daemon."""

import asyncio
import json
from typing import Self
from urllib.request import Request

import pytest

from satori.core.conversation import ConversationMessageRole
from satori.core.episode import (
    EpisodeFormationProviderError,
    EpisodeFormationRequest,
    EpisodeSourceMessage,
)
from satori.infrastructure.providers.ollama_episode import OllamaEpisodeFormationAdapter


class FakeHttpResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self.body


def request() -> EpisodeFormationRequest:
    from datetime import UTC, datetime

    return EpisodeFormationRequest(
        schema_version=1,
        trace_id="trace-episode-adapter",
        interaction_id="interaction-1",
        occurred_at=datetime(2026, 7, 28, tzinfo=UTC),
        formation_version=1,
        messages=(
            EpisodeSourceMessage(
                message_id="message-user",
                role=ConversationMessageRole.USER,
                content="Я впервые запустил проект.",
            ),
            EpisodeSourceMessage(
                message_id="message-assistant",
                role=ConversationMessageRole.ASSISTANT,
                content="Поздравляю.",
            ),
        ),
    )


def adapter() -> OllamaEpisodeFormationAdapter:
    return OllamaEpisodeFormationAdapter(
        base_url="http://127.0.0.1:11434",
        model="qwen3:4b-instruct",
        timeout_seconds=30.0,
        max_output_tokens=512,
    )


def test_ollama_episode_adapter_uses_schema_and_maps_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    proposal = {
        "schema_version": 1,
        "should_create": True,
        "summary": "Пользователь впервые запустил проект.",
        "importance": 0.8,
        "confidence": 0.95,
        "evidence": [{"message_id": "message-user", "quote": "Я впервые запустил проект."}],
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
        "satori.infrastructure.providers.ollama_episode.urlopen",
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
    assert payload["options"] == {"temperature": 0.0, "num_predict": 512}
    assert payload["format"]["type"] == "object"
    assert "Я впервые запустил проект." in payload["messages"][1]["content"]
    assert payload["messages"][0]["role"] == "system"
    assert captured["timeout"] == 30.0
    assert result.proposal.summary == "Пользователь впервые запустил проект."
    assert result.proposal.evidence[0].message_id == "message-user"


@pytest.mark.parametrize(
    "body",
    [
        b"{",
        json.dumps(
            {
                "model": "qwen3:4b-instruct",
                "message": {"role": "assistant", "content": "{}"},
                "done": True,
            }
        ).encode(),
        json.dumps(
            {
                "model": "qwen3:4b-instruct",
                "message": {"role": "assistant", "content": "{}"},
                "done": False,
            }
        ).encode(),
    ],
)
def test_ollama_episode_adapter_rejects_malformed_results(
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
) -> None:
    def fake_urlopen(_request: Request, *, timeout: float) -> FakeHttpResponse:
        assert timeout == 30.0
        return FakeHttpResponse(body)

    monkeypatch.setattr(
        "satori.infrastructure.providers.ollama_episode.urlopen",
        fake_urlopen,
    )

    with pytest.raises(EpisodeFormationProviderError):
        asyncio.run(adapter().generate_structured(request()))
