"""Synthetic HTTPS/DNS/framing tests; no external sockets or personal source data."""

import importlib
import socket
import ssl
import threading
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from satori.infrastructure.public_sources import public_web
from satori.infrastructure.public_sources.parsing import MAX_DISPLAY_CHARACTERS
from satori.infrastructure.public_sources.public_web import (
    MAX_HEADER_BYTES,
    MAX_WEB_BYTES,
    USER_AGENT,
    Deadline,
    PublicTarget,
    PublicWebError,
    read_public_url,
    require_public_address,
)

PUBLIC = "8.8.8.8"
URL = "https://example.org/public?query=synthetic"


@dataclass
class Clock:
    value: float = 0.0

    def __call__(self) -> float:
        return self.value


def response(
    body: bytes = b"SYNTHETIC PUBLIC TEXT",
    *,
    media: str = "text/plain; charset=utf-8",
    extra: bytes = b"",
    length: bool = True,
    status: int = 200,
) -> bytes:
    size = f"Content-Length: {len(body)}\r\n".encode() if length else b""
    return (
        f"HTTP/1.1 {status} Synthetic\r\nContent-Type: {media}\r\n".encode()
        + size
        + extra
        + b"\r\n"
        + body
    )


class Stream:
    def __init__(
        self,
        data: bytes,
        *,
        peer: str = PUBLIC,
        clock: Clock | None = None,
        fragment: int = 8192,
        delay: float = 0,
    ) -> None:
        self.peer_address = peer
        self.data = data
        self.clock, self.fragment, self.delay = clock, fragment, delay
        self.sent: list[bytes] = []
        self.closed = False
        self.receives = 0

    def send(self, data: bytes, deadline: Deadline) -> None:
        del deadline
        self.sent.append(data)

    def receive(self, size: int, deadline: Deadline) -> bytes:
        del deadline
        self.receives += 1
        if self.clock is not None:
            self.clock.value += self.delay
        count = min(size, self.fragment)
        result, self.data = self.data[:count], self.data[count:]
        return result

    def close(self) -> None:
        self.closed = True


class Transport:
    def __init__(
        self,
        data: bytes | None = None,
        *,
        addresses: tuple[str, ...] = (PUBLIC,),
        peer: str = PUBLIC,
        clock: Clock | None = None,
        fragment: int = 8192,
        delay: float = 0,
    ) -> None:
        self.addresses = addresses
        self.stream = Stream(
            response() if data is None else data,
            peer=peer,
            clock=clock,
            fragment=fragment,
            delay=delay,
        )
        self.resolved: list[str] = []
        self.opened: list[tuple[PublicTarget, str]] = []

    def resolve(self, hostname: str, deadline: Deadline) -> tuple[str, ...]:
        del deadline
        self.resolved.append(hostname)
        return self.addresses

    def open(self, target: PublicTarget, address: str, deadline: Deadline) -> Stream:
        del deadline
        self.opened.append((target, address))
        return self.stream


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org",
        "file:///etc/passwd",
        "ftp://example.org",
        "https://",
        "https://user:password@example.org",
        "https://example.org:444/",
        "https://example.org/#fragment",
        "https://example.org/#",
        "https://example.org/\n",
        "https://example.org/\r\nHost: localhost",
        "https://example.org/\\foo",
        "https://example.org/%xx",
        "https://localhost/",
        "https://service.local/",
        "https://metadata.google.internal/",
        "https://host.home.arpa/",
        "https://example.org./",
        "https://EXAMPLE.ORG/",
        "https://127.0.0.1:443/",
        "https://[::1]/",
        "https://[fe80::1%25en0]/",
        "https://2130706433/",
        "https://0x7f000001/",
        "https://-bad.example/",
        "https://bad_.example/",
        "https://example.org:",
        "https://example.org:0443/",
        "https://пример.рф/",
    ],
)
def test_invalid_or_literal_private_url_never_resolves_or_connects(url: str) -> None:
    transport = Transport()
    with pytest.raises(PublicWebError):
        read_public_url(url, transport=transport)
    assert transport.resolved == []
    assert transport.opened == []
    assert transport.stream.sent == []


@pytest.mark.parametrize(
    "address",
    [
        "0.0.0.0",
        "10.0.0.1",
        "100.64.1.1",
        "100.100.100.200",
        "127.0.0.1",
        "169.254.169.254",
        "172.16.1.1",
        "192.0.0.8",
        "192.0.0.9",
        "192.0.2.1",
        "192.88.99.1",
        "192.168.1.1",
        "198.18.0.1",
        "198.51.100.1",
        "203.0.113.1",
        "224.0.0.1",
        "239.255.255.255",
        "240.0.0.1",
        "255.255.255.255",
        "168.63.129.16",
        "::",
        "::1",
        "::ffff:127.0.0.1",
        "::ffff:8.8.8.8",
        "64:ff9b::7f00:1",
        "64:ff9b:1::a00:1",
        "100::1",
        "2001::1",
        "2001:20::1",
        "2001:db8::1",
        "2002:7f00:1::",
        "2002:808:808::",
        "fc00::1",
        "fe80::1",
        "ff02::1",
        "3fff::1",
        "5f00::1",
        "fe80::1%en0",
    ],
)
def test_every_dns_answer_is_public_unicast_before_any_connection(address: str) -> None:
    transport = Transport(addresses=(PUBLIC, address))
    with pytest.raises(PublicWebError, match=r"^destination_denied$"):
        read_public_url(URL, transport=transport)
    assert transport.resolved == ["example.org"]
    assert transport.opened == []


@pytest.mark.parametrize(
    "address", ["8.8.8.8", "1.1.1.1", "2001:4860:4860::8888", "2606:4700::1111"]
)
def test_normal_public_unicast_is_supported(address: str) -> None:
    assert require_public_address(address) == address


def test_plain_read_is_exact_bounded_and_source_free_in_repr() -> None:
    transport = Transport()
    result = read_public_url(URL, transport=transport)
    assert result.text == "SYNTHETIC PUBLIC TEXT"
    assert result.url == URL
    assert result.status == "complete"
    assert result.media_type == "text/plain"
    assert result.raw_bytes == 21
    assert result.expires_at - result.observed_at == timedelta(minutes=5)
    assert result.observed_at.utcoffset() == timedelta(0)
    assert "SYNTHETIC" not in repr(result)
    assert "example.org" not in repr(result)
    assert transport.resolved == ["example.org"]
    assert transport.opened[0][1] == PUBLIC
    assert transport.stream.closed
    request = transport.stream.sent[0]
    assert request.startswith(b"GET /public?query=synthetic HTTP/1.1\r\nHost: example.org\r\n")
    assert USER_AGENT.encode() in request
    assert b"Accept-Encoding: identity" in request
    for forbidden in (b"Cookie:", b"Authorization:", b"Proxy-Authorization:", b"Referer:"):
        assert forbidden not in request


@pytest.mark.parametrize("peer", ["127.0.0.1", "169.254.169.254", "1.1.1.1", "::ffff:8.8.8.8"])
def test_dns_rebind_or_wrong_peer_never_sends_http(peer: str) -> None:
    transport = Transport(peer=peer)
    with pytest.raises(PublicWebError, match=r"^destination_denied$"):
        read_public_url(URL, transport=transport)
    assert transport.stream.sent == []
    assert transport.stream.closed


@pytest.mark.parametrize("code", [300, 301, 302, 303, 304, 305, 307, 308, 399])
def test_all_redirects_are_denied_without_following_even_public_destinations(code: int) -> None:
    transport = Transport(response(status=code, extra=b"Location: https://example.net/\r\n"))
    with pytest.raises(PublicWebError, match=r"^redirect_denied$"):
        read_public_url(URL, transport=transport)
    assert len(transport.opened) == 1
    assert len(transport.stream.sent) == 1
    assert transport.stream.closed


@pytest.mark.parametrize(
    "location", ["http://127.0.0.1/", "https://169.254.169.254/", "//[::1]/", "/next"]
)
def test_private_relative_and_protocol_relative_redirects_are_never_followed(location: str) -> None:
    transport = Transport(response(status=302, extra=f"Location: {location}\r\n".encode()))
    with pytest.raises(PublicWebError, match=r"^redirect_denied$"):
        read_public_url(URL, transport=transport)
    assert len(transport.opened) == 1


@pytest.mark.parametrize("media", ["application/pdf", "image/png", "application/octet-stream", ""])
def test_nontext_media_is_honestly_unsupported(media: str) -> None:
    transport = Transport(response(media=media))
    with pytest.raises(PublicWebError, match=r"^media_unsupported$"):
        read_public_url(URL, transport=transport)
    assert transport.stream.closed


@pytest.mark.parametrize("encoding", ["gzip", "deflate", "br", "identity,gzip", "compress"])
def test_encoded_or_compressed_content_is_rejected_before_decompression(encoding: str) -> None:
    transport = Transport(response(extra=f"Content-Encoding: {encoding}\r\n".encode()))
    with pytest.raises(PublicWebError, match=r"^encoding_unsupported$"):
        read_public_url(URL, transport=transport)


@pytest.mark.parametrize(
    "media", ["text/plain; charset=iso-8859-1", "text/html; charset=windows-1251"]
)
def test_non_utf8_charset_is_not_guessed(media: str) -> None:
    with pytest.raises(PublicWebError, match=r"^encoding_unsupported$"):
        read_public_url(URL, transport=Transport(response(media=media)))


def test_invalid_utf8_and_controls_are_never_silently_replaced() -> None:
    for body, expected in [
        (b"\xff", "encoding_unsupported"),
        (b"hello\x1b[31m", "content_invalid"),
    ]:
        with pytest.raises(PublicWebError) as error:
            read_public_url(URL, transport=Transport(response(body)))
        assert error.value.code == expected
        assert "hello" not in str(error.value)


def test_html_extracts_only_inert_text_without_scripts_resources_or_markup() -> None:
    body = (
        b"<html><head><title>HIDDEN</title><script>STEAL()</script></head><body>"
        b"<p>PUBLIC &amp; TEXT <a href='https://private.invalid'>label</a></p>"
        b"<img src='https://private.invalid/pixel' onerror='STEAL()'>"
        b"<script>EVIL</script><style>CSS</style><template>HIDDEN</template>"
        b"<noscript>HIDDEN</noscript><iframe src='https://elsewhere.invalid'>HIDDEN</iframe>"
        b"</body></html>"
    )
    transport = Transport(response(body, media="text/html"))
    result = read_public_url(URL, transport=transport)
    assert "PUBLIC & TEXT label" in result.text
    for forbidden in ("HIDDEN", "EVIL", "CSS", "STEAL", "<", "private.invalid"):
        assert forbidden not in result.text
    assert len(transport.opened) == 1


@pytest.mark.parametrize("media", ["text/plain", "text/markdown", "text/csv", "application/json"])
def test_supported_nonhtml_text_is_not_interpreted(media: str) -> None:
    result = read_public_url(
        URL, transport=Transport(response(b"<script>DATA</script>", media=media))
    )
    assert result.text == "<script>DATA</script>"


def test_empty_and_display_partial_do_not_claim_complete_source_coverage() -> None:
    empty = read_public_url(URL, transport=Transport(response(b"")))
    assert empty.status == "empty"
    assert empty.text == ""
    partial = read_public_url(
        URL, transport=Transport(response(b"a" * (MAX_DISPLAY_CHARACTERS + 1)))
    )
    assert partial.status == "partial"
    assert len(partial.text) == MAX_DISPLAY_CHARACTERS
    assert partial.raw_bytes == MAX_DISPLAY_CHARACTERS + 1


@pytest.mark.parametrize("framing", ["length", "eof", "chunked"])
def test_all_supported_framing_modes_have_raw_byte_bounds(framing: str) -> None:
    size = MAX_WEB_BYTES + 1
    if framing == "length":
        data = response(b"x" * size)
    elif framing == "eof":
        data = response(b"x" * size, length=False)
    else:
        data = response(
            f"{size:x}\r\n".encode() + b"x" * size + b"\r\n0\r\n\r\n",
            length=False,
            extra=b"Transfer-Encoding: chunked\r\n",
        )
    with pytest.raises(PublicWebError, match=r"^response_too_large$"):
        read_public_url(URL, transport=Transport(data))


def test_valid_chunked_response_and_eof_response() -> None:
    chunked = response(
        b"5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n",
        length=False,
        extra=b"Transfer-Encoding: chunked\r\n",
    )
    assert read_public_url(URL, transport=Transport(chunked)).text == "hello world"
    assert (
        read_public_url(URL, transport=Transport(response(b"hello", length=False))).text == "hello"
    )


@pytest.mark.parametrize(
    "extra",
    [
        b"Content-Length: 1\r\n",
        b"Content-Type: text/html\r\n",
        b"Transfer-Encoding: chunked\r\n",
        b"Content-Encoding: identity\r\nContent-Encoding: gzip\r\n",
        b" folded: value\r\n",
        b"Bad Header: value\r\n",
        b"Bad: value\x00\r\n",
    ],
)
def test_ambiguous_headers_fail_closed(extra: bytes) -> None:
    with pytest.raises(PublicWebError, match=r"^response_invalid$"):
        read_public_url(URL, transport=Transport(response(extra=extra)))


@pytest.mark.parametrize(
    "body", [b"-1\r\n", b"2;foo=bar\r\nab\r\n0\r\n\r\n", b"1\r\nxXX", b"0\r\nX-Trailer: y\r\n\r\n"]
)
def test_unsupported_or_malformed_chunk_framing_is_rejected(body: bytes) -> None:
    data = response(body, length=False, extra=b"Transfer-Encoding: chunked\r\n")
    with pytest.raises(PublicWebError, match=r"^response_invalid$"):
        read_public_url(URL, transport=Transport(data))


def test_header_bytes_and_count_are_independently_bounded() -> None:
    too_many = b"X-Synthetic: x\r\n" * 101
    too_long = b"".join(f"X-{n}: ".encode() + b"a" * 8000 + b"\r\n" for n in range(5))
    assert len(too_long) > MAX_HEADER_BYTES
    for headers in (too_many, too_long):
        with pytest.raises(PublicWebError, match=r"^response_too_large$"):
            read_public_url(URL, transport=Transport(response(extra=headers)))


@pytest.mark.parametrize("body_fragment", [False, True])
def test_slow_drip_headers_and_body_share_absolute_deadline(body_fragment: bool) -> None:
    clock = Clock()
    data = response(b"a" * 100)
    transport = Transport(data, clock=clock, fragment=1, delay=0.6)
    if body_fragment:
        transport.stream.data = data
        original = transport.stream.receive

        def first_header(size: int, deadline: Deadline) -> bytes:
            if transport.stream.receives == 0:
                count = data.index(b"\r\n\r\n") + 4
                transport.stream.receives += 1
                result, transport.stream.data = (
                    transport.stream.data[:count],
                    transport.stream.data[count:],
                )
                return result
            return original(size, deadline)

        transport.stream.receive = first_header  # type: ignore[method-assign]
    with pytest.raises(PublicWebError, match=r"^timeout$"):
        read_public_url(URL, transport=transport, timeout=2.0, clock=clock)
    assert clock.value <= 2.4
    assert transport.stream.closed


def test_cancel_before_dns_or_after_open_never_sends_request() -> None:
    transport = Transport()
    with pytest.raises(PublicWebError, match=r"^cancelled$"):
        read_public_url(URL, transport=transport, cancelled=lambda: True)
    assert transport.resolved == []
    assert transport.opened == []
    opened = False
    original = transport.open

    def open_and_cancel(target: PublicTarget, address: str, deadline: Deadline) -> Stream:
        nonlocal opened
        opened = True
        return original(target, address, deadline)

    transport.open = open_and_cancel  # type: ignore[method-assign]
    with pytest.raises(PublicWebError, match=r"^cancelled$"):
        read_public_url(URL, transport=transport, cancelled=lambda: opened)
    assert transport.stream.sent == []
    assert transport.stream.closed


def test_resolver_does_not_extend_deadline_or_allow_unbounded_answers() -> None:
    for addresses in [(), (PUBLIC,) * 33]:
        with pytest.raises(PublicWebError, match=r"^dns_failed$"):
            read_public_url(URL, transport=Transport(addresses=addresses))


def test_direct_socket_connect_is_pinned_numeric_and_tls_uses_original_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connected: list[tuple[str, int]] = []
    sni: list[str] = []
    resolved: list[str] = []
    stream = Stream(response())

    class Raw:
        def settimeout(self, timeout: float) -> None:
            assert 0 < timeout <= 10

        def connect(self, target: tuple[str, int]) -> None:
            connected.append(target)

        def getpeername(self) -> tuple[str, int]:
            return PUBLIC, 443

        def selected_alpn_protocol(self) -> str:
            return "http/1.1"

        def sendall(self, data: bytes) -> None:
            stream.sent.append(data)

        def recv(self, size: int) -> bytes:
            return stream.receive(size, Deadline(10, lambda: False, lambda: 0))

        def close(self) -> None:
            stream.close()

    raw = Raw()

    class Context:
        minimum_version: Any = None
        check_hostname: bool
        verify_mode: ssl.VerifyMode

        def set_alpn_protocols(self, protocols: list[str]) -> None:
            assert protocols == ["http/1.1"]

        def wrap_socket(self, value: Raw, *, server_hostname: str) -> Raw:
            assert value is raw
            sni.append(server_hostname)
            return raw

    def resolve(hostname: str, port: int, **kwargs: Any) -> list[Any]:
        del kwargs
        resolved.append(hostname)
        assert port == 443
        # A hypothetical second DNS call would point privately; it must never happen.
        value = PUBLIC if len(resolved) == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (value, 443))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket, "socket", lambda *args: raw)
    monkeypatch.setattr(public_web, "_verified_context", Context)
    result = read_public_url(URL)
    assert result.status == "complete"
    assert resolved == ["example.org"]
    assert connected == [(PUBLIC, 443)]
    assert sni == ["example.org"]
    assert stream.closed


def test_blocked_dns_finishes_with_deadline_without_waiting_for_resolver_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()

    def blocked(*args: Any, **kwargs: Any) -> list[Any]:
        del args, kwargs
        release.wait(1)
        return []

    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    try:
        with pytest.raises(PublicWebError, match=r"^timeout$"):
            read_public_url(URL, timeout=0.03)
    finally:
        release.set()


@pytest.mark.parametrize(
    "exception",
    [OSError("PRIVATE EXCEPTION"), ssl.SSLError("PRIVATE TLS"), TimeoutError("PRIVATE TIMEOUT")],
)
def test_transport_exception_is_closed_and_never_echoes_raw_exception(exception: Exception) -> None:
    transport = Transport()

    def failing(target: PublicTarget, address: str, deadline: Deadline) -> Stream:
        del target, address, deadline
        raise exception

    transport.open = failing  # type: ignore[method-assign]
    with pytest.raises(PublicWebError) as error:
        read_public_url(URL, transport=transport)
    assert "PRIVATE" not in str(error.value)
    assert "example.org" not in str(error.value)


def test_large_chunk_framing_overhead_cannot_bypass_total_wire_bound() -> None:
    body = b"1\r\nx\r\n" * 100_000 + b"0\r\n\r\n"
    data = response(body, length=False, extra=b"Transfer-Encoding: chunked\r\n")
    with pytest.raises(PublicWebError, match=r"^response_too_large$"):
        read_public_url(URL, transport=Transport(data))


def test_clock_reversal_during_read_fails_closed() -> None:
    clock = Clock(10)
    transport = Transport(clock=clock, fragment=1, delay=-0.1)
    with pytest.raises(PublicWebError, match=r"^clock_invalid$"):
        read_public_url(URL, transport=transport, clock=clock)
    assert transport.stream.closed


@pytest.mark.parametrize("value", [0.0, -1.0, 10.01, float("inf"), float("nan"), True])
def test_invalid_timeout_never_starts_source_io(value: float) -> None:
    transport = Transport()
    with pytest.raises(PublicWebError, match=r"^timeout$"):
        read_public_url(URL, transport=transport, timeout=value)
    assert transport.resolved == []
    assert transport.opened == []


@pytest.mark.parametrize(
    ("check_hostname", "verify_mode"),
    [(True, ssl.CERT_REQUIRED), (False, ssl.CERT_REQUIRED), (True, ssl.CERT_NONE)],
)
def test_bundled_ca_loading_preserves_chain_and_hostname_verification(
    monkeypatch: pytest.MonkeyPatch, check_hostname: bool, verify_mode: ssl.VerifyMode
) -> None:
    calls: list[dict[str, str]] = []

    class Context:
        minimum_version: Any = None
        check_hostname: bool
        verify_mode: ssl.VerifyMode

        def set_alpn_protocols(self, protocols: list[str]) -> None:
            assert protocols == ["http/1.1"]

    context = Context()
    context.check_hostname = check_hostname
    context.verify_mode = verify_mode

    def load(**kwargs: str) -> Context:
        calls.append(kwargs)
        return context

    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name: SimpleNamespace(where=lambda: "SYNTHETIC BUNDLED CA.pem"),
    )
    monkeypatch.setattr(ssl, "create_default_context", load)
    if check_hostname and verify_mode == ssl.CERT_REQUIRED:
        actual: object = public_web._verified_context()
        assert actual is context
        assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    else:
        with pytest.raises(PublicWebError, match=r"^tls_failed$"):
            public_web._verified_context()
    assert calls == [{"cafile": "SYNTHETIC BUNDLED CA.pem"}]
