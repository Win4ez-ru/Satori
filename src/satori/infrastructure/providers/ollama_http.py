"""Small reusable HTTP/1.1 connection pool for long-lived local Ollama runtimes."""

import http.client
import json
import threading
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit


class OllamaHttpStatusError(Exception):
    """An Ollama request reached the server and returned a non-success status."""

    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"Ollama returned HTTP {status}")


class OllamaHttpClient:
    """Reuse a bounded pool of HTTP connections without leaking it into application code."""

    def __init__(self, base_url: str, *, pool_size: int = 4) -> None:
        parsed = urlsplit(base_url.strip().rstrip("/"))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Ollama HTTP client requires an HTTP(S) origin")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("Ollama HTTP client base_url must be an origin")
        if pool_size < 1:
            raise ValueError("Ollama HTTP pool_size must be positive")
        self._scheme = parsed.scheme
        self._host = parsed.hostname
        self._port = parsed.port
        self._pool_size = pool_size
        self._available: list[http.client.HTTPConnection] = []
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
        """POST JSON and fully consume the response so the connection can be reused."""

        connection = self._acquire(timeout_seconds)
        healthy = False
        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            connection.timeout = timeout_seconds
            connection.request(
                "POST",
                path,
                body=body,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            response = connection.getresponse()
            response_body = response.read(max_response_bytes + 1)
            healthy = not response.will_close and len(response_body) <= max_response_bytes
            if not 200 <= response.status < 300:
                raise OllamaHttpStatusError(response.status)
            return response_body
        except (OllamaHttpStatusError, TimeoutError, OSError, http.client.HTTPException):
            connection.close()
            raise
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

    def _acquire(self, timeout_seconds: float) -> http.client.HTTPConnection:
        with self._condition:
            while True:
                if self._closed:
                    raise RuntimeError("Ollama HTTP client is closed")
                if self._available:
                    return self._available.pop()
                if self._created < self._pool_size:
                    self._created += 1
                    connection_type: type[http.client.HTTPConnection]
                    connection_type = (
                        http.client.HTTPSConnection
                        if self._scheme == "https"
                        else http.client.HTTPConnection
                    )
                    return connection_type(self._host, self._port, timeout=timeout_seconds)
                self._condition.wait()

    def _release(self, connection: http.client.HTTPConnection, *, healthy: bool) -> None:
        with self._condition:
            if healthy and not self._closed:
                self._available.append(connection)
            else:
                connection.close()
                self._created -= 1
            self._condition.notify()
