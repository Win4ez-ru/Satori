"""Fixed-origin, credential-local Google HTTPS transport without ambient auth.

This layer performs one exchange, never redirects, retries, refreshes, or logs.
Cancellation/deadlines are checked between reads; DNS/open remains blocking and
socket operations use the bounded explicit timeout. Owning result fences remain
necessary when cancellation arrives during blocking I/O.
"""

import http.client
import importlib
import math
import re
import ssl
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from email.message import Message
from typing import IO, Literal, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from pydantic import SecretStr

from satori.application.assistant.mail_search_contracts import valid_mail_search_query
from satori.infrastructure.gmail.contracts import (
    GmailError,
    GmailErrorCode,
    GmailResponseDiagnostic,
)

GMAIL_ORIGIN = "https://gmail.googleapis.com"
PROFILE_URL = f"{GMAIL_ORIGIN}/gmail/v1/users/me/profile"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
LIST_FIELDS = "messages(id,threadId),nextPageToken,resultSizeEstimate"
METADATA_FIELDS = "id,threadId,internalDate,historyId,payload(headers)"
FULL_FIELDS = "id,threadId,internalDate,historyId,payload"
METADATA_HEADERS = ("From", "Reply-To", "To", "Cc", "Subject", "Date", "Message-ID")
MAX_HTTP_RESPONSE_BYTES = 1024 * 1024
DEFAULT_HTTP_RESPONSE_BYTES = 512 * 1024
MAX_HTTP_FORM_BYTES = 32 * 1024
MAX_HTTP_TIMEOUT = 10.0
READ_BLOCK_BYTES = 16 * 1024
_MESSAGE_PATH = re.compile(r"/gmail/v1/users/me/messages/[A-Za-z0-9_-]{1,255}")
_TOKEN_FORM_FIELDS = frozenset(
    {"code", "client_id", "client_secret", "redirect_uri", "grant_type", "code_verifier"}
)


class GoogleResponse(Protocol):
    """The bounded subset supplied by urllib's HTTPS response or a test fixture."""

    status: int
    headers: Message

    def geturl(self) -> str: ...

    def read1(self, size: int) -> bytes: ...

    def close(self) -> None: ...


class GoogleOpener(Protocol):
    def open(self, request: Request, *, timeout: float) -> GoogleResponse: ...


def _require(
    condition: bool,
    code: GmailErrorCode = "request_invalid",
    *,
    response_diagnostic: GmailResponseDiagnostic | None = None,
) -> None:
    if not condition:
        raise GmailError(code, response_diagnostic=response_diagnostic)


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> Request | None:
        return None


def _verified_context(context: ssl.SSLContext | None) -> ssl.SSLContext:
    try:
        if context is None:
            try:
                certifi = importlib.import_module("certifi")
            except ImportError:
                context = ssl.create_default_context()
            else:
                where = getattr(certifi, "where", None)
                _require(callable(where), "tls_unavailable")
                cafile = cast(Callable[[], str], where)()
                _require(type(cafile) is str, "tls_unavailable")
                context = ssl.create_default_context(cafile=cafile)
        _require(isinstance(context, ssl.SSLContext), "tls_unavailable")
        _require(
            context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED,
            "tls_unavailable",
        )
        context.set_alpn_protocols(["http/1.1"])
        return context
    except (OSError, ValueError):
        raise GmailError("tls_unavailable") from None


def _make_opener(context: ssl.SSLContext) -> GoogleOpener:
    # An explicit empty ProxyHandler suppresses environment/system proxy lookup.
    # No CookieProcessor, password manager, netrc reader or auth handler is added.
    return cast(
        GoogleOpener,
        build_opener(ProxyHandler({}), HTTPSHandler(context=context), _NoRedirects()),
    )


def _query(url: str) -> tuple[str, dict[str, list[str]]]:
    _require(type(url) is str and 0 < len(url) <= 8192)
    _require(all(33 <= ord(char) <= 126 for char in url) and "\\" not in url and "#" not in url)
    _require(re.search(r"%(?![0-9A-Fa-f]{2})", url) is None)
    try:
        parsed = urlsplit(url)
        _require(
            url.startswith("https://")
            and parsed.scheme == "https"
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
        )
        origin = f"https://{parsed.hostname}"
        expected_netloc = parsed.hostname or ""
        if parsed.port is not None:
            expected_netloc += ":443"
        _require(parsed.netloc == expected_netloc)
        pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            encoding="utf-8",
            errors="strict",
            max_num_fields=16,
        )
    except (ValueError, UnicodeError):
        raise GmailError("request_invalid") from None
    query: dict[str, list[str]] = {}
    for key, value in pairs:
        _require(key == "metadataHeaders" or key not in query)
        query.setdefault(key, []).append(value)
    return origin + parsed.path, query


def _allow_endpoint(method: str, url: str) -> Literal["token", "revoke", "read"]:
    endpoint, query = _query(url)
    if endpoint in {TOKEN_URL, REVOKE_URL}:
        _require(method == "POST" and not query)
        return "token" if endpoint == TOKEN_URL else "revoke"
    _require(method == "GET")
    if endpoint == USERINFO_URL:
        _require(not query)
        return "read"
    if endpoint == PROFILE_URL:
        _require(query in ({}, {"fields": ["emailAddress"]}))
        return "read"
    if endpoint == f"{GMAIL_ORIGIN}/gmail/v1/users/me/messages":
        _require(
            query
            == {
                "labelIds": ["INBOX"],
                "maxResults": ["20"],
                "includeSpamTrash": ["false"],
                "fields": [LIST_FIELDS],
            }
            or (
                set(query) == {"q", "maxResults", "includeSpamTrash", "fields"}
                and len(query["q"]) == 1
                and valid_mail_search_query(query["q"][0])
                and query["maxResults"] == ["20"]
                and query["includeSpamTrash"] == ["true"]
                and query["fields"] == [LIST_FIELDS]
            )
        )
        return "read"
    parsed = urlsplit(endpoint)
    _require(
        parsed.netloc == "gmail.googleapis.com" and _MESSAGE_PATH.fullmatch(parsed.path) is not None
    )
    _require(
        parsed.path.rsplit("/", 1)[-1]
        not in {"send", "import", "insert", "batchDelete", "batchModify"}
    )
    if query.get("format") == ["full"]:
        _require(query == {"format": ["full"], "fields": [FULL_FIELDS]})
    else:
        _require(
            query
            == {
                "format": ["metadata"],
                "fields": [METADATA_FIELDS],
                "metadataHeaders": list(METADATA_HEADERS),
            }
        )
    return "read"


def _form_body(endpoint: str, form: dict[str, str] | None) -> bytes:
    _require(type(form) is dict)
    form = cast(dict[str, str], form)
    expected = _TOKEN_FORM_FIELDS if endpoint == "token" else frozenset({"token"})
    _require(set(form) == expected)
    for key, value in form.items():
        _require(type(key) is str and type(value) is str)
        _require(0 < len(value) <= 8192 and all(33 <= ord(char) <= 126 for char in value))
    if endpoint == "token":
        _require(form["grant_type"] == "authorization_code")
    encoded = urlencode(form).encode("ascii")
    _require(len(encoded) <= MAX_HTTP_FORM_BYTES)
    return encoded


def _response_length(headers: Message, maximum: int) -> int | None:
    encodings = headers.get_all("Content-Encoding", [])
    _require(
        len(encodings) <= 1 and (not encodings or encodings[0].strip().lower() == "identity"),
        "response_invalid",
        response_diagnostic="http_content_encoding",
    )
    lengths = headers.get_all("Content-Length", [])
    _require(
        len(lengths) <= 1, "response_invalid", response_diagnostic="http_content_length_duplicate"
    )
    transfer = headers.get_all("Transfer-Encoding", [])
    _require(len(transfer) <= 1, "response_invalid", response_diagnostic="http_transfer_encoding")
    _require(
        not transfer or (not lengths and transfer[0].strip().lower() == "chunked"),
        "response_invalid",
        response_diagnostic="http_transfer_encoding",
    )
    if not lengths:
        return None
    length = lengths[0].strip()
    _require(
        re.fullmatch(r"[0-9]{1,12}", length) is not None,
        "response_invalid",
        response_diagnostic="http_content_length_syntax",
    )
    count = int(length)
    _require(count <= maximum, "response_too_large")
    return count


def _status_error(status: int) -> GmailErrorCode:
    if status == 401:
        return "auth_expired"
    if status == 403:
        return "permission_denied"
    return "http_error"


class GoogleHTTP:
    """One allowlisted Google exchange with no automatic redirect or recovery."""

    def __init__(
        self,
        *,
        opener: GoogleOpener | None = None,
        context: ssl.SSLContext | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        verified = _verified_context(context)
        self._opener = opener if opener is not None else _make_opener(verified)
        self._clock = clock

    def _remaining(self, cancel: threading.Event, deadline: float) -> float:
        if cancel.is_set():
            raise GmailError("cancelled")
        remaining = deadline - self._clock()
        _require(remaining > 0, "timeout")
        return remaining

    def request(
        self,
        method: Literal["GET", "POST"],
        url: str,
        *,
        token: SecretStr | None = None,
        form: dict[str, str] | None = None,
        max_bytes: int = DEFAULT_HTTP_RESPONSE_BYTES,
        cancel: threading.Event,
        timeout: float = MAX_HTTP_TIMEOUT,
    ) -> bytes:
        _require(isinstance(cancel, threading.Event))
        if cancel.is_set():
            raise GmailError("cancelled")
        _require(type(max_bytes) is int and 1 <= max_bytes <= MAX_HTTP_RESPONSE_BYTES)
        _require(type(timeout) in {int, float} and math.isfinite(timeout))
        _require(0 < timeout <= MAX_HTTP_TIMEOUT)
        _require(type(method) is str and method in {"GET", "POST"})
        endpoint = _allow_endpoint(method, url)
        headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
        if endpoint == "read":
            _require(form is None and type(token) is SecretStr)
            secret = cast(SecretStr, token).get_secret_value()
            _require(0 < len(secret) <= 8192 and all(33 <= ord(char) <= 126 for char in secret))
            headers["Authorization"] = f"Bearer {secret}"
            data = None
        else:
            _require(token is None)
            data = _form_body(endpoint, form)
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = Request(url, data=data, headers=headers, method=method)
        deadline = self._clock() + timeout
        response: GoogleResponse | None = None
        try:
            response = self._opener.open(request, timeout=self._remaining(cancel, deadline))
            self._remaining(cancel, deadline)
            _require(response.geturl() == url, "http_error")
            _require(
                type(response.status) is int,
                "response_invalid",
                response_diagnostic="http_status_type",
            )
            _require(response.status == 200, _status_error(response.status))
            _require(
                not response.headers.get_all("Content-Range", []),
                "response_invalid",
                response_diagnostic="http_content_range",
            )
            length = _response_length(response.headers, max_bytes)
            chunks: list[bytes] = []
            count = 0
            while True:
                self._remaining(cancel, deadline)
                block = response.read1(min(READ_BLOCK_BYTES, max_bytes + 1 - count))
                self._remaining(cancel, deadline)
                _require(
                    type(block) is bytes, "response_invalid", response_diagnostic="http_body_type"
                )
                if not block:
                    break
                count += len(block)
                _require(count <= max_bytes, "response_too_large")
                chunks.append(block)
            _require(
                length is None or length == count,
                "response_invalid",
                response_diagnostic="http_body_length_mismatch",
            )
            return b"".join(chunks)
        except HTTPError as error:
            with suppress(OSError):
                error.close()
            if cancel.is_set():
                raise GmailError("cancelled") from None
            raise GmailError(_status_error(error.code)) from None
        except URLError as error:
            if cancel.is_set():
                raise GmailError("cancelled") from None
            if isinstance(error.reason, ssl.SSLError):
                raise GmailError("tls_unavailable") from None
            if isinstance(error.reason, TimeoutError):
                raise GmailError("timeout") from None
            raise GmailError("http_unavailable") from None
        except ssl.SSLError:
            raise GmailError("cancelled" if cancel.is_set() else "tls_unavailable") from None
        except TimeoutError:
            raise GmailError("cancelled" if cancel.is_set() else "timeout") from None
        except (OSError, http.client.HTTPException):
            raise GmailError("cancelled" if cancel.is_set() else "http_unavailable") from None
        finally:
            if response is not None:
                with suppress(OSError):
                    response.close()
