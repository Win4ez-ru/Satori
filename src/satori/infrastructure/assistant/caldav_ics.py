"""Bounded iCalendar projection and exact own-event readback; unsupported is explicit."""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Literal, TypedDict, cast
from zoneinfo import ZoneInfo

from satori.application.assistant.calendar_actions_contracts import CalendarCreateAttempt
from satori.application.assistant.contracts import CalendarOccurrence, CalendarSourceRef
from satori.infrastructure.assistant.caldav_transport import MAX_DAV_BYTES, CalDAVError


class _OccurrenceFields(TypedDict):
    source: CalendarSourceRef
    event_id: str
    occurrence_id: str
    title: str
    timezone: str
    status: Literal["confirmed", "tentative", "cancelled"]
    revision: str


def utc_wire(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


@dataclass(frozen=True, slots=True, repr=False)
class ICSProperty:
    name: str
    params: dict[str, str] = field(repr=False)
    value: str = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class ICSEvent:
    properties: tuple[ICSProperty, ...] = field(repr=False)
    nested: bool = False

    def one(self, name: str, *, required: bool = True) -> ICSProperty | None:
        found = tuple(prop for prop in self.properties if prop.name == name)
        if len(found) > 1 or (required and not found):
            raise CalDAVError("caldav_ics_unsupported")
        return found[0] if found else None


def parse_calendar(data: bytes) -> tuple[ICSEvent, ...]:
    if len(data) > MAX_DAV_BYTES or b"\x00" in data:
        raise CalDAVError("caldav_ics_limit")
    # RFC5545 unfolds bytes before UTF-8 decoding. XML calendar-data may normalize CRLF.
    unfolded = re.sub(rb"\r?\n[ \t]", b"", data)
    if b"\r" in unfolded.replace(b"\r\n", b""):
        raise CalDAVError("caldav_ics_unsupported")
    try:
        lines = unfolded.decode("utf-8", errors="strict").replace("\r\n", "\n").split("\n")
    except UnicodeError:
        raise CalDAVError("caldav_ics_unsupported") from None
    if len(lines) > 10000:
        raise CalDAVError("caldav_ics_limit")
    stack: list[str] = []
    events: list[ICSEvent] = []
    props: list[ICSProperty] = []
    nested = False
    calendar_count = 0
    for line in lines:
        if not line:
            continue
        if len(line.encode()) > 16384 or any(ord(c) < 32 and c != "\t" for c in line):
            raise CalDAVError("caldav_ics_unsupported")
        prop = _property(line)
        if prop.name == "BEGIN":
            component = prop.value.upper()
            if not stack:
                if component != "VCALENDAR" or calendar_count:
                    raise CalDAVError("caldav_ics_unsupported")
                calendar_count += 1
            elif len(stack) == 1 and component != "VEVENT":
                # No custom VTIMEZONE/unknown components silently discarded as complete.
                raise CalDAVError("caldav_ics_unsupported")
            elif len(stack) > 1:
                if stack != ["VCALENDAR", "VEVENT"] or component != "VALARM":
                    raise CalDAVError("caldav_ics_unsupported")
                nested = True
            stack.append(component)
            if len(stack) > 4:
                raise CalDAVError("caldav_ics_limit")
            if component == "VEVENT" and len(stack) == 2:
                props = []
                nested = False
        elif prop.name == "END":
            if not stack or stack.pop() != prop.value.upper():
                raise CalDAVError("caldav_ics_unsupported")
            if prop.value.upper() == "VEVENT" and len(stack) == 1:
                events.append(ICSEvent(tuple(props), nested))
                if len(events) > 1000:
                    raise CalDAVError("caldav_ics_limit")
        elif stack == ["VCALENDAR"] and prop.name == "METHOD":
            raise CalDAVError("caldav_ics_unsupported")
        elif stack == ["VCALENDAR", "VEVENT"]:
            props.append(prop)
        elif not stack:
            raise CalDAVError("caldav_ics_unsupported")
    if stack or calendar_count != 1:
        raise CalDAVError("caldav_ics_unsupported")
    return tuple(events)


def _property(line: str) -> ICSProperty:
    # Quoted parameters may contain colon; do not split at a quoted delimiter.
    quoted = False
    split = -1
    for index, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        elif char == ":" and not quoted:
            split = index
            break
    if split < 1 or quoted:
        raise CalDAVError("caldav_ics_unsupported")
    head, value = line[:split], line[split + 1 :]
    parts = head.split(";")
    name = parts[0].upper()
    if not re.fullmatch(r"[A-Z0-9-]{1,64}", name):
        raise CalDAVError("caldav_ics_unsupported")
    params: dict[str, str] = {}
    for part in parts[1:]:
        key, separator, val = part.partition("=")
        key = key.upper()
        if not separator or key in params or not re.fullmatch(r"[A-Z0-9-]{1,64}", key):
            raise CalDAVError("caldav_ics_unsupported")
        params[key] = val.strip('"')
    return ICSProperty(name, params, value)


def text_value(value: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\":
            index += 1
            if index >= len(value) or value[index] not in "\\,;nN":
                raise CalDAVError("caldav_ics_unsupported")
            char = "\n" if value[index] in "nN" else value[index]
        result.append(char)
        index += 1
    return "".join(result)


def _instant(prop: ICSProperty, zone: str) -> datetime | date:
    value = prop.value
    if prop.params == {"VALUE": "DATE"}:
        try:
            if not re.fullmatch(r"[0-9]{8}", value):
                raise ValueError
            return datetime.strptime(value, "%Y%m%d").date()
        except ValueError:
            raise CalDAVError("caldav_ics_unsupported") from None
    if set(prop.params) - {"TZID", "VALUE"} or prop.params.get("VALUE", "DATE-TIME") != "DATE-TIME":
        raise CalDAVError("caldav_ics_unsupported")
    try:
        if value.endswith("Z"):
            if "TZID" in prop.params or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", value):
                raise ValueError
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        if "TZID" not in prop.params:
            raise ValueError
        if not re.fullmatch(r"[0-9]{8}T[0-9]{6}", value):
            raise ValueError
        local_zone = ZoneInfo(prop.params["TZID"])
        naive = datetime.strptime(value, "%Y%m%dT%H%M%S")
        first = naive.replace(tzinfo=local_zone, fold=0)
        second = naive.replace(tzinfo=local_zone, fold=1)
        if first.utcoffset() != second.utcoffset():
            raise ValueError  # ambiguous/nonexistent local time needs server UTC expansion
        if first.astimezone(UTC).astimezone(local_zone).replace(tzinfo=None) != naive:
            raise ValueError
        return first.astimezone(UTC)
    except (ValueError, KeyError):
        raise CalDAVError("caldav_ics_unsupported") from None


def project_event(
    event: ICSEvent,
    *,
    source: CalendarSourceRef,
    href: str,
    revision: str,
    timezone: str,
) -> CalendarOccurrence:
    if any(p.name in {"RRULE", "RDATE", "EXRULE", "EXDATE"} for p in event.properties):
        raise CalDAVError("caldav_ics_unsupported")
    uid, start_prop, end_prop = event.one("UID"), event.one("DTSTART"), event.one("DTEND")
    assert uid is not None
    assert start_prop is not None
    assert end_prop is not None
    if not uid.value or len(uid.value) > 1024:
        raise CalDAVError("caldav_ics_unsupported")
    start, end = _instant(start_prop, timezone), _instant(end_prop, timezone)
    summary = event.one("SUMMARY", required=False)
    title = text_value(summary.value) if summary else ""
    title = " ".join(title.split())
    if len(title) > 512:
        raise CalDAVError("caldav_ics_limit")
    status_prop = event.one("STATUS", required=False)
    status = status_prop.value.upper() if status_prop else "CONFIRMED"
    if status not in {"CONFIRMED", "TENTATIVE", "CANCELLED"}:
        raise CalDAVError("caldav_ics_unsupported")
    typed_status = cast(Literal["confirmed", "tentative", "cancelled"], status.lower())
    recurrence = event.one("RECURRENCE-ID", required=False)
    event_id = hashlib.sha256((href + "\0" + uid.value).encode()).hexdigest()
    occurrence_id = hashlib.sha256(
        (event_id + "\0" + (recurrence.value if recurrence else "") + "\0" + str(start)).encode()
    ).hexdigest()
    common = _OccurrenceFields(
        source=source,
        event_id=event_id,
        occurrence_id=occurrence_id,
        title=title,
        timezone=timezone,
        status=typed_status,
        revision=hashlib.sha256(revision.encode()).hexdigest(),
    )
    try:
        if isinstance(start, datetime) and isinstance(end, datetime):
            return CalendarOccurrence(**common, starts_at=start, ends_at=end)
        if type(start) is date and type(end) is date:
            return CalendarOccurrence(**common, all_day_start=start, all_day_end=end)
    except ValueError:
        pass
    raise CalDAVError("caldav_ics_unsupported")


def intersects(item: CalendarOccurrence, start: datetime, end: datetime) -> bool:
    if item.starts_at is not None and item.ends_at is not None:
        return item.starts_at < end and item.ends_at > start
    assert item.all_day_start is not None
    assert item.all_day_end is not None
    zone = ZoneInfo(item.timezone)
    left = datetime.combine(item.all_day_start, time.min, zone).astimezone(UTC)
    right = datetime.combine(item.all_day_end, time.min, zone).astimezone(UTC)
    return left < end and right > start


def event_uid(attempt: CalendarCreateAttempt) -> str:
    return "satori-" + attempt.idempotency_token + "@satori.invalid"


def create_calendar(attempt: CalendarCreateAttempt, now: datetime) -> bytes:
    command = attempt.proposal.command
    title = command.title.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    lines = (
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Satori//Reviewed Calendar Create 1//EN",
        "BEGIN:VEVENT",
        "UID:" + event_uid(attempt),
        "DTSTAMP:" + utc_wire(now),
        "DTSTART:" + utc_wire(command.starts_at),
        "DTEND:" + utc_wire(command.ends_at),
        "SUMMARY:" + title,
        "URL:satori-operation://calendar-create/v1/" + attempt.idempotency_token,
        "X-SATORI-TIMEZONE:" + command.timezone,
        "X-SATORI-PAYLOAD-DIGEST:" + attempt.proposal.payload_digest,
        "END:VEVENT",
        "END:VCALENDAR",
    )
    return b"".join(_fold(line) for line in lines)


def _fold(value: str) -> bytes:
    result = bytearray()
    length = 0
    for char in value:
        chunk = char.encode("utf-8")
        if length + len(chunk) > 75:
            result.extend(b"\r\n ")
            length = 1
        result.extend(chunk)
        length += len(chunk)
    result.extend(b"\r\n")
    return bytes(result)


def exact_own_event(data: bytes, attempt: CalendarCreateAttempt) -> bool:
    try:
        events = parse_calendar(data)
        if len(events) != 1 or events[0].nested:
            return False
        event = events[0]
        allowed = {
            "UID",
            "DTSTAMP",
            "DTSTART",
            "DTEND",
            "SUMMARY",
            "URL",
            "X-SATORI-TIMEZONE",
            "X-SATORI-PAYLOAD-DIGEST",
            "CREATED",
            "LAST-MODIFIED",
            "SEQUENCE",
            "STATUS",
            "TRANSP",
        }
        if any(p.name not in allowed for p in event.properties):
            return False
        command = attempt.proposal.command
        expected = {
            "UID": event_uid(attempt),
            "DTSTART": utc_wire(command.starts_at),
            "DTEND": utc_wire(command.ends_at),
            "URL": "satori-operation://calendar-create/v1/" + attempt.idempotency_token,
            "X-SATORI-TIMEZONE": command.timezone,
            "X-SATORI-PAYLOAD-DIGEST": attempt.proposal.payload_digest,
        }
        for key, value in expected.items():
            prop = event.one(key)
            if prop is None or prop.params or prop.value != value:
                return False
        summary = event.one("SUMMARY")
        status = event.one("STATUS", required=False)
        transparent = event.one("TRANSP", required=False)
        return (
            summary is not None
            and not summary.params
            and text_value(summary.value) == command.title
            and (status is None or status.value == "CONFIRMED")
            and (transparent is None or transparent.value == "OPAQUE")
        )
    except (ValueError, KeyError):
        return False
