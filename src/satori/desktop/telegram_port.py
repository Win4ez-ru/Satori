"""Bounded r5 GUI wire and transient projection; no TDLib or persistent state."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, cast

INPUT_CAP = 32 * 1024
OUTPUT_CAP = 512 * 1024
REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
SCHEMA_KEYS = frozenset(
    {
        "v",
        "phase",
        "reason",
        "operation",
        "step",
        "decoder_reason",
        "wire_bucket",
        "auth_kind",
        "cleared_reasons",
        "cleanup",
        "page_stale_reason",
    }
)
COMMAND_FIELDS = {
    "connect": {"session_only", "api_id", "api_hash"},
    "auth_submit": {"challenge_id", "kind", "value"},
    "confirm_account": {"account_token"},
    "account_preview": set(),
    "catalog": set(),
    "select_chat": {"catalog_id", "chat_token", "read_view_consent"},
    "read": set(),
    "viewport": {"page_id", "sample_id", "foreground", "active", "viewport", "rows"},
    "conceal": set(),
    "close": set(),
}
BASE = {"v", "event_seq", "type", "request_id"}
SOURCE_TYPES = {"auth", "account", "ready", "catalog", "selection", "page"}
CORRELATIONS = {
    "auth": {"connect", "auth_submit"},
    "account": {"connect", "auth_submit", "account_preview"},
    "ready": {"confirm_account"},
    "catalog": {"catalog"},
    "selection": {"select_chat"},
    "page": {"read"},
}


class WireRejected(ValueError):
    """Closed local error; never includes raw frame or exception text."""

    def __init__(self) -> None:
        super().__init__("TELEGRAM_PROTOCOL_REJECTED")


def require(condition: bool) -> None:
    if not condition:
        raise WireRejected()


def text(value: Any, limit: int, *, empty: bool = False) -> str:
    require(type(value) is str)
    try:
        require(
            (bool(value) or empty) and len(value.encode("utf-8")) <= limit and "\0" not in value
        )
    except UnicodeError:
        raise WireRejected() from None
    return cast(str, value)


def integer(value: Any, low: int, high: int) -> int:
    require(type(value) is int and low <= value <= high)
    return cast(int, value)


def boolean(value: Any) -> bool:
    require(type(value) is bool)
    return cast(bool, value)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def strict_object(raw: bytes, *, cap: int = OUTPUT_CAP) -> dict[str, Any]:
    require(0 < len(raw) <= cap)
    depth = 0
    quoted = escaped = False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            require(depth <= 24)
        elif byte in (93, 125):
            depth -= 1
            require(depth >= 0)
    try:
        value = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(WireRejected()),
        )
    except (UnicodeError, ValueError, RecursionError, OverflowError):
        raise WireRejected() from None
    require(type(value) is dict)
    return cast(dict[str, Any], value)


def schema_from(raw: bytes) -> dict[str, frozenset[str]]:
    value = strict_object(raw, cap=32768)
    require(set(value) == SCHEMA_KEYS and type(value["v"]) is int and value["v"] == 1)
    result = {}
    for key in SCHEMA_KEYS - {"v"}:
        values = value[key]
        require(
            type(values) is list
            and 0 < len(values) <= 256
            and all(type(v) is str and re.fullmatch(r"[A-Z0-9_]{1,80}", v) for v in values)
        )
        require(len(set(values)) == len(values))
        result[key] = frozenset(values)
    require(result["page_stale_reason"] == {"NEW_MESSAGE"})
    return result


class LineFramer:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        # Callers read bounded chunks; no frame or unterminated tail may exceed cap.
        require(len(chunk) <= 65536)
        self.buffer.extend(chunk)
        output = []
        while (index := self.buffer.find(b"\n")) >= 0:
            require(0 < index <= OUTPUT_CAP)
            output.append(bytes(self.buffer[:index]))
            del self.buffer[: index + 1]
        require(len(self.buffer) <= OUTPUT_CAP)
        return output

    def eof(self) -> None:
        require(not self.buffer)


@dataclass(frozen=True)
class Sent:
    command: str
    epoch: int
    started: float


@dataclass(frozen=True)
class Message:
    handle: str = field(repr=False)
    sender: str = field(repr=False)
    text: str = field(repr=False)
    date: int


@dataclass
class WireState:
    schema: dict[str, frozenset[str]]
    sequence: int = 0
    epoch: int = 0
    sent: dict[str, Sent] = field(default_factory=dict, repr=False)
    phase: str = "DISCONNECTED"
    first_failure: str | None = None
    diagnostics: str = "DISCONNECTED / NONE / NONE / NONE / NONE / NONE"
    challenge: tuple[str, str] | None = field(default=None, repr=False)
    account_token: str | None = field(default=None, repr=False)
    account_label: str = field(default="", repr=False)
    account_preview_needed: bool = False
    confirmed: bool = False
    session_started: float = 0
    session_deadline: float = 0
    catalog_id: str | None = field(default=None, repr=False)
    catalog_deadline: float = 0
    chats: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    selected: str | None = field(default=None, repr=False)
    selected_title: str = field(default="", repr=False)
    selection_deadline: float = 0
    page_id: str | None = field(default=None, repr=False)
    known_pages: set[str] = field(default_factory=set, repr=False)
    page_deadline: float = 0
    messages: tuple[Message, ...] = field(default=(), repr=False)
    omitted: int = 0
    stale: bool = False
    pending: dict[str, str] = field(default_factory=dict, repr=False)
    pending_selection_token: str | None = field(default=None, repr=False)
    terminal: bool = False
    closed: bool = False
    cleanup: str = "NOT_CREATED"
    last_clock: float | None = None

    def clear_page(self) -> None:
        self.page_id, self.page_deadline, self.messages, self.omitted = None, 0, (), 0

    def clear_selection(self) -> None:
        self.clear_page()
        self.stale, self.selected, self.selected_title, self.selection_deadline = False, None, "", 0
        self.pending.pop("read", None)
        self.pending.pop("select_chat", None)
        self.pending_selection_token = None

    def clear_catalog(self) -> None:
        self.catalog_id, self.catalog_deadline, self.chats = None, 0, ()

    def conceal(self) -> None:
        self.epoch += 1
        self.clear_selection()
        self.clear_catalog()
        self.pending.pop("catalog", None)
        self.account_preview_needed |= not self.confirmed and self.account_token is not None
        self.account_token, self.account_label = None, ""

    def reject(self) -> None:
        self.terminal = True
        self.conceal()
        self.confirmed, self.challenge = False, None
        self.pending.clear()

    def expire(self, now: float) -> bool:
        require(type(now) in (int, float) and math.isfinite(now) and now >= 0)
        if self.last_clock is not None and now < self.last_clock:
            self.reject()
            return True
        self.last_clock = now
        if self.session_deadline and now >= self.session_deadline:
            self.reject()
            return True
        if (self.selection_deadline and now >= self.selection_deadline) or (
            self.page_deadline and now >= self.page_deadline
        ):
            self.clear_selection()
            self.clear_catalog()
            return True
        if self.catalog_deadline and now >= self.catalog_deadline:
            self.clear_catalog()
            if "select_chat" in self.pending:
                self.clear_selection()
            return True
        return False

    def register(self, ident: str, command: str, now: float) -> None:
        require(
            bool(REQUEST_ID.fullmatch(ident))
            and ident not in self.sent
            and len(self.sent) < 4096
            and command in COMMAND_FIELDS
        )
        require(
            command not in {"read", "catalog", "select_chat", "confirm_account"}
            or command not in self.pending
        )
        if command == "catalog":
            self.epoch += 1
        self.sent[ident] = Sent(command, self.epoch, now)
        if command in {"read", "catalog", "select_chat", "confirm_account"}:
            self.pending[command] = ident
        if command == "read":
            self.clear_page()
        if command == "connect":
            self.session_started = now

    def _member(self, key: str, value: Any) -> str:
        require(type(value) is str and value in self.schema[key])
        return cast(str, value)

    def _shape(self, event: dict[str, Any], kind: str, request: str | None) -> dict[str, Any]:
        fields = {
            "status": {"phase", "reason", "operation", "step", "decoder_reason", "wire_bucket"},
            "auth": {"challenge_id", "kind"},
            "account": {"account_token", "label", "session_seconds"},
            "ready": {"session_seconds"},
            "catalog": {"catalog_id", "expires_in_seconds", "chats", "bounded"},
            "selection": {"chat_token", "title", "grant_seconds"},
            "page": {"page_id", "expires_in_seconds", "messages", "bounded", "omitted"},
            "page_stale": {"reason", "page_id"},
            "cleared": {"reason"},
            "closed": {"cleanup", "reason"},
        }
        require(kind in fields and set(event) == BASE | fields[kind])
        sent = self.sent.get(request) if request else None
        if kind in SOURCE_TYPES:
            require(sent is not None and sent.command in CORRELATIONS[kind])
        result: dict[str, Any] = {}
        if kind == "status":
            for key in fields[kind]:
                result[key] = self._member(key, event[key])
        elif kind == "auth":
            result = {
                "id": text(event["challenge_id"], 80),
                "kind": self._member("auth_kind", event["kind"]),
            }
        elif kind in {"account", "ready"}:
            result["seconds"] = integer(event["session_seconds"], 1, 86400)
            if kind == "account":
                result.update(
                    token=text(event["account_token"], 80), label=text(event["label"], 1024)
                )
        elif kind == "catalog":
            require(
                boolean(event["bounded"])
                and type(event["chats"]) is list
                and len(event["chats"]) <= 100
            )
            chats = []
            for row in event["chats"]:
                require(type(row) is dict and set(row) == {"chat_token", "title"})
                chats.append((text(row["chat_token"], 80), text(row["title"], 1024)))
            require(len({c[0] for c in chats}) == len(chats))
            result = {
                "id": text(event["catalog_id"], 80),
                "seconds": integer(event["expires_in_seconds"], 1, 60),
                "chats": tuple(chats),
            }
        elif kind == "selection":
            result = {
                "token": text(event["chat_token"], 80),
                "title": text(event["title"], 1024),
                "seconds": integer(event["grant_seconds"], 1, 900),
            }
        elif kind == "page":
            require(
                boolean(event["bounded"])
                and type(event["messages"]) is list
                and len(event["messages"]) <= 50
            )
            messages = []
            for row in event["messages"]:
                require(type(row) is dict and set(row) == {"handle", "sender", "text", "date"})
                messages.append(
                    Message(
                        text(row["handle"], 80),
                        text(row["sender"], 1024),
                        text(row["text"], 16384, empty=True),
                        integer(row["date"], 1, (1 << 63) - 1),
                    )
                )
            require(
                len({m.handle for m in messages}) == len(messages)
                and sum(len(m.text.encode("utf-8")) for m in messages) <= 262144
            )
            result = {
                "id": text(event["page_id"], 80),
                "seconds": integer(event["expires_in_seconds"], 1, 300),
                "messages": tuple(messages),
                "omitted": integer(event["omitted"], 0, 50),
            }
        elif kind == "page_stale":
            self._member("page_stale_reason", event["reason"])
            if event["page_id"] is None:
                require(sent is not None and sent.command == "read")
            else:
                text(event["page_id"], 80)
                require(event["page_id"] == self.page_id or event["page_id"] in self.known_pages)
            result["page"] = event["page_id"]
        elif kind == "cleared":
            result["reason"] = self._member("cleared_reasons", event["reason"])
        elif kind == "closed":
            result = {
                "reason": self._member("reason", event["reason"]),
                "cleanup": self._member("cleanup", event["cleanup"]),
            }
        return result

    def accept(self, event: dict[str, Any], now: float) -> str:
        require(
            not self.closed
            and integer(event.get("v"), 1, 1) == 1
            and integer(event.get("event_seq"), 1, (1 << 53) - 1) == self.sequence + 1
        )
        kind = text(event.get("type"), 32)
        require("request_id" in event)
        request = event["request_id"]
        require(request is None or (type(request) is str and request in self.sent))
        values = self._shape(event, kind, request)
        sent = self.sent.get(request) if request else None
        if kind == "page":
            if not self.terminal and sent is not None and sent.epoch == self.epoch:
                require(values["id"] not in self.known_pages)
            require(values["id"] in self.known_pages or len(self.known_pages) < 512)
            self.known_pages.add(values["id"])
        self.sequence += 1
        if kind == "page_stale":
            match_page = values["page"] is not None and values["page"] == self.page_id
            match_read = (
                request is not None
                and request == self.pending.get("read")
                and sent is not None
                and sent.epoch == self.epoch
            )
            if not self.terminal and (match_page or match_read):
                self.epoch += 1
                self.pending.pop("read", None)
                self.clear_page()
                self.stale, self.phase = True, "SELECTED"
            return kind
        if kind in SOURCE_TYPES and (
            self.terminal
            or (kind not in {"auth", "ready"} and sent is not None and sent.epoch != self.epoch)
        ):
            if kind == "account" and not self.terminal:
                self.account_preview_needed, self.challenge, self.phase = (
                    True,
                    None,
                    "ACCOUNT_CONFIRM",
                )
            return kind
        require(not self.terminal or kind in {"status", "cleared", "closed"})
        if kind == "status":
            self.phase = values["phase"]
            tuple_text = " / ".join(
                values[key]
                for key in ("phase", "reason", "operation", "step", "decoder_reason", "wire_bucket")
            )
            failed = values["reason"] not in {"NONE", "USER_CLOSE", "CONCEALED", "CANCELLED"}
            if failed and self.first_failure is None and values["operation"] != "CLOSE":
                self.first_failure = tuple_text
            self.diagnostics = self.first_failure or tuple_text
            if failed or self.phase == "STOPPED":
                self.reject()
        elif kind == "auth":
            require(not self.confirmed)
            self.challenge, self.phase = (values["id"], values["kind"]), "AUTH"
        elif kind == "account":
            require(not self.confirmed)
            deadline = min(now + values["seconds"], self.session_started + 86400)
            require(deadline > now)
            (
                self.account_token,
                self.account_label,
                self.account_preview_needed,
                self.challenge,
                self.phase,
            ) = values["token"], values["label"], False, None, "ACCOUNT_CONFIRM"
            self.session_deadline = min(self.session_deadline or deadline, deadline)
        elif kind == "ready":
            require(request == self.pending.get("confirm_account") and self.session_deadline > now)
            (
                self.confirmed,
                self.account_token,
                self.account_label,
                self.account_preview_needed,
                self.challenge,
                self.phase,
            ) = True, None, "", False, None, "READY"
            self.session_deadline = min(self.session_deadline, now + values["seconds"])
            self.pending.pop("confirm_account", None)
        elif kind == "catalog":
            require(request == self.pending.get("catalog") and self.confirmed and sent is not None)
            assert sent is not None
            deadline = min(now + values["seconds"], sent.started + 60, self.session_deadline)
            require(deadline > now)
            self.clear_selection()
            self.catalog_id, self.catalog_deadline, self.chats, self.phase = (
                values["id"],
                deadline,
                values["chats"],
                "CATALOG",
            )
            self.pending.pop("catalog", None)
        elif kind == "selection":
            require(
                request == self.pending.get("select_chat")
                and self.confirmed
                and now < self.catalog_deadline
                and values["token"] == self.pending_selection_token
                and any(c[0] == values["token"] for c in self.chats)
                and sent is not None
            )
            assert sent is not None
            deadline = min(now + values["seconds"], sent.started + 900, self.session_deadline)
            require(deadline > now)
            self.clear_page()
            self.selected, self.selected_title, self.selection_deadline, self.phase = (
                values["token"],
                values["title"],
                deadline,
                "SELECTED",
            )
            self.pending.pop("select_chat", None)
            self.pending_selection_token = None
        elif kind == "page":
            require(
                request == self.pending.get("read")
                and self.confirmed
                and self.selected is not None
                and now < self.selection_deadline
                and sent is not None
            )
            assert sent is not None
            deadline = min(now + values["seconds"], sent.started + 300, self.selection_deadline)
            require(deadline > now)
            (
                self.page_id,
                self.page_deadline,
                self.messages,
                self.omitted,
                self.stale,
                self.phase,
            ) = values["id"], deadline, values["messages"], values["omitted"], False, "IDLE"
            self.pending.pop("read", None)
        elif kind == "cleared":
            if (
                values["reason"] == "CONCEALED"
                and request is not None
                and sent is not None
                and sent.epoch == self.epoch
                and request in (self.pending.get("catalog"), self.pending.get("select_chat"))
            ):
                self.clear_page()
                self.selected, self.selected_title, self.selection_deadline = None, "", 0
                self.pending.pop("read", None)
            else:
                self.conceal()
        elif kind == "closed":
            self.reject()
            self.closed, self.cleanup, self.phase = True, values["cleanup"], "STOPPED"
        return kind
