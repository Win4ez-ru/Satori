"""Versioned query reads over the existing TDLib client and its single RPC receiver."""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from satori.application.assistant.source_answer_contracts import (
    SourceObservation,
    SourceSearchPlan,
    SourceTaskRequest,
    SourceTaskSnapshot,
)
from satori.application.assistant.source_task_reads import (
    clip_utf8,
    task_search_admission,
    task_snapshot,
)

TASK_SEARCH_FUNCTIONS = frozenset({"searchMessages", "searchChatsOnServer"})
TASK_NATIVE_FUNCTIONS = TASK_SEARCH_FUNCTIONS | {"getChat", "getUser", "getChatHistory"}
TASK_WIRE_VERSION = 3
PEER_CANDIDATES = 8
PEER_HISTORY_ROWS = 12


@dataclass(frozen=True, slots=True)
class _PrivateChat:
    title: str
    peer_id: int
    title_changed: bool
    handle_matches: bool = False


def _name_key(name: str) -> str:
    return " ".join(unicodedata.normalize("NFC", name).casefold().split())


class TelegramTaskExtension:
    """RAM-only query authority. It never invents a source-tab selection or viewport."""

    def __init__(self, engine: Any, principal: Any, generation: int) -> None:
        self.engine, self.principal, self.generation = engine, principal, generation
        self.native_guard: Any = None
        self.request: SourceTaskRequest | None = None
        self.plan: SourceSearchPlan | None = None
        self.wire_id: str | None = None
        self.account_id: int | None = None
        self.deadline = 0.0
        self.references: set[tuple[int, int]] = set()
        self.touched_messages: set[tuple[int, int]] = set()
        self.touched_chats: set[int] = set()
        self.peer_candidates: set[int] = set()
        self.resolved_peer: int | None = None
        self.seen: set[UUID] = set()
        self.active = False
        self.retain_until = 0.0

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.engine.wall(), UTC)

    def require(self) -> None:
        e, request, plan = self.engine, self.request, self.plan
        if (
            request is None
            or plan is None
            or e.stopped
            or e.closing
            or e.output_lost
            or e.interrupted.is_set()
            or not e.account_confirmed
            or e.auth_state != "authorizationStateReady"
            or e.account_id != self.account_id
            or e.clock() >= self.deadline
            or e.clock() >= e.session_until
            or request.scope.profile_id != self.principal.profile_id
        ):
            raise ValueError("telegram_task_scope_unavailable")
        task_search_admission(request, plan, "telegram", self.now())
        e.tick(allow_maintenance=False)

    def rpc(self, query: dict[str, Any]) -> dict[str, Any]:
        if query.get("@type") not in TASK_NATIVE_FUNCTIONS or self.native_guard is not None:
            raise ValueError("telegram_task_rpc_unavailable")
        self.require()
        self.native_guard = self.require
        try:
            result = self.engine.rpc(query)
            self.require()
            return cast(dict[str, Any], result)
        finally:
            self.native_guard = None

    def emit(self, kind: str, **fields: Any) -> None:
        e = self.engine
        if e.output_lost:
            return
        e.event_seq += 1
        packet = {
            "v": TASK_WIRE_VERSION,
            "event_seq": e.event_seq,
            "type": kind,
            "request_id": self.wire_id,
            **fields,
        }
        if len(json.dumps(packet, ensure_ascii=False).encode("utf-8")) > 256 * 1024:
            raise ValueError("telegram_task_output_bound")
        try:
            e.emit_sink(packet)
        except OSError:
            e.output_lost = True
            e.interrupted.set()

    def invalidate(self, *, publish: bool = False) -> None:
        task_id = self.request.request_id if self.request is not None else None
        self.references.clear()
        self.peer_candidates.clear()
        self.resolved_peer = None
        if publish and task_id is not None:
            self.emit("task_invalidated", task_id=str(task_id), reason="source_changed")
        self.request = self.plan = None
        self.account_id = None
        self.deadline = 0

    def expire(self) -> None:
        if (
            self.request is not None
            and not self.active
            and self.engine.clock() >= self.retain_until
        ):
            self.invalidate()

    def observe(self, event: dict[str, Any]) -> None:
        if self.request is None:
            return
        chat_id, kind = event.get("chat_id"), event.get("@type")
        if kind == "updateChat" and type(event.get("chat")) is dict:
            chat_id = event["chat"].get("id")
        if kind == "updateUser" and type(event.get("user")) is dict:
            chat_id = event["user"].get("id")
        if type(chat_id) is not int:
            return
        peer_changed = kind in {"updateChatTitle", "updateUser"} and (
            chat_id in self.peer_candidates or chat_id == self.resolved_peer
        )
        chat_changed = (
            kind
            in {
                "updateChat",
                "updateChatHasProtectedContent",
                "updateChatMessageAutoDeleteTime",
            }
            or peer_changed
        )
        changed_ids: set[int] = set()
        if (
            kind in {"updateMessageEdited", "updateMessageContent", "updateMessageContentOpened"}
            and type(event.get("message_id")) is int
        ):
            changed_ids.add(event["message_id"])
        if kind == "updateDeleteMessages":
            removed = event.get("message_ids")
            if type(removed) is list:
                if len(removed) > 256:
                    self.invalidate(publish=True)
                    return
                changed_ids.update(mid for mid in removed if type(mid) is int)
        # Changes can arrive in the receiver before searchMessages returns its IDs.
        # Keep bounded ID-only tombstones throughout the query, never update bodies.
        if self.active:
            if chat_changed:
                self.touched_chats.add(chat_id)
            self.touched_messages.update((chat_id, mid) for mid in changed_ids)
            if len(self.touched_chats) + len(self.touched_messages) > 256:
                self.invalidate(publish=True)
                return
        referenced = {mid for cid, mid in self.references if cid == chat_id}
        # A later arrival is not a mutation of an already observed snapshot.
        if (
            chat_changed and (referenced or chat_id == self.resolved_peer)
        ) or referenced.intersection(changed_ids):
            self.invalidate(publish=True)

    def admit_reference(self, chat_id: int, message_id: int) -> None:
        if chat_id in self.touched_chats or (chat_id, message_id) in self.touched_messages:
            self.invalidate(publish=True)
            raise ValueError("telegram_task_source_changed")
        # Register before getChat/getUser: their receive loops also dispatch updates.
        self.references.add((chat_id, message_id))
        self.require()

    @staticmethod
    def integer(value: Any, *, positive: bool = False) -> int:
        if type(value) is not int or abs(value) > (1 << 53) - 1 or (positive and value <= 0):
            raise ValueError("telegram_task_source_invalid")
        return value

    @staticmethod
    def plain(value: str, maximum: int, *, multiline: bool = True) -> tuple[str, bool]:
        normalized = "".join(
            char
            for char in value
            if unicodedata.category(char) not in {"Cc", "Cf", "Cs"}
            or (multiline and char in "\n\t")
        )
        clipped, changed = clip_utf8(normalized, maximum)
        return clipped, changed or normalized != value

    def chat(
        self, chat_id: int, cache: dict[int, _PrivateChat], *, handle: str | None = None
    ) -> _PrivateChat | None:
        if chat_id in cache:
            return cache[chat_id]
        chat = self.rpc({"@type": "getChat", "chat_id": chat_id})
        if chat.get("@type") != "chat" or chat.get("id") != chat_id:
            raise ValueError("telegram_task_source_invalid")
        kind = chat.get("type")
        if (
            type(kind) is not dict
            or kind.get("@type") != "chatTypePrivate"
            or chat.get("has_protected_content") is not False
            or type(chat.get("message_auto_delete_time")) is not int
            or chat["message_auto_delete_time"] != 0
        ):
            return None
        peer = self.integer(kind.get("user_id"), positive=True)
        if peer != chat_id:
            return None
        user = self.rpc({"@type": "getUser", "user_id": peer})
        if (
            user.get("@type") != "user"
            or user.get("id") != peer
            or type(user.get("type")) is not dict
            or user["type"].get("@type") != "userTypeRegular"
        ):
            return None
        title = chat.get("title")
        if type(title) is not str or len(title.encode("utf-8")) > 2048:
            raise ValueError("telegram_task_source_invalid")
        plain_title, changed = self.plain(title, 500, multiline=False)
        handle_matches = False
        if handle is not None:
            usernames = user.get("usernames")
            if usernames is not None:
                active = usernames.get("active_usernames") if type(usernames) is dict else None
                if (
                    type(active) is not list
                    or len(active) > 128
                    or any(type(name) is not str or len(name) > 32 for name in active)
                ):
                    raise ValueError("telegram_task_source_invalid")
                handle_matches = any(name.casefold() == handle for name in active)
        cache[chat_id] = _PrivateChat(plain_title, peer, changed, handle_matches)
        return cache[chat_id]

    def message(self, row: Any, cache: dict[int, _PrivateChat]) -> SourceObservation | None:
        request, plan = self.request, self.plan
        assert request is not None
        assert plan is not None
        if type(row) is not dict or row.get("@type") != "message":
            raise ValueError("telegram_task_source_invalid")
        chat_id = self.integer(row.get("chat_id"), positive=True)
        message_id = self.integer(row.get("id"), positive=True)
        date = self.integer(row.get("date"), positive=True)
        timestamp = datetime.fromtimestamp(date, UTC)
        if not plan.window_start <= timestamp < plan.window_end:
            return None
        if (
            row.get("is_secret") is True
            or row.get("can_be_saved") is not True
            or row.get("self_destruct_type") is not None
            or type(row.get("auto_delete_in")) not in {int, float}
            or row["auto_delete_in"] != 0
            or (
                row.get("self_destruct_in") is not None
                and (
                    type(row["self_destruct_in"]) not in {int, float}
                    or row["self_destruct_in"] != 0
                )
            )
            or row.get("forward_info") is not None
        ):
            return None
        reply = row.get("reply_to")
        if reply is not None and (
            type(reply) is not dict or reply.get("@type") != "messageReplyToMessage"
        ):
            return None
        sender, content = row.get("sender_id"), row.get("content")
        if (
            type(sender) is not dict
            or sender.get("@type") != "messageSenderUser"
            or type(content) is not dict
            or content.get("@type") != "messageText"
        ):
            return None
        formatted = content.get("text")
        if type(formatted) is not dict or type(formatted.get("text")) is not str:
            raise ValueError("telegram_task_source_invalid")
        body = formatted["text"]
        self.admit_reference(chat_id, message_id)
        checked_chat = self.chat(chat_id, cache)
        if checked_chat is None:
            return None
        sender_id = self.integer(sender.get("user_id"), positive=True)
        if sender_id not in {self.account_id, checked_chat.peer_id}:
            return None
        text, clipped = self.plain(body, 4000)
        self.require()
        ref = (
            "telegram:"
            + hashlib.sha256(f"{request.request_id}:{chat_id}:{message_id}".encode()).hexdigest()[
                :32
            ]
        )
        return SourceObservation(
            source="telegram",
            source_ref=ref,
            observed_at=self.now(),
            event_at=timestamp,
            title=checked_chat.title,
            sender="Ты" if sender_id == self.account_id else checked_chat.title,
            text=text,
            completeness="partial" if clipped or checked_chat.title_changed else "complete",
        )

    def resolve_peer(self, query: str, cache: dict[int, _PrivateChat]) -> tuple[int | None, str]:
        key = _name_key(query)
        handle = key[1:] if key.startswith("@") else None
        if handle is not None and (
            not 1 <= len(handle) <= 32
            or not handle.isascii()
            or not handle.replace("_", "a").isalnum()
        ):
            return None, "peer_not_found"
        first_name = key.isalpha()
        result = self.rpc(
            {
                "@type": "searchChatsOnServer",
                "query": handle if handle is not None else query,
                "type_filter": None,
                "limit": PEER_CANDIDATES,
            }
        )
        raw_ids = result.get("chat_ids")
        total = result.get("total_count")
        if (
            result.get("@type") != "chats"
            or type(raw_ids) is not list
            or len(raw_ids) > PEER_CANDIDATES
            or type(total) is not int
            or total < 0
        ):
            raise ValueError("telegram_task_source_invalid")
        ids = {self.integer(chat_id) for chat_id in raw_ids}
        if 0 in ids or len(ids) != len(raw_ids):
            raise ValueError("telegram_task_source_invalid")
        if len(ids) == PEER_CANDIDATES or total > len(ids):
            return None, "peer_lookup_incomplete"
        # Only ordinary private chat IDs can be positive. The account's own chat
        # is excluded before reading any further candidate metadata.
        self.peer_candidates = {cid for cid in ids if cid > 0 and cid != self.account_id}
        matches = []
        unresolved = False
        for chat_id in sorted(self.peer_candidates):
            chat = self.chat(chat_id, cache, handle=handle)
            if chat is None or chat.title_changed:
                unresolved = True
                continue
            title = _name_key(chat.title)
            if (
                chat.handle_matches
                if handle is not None
                else title == key or (first_name and title.partition(" ")[0] == key)
            ):
                matches.append(chat_id)
        self.require()
        if self.touched_chats.intersection(self.peer_candidates):
            return None, "peer_lookup_incomplete"
        if len(matches) > 1:
            return None, "peer_ambiguous"
        if unresolved:
            return None, "peer_unresolved"
        return (matches[0], "") if matches else (None, "peer_not_found")

    def peer_snapshot(self, query: str) -> SourceTaskSnapshot:
        request, plan = self.request, self.plan
        assert request is not None
        assert plan is not None
        cache: dict[int, _PrivateChat] = {}
        peer, detail = self.resolve_peer(query, cache)
        if peer is None:
            return task_snapshot(
                request,
                plan,
                "telegram",
                self.now(),
                (),
                status="partial",
                more=detail == "peer_lookup_incomplete",
                detail=detail,
            )

        self.resolved_peer = peer
        result = self.rpc(
            {
                "@type": "getChatHistory",
                "chat_id": peer,
                "from_message_id": 0,
                "offset": 0,
                "limit": PEER_HISTORY_ROWS,
                "only_local": False,
            }
        )
        rows = result.get("messages")
        if (
            result.get("@type") != "messages"
            or type(rows) is not list
            or len(rows) > PEER_HISTORY_ROWS
        ):
            raise ValueError("telegram_task_source_invalid")
        items = []
        omitted = False
        covered_window = False
        previous_id, previous_date = (1 << 53), (1 << 53)
        for row in rows:
            if (
                type(row) is not dict
                or row.get("@type") != "message"
                or self.integer(row.get("chat_id"), positive=True) != peer
            ):
                raise ValueError("telegram_task_source_invalid")
            message_id = self.integer(row.get("id"), positive=True)
            date = self.integer(row.get("date"), positive=True)
            if message_id >= previous_id or date > previous_date:
                raise ValueError("telegram_task_source_invalid")
            previous_id, previous_date = message_id, date
            timestamp = datetime.fromtimestamp(date, UTC)
            if timestamp < plan.window_start:
                covered_window = True
                continue
            if timestamp >= plan.window_end:
                continue
            sender = row.get("sender_id")
            user_id = (
                sender.get("user_id")
                if type(sender) is dict and sender.get("@type") == "messageSenderUser"
                else None
            )
            if (
                row.get("is_outgoing") is True
                and type(user_id) is int
                and user_id == self.account_id
            ):
                continue
            if row.get("is_outgoing") is not False or type(user_id) is not int or user_id != peer:
                omitted = True
                continue
            item = self.message(row, cache)
            if item is None:
                omitted = True
            else:
                items.append(item)
        # TDLib can return fewer than the requested rows even when history remains.
        # Only an older row proves the bounded page reached the start of this window.
        self.require()
        return task_snapshot(
            request,
            plan,
            "telegram",
            self.now(),
            tuple(items),
            status="searched" if covered_window and not omitted else "partial",
            more=not covered_window,
            detail="peer_incoming" if covered_window and not omitted else "peer_history_incomplete",
        )

    def command(self, value: dict[str, Any]) -> None:
        e = self.engine
        if type(value.get("v")) is not int or value.get("v") != TASK_WIRE_VERSION:
            raise ValueError("telegram_task_command_invalid")
        if set(value) != {"v", "command", "request_id", "request", "plan"}:
            raise ValueError("telegram_task_command_invalid")
        wire_id = value["request_id"]
        if (
            type(wire_id) is not str
            or len(wire_id) > 80
            or not wire_id.isalnum()
            or wire_id in e.request_ids
            or len(e.request_ids) >= 4096
        ):
            raise ValueError("telegram_task_command_invalid")
        e.request_ids.add(wire_id)
        request = SourceTaskRequest.model_validate_json(json.dumps(value["request"]))
        plan = SourceSearchPlan.model_validate_json(json.dumps(value["plan"]))
        if value["command"] == "task_cancel":
            if self.request is not None and self.request.request_id == request.request_id:
                self.invalidate()
            self.wire_id = wire_id
            self.emit("task_cancelled", task_id=str(request.request_id))
            return
        if (
            value["command"] != "task_search"
            or request.request_id in self.seen
            or len(self.seen) >= 512
        ):
            raise ValueError("telegram_task_command_invalid")
        try:
            query = task_search_admission(request, plan, "telegram", self.now())
        except ValueError:
            self.wire_id = wire_id
            snapshot = task_snapshot(
                request,
                plan,
                "telegram",
                self.now(),
                (),
                status="error",
                detail="telegram_task_expired",
            )
            self.emit(
                "task_result",
                task_id=str(request.request_id),
                snapshot=snapshot.model_dump(mode="json"),
            )
            return
        if len(query) > 512 or any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in query):
            raise ValueError("telegram_task_query_invalid")
        self.invalidate()
        self.request, self.plan, self.wire_id = request, plan, wire_id
        self.account_id = e.account_id
        self.deadline = min(
            e.session_until,
            e.clock() + 30,
            e.clock() + (request.expires_at - self.now()).total_seconds(),
        )
        self.seen.add(request.request_id)
        self.active = True
        self.touched_messages.clear()
        self.touched_chats.clear()
        self.retain_until = min(
            e.session_until, e.clock() + (request.expires_at - self.now()).total_seconds()
        )
        omitted, related = 0, 0
        cache: dict[int, _PrivateChat] = {}
        items: dict[str, SourceObservation] = {}
        previous_deadline = e.command_deadline
        e.command_deadline = self.deadline
        try:
            self.require()
            # Old selected-page authority is never reused by a task. The conversation
            # host has already concealed that surface; refuse a concurrently open one.
            if e.selected_chat is not None or e.page_id is not None:
                raise ValueError("telegram_task_source_busy")
            if plan.telegram_match == "peer":
                snapshot = self.peer_snapshot(query)
                self.require()
                self.emit(
                    "task_result",
                    task_id=str(request.request_id),
                    snapshot=snapshot.model_dump(mode="json"),
                )
                return
            found = self.rpc(
                {
                    "@type": "searchMessages",
                    "chat_list": None,
                    "query": query,
                    "offset": "",
                    "limit": 5,
                    "filter": None,
                    "chat_type_filter": {"@type": "searchMessagesChatTypeFilterPrivate"},
                    "min_date": math.floor(plan.window_start.timestamp()),
                    "max_date": math.ceil(plan.window_end.timestamp()),
                }
            )
            rows = found.get("messages")
            if found.get("@type") != "foundMessages" or type(rows) is not list or len(rows) > 5:
                raise ValueError("telegram_task_source_invalid")
            seen_rows: set[tuple[int, int]] = set()
            anchors: list[tuple[int, int]] = []
            for row in rows:
                if type(row) is not dict:
                    raise ValueError("telegram_task_source_invalid")
                key = (
                    self.integer(row.get("chat_id"), positive=True),
                    self.integer(row.get("id"), positive=True),
                )
                if key in seen_rows:
                    raise ValueError("telegram_task_source_invalid")
                seen_rows.add(key)
                item = self.message(row, cache)
                if item is None:
                    omitted += 1
                else:
                    items[item.source_ref] = item
                    anchors.append(key)
            # Read only a tiny context around three exact query matches, never crawl.
            for chat_id, message_id in anchors[:3]:
                history = self.rpc(
                    {
                        "@type": "getChatHistory",
                        "chat_id": chat_id,
                        "from_message_id": message_id,
                        "offset": -2,
                        "limit": 5,
                        "only_local": False,
                    }
                )
                context = history.get("messages")
                if (
                    history.get("@type") != "messages"
                    or type(context) is not list
                    or len(context) > 5
                ):
                    raise ValueError("telegram_task_source_invalid")
                if not any(
                    type(row) is dict
                    and row.get("id") == message_id
                    and row.get("chat_id") == chat_id
                    for row in context
                ):
                    raise ValueError("telegram_task_source_changed")
                for row in context:
                    if type(row) is not dict or row.get("chat_id") != chat_id:
                        raise ValueError("telegram_task_source_invalid")
                    item = self.message(row, cache)
                    if item is None:
                        omitted += 1
                    elif item.source_ref not in items:
                        related += 1
                        items[item.source_ref] = item
            self.require()
            cursor = found.get("next_offset")
            if type(cursor) is not str or len(cursor) > 4096:
                raise ValueError("telegram_task_source_invalid")
            snapshot = task_snapshot(
                request,
                plan,
                "telegram",
                self.now(),
                tuple(items.values()),
                status="partial" if omitted else "searched",
                more=bool(cursor),
                detail=f"private_chats_only;omitted={omitted};related={related}",
            )
            self.require()
            self.emit(
                "task_result",
                task_id=str(request.request_id),
                snapshot=snapshot.model_dump(mode="json"),
            )
        except Exception as error:
            self.references.clear()
            self.resolved_peer = None
            if getattr(error, "reason", None) in {
                "AUTH_LOST",
                "SESSION_EXPIRED",
                "CLOCK_ROLLBACK",
                "CLIENT_INVALID_NATIVE_FRAME",
            }:
                raise
            snapshot = task_snapshot(
                request,
                plan,
                "telegram",
                self.now(),
                (),
                status="error",
                detail="telegram_task_read_failed",
            )
            self.emit(
                "task_result",
                task_id=str(request.request_id),
                snapshot=snapshot.model_dump(mode="json"),
            )
        finally:
            self.active = False
            self.peer_candidates.clear()
            self.touched_messages.clear()
            self.touched_chats.clear()
            e.command_deadline = previous_deadline
