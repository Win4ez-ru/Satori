"""Bounded offline Gmail FULL JSON projection; every source remains untrusted.

This module has no transport, credentials, inference, renderer or persistence.
MIME paths and text spans attribute local projections without authenticating senders.
"""

import base64
import binascii
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.errors import HeaderParseError
from email.header import decode_header
from email.message import Message
from html import unescape
from html.entities import html5
from html.parser import HTMLParser
from typing import Literal, cast

from pydantic import ValidationError

from satori.application.assistant.mail_contracts import (
    MAIL_PROJECTION_VERSION,
    MAX_MAIL_BATCH_BYTES,
    MAX_MAIL_BATCH_DECODED_BYTES,
    MAX_MAIL_BATCH_TEXT_CHARS,
    MAX_MAIL_DECODED_BYTES,
    MAX_MAIL_MESSAGE_BYTES,
    MAX_MAIL_MESSAGES,
    MAX_MAIL_TEXT_CHARS,
    MailAccessCode,
    MailAccessDiagnostic,
    MailAccessError,
    MailHeaderClaim,
    MailOmission,
    MailOmissionReason,
    MailPartRef,
    MailProjection,
    MailSourceRef,
    MailTextSpan,
    mail_identifier,
)

MAX_MIME_PARTS = 64
MAX_MIME_DEPTH = 8
MAX_HEADERS = 64
MAX_HEADER_CHARS = 2048
MAX_HTML_DEPTH = 64
_OPACITY_DECLARATION = re.compile(
    r"[ \t\n\r\f]*opacity(?=[ \t\n\r\f:]|$)", re.ASCII | re.IGNORECASE
)
_ZERO_OPACITY_DECLARATION = re.compile(
    r"[ \t\n\r\f]*opacity[ \t\n\r\f]*:[ \t\n\r\f]*0[ \t\n\r\f]*"
    r"(?:![ \t\n\r\f]*important[ \t\n\r\f]*)?",
    re.ASCII | re.IGNORECASE,
)
# Only these formatting characters may be omitted in already discarded HTML data.
# Other literal Cf remain unsupported. Clean discarded references preserve the
# existing non-bidi entity route; directional controls never use either route.
_DISCARDED_HTML_FORMAT = frozenset("\u00ad\u200b\u200c\u200d\u2060\ufeff")
_BIDI_FORMAT = frozenset(
    "\u061c\u200e\u200f"
    + "".join(chr(value) for value in range(0x202A, 0x202F))
    + "".join(chr(value) for value in range(0x2066, 0x2070))
)
_SINGLETON_HEADERS = frozenset(
    {
        "from",
        "sender",
        "reply-to",
        "to",
        "cc",
        "bcc",
        "date",
        "subject",
        "message-id",
        "in-reply-to",
        "references",
        "mime-version",
        "content-type",
        "content-transfer-encoding",
        "content-disposition",
        "content-id",
        "content-description",
        "content-location",
        "return-path",
    }
)
_TEXT_CHARSETS = frozenset({"utf-8", "utf8", "ascii", "us-ascii"})
_ENCODED_WORD = re.compile(r"=\?([^?\s]+)\?([bBqQ])\?([^?]*)\?=")
_MIME_TOKEN = r"[A-Za-z0-9!#$%&'*+.^_`|~-]+"
_QUOTED_PARAMETER = r'"(?:[^"\\\r\n]|\\[^\r\n])*"'
_MIME_PARAMETER = re.compile(
    rf";[ \t]*({_MIME_TOKEN})[ \t]*=[ \t]*({_MIME_TOKEN}|{_QUOTED_PARAMETER})[ \t]*"
)


def _require(
    condition: bool, code: MailAccessCode, diagnostic: MailAccessDiagnostic | None = None
) -> None:
    if not condition:
        raise MailAccessError(code, diagnostic)


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def _object(
    value: object, code: MailAccessCode, diagnostic: MailAccessDiagnostic | None = None
) -> dict[str, object]:
    _require(type(value) is dict, code, diagnostic)
    return cast(dict[str, object], value)


def _array(
    value: object, code: MailAccessCode, diagnostic: MailAccessDiagnostic | None = None
) -> list[object]:
    _require(type(value) is list, code, diagnostic)
    return cast(list[object], value)


def _controls(value: str, *, multiline: bool = False, allow_horizontal_tab: bool = False) -> bool:
    return any(
        unicodedata.category(char) in {"Cc", "Cf", "Cs"}
        and not ((multiline and char in "\n\t") or (allow_horizontal_tab and char == "\t"))
        for char in value
    )


def _header_control_diagnostic(value: str) -> MailAccessDiagnostic:
    if "\r\n" in value:
        return "header_folding"
    if "\r" in value:
        return "header_bare_cr"
    if "\n" in value:
        return "header_bare_lf"
    if any(unicodedata.category(char) == "Cs" for char in value):
        return "header_surrogate_controls"
    if any(unicodedata.category(char) == "Cf" for char in value):
        return "header_format_controls"
    return "header_controls"


def _body_control_diagnostic(value: str, *, html: bool) -> MailAccessDiagnostic:
    if any(unicodedata.category(char) == "Cs" for char in value):
        return "body_surrogate_controls"
    if any(unicodedata.category(char) == "Cf" for char in value):
        return "body_html_format_controls" if html else "body_format_controls"
    return "body_controls"


def _string(
    value: object,
    code: MailAccessCode,
    *,
    maximum: int,
    type_diagnostic: MailAccessDiagnostic | None = None,
    bound_diagnostic: MailAccessDiagnostic | None = None,
    control_diagnostic: MailAccessDiagnostic | None = None,
) -> str:
    _require(type(value) is str, code, type_diagnostic)
    text = cast(str, value)
    _require(len(text) <= maximum, code, bound_diagnostic)
    _require(not _controls(text), code, control_diagnostic)
    return text


def _identifier(value: object) -> str:
    text = _string(value, "invalid_message", maximum=255)
    try:
        return mail_identifier(text)
    except ValueError:
        raise MailAccessError("invalid_message") from None


def _json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        _require(key not in result, "invalid_message")
        result[key] = value
    return result


def _no_constant(value: str) -> object:
    raise MailAccessError("invalid_message")


def _decode_body(value: object, remaining: int) -> bytes:
    _require(type(value) is str, "invalid_body", "body_structure")
    encoded = cast(str, value)
    _require(len(encoded) <= ((remaining + 2) // 3) * 4, "invalid_body", "body_encoded_limit")
    _require(
        re.fullmatch(r"[A-Za-z0-9_-]*={0,2}", encoded) is not None,
        "invalid_body",
        "body_base64_alphabet",
    )
    core = encoded.rstrip("=")
    _require(len(core) % 4 != 1, "invalid_body", "body_base64_length")
    padded = core + "=" * ((-len(core)) % 4)
    _require("=" not in encoded or encoded == padded, "invalid_body", "body_base64_padding")
    try:
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        raise MailAccessError("invalid_body", "body_base64_decode") from None
    _require(len(decoded) <= remaining, "invalid_body", "body_decoded_limit")
    canonical = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
    _require(canonical == core, "invalid_body", "body_base64_noncanonical")
    return decoded


@dataclass(frozen=True)
class _Headers:
    claims: tuple[MailHeaderClaim, ...]
    singletons: dict[str, str]
    decoding_unsupported: bool


def _header_text(value: str) -> tuple[str, bool]:
    if "=?" not in value:
        return value, False
    words = tuple(_ENCODED_WORD.finditer(value))
    if "=?" in _ENCODED_WORD.sub("", value):
        return value, True
    for word in words:
        charset, encoding, encoded = word.groups()
        if charset.lower() not in _TEXT_CHARSETS:
            return value, True
        if encoding.lower() == "b":
            try:
                decoded_octets = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                return value, True
            if base64.b64encode(decoded_octets).decode("ascii") != encoded:
                return value, True
        elif re.fullmatch(r"(?:[!-<>-~]|=[0-9A-Fa-f]{2})*", encoded) is None:
            return value, True
    try:
        chunks = decode_header(value)
    except (ValueError, LookupError, HeaderParseError):
        return value, True
    if all(type(chunk) is str for chunk, _ in chunks):
        return value, True
    decoded: list[str] = []
    for chunk, charset in chunks:
        if isinstance(chunk, bytes):
            encoding = (charset or "ascii").lower()
            if encoding not in _TEXT_CHARSETS:
                return value, True
            try:
                decoded.append(chunk.decode(encoding, errors="strict"))
            except (UnicodeDecodeError, LookupError):
                return value, True
        else:
            decoded.append(chunk)
    text = "".join(decoded)
    _require(len(text) <= MAX_HEADER_CHARS, "invalid_header", "header_decoded_limit")
    _require(
        not _controls(text, allow_horizontal_tab=True), "invalid_header", "header_decoded_controls"
    )
    return text, False


def _headers(part: dict[str, object]) -> _Headers:
    entries = _array(part.get("headers", []), "invalid_header", "header_structure")
    _require(len(entries) <= MAX_HEADERS, "invalid_header", "header_count_limit")
    singletons: dict[str, str] = {}
    claims: list[MailHeaderClaim] = []
    unsupported = False
    for index, entry in enumerate(entries):
        item = _object(entry, "invalid_header", "header_structure")
        name = _string(
            item.get("name"),
            "invalid_header",
            maximum=64,
            type_diagnostic="header_name_type",
            bound_diagnostic="header_name_limit",
            control_diagnostic="header_name_controls",
        )
        _require(
            re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}", name) is not None,
            "invalid_header",
            "header_name_syntax",
        )
        name = name.lower()
        raw_value = item.get("value")
        _require(type(raw_value) is str, "invalid_header", "header_value_type")
        raw_value = cast(str, raw_value)
        _require(len(raw_value) <= MAX_HEADER_CHARS, "invalid_header", "header_value_limit")
        raw_value = re.sub(r"\r\n[ \t]+", " ", raw_value)
        _require(
            not _controls(raw_value, allow_horizontal_tab=True),
            "invalid_header",
            _header_control_diagnostic(raw_value),
        )
        if name in _SINGLETON_HEADERS:
            _require(name not in singletons, "invalid_header", "header_duplicate")
            singletons[name] = raw_value
        value, incomplete = _header_text(raw_value)
        unsupported |= incomplete
        claims.append(MailHeaderClaim(name=name, value=value, index=index))
    return _Headers(tuple(claims), singletons, unsupported)


def _mime_parameters(value: str, *, expected: str | None = None) -> set[str]:
    head = value.partition(";")[0].strip().lower()
    _require(
        head == expected if expected is not None else head in {"inline", "attachment"},
        "invalid_header",
        "header_mime_mismatch",
    )
    cursor = value.find(";")
    names: set[str] = set()
    while cursor != -1 and cursor < len(value):
        match = _MIME_PARAMETER.match(value, cursor)
        _require(match is not None, "invalid_header", "header_mime_parameter")
        assert match is not None
        name = match.group(1).lower()
        _require(name not in names, "invalid_header", "header_mime_duplicate_parameter")
        _require(not name.startswith("charset*"), "invalid_header", "header_mime_extended_charset")
        names.add(name)
        cursor = match.end()
    return names


def _mime_metadata(headers: _Headers, mime: str) -> Message:
    metadata = Message()
    content_type = headers.singletons.get("content-type", mime)
    names = _mime_parameters(content_type, expected=mime)
    metadata["Content-Type"] = content_type
    if "content-disposition" in headers.singletons:
        disposition = headers.singletons["content-disposition"]
        _mime_parameters(disposition)
        metadata["Content-Disposition"] = disposition
    _require(metadata.get_content_type().lower() == mime, "invalid_header", "header_mime_mismatch")
    if "charset" in names:
        charset = metadata.get_content_charset()
        _require(
            charset is not None and bool(charset.strip()), "invalid_header", "header_mime_charset"
        )
    for header in ("content-type", "content-disposition"):
        params = metadata.get_params(header=header, failobj=[]) or []
        parsed_names = [name.lower() for name, _ in params[1:]]
        _require(
            len(parsed_names) == len(set(parsed_names)),
            "invalid_header",
            "header_mime_duplicate_parameter",
        )
    return metadata


def _style_has_exact_zero_opacity(style: str) -> bool:
    """Recognize one bounded literal declaration, without parsing CSS or its cascade."""
    # Strings, escapes, comments, functions and nested blocks cannot grant discard.
    if any(marker in style for marker in "\"'\\/*(){}[]"):
        return False
    declarations = [
        declaration for declaration in style.split(";") if _OPACITY_DECLARATION.match(declaration)
    ]
    return (
        len(declarations) == 1 and _ZERO_OPACITY_DECLARATION.fullmatch(declarations[0]) is not None
    )


class _HTMLText(HTMLParser):
    """Lossy preview; CSS visibility and semantic injection safety are unproven."""

    _DROP = frozenset(
        {
            "script",
            "style",
            "noscript",
            "template",
            "head",
            "iframe",
            "object",
            "embed",
            "svg",
            "math",
            "form",
            "button",
            "select",
            "textarea",
            "video",
            "audio",
        }
    )
    _VOID = frozenset(
        {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }
    )
    _BLOCK = frozenset({"p", "div", "li", "section", "article", "tr", "h1", "h2", "h3", "br"})

    def __init__(self, source: str) -> None:
        # Raw data callbacks retain exact positions; references are decoded explicitly.
        super().__init__(convert_charrefs=False)
        self._source = source
        self._line_starts = [0, *(match.end() for match in re.finditer("\n", source))]
        self._raw_format = {
            index for index, char in enumerate(source) if unicodedata.category(char) == "Cf"
        }
        # HTMLParser accepts some unterminated references by consuming a prefix.
        # A literal format character inside the original '&...;' token must not
        # become ordinary data merely because the tokenizer split that token.
        reference_open = False
        markup_open = False
        for char in source:
            if char == "<":
                markup_open = True
            elif char == ">":
                markup_open = False
            elif markup_open and unicodedata.category(char) == "Cf":
                raise MailAccessError("invalid_body", "body_html_format_controls")
            if char == "&":
                reference_open = True
            elif char in ";<> \t\n\r\f":
                reference_open = False
            elif reference_open and unicodedata.category(char) == "Cf":
                raise MailAccessError("invalid_body", "body_html_format_controls")
        self._discarded_raw_format: set[int] = set()
        self.discarded_format_controls = False
        self._stack: list[tuple[str, bool]] = []
        self._fragments: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for value in (tag, *(value for pair in attrs for value in pair if value is not None)):
            _require(
                not _controls(value, multiline=True),
                "invalid_body",
                _body_control_diagnostic(value, html=True),
            )
        _require(len(self._stack) < MAX_HTML_DEPTH, "unsupported_structure")
        names = [name for name, _ in attrs]
        _require(len(names) == len(set(names)), "unsupported_structure")
        values = dict(attrs)
        style = re.sub(r"\s+", "", values.get("style") or "").lower()
        hidden = (
            "hidden" in values
            or (values.get("aria-hidden") or "").lower() == "true"
            or "display:none" in style
            or "visibility:hidden" in style
            or _style_has_exact_zero_opacity(values.get("style") or "")
        )
        dropped = bool(self._stack and self._stack[-1][1]) or hidden or tag in self._DROP
        if not dropped and tag in self._BLOCK:
            self._fragments.append("\n")
        if tag not in self._VOID:
            self._stack.append((tag, dropped))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        _require(not _controls(tag), "invalid_body", _body_control_diagnostic(tag, html=True))
        if tag in self._VOID:
            return
        _require(bool(self._stack) and self._stack[-1][0] == tag, "unsupported_structure")
        _, dropped = self._stack.pop()
        if not dropped and tag in self._BLOCK:
            self._fragments.append("\n")

    def handle_data(self, data: str) -> None:
        line, column = self.getpos()
        start = self._line_starts[line - 1] + column
        _require(self._source[start : start + len(data)] == data, "unsupported_structure")
        self._data(data, raw_start=start)

    def handle_entityref(self, name: str) -> None:
        token, terminated = self._reference(f"&{name}")
        self._data(
            unescape(token),
            format_reference=terminated and name + ";" in html5,
        )

    def handle_charref(self, name: str) -> None:
        token, terminated = self._reference(f"&#{name}")
        self._data(
            unescape(token),
            format_reference=terminated
            and bool(re.fullmatch(r"(?:[0-9]+|[xX][0-9A-Fa-f]+)", name)),
        )

    def _reference(self, prefix: str) -> tuple[str, bool]:
        line, column = self.getpos()
        start = self._line_starts[line - 1] + column
        _require(self._source[start : start + len(prefix)] == prefix, "unsupported_structure")
        terminated = self._source[start + len(prefix) : start + len(prefix) + 1] == ";"
        return prefix + (";" if terminated else ""), terminated

    def _data(
        self, data: str, *, raw_start: int | None = None, format_reference: bool = False
    ) -> None:
        dropped = bool(self._stack and self._stack[-1][1])
        for index, char in enumerate(data):
            if not _controls(char, multiline=True):
                continue
            _require(
                dropped
                and (
                    (raw_start is not None and char in _DISCARDED_HTML_FORMAT)
                    or (
                        format_reference
                        and unicodedata.category(char) == "Cf"
                        and char not in _BIDI_FORMAT
                    )
                ),
                "invalid_body",
                _body_control_diagnostic(char, html=True)
                if raw_start is not None
                else "body_html_controls",
            )
            self.discarded_format_controls = True
            if raw_start is not None:
                self._discarded_raw_format.add(raw_start + index)
        if not dropped:
            self._fragments.append(data)

    def projected(self) -> str:
        _require(not self._stack, "unsupported_structure")
        _require(
            self._raw_format == self._discarded_raw_format,
            "invalid_body",
            "body_html_format_controls",
        )
        return re.sub(r"[ \t\f\v]+", " ", "".join(self._fragments)).strip()


def _extract_html(text: str) -> tuple[str, bool]:
    parser = _HTMLText(text)
    try:
        parser.feed(text)
        parser.close()
        extracted = parser.projected()
    except (RecursionError, AssertionError):
        raise MailAccessError("unsupported_structure") from None
    _require(not _controls(extracted, multiline=True), "invalid_body", "body_html_controls")
    return extracted, parser.discarded_format_controls


@dataclass(frozen=True)
class _TextPart:
    ref: MailPartRef
    mime: str
    text: str
    html_text: str | None = None


@dataclass(frozen=True)
class _Part:
    ref: MailPartRef
    mime: str
    headers: _Headers
    body: dict[str, object]
    charset: str
    children: tuple["_Part", ...]
    omission: MailOmissionReason | None
    has_plain: bool


_CONTAINERS = frozenset({"multipart/alternative", "multipart/mixed", "multipart/related"})


class _Projector:
    def __init__(self) -> None:
        self.decoded_bytes = 0
        self.visited_parts = 0
        self.omissions: list[MailOmission] = []
        self._provider_ids: set[str] = set()
        self._text_cache: dict[MailPartRef, _TextPart | None] = {}
        self._decoded_refs: set[MailPartRef] = set()
        self._plain_cache: dict[MailPartRef, tuple[_TextPart, ...]] = {}
        self._plain_choice: dict[MailPartRef, int | None] = {}

    def omit(self, reason: MailOmissionReason, ref: MailPartRef) -> None:
        omission = MailOmission(reason=reason, part=ref)
        if omission not in self.omissions:
            self.omissions.append(omission)
        _require(len(self.omissions) <= 128, "mime_limit")

    def build(self, value: object, path: tuple[int, ...] = ()) -> _Part:
        """Validate the whole bounded tree once, without expanding any body bytes."""
        _require(len(path) <= MAX_MIME_DEPTH, "mime_limit")
        self.visited_parts += 1
        _require(self.visited_parts <= MAX_MIME_PARTS, "mime_limit")
        part = _object(value, "unsupported_structure")
        raw_id = part.get("partId")
        provider_id = (
            _string(raw_id, "invalid_message", maximum=255) if raw_id is not None else None
        )
        if provider_id:
            _require(provider_id not in self._provider_ids, "invalid_message")
            self._provider_ids.add(provider_id)
        ref = MailPartRef(path=path, provider_part_id=provider_id or None)
        mime = _string(part.get("mimeType"), "invalid_message", maximum=127).lower()
        _require(
            re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", mime) is not None,
            "invalid_message",
        )
        headers = _headers(part)
        if headers.decoding_unsupported:
            self.omit("header_decoding_unsupported", ref)
        filename = _string(part.get("filename", ""), "invalid_message", maximum=1024)
        metadata = _mime_metadata(headers, mime)
        body = _object(part.get("body", {}), "invalid_body", "body_structure")
        if "size" in body:
            declared_size = body["size"]
            _require(
                type(declared_size) is int and declared_size >= 0,
                "invalid_body",
                "body_size_value",
            )
        if "data" in body:
            _require(type(body["data"]) is str, "invalid_body", "body_structure")
        if "attachmentId" in body:
            _string(
                body["attachmentId"],
                "invalid_body",
                maximum=255,
                type_diagnostic="body_structure",
                bound_diagnostic="body_encoded_limit",
                control_diagnostic="body_controls",
            )
        children = _array(part.get("parts", []), "unsupported_structure")
        _require(len(children) <= MAX_MIME_PARTS, "mime_limit")
        if mime in _CONTAINERS:
            _require(
                "size" not in body or body["size"] == 0,
                "invalid_body",
                "body_container_size",
            )
            _require(not body.get("data"), "unsupported_structure")
        elif mime in {"text/plain", "text/html"}:
            _require(not children, "unsupported_structure")
        # Validate descendants even when their bodies will be opaque/omitted. This does not
        # make attachment, nested-message or unsupported-container contents selectable.
        nodes = tuple(self.build(child, (*path, index)) for index, child in enumerate(children))
        omission: MailOmissionReason | None = None
        if mime in {"message/rfc822", "message/global"}:
            omission = "nested_message_omitted"
        elif (
            filename
            or metadata.get_filename()
            or metadata.get_content_disposition() == "attachment"
        ):
            omission = "attachment_omitted"
        elif body.get("attachmentId"):
            omission = "external_body_omitted"
        elif mime not in _CONTAINERS and mime not in {"text/plain", "text/html"}:
            omission = (
                "inline_resource_omitted"
                if mime.startswith(("image/", "audio/", "video/"))
                else "unsupported_mime"
            )
        return _Part(
            ref=ref,
            mime=mime,
            headers=headers,
            body=body,
            charset=(metadata.get_content_charset("utf-8") or "utf-8").lower(),
            children=nodes,
            omission=omission,
            has_plain=omission is None
            and (mime == "text/plain" or any(n.has_plain for n in nodes)),
        )

    def _text(self, part: _Part) -> list[_TextPart]:
        if part.ref in self._text_cache:
            cached = self._text_cache[part.ref]
            return [cached] if cached is not None else []
        if part.charset not in _TEXT_CHARSETS:
            self.omit("unsupported_charset", part.ref)
            self._text_cache[part.ref] = None
            return []
        decoded = _decode_body(
            part.body.get("data", ""), MAX_MAIL_DECODED_BYTES - self.decoded_bytes
        )
        _require(
            "size" not in part.body or part.body["size"] == len(decoded),
            "invalid_body",
            "body_size_mismatch",
        )
        self.decoded_bytes += len(decoded)
        try:
            text = decoded.decode(part.charset, errors="strict")
        except (UnicodeDecodeError, LookupError):
            raise MailAccessError("invalid_body", "body_charset_decode") from None
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        html_text = None
        if part.mime == "text/html" and _controls(text, multiline=True):
            # Only Cf candidates reach positional validation. Other controls and
            # all unapproved/directional Cf still fail before parsing.
            _require(
                all(
                    not _controls(char, multiline=True) or char in _DISCARDED_HTML_FORMAT
                    for char in text
                ),
                "invalid_body",
                _body_control_diagnostic(text, html=True),
            )
            html_text, discarded_format = _extract_html(text)
            if discarded_format:
                self.omit("html_discarded_format_controls", part.ref)
        else:
            _require(
                not _controls(text, multiline=True),
                "invalid_body",
                _body_control_diagnostic(text, html=False),
            )
        self._decoded_refs.add(part.ref)
        if not text.strip():
            self.omit("empty_body", part.ref)
            self._text_cache[part.ref] = None
            return []
        result = _TextPart(part.ref, part.mime, text, html_text)
        self._text_cache[part.ref] = result
        return [result]

    def plain(self, part: _Part) -> list[_TextPart]:
        """Find usable plain text without decoding HTML or opening omitted MIME contents."""
        if part.ref in self._plain_cache:
            return list(self._plain_cache[part.ref])
        result: list[_TextPart] = []
        if part.has_plain:
            if part.mime == "text/plain":
                result = self._text(part)
            elif part.mime == "multipart/alternative":
                chosen = None
                for index, child in enumerate(part.children):
                    candidate = self.plain(child)
                    if candidate:
                        result = candidate
                        chosen = index
                        break
                self._plain_choice[part.ref] = chosen
            else:
                result = [text for child in part.children for text in self.plain(child)]
        self._plain_cache[part.ref] = tuple(result)
        return result

    def _omit_alternative(self, part: _Part) -> None:
        if part.children and part.omission is None and part.mime in _CONTAINERS:
            for child in part.children:
                self._omit_alternative(child)
        else:
            self.omit(
                "alternative_omitted"
                if part.ref in self._decoded_refs
                else "alternative_not_decoded",
                part.ref,
            )

    def walk(self, part: _Part) -> list[_TextPart]:
        """Decode only attempted candidates, retaining one cumulative bounded budget."""
        if part.omission is not None:
            self.omit(part.omission, part.ref)
            return []
        if part.mime == "multipart/alternative":
            if part.ref not in self._plain_choice:
                self.plain(part)
            chosen = self._plain_choice.get(part.ref)
            if chosen is not None:
                result = self.walk(part.children[chosen])
            else:
                result = []
                for index, child in enumerate(part.children):
                    candidate = self.walk(child)
                    if candidate:
                        result = candidate
                        chosen = index
                        break
            for index, child in enumerate(part.children):
                if index != chosen:
                    self._omit_alternative(child)
            return result
        if part.mime in _CONTAINERS:
            return [text for child in part.children for text in self.walk(child)]
        return self._text(part)


def _internal_date(value: object) -> datetime | None:
    if value is None:
        return None
    text = _string(value, "invalid_message", maximum=16)
    _require(re.fullmatch(r"[0-9]{1,16}", text) is not None, "invalid_message")
    try:
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=int(text))
    except OverflowError:
        raise MailAccessError("invalid_message") from None


def _project(raw: bytes, source: MailSourceRef) -> MailProjection:
    _require(type(raw) is bytes and type(source) is MailSourceRef, "invalid_message")
    _require(0 < len(raw) <= MAX_MAIL_MESSAGE_BYTES, "message_too_large")
    try:
        parsed: object = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_json_pairs,
            parse_constant=_no_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        if isinstance(error, MailAccessError):
            raise
        raise MailAccessError("invalid_message") from None
    message = _object(parsed, "invalid_message")
    _require("messages" not in message, "invalid_message")
    _require(_identifier(message.get("id")) == source.message_id, "scope_mismatch")
    thread_id = _identifier(message["threadId"]) if "threadId" in message else None
    history_id = _identifier(message["historyId"]) if "historyId" in message else None
    internal_date = _internal_date(message.get("internalDate"))
    payload = _object(message.get("payload"), "invalid_message")
    projector = _Projector()
    tree = projector.build(payload)
    root_headers = tree.headers
    candidates = projector.walk(tree)
    plain = [item for item in candidates if item.mime == "text/plain" and item.text.strip()]
    html = [item for item in candidates if item.mime == "text/html" and item.text.strip()]
    selected: list[_TextPart] = []
    format_name: Literal["plain", "html_plaintext", "none"]
    if plain:
        selected = plain
        for item in html:
            projector.omit("html_lossy", item.ref)
        format_name = "plain"
    elif html:
        for item in html:
            if item.html_text is None:
                extracted, discarded_format = _extract_html(item.text)
                if discarded_format:
                    projector.omit("html_discarded_format_controls", item.ref)
            else:
                extracted = item.html_text
            projector.omit("html_lossy", item.ref)
            if extracted:
                selected.append(_TextPart(item.ref, item.mime, extracted))
        format_name = "html_plaintext"
    else:
        format_name = "none"
    text = ""
    spans: list[MailTextSpan] = []
    raw_digest = _digest(raw)
    for item in selected:
        separator = "\n\n" if text else ""
        start = len(text) + len(separator)
        available = MAX_MAIL_TEXT_CHARS - start
        if available <= 0:
            projector.omit("text_truncated", item.ref)
            break
        fragment = item.text[:available]
        if fragment:
            text += separator + fragment
            text_digest = _digest(fragment.encode("utf-8"))
            span_id = _digest(
                _canonical(
                    {
                        "source": source.model_dump(mode="json"),
                        "raw": raw_digest,
                        "version": MAIL_PROJECTION_VERSION,
                        "part": item.ref.model_dump(mode="json"),
                        "start": start,
                        "end": len(text),
                        "digest": text_digest,
                    }
                )
            )
            spans.append(
                MailTextSpan(
                    span_id=span_id,
                    part=item.ref,
                    start=start,
                    end=len(text),
                    text_digest=text_digest,
                )
            )
        if len(fragment) < len(item.text):
            projector.omit("text_truncated", item.ref)
            break
    if not text:
        projector.omit("empty_body", MailPartRef(path=()))
    complete = (
        bool(text)
        and format_name == "plain"
        and all(item.reason == "alternative_omitted" for item in projector.omissions)
    )
    completeness: Literal["complete", "partial", "unsupported"] = (
        "complete" if complete else ("partial" if text else "unsupported")
    )
    values = {
        "source": source.model_dump(mode="json"),
        "raw": raw_digest,
        "text": text,
        "headers": [item.model_dump(mode="json") for item in root_headers.claims],
        "spans": [item.model_dump(mode="json") for item in spans],
        "omissions": [item.model_dump(mode="json") for item in projector.omissions],
        "format": format_name,
        "completeness": completeness,
        "version": MAIL_PROJECTION_VERSION,
        "thread": thread_id,
        "internal_date": internal_date.isoformat() if internal_date else None,
        "history": history_id,
        "scope": "local_untrusted_preview_no_core",
    }
    return MailProjection(
        source=source,
        thread_id=thread_id,
        internal_date=internal_date,
        history_id=history_id,
        headers=root_headers.claims,
        text=text,
        spans=tuple(spans),
        format=format_name,
        completeness=completeness,
        omissions=tuple(projector.omissions),
        raw_payload_digest=raw_digest,
        projection_digest=_digest(_canonical(values)),
        decoded_bytes=projector.decoded_bytes,
        wire_bytes=len(raw),
    )


def project_gmail_message(raw: bytes, source: MailSourceRef) -> MailProjection:
    """Project one exact selected message, without promoting its claims to authority."""
    try:
        return _project(raw, source)
    except ValidationError:
        raise MailAccessError("invalid_message") from None


def project_gmail_batch(
    raw_messages: tuple[bytes, ...], sources: tuple[MailSourceRef, ...]
) -> tuple[MailProjection, ...]:
    """Admit an exact one-account batch atomically; no partial successful tuple."""
    _require(type(raw_messages) is tuple and type(sources) is tuple, "result_invalid")
    _require(1 <= len(sources) <= MAX_MAIL_MESSAGES, "selection_invalid")
    _require(all(type(source) is MailSourceRef for source in sources), "selection_invalid")
    _require(len(set(sources)) == len(sources), "selection_invalid")
    _require(all(source.account == sources[0].account for source in sources), "scope_mismatch")
    _require(len(raw_messages) == len(sources), "scope_mismatch")
    _require(all(type(raw) is bytes for raw in raw_messages), "result_invalid")
    _require(sum(len(raw) for raw in raw_messages) <= MAX_MAIL_BATCH_BYTES, "batch_too_large")
    projected = tuple(
        project_gmail_message(raw, source)
        for raw, source in zip(raw_messages, sources, strict=True)
    )
    _require(
        sum(item.decoded_bytes for item in projected) <= MAX_MAIL_BATCH_DECODED_BYTES,
        "batch_too_large",
    )
    _require(
        sum(len(item.text) for item in projected) <= MAX_MAIL_BATCH_TEXT_CHARS, "batch_too_large"
    )
    return projected
