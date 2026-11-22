"""Injected HTTPS fixtures only; no Gmail account, OAuth exchange or network evidence."""

import io
import socket
import ssl
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from email.message import Message
from types import ModuleType, SimpleNamespace
from typing import Literal, Never, cast
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode
from urllib.request import HTTPSHandler, ProxyHandler, Request

import pytest
from pydantic import SecretStr

from satori.infrastructure.gmail import http
from satori.infrastructure.gmail.contracts import GmailError
from satori.infrastructure.gmail.http import (
    FULL_FIELDS,
    GMAIL_ORIGIN,
    LIST_FIELDS,
    MAX_HTTP_RESPONSE_BYTES,
    METADATA_FIELDS,
    METADATA_HEADERS,
    PROFILE_URL,
    REVOKE_URL,
    TOKEN_URL,
    USERINFO_URL,
    GoogleHTTP,
    GoogleResponse,
)

TOKEN = SecretStr("synthetic-access-token-never-log")
FORM = {
    "code": "synthetic-code-never-log",
    "client_id": "synthetic-client.apps.googleusercontent.com",
    "client_secret": "synthetic-client-secret-never-log",
    "redirect_uri": "http://127.0.0.1:34567/oauth/callback",
    "grant_type": "authorization_code",
    "code_verifier": "synthetic-code-verifier-never-log",
}


def read_url(format_name: str = "full") -> str:
    pairs = [
        ("format", format_name),
        ("fields", FULL_FIELDS if format_name == "full" else METADATA_FIELDS),
    ]
    if format_name == "metadata":
        pairs.extend(("metadataHeaders", name) for name in METADATA_HEADERS)
    return f"{GMAIL_ORIGIN}/gmail/v1/users/me/messages/m1?{urlencode(pairs)}"


def list_url(**extra: str) -> str:
    return f"{GMAIL_ORIGIN}/gmail/v1/users/me/messages?" + urlencode(
        {
            "labelIds": "INBOX",
            "maxResults": "20",
            "includeSpamTrash": "false",
            "fields": LIST_FIELDS,
            **extra,
        }
    )


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> Never:
        raise AssertionError("external_network_forbidden_in_http_unit_tests")

    monkeypatch.setattr(socket, "create_connection", forbidden)


@dataclass
class Clock:
    value: float = 100.0

    def __call__(self) -> float:
        return self.value


@dataclass
class Response:
    url: str = PROFILE_URL
    body: bytes = b'{"emailAddress":"fixture@example.invalid"}'
    status: int = 200
    headers: Message = field(default_factory=Message)
    position: int = 0
    reads: list[int] = field(default_factory=list)
    on_read: Callable[[], None] | None = None
    closed: bool = False

    def geturl(self) -> str:
        return self.url

    def read1(self, size: int) -> bytes:
        self.reads.append(size)
        if self.on_read is not None:
            self.on_read()
        block = self.body[self.position : self.position + size]
        self.position += len(block)
        return block

    def close(self) -> None:
        self.closed = True


@dataclass
class Opener:
    response: Response = field(default_factory=Response)
    error: Exception | None = None
    on_open: Callable[[], None] | None = None
    calls: list[tuple[Request, float]] = field(default_factory=list)

    def open(self, request: Request, *, timeout: float) -> GoogleResponse:
        self.calls.append((request, timeout))
        if self.on_open is not None:
            self.on_open()
        if self.error is not None:
            raise self.error
        return self.response


def client(response: Response | None = None) -> tuple[GoogleHTTP, Opener]:
    opener = Opener(response=response or Response())
    return GoogleHTTP(opener=opener), opener


@pytest.mark.parametrize(
    "url",
    [
        PROFILE_URL,
        PROFILE_URL + "?fields=emailAddress",
        USERINFO_URL,
        list_url(),
        read_url(),
        read_url("metadata"),
    ],
)
def test_exact_read_endpoints_and_queries_are_accepted(url: str) -> None:
    transport, opener = client(Response(url=url, body=b"{}"))
    assert transport.request("GET", url, token=TOKEN, cancel=threading.Event()) == b"{}"
    request, timeout = opener.calls[0]
    assert request.get_method() == "GET"
    assert request.full_url == url
    assert request.get_header("Authorization") == f"Bearer {TOKEN.get_secret_value()}"
    assert request.get_header("Accept-encoding") == "identity"
    assert request.get_header("Cookie") is None
    assert request.get_header("Proxy-authorization") is None
    assert request.data is None
    assert 0 < timeout <= 10
    assert opener.response.closed


def test_token_exchange_places_code_and_secret_only_in_form_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport, opener = client(Response(url=TOKEN_URL, body=b"{}"))
    assert transport.request("POST", TOKEN_URL, form=FORM, cancel=threading.Event()) == b"{}"
    request, _ = opener.calls[0]
    assert request.full_url == TOKEN_URL
    assert request.get_header("Authorization") is None
    assert request.get_header("Content-type") == "application/x-www-form-urlencoded"
    data = request.data
    assert isinstance(data, bytes)
    assert parse_qs(data.decode()) == {key: [value] for key, value in FORM.items()}
    assert all(value not in caplog.text for value in FORM.values())


def test_revoke_token_is_posted_in_body_and_empty_response_supported() -> None:
    transport, opener = client(Response(url=REVOKE_URL, body=b""))
    result = transport.request(
        "POST", REVOKE_URL, form={"token": TOKEN.get_secret_value()}, cancel=threading.Event()
    )
    request, _ = opener.calls[0]
    assert result == b""
    assert request.full_url == REVOKE_URL
    assert request.get_header("Authorization") is None
    data = request.data
    assert isinstance(data, bytes)
    assert parse_qs(data.decode()) == {"token": [TOKEN.get_secret_value()]}


@pytest.mark.parametrize(
    "url",
    [
        "http://gmail.googleapis.com/gmail/v1/users/me/profile",
        "https://user:password@gmail.googleapis.com/gmail/v1/users/me/profile",
        "https://gmail.googleapis.com:444/gmail/v1/users/me/profile",
        "https://gmail.googleapis.com:invalid/gmail/v1/users/me/profile",
        PROFILE_URL + "#fragment",
        " " + PROFILE_URL,
        PROFILE_URL + "\n",
        "https://gmail.googleapis.com./gmail/v1/users/me/profile",
        "https://gmail.googleapis.com.evil.invalid/gmail/v1/users/me/profile",
        "https://www.googleapis.com/gmail/v1/users/me/profile",
        "https://127.0.0.1/gmail/v1/users/me/profile",
        "file:///etc/passwd",
        "data:text/plain,secret",
        "ftp://gmail.googleapis.com/file",
        "https://gmail.googleapis.com/gmail/v1/users/other/profile",
        "https://gmail.googleapis.com/gmail/v1/users/me/threads/thread1",
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/m1/attachments/a1",
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/m1/modify",
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/%2e%2e/profile",
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/../profile",
        "https://gmail.googleapis.com\\@evil.invalid/gmail/v1/users/me/profile",
        PROFILE_URL + "?access_token=secret",
        PROFILE_URL + "?fields=emailAddress&fields=emailAddress",
        PROFILE_URL + "?fields=%GG",
        PROFILE_URL + "?fields=%ff",
        PROFILE_URL + "?fields=",
        TOKEN_URL + "?code=secret",
        REVOKE_URL + "?token=secret",
        USERINFO_URL + "?access_token=secret",
    ],
)
def test_unapproved_urls_rejected_before_opener(url: str) -> None:
    transport, opener = client()
    with pytest.raises(GmailError, match="request_invalid"):
        transport.request("GET", url, token=TOKEN, cancel=threading.Event())
    assert not opener.calls


@pytest.mark.parametrize(
    "url",
    [
        list_url(labelIds="SENT"),
        list_url(maxResults="21"),
        list_url(includeSpamTrash="true"),
        list_url(q="sensitive query"),
        list_url(pageToken="next-page"),
        list_url(fields="*"),
        read_url() + "&access_token=secret",
        read_url().replace("format=full", "format=raw"),
        read_url().replace(urlencode({"fields": FULL_FIELDS}), "fields=*"),
        read_url("metadata").replace("metadataHeaders=From", "metadataHeaders=Bcc"),
        read_url("metadata") + "&metadataHeaders=From",
    ],
)
def test_query_scope_cannot_expand_beyond_connector_generated_parameters(url: str) -> None:
    transport, opener = client()
    with pytest.raises(GmailError, match="request_invalid"):
        transport.request("GET", url, token=TOKEN, cancel=threading.Event())
    assert not opener.calls


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("POST", PROFILE_URL),
        ("GET", TOKEN_URL),
        ("GET", REVOKE_URL),
        ("POST", USERINFO_URL),
        ("POST", read_url()),
    ],
)
def test_disallowed_method_endpoint_pairs_are_not_dispatched(method: str, url: str) -> None:
    transport, opener = client()
    with pytest.raises(GmailError, match="request_invalid"):
        transport.request(cast(Literal["GET", "POST"], method), url, cancel=threading.Event())
    assert not opener.calls


@pytest.mark.parametrize(
    "changes",
    [
        {"grant_type": "refresh_token"},
        {"refresh_token": "secret"},
        {"code": "bad\r\nHeader: injected"},
        {"client_secret": ""},
        {"client_secret": "x" * 8193},
    ],
)
def test_oauth_form_is_exact_and_bounded(changes: dict[str, str]) -> None:
    transport, opener = client()
    with pytest.raises(GmailError, match="request_invalid"):
        transport.request("POST", TOKEN_URL, form={**FORM, **changes}, cancel=threading.Event())
    assert not opener.calls


def test_get_has_no_form_and_post_has_no_bearer_header() -> None:
    transport, opener = client()
    with pytest.raises(GmailError, match="request_invalid"):
        transport.request(
            "GET", PROFILE_URL, token=TOKEN, form={"code": "secret"}, cancel=threading.Event()
        )
    with pytest.raises(GmailError, match="request_invalid"):
        transport.request("POST", TOKEN_URL, token=TOKEN, form=FORM, cancel=threading.Event())
    assert not opener.calls


@pytest.mark.parametrize(
    "token",
    [
        None,
        SecretStr(""),
        SecretStr("bad\nHeader: injected"),
        SecretStr("contains space"),
        SecretStr("x" * 8193),
    ],
)
def test_get_requires_nonempty_header_safe_secret(token: SecretStr | None) -> None:
    transport, opener = client()
    with pytest.raises(GmailError, match="request_invalid"):
        transport.request("GET", PROFILE_URL, token=token, cancel=threading.Event())
    assert not opener.calls


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (301, "http_error"),
        (302, "http_error"),
        (307, "http_error"),
        (308, "http_error"),
        (206, "http_error"),
        (401, "auth_expired"),
        (403, "permission_denied"),
        (429, "http_error"),
        (500, "http_error"),
    ],
)
def test_non_success_status_is_closed_and_does_not_read_error_body(status: int, code: str) -> None:
    transport, opener = client(Response(status=status, body=b"SECRET PROVIDER ERROR"))
    with pytest.raises(GmailError, match=f"^{code}$"):
        transport.request("GET", PROFILE_URL, token=TOKEN, cancel=threading.Event())
    assert len(opener.calls) == 1
    assert not opener.response.reads
    assert opener.response.closed


@pytest.mark.parametrize(
    ("status", "code"),
    [(302, "http_error"), (401, "auth_expired"), (403, "permission_denied"), (429, "http_error")],
)
def test_urllib_http_errors_are_closed_without_reading_or_retry(status: int, code: str) -> None:
    body = io.BytesIO(b"SECRET PROVIDER ERROR")
    opener = Opener(error=HTTPError(PROFILE_URL, status, "SECRET reason", Message(), body))
    transport = GoogleHTTP(opener=opener)
    with pytest.raises(GmailError, match=f"^{code}$") as caught:
        transport.request("GET", PROFILE_URL, token=TOKEN, cancel=threading.Event())
    assert "SECRET" not in str(caught.value)
    assert body.closed
    assert len(opener.calls) == 1


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (URLError("SECRET transport detail"), "http_unavailable"),
        (URLError(TimeoutError("SECRET timeout")), "timeout"),
        (TimeoutError("SECRET timeout"), "timeout"),
        (OSError("SECRET transport detail"), "http_unavailable"),
        (ssl.SSLError("SECRET TLS detail"), "tls_unavailable"),
        (URLError(ssl.SSLCertVerificationError("SECRET certificate detail")), "tls_unavailable"),
    ],
)
def test_transport_failures_have_closed_codes_and_no_implicit_retry(
    error: Exception, code: str
) -> None:
    opener = Opener(error=error)
    transport = GoogleHTTP(opener=opener)
    with pytest.raises(GmailError, match=f"^{code}$") as caught:
        transport.request("GET", PROFILE_URL, token=TOKEN, cancel=threading.Event())
    assert "SECRET" not in str(caught.value)
    assert len(opener.calls) == 1


def test_cancel_before_dispatch_performs_no_open() -> None:
    transport, opener = client()
    event = threading.Event()
    event.set()
    with pytest.raises(GmailError, match="cancelled"):
        transport.request("GET", PROFILE_URL, token=TOKEN, cancel=event)
    assert not opener.calls


def test_cancel_during_open_discards_and_closes_late_response() -> None:
    event = threading.Event()
    opener = Opener(on_open=event.set)
    transport = GoogleHTTP(opener=opener)
    with pytest.raises(GmailError, match="cancelled"):
        transport.request("GET", PROFILE_URL, token=TOKEN, cancel=event)
    assert opener.response.closed
    assert not opener.response.reads


def test_cancel_during_read_discards_partial_bytes() -> None:
    event = threading.Event()
    response = Response(body=b"x" * 40000, on_read=event.set)
    transport, _ = client(response)
    with pytest.raises(GmailError, match="cancelled"):
        transport.request("GET", PROFILE_URL, token=TOKEN, cancel=event)
    assert len(response.reads) == 1
    assert response.closed


def test_overall_deadline_is_rechecked_after_open_and_read_blocks() -> None:
    for during_open in (True, False):
        clock = Clock()

        def advance(clock: Clock = clock) -> None:
            clock.value += 11

        response = Response(on_read=None if during_open else advance)
        opener = Opener(response=response, on_open=advance if during_open else None)
        transport = GoogleHTTP(opener=opener, clock=clock)
        with pytest.raises(GmailError, match="timeout"):
            transport.request("GET", PROFILE_URL, token=TOKEN, cancel=threading.Event())
        assert response.closed
        assert len(response.reads) == (0 if during_open else 1)


@pytest.mark.parametrize("timeout", [0.0, -1.0, 10.1, float("inf"), float("nan"), True])
def test_timeout_must_be_finite_positive_and_at_most_ten_seconds(timeout: float) -> None:
    transport, opener = client()
    with pytest.raises(GmailError, match="request_invalid"):
        transport.request(
            "GET", PROFILE_URL, token=TOKEN, cancel=threading.Event(), timeout=timeout
        )
    assert not opener.calls


@pytest.mark.parametrize("maximum", [0, -1, MAX_HTTP_RESPONSE_BYTES + 1, True])
def test_response_maximum_is_bounded_and_strict(maximum: int) -> None:
    transport, opener = client()
    with pytest.raises(GmailError, match="request_invalid"):
        transport.request(
            "GET", PROFILE_URL, token=TOKEN, cancel=threading.Event(), max_bytes=maximum
        )
    assert not opener.calls


def test_stream_count_rejects_oversize_without_trusting_missing_or_small_content_length() -> None:
    for length in (None, "1"):
        response = Response(body=b"x" * 101)
        if length is not None:
            response.headers["Content-Length"] = length
        transport, _ = client(response)
        with pytest.raises(GmailError, match="response_too_large"):
            transport.request(
                "GET", PROFILE_URL, token=TOKEN, cancel=threading.Event(), max_bytes=100
            )
        assert response.closed
        assert max(response.reads) <= 101


def test_content_length_oversize_rejected_before_body_read_and_short_body_is_invalid() -> None:
    for length, code in (("101", "response_too_large"), ("5", "response_invalid")):
        response = Response(body=b"shorter" if length == "101" else b"four")
        response.headers["Content-Length"] = length
        transport, _ = client(response)
        with pytest.raises(GmailError, match=code):
            transport.request(
                "GET", PROFILE_URL, token=TOKEN, cancel=threading.Event(), max_bytes=100
            )
        assert response.closed
        if length == "101":
            assert not response.reads


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("Content-Encoding", "gzip"),
        ("Content-Encoding", "br"),
        ("Content-Encoding", "identity,gzip"),
        ("Content-Length", "-1"),
        ("Content-Length", "1,1"),
        ("Content-Length", "abc"),
        ("Transfer-Encoding", "gzip"),
    ],
)
def test_compression_or_invalid_framing_rejected_before_body_read(name: str, value: str) -> None:
    response = Response()
    response.headers[name] = value
    transport, _ = client(response)
    with pytest.raises(GmailError, match="response_invalid"):
        transport.request("GET", PROFILE_URL, token=TOKEN, cancel=threading.Event())
    assert not response.reads
    assert response.closed


def test_duplicate_lengths_encodings_and_conflicting_chunked_length_rejected() -> None:
    for pairs in (
        (("Content-Length", "1"), ("Content-Length", "1")),
        (("Content-Encoding", "identity"), ("Content-Encoding", "identity")),
        (("Transfer-Encoding", "chunked"), ("Content-Length", "1")),
    ):
        response = Response()
        for name, value in pairs:
            response.headers[name] = value
        transport, _ = client(response)
        with pytest.raises(GmailError, match="response_invalid"):
            transport.request("GET", PROFILE_URL, token=TOKEN, cancel=threading.Event())
        assert not response.reads


def test_exact_length_identity_and_chunked_response_payloads_are_bounded() -> None:
    for framing in ("length", "chunked"):
        response = Response(body=b"x" * 40000)
        response.headers["Content-Encoding"] = "identity"
        if framing == "length":
            response.headers["Content-Length"] = "40000"
        else:
            response.headers["Transfer-Encoding"] = "chunked"
        transport, _ = client(response)
        assert (
            len(
                transport.request(
                    "GET", PROFILE_URL, token=TOKEN, cancel=threading.Event(), max_bytes=40000
                )
            )
            == 40000
        )
        assert len(response.reads) >= 3
        assert max(response.reads) <= http.READ_BLOCK_BYTES


def test_changed_response_url_is_rejected_even_with_injected_redirecting_opener() -> None:
    response = Response(url="https://attacker.invalid/redirect", body=b"secret")
    transport, _ = client(response)
    with pytest.raises(GmailError, match="http_error"):
        transport.request("GET", PROFILE_URL, token=TOKEN, cancel=threading.Event())
    assert not response.reads
    assert response.closed


def test_partial_content_header_cannot_claim_a_complete_successful_read() -> None:
    response = Response(body=b"{}")
    response.headers["Content-Range"] = "bytes 0-1/1000"
    transport, _ = client(response)
    with pytest.raises(GmailError, match="response_invalid"):
        transport.request("GET", PROFILE_URL, token=TOKEN, cancel=threading.Event())
    assert not response.reads
    assert response.closed


def test_reserved_action_path_is_rejected_even_with_valid_full_query() -> None:
    transport, opener = client()
    with pytest.raises(GmailError, match="request_invalid"):
        transport.request(
            "GET", read_url().replace("/m1?", "/send?"), token=TOKEN, cancel=threading.Event()
        )
    assert not opener.calls


def test_certifi_bundle_is_optional_but_used_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = ssl.create_default_context()
    module = ModuleType("certifi")
    module.__dict__["where"] = lambda: "/synthetic/ca-bundle.pem"
    calls: list[str | None] = []

    def create(*, cafile: str | None = None) -> ssl.SSLContext:
        calls.append(cafile)
        return context

    monkeypatch.setattr(http, "importlib", SimpleNamespace(import_module=lambda name: module))
    monkeypatch.setattr(ssl, "create_default_context", create)
    GoogleHTTP(opener=Opener())
    assert calls == ["/synthetic/ca-bundle.pem"]
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_missing_certifi_uses_verified_stdlib_context_without_new_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = ssl.create_default_context()
    calls: list[str | None] = []

    def unavailable(name: str) -> Never:
        raise ImportError("synthetic missing optional module")

    def create(*, cafile: str | None = None) -> ssl.SSLContext:
        calls.append(cafile)
        return context

    monkeypatch.setattr(http, "importlib", SimpleNamespace(import_module=unavailable))
    monkeypatch.setattr(ssl, "create_default_context", create)
    GoogleHTTP(opener=Opener())
    assert calls == [None]
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_tls_context_failure_exposes_only_closed_code(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*args: object, **kwargs: object) -> Never:
        raise ssl.SSLError("SECRET local certificate details")

    monkeypatch.setattr(ssl, "create_default_context", unavailable)
    with pytest.raises(GmailError, match=r"^tls_unavailable$") as caught:
        GoogleHTTP(opener=Opener())
    assert "SECRET" not in str(caught.value)


def test_unverified_ssl_context_is_rejected_without_dispatch() -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with pytest.raises(GmailError, match="tls_unavailable"):
        GoogleHTTP(opener=Opener(), context=context)


def test_default_opener_has_explicit_empty_proxy_verified_tls_and_no_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []
    opener = Opener()

    def build(*handlers: object) -> Opener:
        captured.extend(handlers)
        return opener

    monkeypatch.setattr(http, "build_opener", build)
    monkeypatch.setenv("https_proxy", "http://ambient-credentials@proxy.invalid")
    monkeypatch.setenv("http_proxy", "http://ambient-credentials@proxy.invalid")
    GoogleHTTP()
    proxies = [handler for handler in captured if isinstance(handler, ProxyHandler)]
    assert len(proxies) == 1
    assert vars(proxies[0])["proxies"] == {}
    assert any(isinstance(handler, HTTPSHandler) for handler in captured)
    redirects = [handler for handler in captured if isinstance(handler, http._NoRedirects)]
    assert len(redirects) == 1
    assert (
        redirects[0].redirect_request(
            Request(PROFILE_URL),
            io.BytesIO(),
            302,
            "redirect",
            Message(),
            "https://attacker.invalid",
        )
        is None
    )
