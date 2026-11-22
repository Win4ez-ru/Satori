"""Small bounded CalDAV XML vocabulary, without DTD/entity or recursive discovery."""

import re
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from satori.infrastructure.assistant.caldav_transport import MAX_DAV_BYTES, CalDAVError

DAV = "DAV:"
CAL = "urn:ietf:params:xml:ns:caldav"


def tag(namespace: str, name: str) -> str:
    return "{" + namespace + "}" + name


@dataclass(frozen=True, slots=True, repr=False)
class DAVResource:
    href: str
    properties: dict[str, ET.Element] = field(repr=False)
    failed: bool = False


def multistatus(data: bytes) -> tuple[DAVResource, ...]:
    if (
        len(data) > MAX_DAV_BYTES
        or b"\x00" in data
        or re.search(rb"<!\s*(?:DOCTYPE|ENTITY)\b", data, re.IGNORECASE)
    ):
        raise CalDAVError("caldav_xml_invalid")
    try:
        text = data.decode("utf-8", errors="strict")
        root = ET.fromstring(text)
    except (UnicodeError, ET.ParseError):
        raise CalDAVError("caldav_xml_invalid") from None
    pending = [(root, 0)]
    count = 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if depth > 16 or count > 12000 or len(node.attrib) > 20:
            raise CalDAVError("caldav_xml_limit")
        pending.extend((child, depth + 1) for child in node)
    if root.tag != tag(DAV, "multistatus") or len(root) > 1001:
        raise CalDAVError("caldav_xml_invalid")
    result: list[DAVResource] = []
    for response in root:
        if response.tag != tag(DAV, "response"):
            raise CalDAVError("caldav_xml_invalid")
        hrefs = response.findall(tag(DAV, "href"))
        if len(hrefs) != 1 or not hrefs[0].text or len(hrefs[0].text) > 4096:
            raise CalDAVError("caldav_xml_invalid")
        properties: dict[str, ET.Element] = {}
        failed = False
        direct_status = response.find(tag(DAV, "status"))
        if direct_status is not None:
            failed = _status(direct_status.text) != 200
        propstats = response.findall(tag(DAV, "propstat"))
        for propstat in propstats:
            statuses = propstat.findall(tag(DAV, "status"))
            props = propstat.findall(tag(DAV, "prop"))
            if len(statuses) != 1 or len(props) != 1:
                raise CalDAVError("caldav_xml_invalid")
            success = _status(statuses[0].text) == 200
            for prop in props[0]:
                if success:
                    if prop.tag in properties:
                        raise CalDAVError("caldav_xml_invalid")
                    properties[prop.tag] = prop
        if not propstats and direct_status is None:
            raise CalDAVError("caldav_xml_invalid")
        result.append(DAVResource(hrefs[0].text, properties, failed))
    return tuple(result)


def _status(value: str | None) -> int:
    match = re.fullmatch(r"HTTP/1\.[01] ([1-5][0-9]{2})(?: [^\r\n]*)?", value or "")
    if match is None:
        raise CalDAVError("caldav_xml_invalid")
    return int(match[1])


def propfind(*names: tuple[str, str]) -> bytes:
    root = ET.Element(tag(DAV, "propfind"))
    prop = ET.SubElement(root, tag(DAV, "prop"))
    for namespace, name in names:
        ET.SubElement(prop, tag(namespace, name))
    result = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    assert isinstance(result, bytes)
    return result


def calendar_query(start: str, end: str) -> bytes:
    root = ET.Element(tag(CAL, "calendar-query"))
    prop = ET.SubElement(root, tag(DAV, "prop"))
    ET.SubElement(prop, tag(DAV, "getetag"))
    data = ET.SubElement(prop, tag(CAL, "calendar-data"))
    ET.SubElement(data, tag(CAL, "expand"), {"start": start, "end": end})
    filters = ET.SubElement(root, tag(CAL, "filter"))
    calendar = ET.SubElement(filters, tag(CAL, "comp-filter"), {"name": "VCALENDAR"})
    event = ET.SubElement(calendar, tag(CAL, "comp-filter"), {"name": "VEVENT"})
    ET.SubElement(event, tag(CAL, "time-range"), {"start": start, "end": end})
    result = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    assert isinstance(result, bytes)
    return result
