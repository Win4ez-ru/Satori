"""Explicit installed-client configuration and owned cancellable OAuth loopback.

No external HTTP, browser launch, token cache or identity assertion lives here.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import stat
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID, uuid4

from pydantic import SecretStr

from satori.infrastructure.gmail.contracts import GMAIL_SCOPES, GmailClientConfiguration, GmailError

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_ISSUER = "https://accounts.google.com"
MAX_CONFIGURATION_BYTES = 32768
MAX_CALLBACK_BYTES = 8192


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise GmailError("configuration_invalid")
        result[key] = value
    return result


def _visible(value: object, maximum: int) -> bool:
    return (
        type(value) is str and 0 < len(value) <= maximum and all(33 <= ord(c) <= 126 for c in value)
    )


def load_configuration(path: Path) -> GmailClientConfiguration:
    try:
        flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_CONFIGURATION_BYTES:
                raise GmailError("configuration_invalid")
            raw = stream.read(MAX_CONFIGURATION_BYTES + 1)
        if len(raw) > MAX_CONFIGURATION_BYTES:
            raise GmailError("configuration_invalid")
        parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
        if type(parsed) is not dict or "web" in parsed or type(parsed.get("installed")) is not dict:
            raise GmailError("configuration_invalid")
        data = parsed["installed"]
        client_id, secret = data.get("client_id"), data.get("client_secret")
        if (
            not _visible(client_id, 255)
            or re.fullmatch(r"[A-Za-z0-9_-]+\.apps\.googleusercontent\.com", client_id) is None
        ):
            raise GmailError("configuration_invalid")
        if not _visible(secret, 4096):
            raise GmailError("configuration_invalid")
        if data.get("auth_uri") not in {
            AUTHORIZATION_ENDPOINT,
            "https://accounts.google.com/o/oauth2/auth",
        }:
            raise GmailError("configuration_invalid")
        if data.get("token_uri") != "https://oauth2.googleapis.com/token":
            raise GmailError("configuration_invalid")
        redirects = data.get("redirect_uris")
        if type(redirects) is not list or not 1 <= len(redirects) <= 10:
            raise GmailError("configuration_invalid")
        for redirect in redirects:
            if not _visible(redirect, 1024):
                raise GmailError("configuration_invalid")
            uri = urlsplit(redirect)
            if (
                uri.scheme != "http"
                or uri.hostname not in {"localhost", "127.0.0.1", "::1"}
                or uri.username
                or uri.password
                or uri.query
                or uri.fragment
            ):
                raise GmailError("configuration_invalid")
            _ = uri.port
        return GmailClientConfiguration(client_id=client_id, client_secret=SecretStr(secret))
    except GmailError:
        raise
    except OSError:
        raise GmailError("configuration_unavailable") from None
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise GmailError("configuration_invalid") from None


class OAuthLoopback:
    """One owned 127.0.0.1 socket, exact path/state/issuer, no request logging."""

    def __init__(self, configuration: GmailClientConfiguration, *, timeout: float = 120.0) -> None:
        if not 0 < timeout <= 120:
            raise GmailError("request_invalid")
        self.attempt_id: UUID = uuid4()
        self._configuration = configuration
        self._state = secrets.token_urlsafe(32)
        self._verifier = SecretStr(secrets.token_urlsafe(64))
        self._path = "/oauth/" + secrets.token_urlsafe(24)
        self._deadline = time.monotonic() + timeout
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._consumed = False
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.bind(("127.0.0.1", 0))
            self._socket.listen(2)
            self._socket.settimeout(0.2)
            self._port = self._socket.getsockname()[1]
        except OSError:
            if hasattr(self, "_socket"):
                self._socket.close()
            raise GmailError("http_unavailable") from None
        self.redirect_uri = f"http://127.0.0.1:{self._port}{self._path}"
        challenge = (
            base64.urlsafe_b64encode(
                hashlib.sha256(self._verifier.get_secret_value().encode("ascii")).digest()
            )
            .decode("ascii")
            .rstrip("=")
        )
        self.authorization_url = (
            AUTHORIZATION_ENDPOINT
            + "?"
            + urlencode(
                {
                    "client_id": configuration.client_id,
                    "redirect_uri": self.redirect_uri,
                    "response_type": "code",
                    "scope": " ".join(sorted(GMAIL_SCOPES)),
                    "state": self._state,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "prompt": "select_account",
                    "access_type": "online",
                }
            )
        )

    def token_form(self, code: SecretStr) -> dict[str, str]:
        return {
            "code": code.get_secret_value(),
            "client_id": self._configuration.client_id,
            "client_secret": self._configuration.client_secret.get_secret_value(),
            "redirect_uri": self.redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": self._verifier.get_secret_value(),
        }

    def close(self) -> None:
        self._closed.set()
        self._socket.close()

    @staticmethod
    def _respond(connection: socket.socket, code: int) -> None:
        text = b"Return to Satori. No authorization information is shown here."
        response = (
            f"HTTP/1.1 {code} Result\r\nContent-Length: {len(text)}\r\n"
            "Content-Type: text/plain; charset=utf-8\r\nCache-Control: no-store\r\n"
            "Referrer-Policy: no-referrer\r\nConnection: close\r\n\r\n"
        ).encode() + text
        with suppress(OSError):
            connection.sendall(response)

    def _callback(self, raw: bytes) -> SecretStr | None:
        try:
            lines = raw.decode("ascii", errors="strict").split("\r\n")
            method, target, version = lines[0].split(" ")
            if method != "GET" or version not in {"HTTP/1.0", "HTTP/1.1"}:
                raise GmailError("callback_invalid")
            uri = urlsplit(target)
            if uri.scheme or uri.netloc or uri.fragment or uri.path != self._path:
                return None
            headers: dict[str, str] = {}
            for line in lines[1:]:
                if not line:
                    break
                name, value = line.split(":", 1)
                name = name.lower()
                if name in headers or any(ord(c) < 32 and c != "\t" for c in value):
                    raise GmailError("callback_invalid")
                headers[name] = value.strip()
            if (
                headers.get("host") != f"127.0.0.1:{self._port}"
                or headers.get("content-length", "0") != "0"
                or "transfer-encoding" in headers
            ):
                raise GmailError("callback_invalid")
            query = parse_qs(
                uri.query,
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=12,
                errors="strict",
            )
            if any(len(values) != 1 for values in query.values()):
                raise GmailError("callback_invalid")
            state = query.get("state", [""])[0]
            if not state.isascii() or not hmac.compare_digest(state, self._state):
                raise GmailError("state_mismatch")
            if query.get("iss") != [GOOGLE_ISSUER]:
                raise GmailError("callback_invalid")
            if "error" in query:
                if "code" in query:
                    raise GmailError("callback_invalid")
                raise GmailError("login_denied")
            code = query.get("code", [""])[0]
            if not _visible(code, 2048):
                raise GmailError("callback_invalid")
            return SecretStr(code)
        except GmailError:
            raise
        except (ValueError, UnicodeError):
            raise GmailError("callback_invalid") from None

    def wait(self, cancel: threading.Event, active: Callable[[], bool] = lambda: True) -> SecretStr:
        with self._lock:
            if self._consumed:
                raise GmailError("callback_invalid")
            self._consumed = True
        try:
            while not self._closed.is_set() and not cancel.is_set() and active():
                if time.monotonic() >= self._deadline:
                    raise GmailError("timeout")
                try:
                    connection, address = self._socket.accept()
                except TimeoutError:
                    continue
                except OSError:
                    if self._closed.is_set() or cancel.is_set() or not active():
                        raise GmailError("cancelled") from None
                    raise GmailError("http_unavailable") from None
                with connection:
                    if address[0] != "127.0.0.1":
                        continue
                    connection.settimeout(0.2)
                    data = b""
                    while b"\r\n\r\n" not in data:
                        if cancel.is_set() or self._closed.is_set() or not active():
                            raise GmailError("cancelled")
                        if time.monotonic() >= self._deadline:
                            raise GmailError("timeout")
                        try:
                            chunk = connection.recv(min(1024, MAX_CALLBACK_BYTES + 1 - len(data)))
                        except TimeoutError:
                            continue
                        if not chunk:
                            raise GmailError("callback_invalid")
                        data += chunk
                        if len(data) > MAX_CALLBACK_BYTES:
                            self._respond(connection, 400)
                            raise GmailError("callback_invalid")
                    try:
                        code = self._callback(data)
                    except GmailError:
                        self._respond(connection, 400)
                        raise
                    self._respond(connection, 200 if code is not None else 404)
                    if code is not None:
                        if cancel.is_set() or not active():
                            raise GmailError("cancelled")
                        return code
            raise GmailError("cancelled")
        except OSError:
            raise GmailError(
                "cancelled" if cancel.is_set() or self._closed.is_set() else "callback_invalid"
            ) from None
        finally:
            self.close()
