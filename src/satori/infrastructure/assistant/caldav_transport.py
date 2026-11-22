"""Bounded iCloud HTTPS transport; secrets exist only at the actual wire boundary."""

import base64
import contextlib
import http.client
import re
import socket
import ssl
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

MAX_DAV_BYTES = 512 * 1024
MAX_DAV_REQUEST_BYTES = 64 * 1024


class CalDAVError(ValueError):
    """Finite content-free error; never retain a native/provider exception body."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def icloud_url(value: str) -> str:
    """Only exact HTTPS iCloud CalDAV service names, no arbitrary URL/port/authority."""
    if (
        not isinstance(value, str)
        or len(value) > 4096
        or not value.isascii()
        or any(ord(char) < 33 or ord(char) == 127 for char in value)
        or "\\" in value
    ):
        raise CalDAVError("caldav_endpoint_invalid")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or not re.fullmatch(r"(?:caldav|p[0-9]{1,3}-caldav)\.icloud\.com", host)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith("/")
            or re.search(r"%(?![0-9a-fA-F]{2})", parsed.path)
            or re.search(r"%2f", parsed.path, re.IGNORECASE)
        ):
            raise ValueError
        decoded = unquote(parsed.path, errors="strict")
        if (
            "%" in decoded
            or "\\" in decoded
            or any(part in {".", ".."} for part in decoded.split("/"))
            or any(ord(char) < 32 or ord(char) == 127 for char in decoded)
        ):
            raise ValueError
        return urlunsplit(("https", host, parsed.path, "", ""))
    except (ValueError, UnicodeError):
        raise CalDAVError("caldav_endpoint_invalid") from None


def resolve_icloud_href(base: str, href: str) -> str:
    # Validate dot/encoded traversal before urljoin can normalize it away.
    if not href or len(href) > 4096 or any(ord(c) < 33 or ord(c) == 127 for c in href):
        raise CalDAVError("caldav_href_invalid")
    parsed = urlsplit(href)
    decoded = unquote(parsed.path, errors="strict")
    if any(part in {".", ".."} for part in decoded.split("/")) or "%" in decoded:
        raise CalDAVError("caldav_href_invalid")
    return icloud_url(urljoin(icloud_url(base), href))


@dataclass(frozen=True, slots=True, repr=False)
class CalDAVCredentials:
    """Host-injected app-specific credentials; never loaded from env or user files."""

    username: str = field(repr=False)
    app_password: str = field(repr=False)

    def authorization(self) -> str:
        if (
            not self.username
            or not self.app_password
            or len(self.username) > 512
            or len(self.app_password) > 512
            or ":" in self.username
            or any(ord(c) < 32 or ord(c) == 127 for c in self.username + self.app_password)
        ):
            raise CalDAVError("caldav_credentials_unavailable")
        encoded = base64.b64encode((self.username + ":" + self.app_password).encode()).decode()
        return "Basic " + encoded


@dataclass(frozen=True, slots=True)
class CalDAVResponse:
    status: int
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    body: bytes = field(default=b"", repr=False)


class CalDAVHTTP(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes,
        before_dispatch: Callable[[], None],
    ) -> CalDAVResponse: ...


class ICloudHTTPSCalDAVTransport:
    """One bounded request, no redirects, retries, cookies, proxy env or body logs."""

    def __init__(self, credentials: Callable[[], CalDAVCredentials]) -> None:
        self._credentials = credentials

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes,
        before_dispatch: Callable[[], None],
    ) -> CalDAVResponse:
        if method not in {"PROPFIND", "REPORT", "GET", "PUT"}:
            raise CalDAVError("caldav_method_invalid")
        url = icloud_url(url)
        if len(body) > MAX_DAV_REQUEST_BYTES or any(
            not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,63}", key)
            or len(value) > 4096
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or key.lower() in {"authorization", "host", "cookie", "proxy-authorization"}
            for key, value in headers.items()
        ):
            raise CalDAVError("caldav_request_invalid")
        parsed = urlsplit(url)
        assert parsed.hostname is not None
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        connection = http.client.HTTPSConnection(
            parsed.hostname, port=443, timeout=10, context=context
        )
        connected_socket: list[socket.socket] = []
        expired = threading.Event()

        def abort_socket() -> None:
            # Per-read timeouts alone allow a slow peer to hold read(n) indefinitely.
            expired.set()
            for active in connected_socket:
                with contextlib.suppress(OSError):
                    socket.socket.shutdown(active, socket.SHUT_RDWR)

        cutoff = threading.Timer(20, abort_socket)
        cutoff.daemon = True
        cutoff.start()
        try:
            try:
                connection.connect()
                if connection.sock is not None:
                    connected_socket.append(connection.sock)
                if expired.is_set():
                    raise CalDAVError("caldav_transport_unavailable")
                credentials = self._credentials()
                if not isinstance(credentials, CalDAVCredentials):
                    raise CalDAVError("caldav_credentials_unavailable")
                authorization = credentials.authorization()
            except CalDAVError:
                raise
            except Exception:
                raise CalDAVError("caldav_transport_unavailable") from None
            # Must be rechecked after connect/TLS/pool waits, immediately before HTTP bytes.
            before_dispatch()
            if expired.is_set():
                raise CalDAVError("caldav_transport_unavailable")
            try:
                connection.request(
                    method,
                    parsed.path,
                    body=body,
                    headers={
                        **headers,
                        "Authorization": authorization,
                        "Accept-Encoding": "identity",
                        "User-Agent": "Satori-CalDAV/1",
                    },
                )
                response = connection.getresponse()
                raw_headers = response.getheaders()
                if len(raw_headers) > 64:
                    raise CalDAVError("caldav_response_invalid")
                selected: dict[str, str] = {}
                for name, value in raw_headers:
                    key = name.lower()
                    if (
                        len(name) > 128
                        or len(value) > 4096
                        or any(ord(c) < 32 or ord(c) == 127 for c in name + value)
                    ):
                        raise CalDAVError("caldav_response_invalid")
                    if key in {"etag", "location", "content-type", "content-encoding"}:
                        if key in selected:
                            raise CalDAVError("caldav_response_invalid")
                        selected[key] = value
                if selected.get("content-encoding", "identity").lower() != "identity":
                    raise CalDAVError("caldav_response_invalid")
                data = response.read(MAX_DAV_BYTES + 1)
                if len(data) > MAX_DAV_BYTES:
                    raise CalDAVError("caldav_response_limit")
                return CalDAVResponse(response.status, selected, data)
            except CalDAVError:
                raise
            except Exception:
                raise CalDAVError("caldav_transport_unavailable") from None
        finally:
            cutoff.cancel()
            connection.close()
            expired.set()
