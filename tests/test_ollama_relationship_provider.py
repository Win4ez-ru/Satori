"""Ollama Stage 8 compact relationship-appraisal adapter contracts."""

import asyncio
import json
from datetime import UTC, datetime
from typing import cast

import pytest

from satori.core.relationship import (
    RelationshipAppraisalProviderError,
    RelationshipAppraisalRequest,
)
from satori.infrastructure.providers.ollama_http import OllamaHttpClient
from satori.infrastructure.providers.ollama_relationship import (
    RELATIONSHIP_APPRAISAL_METHOD,
    OllamaRelationshipAppraisalAdapter,
)


class FakeHttpClient:
    def __init__(self, document: dict[str, object]) -> None:
        self.document = document
        self.payloads: list[dict[str, object]] = []

    def post_json(
        self,
        path: str,
        payload: dict[str, object],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        assert path == "/api/chat"
        assert timeout_seconds == 30.0
        assert max_response_bytes > 0
        self.payloads.append(payload)
        return json.dumps(
            {
                "model": "qwen3:4b-instruct",
                "message": {"role": "assistant", "content": json.dumps(self.document)},
                "done": True,
                "load_duration": 1_000_000,
                "prompt_eval_count": 90,
                "prompt_eval_duration": 2_000_000,
                "eval_count": 14,
                "eval_duration": 3_000_000,
            }
        ).encode()


def request() -> RelationshipAppraisalRequest:
    return RelationshipAppraisalRequest(
        schema_version=1,
        interaction_id="interaction-1",
        user_message_id="message-1",
        user_content="Я думаю, ты ошибаешься, давай разберём аргументы.",
        observed_at=datetime(2026, 8, 9, tzinfo=UTC),
        trace_id="trace-relationship",
    )


def adapter(client: FakeHttpClient) -> OllamaRelationshipAppraisalAdapter:
    return OllamaRelationshipAppraisalAdapter(
        base_url="http://127.0.0.1:11434",
        model="qwen3:4b-instruct",
        timeout_seconds=30.0,
        max_output_tokens=64,
        keep_alive="10m",
        http_client=cast(OllamaHttpClient, client),
    )


def test_adapter_emits_compact_no_reasoning_schema_and_metadata() -> None:
    client = FakeHttpClient({"v": 1, "k": ["collaborative_reasoning"], "q": 94, "r": ["i", "u"]})
    result = asyncio.run(adapter(client).generate_structured(request()))
    payload = client.payloads[0]

    assert payload["stream"] is False
    assert payload["think"] is False
    assert payload["keep_alive"] == "10m"
    assert payload["options"] == {"temperature": 0.0, "num_predict": 64, "num_ctx": 4096}
    properties = cast(dict[str, object], cast(dict[str, object], payload["format"])["properties"])
    assert set(properties) == {
        "v",
        "k",
        "q",
        "r",
    }
    messages = cast(list[dict[str, str]], payload["messages"])
    assert "relationship vector" in messages[0]["content"]
    assert "'Trust me' is not reliability evidence" in messages[0]["content"]
    assert "Criticism and disagreement are not hostility" in messages[0]["content"]
    assert "Я думаю" in messages[1]["content"]
    assert result.proposal.categories == ("collaborative_reasoning",)
    assert result.proposal.source_refs == ("interaction-1", "message-1")
    assert result.proposal.confidence == 0.94
    assert result.appraisal_method == RELATIONSHIP_APPRAISAL_METHOD
    assert result.metrics is not None
    assert result.metrics.load_duration_ns == 1_000_000


@pytest.mark.parametrize(
    "document",
    [
        {"v": 1, "k": ["warm_engagement"], "q": 90, "r": ["i"]},
        {"v": 1, "k": ["love"], "q": 90, "r": ["i", "u"]},
        {"v": 1, "k": ["warm_engagement"], "q": 101, "r": ["i", "u"]},
        {"v": 1, "k": [], "q": 90, "r": ["i", "u"]},
        {
            "v": 1,
            "k": ["warm_engagement"],
            "q": 90,
            "r": ["i", "u"],
            "affection": 1.0,
        },
    ],
)
def test_adapter_rejects_unknown_direct_state_and_bad_provenance(
    document: dict[str, object],
) -> None:
    with pytest.raises(RelationshipAppraisalProviderError):
        asyncio.run(adapter(FakeHttpClient(document)).generate_structured(request()))
