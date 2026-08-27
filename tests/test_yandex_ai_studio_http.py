"""Credential and endpoint tests for the Yandex AI Studio HTTPS transport."""

import json
from typing import Self

import pytest

from satori.infrastructure.providers.yandex_ai_studio_http import (
    YandexAIStudioHttpClient,
    YandexAIStudioHttpStatusError,
)


class FakeHttpsResponse:
    status = 200
    will_close = False

    def __init__(self, body: bytes = b'{"ok": true}') -> None:
        self.body = body

    def read(self, _limit: int) -> bytes:
        return self.body


class FakeHttpsConnection:
    def __init__(self, host: str, *, timeout: float) -> None:
        self.host = host
        self.timeout = timeout
        self.requests: list[tuple[str, str, bytes, dict[str, str]]] = []
        self.closed = False

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes,
        headers: dict[str, str],
    ) -> None:
        self.requests.append((method, path, body, headers))

    def getresponse(self) -> FakeHttpsResponse:
        return FakeHttpsResponse()

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def test_yandex_http_uses_only_canonical_https_endpoint_and_api_key_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections: list[FakeHttpsConnection] = []

    def connection_factory(host: str, *, timeout: float) -> FakeHttpsConnection:
        connection = FakeHttpsConnection(host, timeout=timeout)
        connections.append(connection)
        return connection

    monkeypatch.setattr(
        "satori.infrastructure.providers.yandex_ai_studio_http.http.client.HTTPSConnection",
        connection_factory,
    )
    client = YandexAIStudioHttpClient(
        "https://ai.api.cloud.yandex.net/v1",
        "private-test-key",
        pool_size=1,
    )
    try:
        body = client.post_json(
            "/chat/completions",
            {"model": "gpt://folder/model"},
            timeout_seconds=12.0,
            max_response_bytes=1000,
        )
    finally:
        client.close()

    assert body == b'{"ok": true}'
    assert len(connections) == 1
    connection = connections[0]
    assert connection.host == "ai.api.cloud.yandex.net"
    method, path, request_body, headers = connection.requests[0]
    assert method == "POST"
    assert path == "/v1/chat/completions"
    assert json.loads(request_body) == {"model": "gpt://folder/model"}
    assert headers["Authorization"] == "Api-Key private-test-key"
    assert headers["Content-Type"] == "application/json"
    assert connection.closed is True


@pytest.mark.parametrize(
    "base_url",
    [
        "http://ai.api.cloud.yandex.net/v1",
        "https://example.com/v1",
        "https://user:secret@ai.api.cloud.yandex.net/v1",
        "https://ai.api.cloud.yandex.net/other",
        "https://ai.api.cloud.yandex.net/v1?key=secret",
    ],
)
def test_yandex_http_rejects_noncanonical_credential_targets(base_url: str) -> None:
    with pytest.raises(ValueError, match="canonical HTTPS"):
        YandexAIStudioHttpClient(base_url, "private-test-key")


def test_yandex_status_error_contains_only_status_metadata() -> None:
    error = YandexAIStudioHttpStatusError(401)

    assert error.status == 401
    assert "401" in str(error)
    assert "private" not in str(error)
