"""Credential-safe reusable HTTPS transport for the canonical OpenAI API."""

import http.client
import json
import threading
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

OPENAI_HOST = "api.openai.com"
OPENAI_BASE_PATH = "/v1"


class OpenAIHttpStatusError(Exception):
    """An OpenAI request reached the service and returned a non-success status."""

    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"OpenAI returned HTTP {status}")


class OpenAITransportError(Exception):
    """The canonical OpenAI endpoint could not complete the HTTPS exchange."""


class OpenAIHttpClient:
    """Reuse bounded HTTPS connections while keeping the API key transport-local."""

    def __init__(self, base_url: str, api_key: str, *, pool_size: int = 4) -> None:
        parsed = urlsplit(base_url.strip().rstrip("/"))
        normalized_key = api_key.strip()
        if (
            parsed.scheme != "https"
            or parsed.hostname != OPENAI_HOST
            or parsed.port is not None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path != OPENAI_BASE_PATH
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("OpenAI HTTP client requires the canonical HTTPS /v1 endpoint")
        if not normalized_key:
            raise ValueError("OpenAI API key must not be blank")
        if pool_size < 1:
            raise ValueError("OpenAI HTTP pool_size must be positive")
        self._api_key = normalized_key
        self._pool_size = pool_size
        self._available: list[http.client.HTTPSConnection] = []
        self._created = 0
        self._closed = False
        self._condition = threading.Condition()

    def post_json(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        """POST one bounded JSON document without exposing credentials to the caller."""

        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("OpenAI request path must be root-relative")
        connection = self._acquire(timeout_seconds)
        healthy = False
        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            connection.timeout = timeout_seconds
            connection.request(
                "POST",
                f"{OPENAI_BASE_PATH}{path}",
                body=body,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            response = connection.getresponse()
            response_body = response.read(max_response_bytes + 1)
            healthy = not response.will_close and len(response_body) <= max_response_bytes
            if not 200 <= response.status < 300:
                raise OpenAIHttpStatusError(response.status)
            return response_body
        except OpenAIHttpStatusError:
            healthy = False
            connection.close()
            raise
        except (TimeoutError, OSError, http.client.HTTPException) as error:
            healthy = False
            connection.close()
            raise OpenAITransportError("OpenAI HTTPS transport failed") from error
        finally:
            self._release(connection, healthy=healthy)

    def close(self) -> None:
        """Close idle connections and reject future acquisitions."""

        with self._condition:
            self._closed = True
            for connection in self._available:
                connection.close()
            self._created -= len(self._available)
            self._available.clear()
            self._condition.notify_all()

    def _acquire(self, timeout_seconds: float) -> http.client.HTTPSConnection:
        with self._condition:
            while True:
                if self._closed:
                    raise RuntimeError("OpenAI HTTP client is closed")
                if self._available:
                    return self._available.pop()
                if self._created < self._pool_size:
                    self._created += 1
                    return http.client.HTTPSConnection(OPENAI_HOST, timeout=timeout_seconds)
                self._condition.wait()

    def _release(
        self,
        connection: http.client.HTTPSConnection,
        *,
        healthy: bool,
    ) -> None:
        with self._condition:
            if healthy and not self._closed:
                self._available.append(connection)
            else:
                connection.close()
                self._created -= 1
            self._condition.notify()
