"""Credential pinning and request-bound tests for the OpenAI HTTPS client."""

from typing import Any

import pytest

from satori.infrastructure.providers.openai_http import OpenAIHttpClient, OpenAIHttpStatusError


class FakeResponse:
    status = 200
    will_close = False

    def read(self, amount: int) -> bytes:
        assert amount == 101
        return b"{}"


class FakeConnection:
    def __init__(self) -> None:
        self.timeout: float | None = None
        self.request_data: tuple[str, str, bytes, dict[str, str]] | None = None
        self.closed = False

    def request(
        self,
        method: str,
        path: str,
        body: bytes,
        headers: dict[str, str],
    ) -> None:
        self.request_data = (method, path, body, headers)

    def getresponse(self) -> FakeResponse:
        return FakeResponse()

    def close(self) -> None:
        self.closed = True


def test_openai_http_pins_host_and_adds_bearer_without_repr_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()

    def connection_factory(host: str, *, timeout: float) -> Any:
        assert host == "api.openai.com"
        assert timeout == 12.0
        return connection

    monkeypatch.setattr(
        "satori.infrastructure.providers.openai_http.http.client.HTTPSConnection",
        connection_factory,
    )
    client = OpenAIHttpClient(
        "https://api.openai.com/v1",
        "private-test-key",
        pool_size=1,
    )

    assert (
        client.post_json(
            "/responses",
            {"model": "gpt-5.6-terra"},
            timeout_seconds=12.0,
            max_response_bytes=100,
        )
        == b"{}"
    )
    assert connection.request_data is not None
    method, path, _, headers = connection.request_data
    assert method == "POST"
    assert path == "/v1/responses"
    assert headers["Authorization"] == "Bearer private-test-key"
    assert "private-test-key" not in repr(client)
    client.close()
    assert connection.closed is True
    client.close()
    with pytest.raises(RuntimeError, match="closed"):
        client.post_json(
            "/responses",
            {"model": "gpt-5.6-terra"},
            timeout_seconds=12.0,
            max_response_bytes=100,
        )


def test_openai_http_status_error_does_not_expose_response_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RejectedResponse(FakeResponse):
        status = 403
        will_close = True

        def read(self, amount: int) -> bytes:
            assert amount == 101
            return b"identity context and private reply"

    class RejectedConnection(FakeConnection):
        def getresponse(self) -> FakeResponse:
            return RejectedResponse()

    connection = RejectedConnection()

    def connection_factory(host: str, *, timeout: float) -> Any:
        assert host == "api.openai.com"
        assert timeout == 12.0
        return connection

    monkeypatch.setattr(
        "satori.infrastructure.providers.openai_http.http.client.HTTPSConnection",
        connection_factory,
    )
    client = OpenAIHttpClient(
        "https://api.openai.com/v1",
        "private-test-key",
        pool_size=1,
    )

    with pytest.raises(OpenAIHttpStatusError) as failure:
        client.post_json(
            "/responses",
            {"model": "gpt-5.6-terra"},
            timeout_seconds=12.0,
            max_response_bytes=100,
        )

    assert failure.value.status == 403
    assert "identity context" not in str(failure.value)
    assert "private-test-key" not in repr(failure.value)
    assert connection.closed is True
    client.close()


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.openai.com/v1",
        "https://example.com/v1",
        "https://api.openai.com/v1?key=secret",
        "https://user:pass@api.openai.com/v1",
    ],
)
def test_openai_http_rejects_noncanonical_credential_targets(base_url: str) -> None:
    with pytest.raises(ValueError, match="canonical HTTPS"):
        OpenAIHttpClient(base_url, "private-test-key")
