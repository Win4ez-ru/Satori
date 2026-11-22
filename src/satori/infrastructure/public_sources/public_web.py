"""One bounded public HTTPS read, with numeric-IP-pinned TLS and no ambient credentials.

No redirects, proxy discovery, cookies, netrc, scripts or remote resources are used.
Run blocking work in the application's owned cancellable helper; the owner must also
fence late results and enforce its independent process deadline.
"""

import importlib
import ipaddress
import math
import re
import socket
import ssl
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol, cast
from urllib.parse import urlsplit

from satori.infrastructure.public_sources.parsing import InvalidPublicText, extract_public_text

MAX_WEB_BYTES = 512 * 1024
MAX_HEADER_BYTES = 32 * 1024
MAX_TIMEOUT = 10.0
USER_AGENT = "SatoriPublicRead/0.1 (+https://github.com/Win4ez-ru/Satori)"
WebErrorCode = Literal[
    "url_invalid",
    "destination_denied",
    "dns_failed",
    "timeout",
    "cancelled",
    "tls_failed",
    "transport_failed",
    "redirect_denied",
    "http_error",
    "response_invalid",
    "response_too_large",
    "media_unsupported",
    "encoding_unsupported",
    "content_invalid",
    "clock_invalid",
]
_ALLOWED_MEDIA = frozenset(
    {"text/plain", "text/markdown", "text/csv", "text/html", "application/json"}
)
_V4_DENIED = tuple(
    ipaddress.ip_network(value)
    for value in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.88.99.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "168.63.129.16/32",
    )
)
_V6_DENIED = tuple(
    ipaddress.ip_network(value)
    for value in ("2001::/23", "2001:db8::/32", "2002::/16", "3fff::/20")
)
_V6_GLOBAL = ipaddress.ip_network("2000::/3")


class PublicWebError(ValueError):
    def __init__(self, code: WebErrorCode) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PublicTarget:
    url: str = field(repr=False)
    hostname: str = field(repr=False)
    host_header: str = field(repr=False)
    request_target: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class PublicWebResult:
    url: str = field(repr=False)
    text: str = field(repr=False)
    media_type: str
    status: Literal["complete", "partial", "empty"]
    observed_at: datetime
    expires_at: datetime
    raw_bytes: int


class Deadline:
    def __init__(
        self, seconds: float, cancelled: Callable[[], bool], clock: Callable[[], float]
    ) -> None:
        self._clock, self._cancelled = clock, cancelled
        self._last = clock()
        if not math.isfinite(self._last):
            raise PublicWebError("clock_invalid")
        self._end = self._last + seconds

    def remaining(self) -> float:
        if self._cancelled():
            raise PublicWebError("cancelled")
        now = self._clock()
        if not math.isfinite(now) or now < self._last:
            raise PublicWebError("clock_invalid")
        self._last = now
        if now >= self._end:
            raise PublicWebError("timeout")
        return self._end - now


class WebStream(Protocol):
    peer_address: str

    def send(self, data: bytes, deadline: Deadline) -> None: ...
    def receive(self, size: int, deadline: Deadline) -> bytes: ...
    def close(self) -> None: ...


class WebTransport(Protocol):
    def resolve(self, hostname: str, deadline: Deadline) -> tuple[str, ...]: ...
    def open(self, target: PublicTarget, address: str, deadline: Deadline) -> WebStream: ...


def require_public_address(value: str) -> str:
    """Conservative public-unicast admission, independent of Python's registry version."""
    try:
        if "%" in value:
            raise ValueError
        address = ipaddress.ip_address(value)
    except ValueError:
        raise PublicWebError("destination_denied") from None
    if (
        not address.is_global
        or address.is_multicast
        or address.is_reserved
        or address.is_loopback
        or address.is_link_local
        or address.is_unspecified
    ):
        raise PublicWebError("destination_denied")
    if isinstance(address, ipaddress.IPv4Address):
        denied = any(address in network for network in _V4_DENIED)
    else:
        denied = (
            address not in _V6_GLOBAL
            or address.ipv4_mapped is not None
            or address.sixtofour is not None
            or address.teredo is not None
            or any(address in network for network in _V6_DENIED)
        )
    if denied:
        raise PublicWebError("destination_denied")
    return str(address)


def validate_public_url(url: str) -> PublicTarget:
    """Syntactic validation only; every resolved destination is checked before connecting."""
    if (
        type(url) is not str
        or not 0 < len(url) <= 4096
        or any(not 33 <= ord(char) <= 126 for char in url)
        or "\\" in url
        or "#" in url
        or re.search(r"%(?![0-9A-Fa-f]{2})", url)
    ):
        raise PublicWebError("url_invalid")
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname or ""
        if (
            not url.startswith("https://")
            or parsed.scheme != "https"
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
            or not hostname
            or "%" in parsed.netloc
        ):
            raise ValueError
        try:
            literal = ipaddress.ip_address(hostname)
        except ValueError:
            if (
                len(hostname) > 253
                or "." not in hostname
                or hostname.endswith(".")
                or any(
                    re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
                    for label in hostname.split(".")
                )
                or hostname.endswith((".localhost", ".local", ".internal", ".home.arpa", ".onion"))
            ):
                raise ValueError from None
            host_header = hostname
        else:
            require_public_address(hostname)
            host_header = f"[{hostname}]" if literal.version == 6 else hostname
        expected_netloc = host_header + (":443" if parsed.port is not None else "")
        if parsed.netloc != expected_netloc:
            raise ValueError
        request_target = parsed.path or "/"
        if not request_target.startswith("/"):
            raise ValueError
        if parsed.query:
            request_target += "?" + parsed.query
        return PublicTarget(url, hostname, host_header, request_target)
    except ValueError as error:
        if isinstance(error, PublicWebError):
            raise
        raise PublicWebError("url_invalid") from None


class _SocketStream:
    def __init__(self, stream: ssl.SSLSocket) -> None:
        self._stream = stream
        self.peer_address = str(stream.getpeername()[0])

    def send(self, data: bytes, deadline: Deadline) -> None:
        self._stream.settimeout(deadline.remaining())
        self._stream.sendall(data)
        deadline.remaining()

    def receive(self, size: int, deadline: Deadline) -> bytes:
        self._stream.settimeout(deadline.remaining())
        data = self._stream.recv(size)
        deadline.remaining()
        return data

    def close(self) -> None:
        self._stream.close()


def _verified_context() -> ssl.SSLContext:
    """Use the bundled desktop CA set when present; never weaken chain/hostname checks."""
    try:
        try:
            certifi = importlib.import_module("certifi")
        except ImportError:
            context = ssl.create_default_context()
        else:
            where = getattr(certifi, "where", None)
            if not callable(where):
                raise PublicWebError("tls_failed")
            cafile = cast(Callable[[], str], where)()
            if type(cafile) is not str or not cafile:
                raise PublicWebError("tls_failed")
            context = ssl.create_default_context(cafile=cafile)
        if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
            raise PublicWebError("tls_failed")
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.set_alpn_protocols(["http/1.1"])
        return context
    except (OSError, ValueError):
        raise PublicWebError("tls_failed") from None


class SocketWebTransport:
    """Direct TCP/TLS only. One daemon resolver call cannot extend the caller's deadline."""

    def resolve(self, hostname: str, deadline: Deadline) -> tuple[str, ...]:
        completed = threading.Event()
        addresses: list[str] = []
        failures: list[bool] = []

        def run() -> None:
            try:
                answers = socket.getaddrinfo(
                    hostname, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
                )
                addresses.extend(str(item[4][0]) for item in answers[:33])
            except Exception:
                failures.append(True)
            finally:
                completed.set()

        threading.Thread(target=run, daemon=True).start()
        while not completed.wait(min(0.05, deadline.remaining())):
            pass
        deadline.remaining()
        if failures or not addresses or len(addresses) > 32:
            raise PublicWebError("dns_failed")
        return tuple(dict.fromkeys(addresses))

    def open(self, target: PublicTarget, address: str, deadline: Deadline) -> WebStream:
        family = socket.AF_INET6 if ipaddress.ip_address(address).version == 6 else socket.AF_INET
        raw = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        secured: ssl.SSLSocket | None = None
        try:
            raw.settimeout(deadline.remaining())
            raw.connect((address, 443))
            if require_public_address(str(raw.getpeername()[0])) != address:
                raise PublicWebError("destination_denied")
            context = _verified_context()
            raw.settimeout(deadline.remaining())
            secured = context.wrap_socket(raw, server_hostname=target.hostname)
            deadline.remaining()
            if secured.selected_alpn_protocol() not in {None, "http/1.1"}:
                secured.close()
                raise PublicWebError("response_invalid")
            return _SocketStream(secured)
        except Exception:
            if secured is not None:
                secured.close()
            raw.close()
            raise


class _Reader:
    def __init__(self, stream: WebStream, deadline: Deadline) -> None:
        self.stream, self.deadline = stream, deadline
        self.buffer = bytearray()
        self.received_bytes = 0

    def receive(self) -> bytes:
        self.deadline.remaining()
        data = self.stream.receive(8192, self.deadline)
        self.deadline.remaining()
        if type(data) is not bytes or len(data) > 8192:
            raise PublicWebError("response_invalid")
        self.received_bytes += len(data)
        if self.received_bytes > MAX_WEB_BYTES + MAX_HEADER_BYTES:
            raise PublicWebError("response_too_large")
        return data

    def exact(self, count: int) -> bytes:
        while len(self.buffer) < count:
            data = self.receive()
            if not data:
                raise PublicWebError("response_invalid")
            self.buffer.extend(data)
        result = bytes(self.buffer[:count])
        del self.buffer[:count]
        return result

    def line(self, maximum: int) -> bytes:
        while True:
            index = self.buffer.find(b"\r\n")
            if index >= 0:
                if index > maximum:
                    raise PublicWebError("response_invalid")
                return self.exact(index + 2)[:-2]
            if len(self.buffer) > maximum:
                raise PublicWebError("response_invalid")
            data = self.receive()
            if not data:
                raise PublicWebError("response_invalid")
            self.buffer.extend(data)


def _headers(reader: _Reader) -> dict[str, str]:
    line = reader.line(1024)
    match = re.fullmatch(rb"HTTP/1\.[01] ([0-9]{3}) [\x20-\x7e]*", line)
    if match is None:
        raise PublicWebError("response_invalid")
    status = int(match[1])
    if 300 <= status < 400:
        raise PublicWebError("redirect_denied")
    if status != 200:
        raise PublicWebError("http_error")
    headers: dict[str, str] = {}
    total = len(line) + 2
    for _ in range(101):
        line = reader.line(8192)
        total += len(line) + 2
        if total > MAX_HEADER_BYTES:
            raise PublicWebError("response_too_large")
        if not line:
            return headers
        key, separator, value = line.partition(b":")
        if (
            not separator
            or re.fullmatch(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key) is None
            or any((byte < 32 and byte != 9) or byte == 127 for byte in value)
        ):
            raise PublicWebError("response_invalid")
        name = key.decode("ascii").lower()
        if name in headers:
            if name in {"content-type", "content-length", "content-encoding", "transfer-encoding"}:
                raise PublicWebError("response_invalid")
        else:
            headers[name] = value.decode("latin-1").strip()
    raise PublicWebError("response_too_large")


def _media(headers: dict[str, str]) -> str:
    if headers.get("content-encoding", "identity").lower() != "identity":
        raise PublicWebError("encoding_unsupported")
    parts = headers.get("content-type", "").split(";")
    media = parts[0].strip().lower()
    if media not in _ALLOWED_MEDIA:
        raise PublicWebError("media_unsupported")
    charset: str | None = None
    for part in parts[1:]:
        key, separator, value = part.strip().partition("=")
        if not separator:
            raise PublicWebError("response_invalid")
        if key.lower() == "charset":
            if charset is not None:
                raise PublicWebError("response_invalid")
            charset = value.strip('"').lower()
    if charset not in {None, "utf-8", "utf8"}:
        raise PublicWebError("encoding_unsupported")
    return media


def _body(reader: _Reader, headers: dict[str, str]) -> bytes:
    length, transfer = headers.get("content-length"), headers.get("transfer-encoding")
    if length is not None and transfer is not None:
        raise PublicWebError("response_invalid")
    if transfer is not None:
        if transfer.lower() != "chunked":
            raise PublicWebError("response_invalid")
        chunks: list[bytes] = []
        total = 0
        while True:
            value = reader.line(16)
            if re.fullmatch(rb"[0-9a-fA-F]{1,8}", value) is None:
                raise PublicWebError("response_invalid")
            size = int(value, 16)
            total += size
            if total > MAX_WEB_BYTES:
                raise PublicWebError("response_too_large")
            if size == 0:
                if reader.line(8192) or reader.buffer:
                    raise PublicWebError("response_invalid")
                return b"".join(chunks)
            chunks.append(reader.exact(size))
            if reader.exact(2) != b"\r\n":
                raise PublicWebError("response_invalid")
    if length is not None:
        if re.fullmatch(r"[0-9]{1,10}", length) is None:
            raise PublicWebError("response_invalid")
        size = int(length)
        if size > MAX_WEB_BYTES:
            raise PublicWebError("response_too_large")
        data = reader.exact(size)
        if reader.buffer:
            raise PublicWebError("response_invalid")
        return data
    data = bytes(reader.buffer)
    reader.buffer.clear()
    while True:
        if len(data) > MAX_WEB_BYTES:
            raise PublicWebError("response_too_large")
        chunk = reader.receive()
        if not chunk:
            return data
        data += chunk


def read_public_url(
    url: str,
    *,
    transport: WebTransport | None = None,
    timeout: float = MAX_TIMEOUT,
    cancelled: Callable[[], bool] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> PublicWebResult:
    """Read one explicitly requested public URL, without redirects/retries or side requests."""
    if (
        type(timeout) not in {float, int}
        or not math.isfinite(timeout)
        or not 0 < timeout <= MAX_TIMEOUT
    ):
        raise PublicWebError("timeout")
    target = validate_public_url(url)
    deadline = Deadline(timeout, cancelled or (lambda: False), clock)
    adapter = transport or SocketWebTransport()
    stream: WebStream | None = None
    try:
        deadline.remaining()
        try:
            literal = ipaddress.ip_address(target.hostname)
        except ValueError:
            answers = adapter.resolve(target.hostname, deadline)
            if not isinstance(answers, tuple) or not 1 <= len(answers) <= 32:
                raise PublicWebError("dns_failed") from None
            addresses = tuple(require_public_address(value) for value in answers)
        else:
            addresses = (require_public_address(str(literal)),)
        deadline.remaining()
        stream = adapter.open(target, addresses[0], deadline)
        deadline.remaining()
        if require_public_address(stream.peer_address) != addresses[0]:
            raise PublicWebError("destination_denied")
        request = (
            f"GET {target.request_target} HTTP/1.1\r\nHost: {target.host_header}\r\n"
            f"User-Agent: {USER_AGENT}\r\nAccept: text/html, text/plain, text/markdown, "
            "text/csv, application/json\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n"
        ).encode("ascii")
        stream.send(request, deadline)
        deadline.remaining()
        reader = _Reader(stream, deadline)
        headers = _headers(reader)
        media = _media(headers)
        raw = _body(reader, headers)
        text, partial = extract_public_text(raw, media)
        deadline.remaining()
        observed_at = datetime.now(UTC)
        return PublicWebResult(
            url,
            text,
            media,
            "partial" if partial else "complete" if text else "empty",
            observed_at,
            observed_at + timedelta(minutes=5),
            len(raw),
        )
    except PublicWebError:
        raise
    except InvalidPublicText as error:
        if str(error) == "encoding_unsupported":
            raise PublicWebError("encoding_unsupported") from None
        raise PublicWebError("content_invalid") from None
    except TimeoutError:
        raise PublicWebError("timeout") from None
    except ssl.SSLError:
        raise PublicWebError("tls_failed") from None
    except (OSError, ValueError, TypeError, AttributeError):
        raise PublicWebError("transport_failed") from None
    finally:
        if stream is not None:
            with suppress(OSError):
                stream.close()
