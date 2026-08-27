"""Ollama Stage 7 structured-appraisal adapter contracts without a daemon."""

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Self
from urllib.request import Request

import pytest

from satori.core.affect import (
    AffectiveAppraisalProviderError,
    AffectiveAppraisalRequest,
    AppraisalFastState,
    AppraisalMoodState,
    AppraisalTrait,
    AppraisalValue,
)
from satori.infrastructure.providers.ollama_affect import (
    APPRAISAL_METHOD,
    OllamaAffectiveAppraisalAdapter,
)


class FakeHttpResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self.body


def appraisal_request() -> AffectiveAppraisalRequest:
    return AffectiveAppraisalRequest(
        schema_version=1,
        trace_id="trace-affect-adapter",
        interaction_id="interaction-1",
        appraised_at=datetime(2026, 7, 30, tzinfo=UTC),
        user_content="I finished an important piece of work successfully today.",
        traits=(AppraisalTrait("curiosity", 0.8),),
        values=(AppraisalValue("growth", 0.9, "Развитие через понимание."),),
        fast_state=AppraisalFastState(
            valence=0.0,
            arousal=0.12,
            tension=0.08,
            curiosity=0.18,
            interest=0.16,
            amusement=0.05,
            concern=0.08,
            frustration=0.04,
            situational_confidence=0.55,
        ),
        mood_state=AppraisalMoodState(valence=0.0, energy=0.3, tension=0.1),
    )


def adapter() -> OllamaAffectiveAppraisalAdapter:
    return OllamaAffectiveAppraisalAdapter(
        base_url="http://127.0.0.1:11434",
        model="qwen3:4b-instruct",
        timeout_seconds=30.0,
        max_output_tokens=512,
    )


def valid_proposal() -> dict[str, object]:
    return {
        "v": 2,
        "k": ["positive_progress"],
        "q": 92,
        "r": ["e"],
    }


def test_adapter_uses_strict_schema_and_returns_provider_neutral_proposal(
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

    monkeypatch.setattr("satori.infrastructure.providers.ollama_affect.urlopen", fake_urlopen)

    result = asyncio.run(adapter().generate_structured(appraisal_request()))

    http_request = captured["request"]
    assert isinstance(http_request, Request)
    assert isinstance(http_request.data, bytes)
    payload = json.loads(http_request.data.decode())
    assert payload["stream"] is False
    assert payload["think"] is False
    assert payload["keep_alive"] == "5m"
    assert payload["options"] == {"temperature": 0.0, "num_predict": 512, "num_ctx": 4096}
    assert payload["format"]["additionalProperties"] is False
    assert payload["format"]["required"]
    assert set(payload["format"]["properties"]) == {"v", "k", "q", "r"}
    assert payload["format"]["properties"]["r"]["items"]["enum"] == ["e"]
    assert payload["format"]["properties"]["r"]["uniqueItems"] is True
    assert payload["format"]["properties"]["k"]["uniqueItems"] is True
    assert "user_event" in payload["messages"][1]["content"]
    assert '"allowed_refs":["e"]' in payload["messages"][1]["content"]
    assert "User emotion is not Satori emotion" in payload["messages"][0]["content"]
    assert "q=classification confidence" in payload["messages"][0]["content"]
    assert "distress when" in payload["messages"][0]["content"]
    assert "relationship state" in payload["messages"][0]["content"]
    assert captured["timeout"] == 30.0
    assert result.proposal.source_refs == ("interaction-1",)
    assert result.proposal.pleasantness == 0.75
    assert result.proposal.interest_signal == 0.8
    assert result.proposal.salience == 0.75
    assert result.proposal.appraisal_confidence == 0.92
    assert result.proposal.reason_codes == ("positive_progress",)
    assert result.appraisal_method == APPRAISAL_METHOD


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload.pop("q"),
        lambda payload: payload.update({"q": 101}),
        lambda payload: payload.update({"affection": 1.0}),
        lambda payload: payload.update({"r": []}),
        lambda payload: payload.update({"k": []}),
        lambda payload: payload.update({"k": ["affection"]}),
    ],
)
def test_adapter_rejects_missing_out_of_range_unknown_and_empty_source_fields(
    monkeypatch: pytest.MonkeyPatch,
    mutator: Callable[[dict[str, object]], object],
) -> None:
    proposal = valid_proposal()
    mutator(proposal)
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

    monkeypatch.setattr("satori.infrastructure.providers.ollama_affect.urlopen", fake_urlopen)

    with pytest.raises(AffectiveAppraisalProviderError):
        asyncio.run(adapter().generate_structured(appraisal_request()))
