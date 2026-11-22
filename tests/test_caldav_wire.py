"""HTTPS, XML and ICS hostile/edge inputs with no socket or account access."""

import http.client
import socket
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import ClassVar
from uuid import uuid4

import pytest

from satori.application.assistant.contracts import CalendarOccurrence, CalendarSourceRef
from satori.infrastructure.assistant.caldav_ics import parse_calendar, project_event
from satori.infrastructure.assistant.caldav_transport import (
    CalDAVCredentials,
    CalDAVError,
    ICloudHTTPSCalDAVTransport,
    icloud_url,
    resolve_icloud_href,
)
from satori.infrastructure.assistant.caldav_xml import multistatus


@pytest.mark.parametrize(
    "url",
    [
        "http://caldav.icloud.com/",
        "https://evil.icloud.com/",
        "https://caldav.icloud.com.evil/",
        "https://caldav.icloud.com:444/",
        "https://user@caldav.icloud.com/",
        "https://caldav.icloud.com/a/../b",
        "https://caldav.icloud.com/%2e%2e/x",
        "https://caldav.icloud.com/%252e%252e/x",
        "https://caldav.icloud.com/a?query=secret",
        "https://caldav.icloud.com/a#fragment",
        "https://caldav.icloud.com/a\\b",
        "https://caldav.icloud.com/%0d%0a",
        "https://caldav.icloud.com/%zz",
    ],
)
def test_endpoint_allowlist(url: str) -> None:
    with pytest.raises(CalDAVError, match=r"^caldav_endpoint_invalid$"):
        icloud_url(url)


@pytest.mark.parametrize(
    "href",
    ["../escape", "%2e%2e/escape", "https://evil.invalid/", "//evil.invalid/", "/%252e%252e/"],
)
def test_href_resolution_never_normalizes_away_traversal(href: str) -> None:
    with pytest.raises(CalDAVError):
        resolve_icloud_href("https://p42-caldav.icloud.com/a/", href)


@pytest.mark.parametrize(
    "body",
    [
        b'<!DOCTYPE x [<!ENTITY y SYSTEM "file:///private">]><multistatus xmlns="DAV:"/>',
        '<!DOCTYPE x><multistatus xmlns="DAV:"/>'.encode("utf-16"),
        b'<multistatus xmlns="DAV:">' + b"<x>" * 18 + b"</x>" * 18 + b"</multistatus>",
        b'<multistatus xmlns="DAV:"><response><href>/</href><propstat>'
        b"<prop/></propstat></response></multistatus>",
    ],
)
def test_xml_dtd_encoding_depth_and_status_are_bounded(body: bytes) -> None:
    with pytest.raises(CalDAVError):
        multistatus(body)


def test_207_does_not_promote_failed_properties() -> None:
    body = b"""<multistatus xmlns="DAV:"><response><href>/</href><propstat>
    <prop><displayname>PUBLIC</displayname></prop><status>HTTP/1.1 403 Forbidden</status>
    </propstat></response></multistatus>"""
    assert not multistatus(body)[0].properties


def project(lines: str) -> tuple[CalendarOccurrence, ...]:
    data = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:public\r\n"
        + lines
        + "\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    ).encode()
    return tuple(
        project_event(
            e,
            source=CalendarSourceRef(
                provider="caldav", account_id="public", calendar_id=str(uuid4())
            ),
            href="https://caldav.icloud.com/a/public.ics",
            revision='"etag"',
            timezone="Europe/Amsterdam",
        )
        for e in parse_calendar(data)
    )


def test_utf8_folding_is_unfolded_before_decode() -> None:
    prefix = b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:"
    data = prefix + b"\xd0\r\n \x90\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    summary = parse_calendar(data)[0].one("SUMMARY")
    assert summary is not None
    assert summary.value == "\u0410"


def test_all_day_exclusive_end_and_iana_local_time() -> None:
    all_day = project("DTSTART;VALUE=DATE:20261025\r\nDTEND;VALUE=DATE:20261026")[0]
    assert all_day.all_day_start is not None
    assert all_day.all_day_start.isoformat() == "2026-10-25"
    local = project(
        "DTSTART;TZID=Europe/Moscow:20261104T120000\r\nDTEND;TZID=Europe/Moscow:20261104T130000"
    )[0]
    assert local.starts_at == datetime(2026, 11, 4, 9, tzinfo=UTC)


@pytest.mark.parametrize("date", ["20261025T023000", "20260329T023000"])
def test_ambiguous_or_missing_dst_instants_are_not_invented(date: str) -> None:
    with pytest.raises(CalDAVError):
        project(f"DTSTART;TZID=Europe/Amsterdam:{date}\r\nDTEND:20261104T130000Z")


def test_text_escapes_cannot_inject_content_lines() -> None:
    projected = project(
        "DTSTART:20261104T090000Z\r\nDTEND:20261104T100000Z\r\nSUMMARY:one\\, two\\; three\\nnext"
    )[0]
    assert projected.title == "one, two; three next"


class Connection:
    calls: ClassVar[list[str]] = []
    reply_headers: ClassVar[list[tuple[str, str]]] = []
    reply_body = b"PUBLIC"
    sock = None

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.calls.append("construct")

    def connect(self) -> None:
        self.calls.append("tls")

    def request(self, method: str, path: str, *, body: bytes, headers: Mapping[str, str]) -> None:
        assert headers["Authorization"].startswith("Basic ")
        self.calls.append("wire")

    def getresponse(self) -> "Connection":
        self.status = 200
        return self

    def getheaders(self) -> list[tuple[str, str]]:
        return self.reply_headers

    def read(self, bound: int) -> bytes:
        return self.reply_body[:bound]

    def close(self) -> None:
        self.calls.append("close")


def transport_fixture(monkeypatch: pytest.MonkeyPatch) -> ICloudHTTPSCalDAVTransport:
    Connection.calls = []
    Connection.reply_headers = []
    Connection.reply_body = b"PUBLIC"
    monkeypatch.setattr(http.client, "HTTPSConnection", Connection)
    return ICloudHTTPSCalDAVTransport(lambda: CalDAVCredentials("PUBLIC", "SYNTHETIC-ONLY"))


def test_actual_transport_guard_is_after_tls_before_authenticated_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http = transport_fixture(monkeypatch)

    def deny() -> None:
        assert Connection.calls == ["construct", "tls"]
        raise ValueError("PUBLIC REVOKE")

    with pytest.raises(ValueError, match="PUBLIC REVOKE"):
        http.request(
            "GET", "https://caldav.icloud.com/a", headers={}, body=b"", before_dispatch=deny
        )
    assert Connection.calls == ["construct", "tls", "close"]


@pytest.mark.parametrize(
    ("headers", "body"),
    [
        ([("Content-Encoding", "gzip")], b"PUBLIC"),
        ([("ETag", '"one"'), ("etag", '"two"')], b"PUBLIC"),
        ([], b"x" * (512 * 1024 + 1)),
    ],
)
def test_actual_transport_bounds_response(
    monkeypatch: pytest.MonkeyPatch, headers: list[tuple[str, str]], body: bytes
) -> None:
    http = transport_fixture(monkeypatch)
    Connection.reply_headers, Connection.reply_body = headers, body
    with pytest.raises(CalDAVError):
        http.request(
            "GET", "https://caldav.icloud.com/a", headers={}, body=b"", before_dispatch=lambda: None
        )
    assert Connection.calls[-1] == "close"


def test_secret_value_is_not_in_repr_or_error() -> None:
    credential = CalDAVCredentials("public", "PUBLIC-SYNTHETIC-SECRET")
    assert "PUBLIC-SYNTHETIC-SECRET" not in repr(credential)
    with pytest.raises(CalDAVError, match=r"^caldav_credentials_unavailable$"):
        CalDAVCredentials("invalid:username", "PUBLIC-SYNTHETIC-SECRET").authorization()


def test_elapsed_absolute_cutoff_cannot_authenticate_after_late_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    class ImmediateTimer:
        def __init__(self, seconds: float, callback: Callable[[], None]) -> None:
            self.callback = callback
            self.daemon = False

        def start(self) -> None:
            self.callback()

        def cancel(self) -> None:
            pass

    http = transport_fixture(monkeypatch)
    monkeypatch.setattr(threading, "Timer", ImmediateTimer)
    with pytest.raises(CalDAVError, match=r"^caldav_transport_unavailable$"):
        http.request(
            "GET",
            "https://caldav.icloud.com/a",
            headers={},
            body=b"",
            before_dispatch=lambda: pytest.fail("expired dispatch"),
        )
    assert Connection.calls == ["construct", "tls", "close"]


def test_watchdog_interrupts_raw_fd_without_ssl_state_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    callbacks: list[Callable[[], None]] = []

    class RecordedTimer:
        def __init__(self, seconds: float, callback: Callable[[], None]) -> None:
            callbacks.append(callback)
            self.daemon = False

        def start(self) -> None:
            pass

        def cancel(self) -> None:
            pass

    class SSLStateSocket(socket.socket):
        def shutdown(self, how: int) -> None:
            raise AssertionError("do not mutate the SSL wrapper during its active read")

    left, right = socket.socketpair()
    active = SSLStateSocket(fileno=left.detach())
    held_file = active.makefile("rb")

    class ClosingResponseConnection(Connection):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            self.sock = active  # type: ignore[assignment]

        def getresponse(self) -> Connection:
            active.close()  # HTTP/1.0 hands fd ownership to response.makefile.
            callbacks[0]()
            right.settimeout(1)
            assert right.recv(1) == b""
            return super().getresponse()

    try:
        transport_fixture(monkeypatch)
        monkeypatch.setattr(http.client, "HTTPSConnection", ClosingResponseConnection)
        monkeypatch.setattr(threading, "Timer", RecordedTimer)
        result = ICloudHTTPSCalDAVTransport(
            lambda: CalDAVCredentials("PUBLIC", "SYNTHETIC")
        ).request(
            "GET", "https://caldav.icloud.com/a", headers={}, body=b"", before_dispatch=lambda: None
        )
        assert result.status == 200
    finally:
        held_file.close()
        active.close()
        right.close()
