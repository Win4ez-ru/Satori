"""Ollama embedding adapter contract tests without a running provider."""

import asyncio
import json
from typing import Self
from urllib.error import URLError
from urllib.request import Request

import pytest

from satori.core.embedding import (
    EmbeddingProviderUnavailable,
    EmbeddingRequest,
    InvalidEmbeddingResponse,
)
from satori.infrastructure.providers.ollama_embedding import OllamaEmbeddingAdapter


class FakeHttpResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self.body


def adapter() -> OllamaEmbeddingAdapter:
    return OllamaEmbeddingAdapter("http://127.0.0.1:11434", "embeddinggemma:300m", 3, 1, 30)


def test_ollama_embedding_uses_batch_no_truncation_and_explicit_dimensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: Request, *, timeout: float) -> FakeHttpResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeHttpResponse(
            json.dumps(
                {
                    "model": "embeddinggemma:300m",
                    "embeddings": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
                }
            ).encode()
        )

    monkeypatch.setattr("satori.infrastructure.providers.ollama_embedding.urlopen", fake_urlopen)
    result = asyncio.run(adapter().embed(EmbeddingRequest(1, "trace", ("one", "two"))))
    request = captured["request"]
    assert isinstance(request, Request)
    assert isinstance(request.data, bytes)
    assert json.loads(request.data) == {
        "model": "embeddinggemma:300m",
        "input": ["one", "two"],
        "truncate": False,
        "dimensions": 3,
    }
    assert captured["timeout"] == 30
    assert result.space == adapter().space


def test_ollama_embedding_wraps_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_request: Request, *, timeout: float) -> FakeHttpResponse:
        raise URLError("offline")

    monkeypatch.setattr("satori.infrastructure.providers.ollama_embedding.urlopen", fail)
    with pytest.raises(EmbeddingProviderUnavailable):
        asyncio.run(adapter().embed(EmbeddingRequest(1, "trace", ("one",))))


def test_ollama_embedding_rejects_wrong_dimension(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(_request: Request, *, timeout: float) -> FakeHttpResponse:
        return FakeHttpResponse(
            json.dumps({"model": "embeddinggemma:300m", "embeddings": [[1.0, 0.0]]}).encode()
        )

    monkeypatch.setattr("satori.infrastructure.providers.ollama_embedding.urlopen", fake_urlopen)
    with pytest.raises(InvalidEmbeddingResponse):
        asyncio.run(adapter().embed(EmbeddingRequest(1, "trace", ("one",))))
