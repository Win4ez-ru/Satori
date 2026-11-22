"""Synthetic canonical OpenAI embedding transport and exact vector-space checks."""

import asyncio
import json
from collections.abc import Mapping
from typing import Any

import pytest

from satori.core.embedding import (
    EmbeddingGenerationFailed,
    EmbeddingProviderUnavailable,
    EmbeddingRequest,
    InvalidEmbeddingResponse,
)
from satori.infrastructure.providers.openai_embedding import OpenAIEmbeddingAdapter
from satori.infrastructure.providers.openai_http import OpenAIHttpStatusError


def document() -> dict[str, object]:
    return {
        "object": "list",
        "model": "text-embedding-3-small",
        "data": [
            {"object": "embedding", "index": 0, "embedding": [0.2, -0.4, 0.8]},
            {"object": "embedding", "index": 1, "embedding": [0.4, 0.2, -0.8]},
        ],
        "usage": {"prompt_tokens": 8, "total_tokens": 8},
    }


class Transport:
    def __init__(self, response: object = None, error: Exception | None = None) -> None:
        self.body = json.dumps(document() if response is None else response).encode()
        self.error = error
        self.calls: list[tuple[str, Mapping[str, Any]]] = []

    def post_json(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        self.calls.append((path, payload))
        if self.error is not None:
            raise self.error
        return self.body


def adapter(transport: Transport) -> OpenAIEmbeddingAdapter:
    return OpenAIEmbeddingAdapter(
        model="text-embedding-3-small",
        dimensions=3,
        input_schema_version=1,
        timeout_seconds=3,
        http_client=transport,
    )


def request() -> EmbeddingRequest:
    return EmbeddingRequest(schema_version=1, trace_id="fixture", texts=("first", "second"))


def test_exact_openai_space_order_dimensions_and_payload() -> None:
    transport = Transport()
    response = asyncio.run(adapter(transport).embed(request()))
    assert response.space.key == "openai/text-embedding-3-small/d3/input-v1"
    assert response.vectors == ((0.2, -0.4, 0.8), (0.4, 0.2, -0.8))
    assert transport.calls == [
        (
            "/embeddings",
            {
                "model": "text-embedding-3-small",
                "input": ["first", "second"],
                "dimensions": 3,
                "encoding_format": "float",
            },
        )
    ]
    assert response.metrics is not None
    assert response.metrics.prompt_eval_count == 8
    assert response.metrics.load_duration_ns is response.metrics.total_duration_ns is None
    assert response.metrics.eval_count is None


@pytest.mark.parametrize(
    "change",
    [
        "model",
        "count",
        "order",
        "duplicate",
        "dimension",
        "boolean",
        "zero",
        "nonfinite",
        "huge",
        "usage",
    ],
)
def test_mismatched_or_invalid_vectors_reject(change: str) -> None:
    response: dict[str, Any] = document()
    if change == "model":
        response["model"] = "embeddinggemma:300m"
    elif change == "count":
        response["data"].pop()
    elif change == "order":
        response["data"].reverse()
    elif change == "duplicate":
        response["data"][1]["index"] = 0
    elif change == "dimension":
        response["data"][0]["embedding"] = [0.1]
    elif change == "boolean":
        response["data"][0]["embedding"][0] = True
    elif change == "zero":
        response["data"][0]["embedding"] = [0, 0, 0]
    elif change == "nonfinite":
        response["data"][0]["embedding"][0] = float("nan")
    elif change == "huge":
        response["data"][0]["embedding"] = [1e308, 1e308, 1e308]
    else:
        response["usage"] = {"prompt_tokens": 8, "total_tokens": 9}
    with pytest.raises(InvalidEmbeddingResponse, match="embedding_response_invalid"):
        asyncio.run(adapter(Transport(response)).embed(request()))


@pytest.mark.parametrize(
    "value",
    [
        EmbeddingRequest(schema_version=2, trace_id="fixture", texts=("text",)),
        EmbeddingRequest(schema_version=1, trace_id="fixture", texts=("x" * 8193,)),
        EmbeddingRequest(schema_version=1, trace_id="fixture", texts=("text",) * 33),
    ],
)
def test_input_bounds_reject_without_truncation_or_transport(value: EmbeddingRequest) -> None:
    transport = Transport()
    with pytest.raises(EmbeddingGenerationFailed, match="embedding_request_rejected"):
        asyncio.run(adapter(transport).embed(value))
    assert not transport.calls


def test_missing_usage_stays_unknown() -> None:
    response = document()
    del response["usage"]
    result = asyncio.run(adapter(Transport(response)).embed(request()))
    assert result.metrics is not None
    assert result.metrics.prompt_eval_count is None


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (400, EmbeddingGenerationFailed),
        (401, EmbeddingGenerationFailed),
        (429, EmbeddingProviderUnavailable),
        (503, EmbeddingProviderUnavailable),
    ],
)
def test_safe_provider_errors(status: int, kind: type[Exception]) -> None:
    with pytest.raises(kind, match=r"^embedding_provider_rejected$"):
        asyncio.run(adapter(Transport(error=OpenAIHttpStatusError(status))).embed(request()))


def test_unknown_model_and_invalid_dimensions_refuse_at_construction() -> None:
    with pytest.raises(ValueError, match="invalid_openai_embedding_configuration"):
        OpenAIEmbeddingAdapter("embeddinggemma:300m", 3, 1, 3, Transport())
    with pytest.raises(ValueError, match="invalid_openai_embedding_configuration"):
        OpenAIEmbeddingAdapter("text-embedding-3-small", 1537, 1, 3, Transport())
