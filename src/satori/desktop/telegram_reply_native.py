"""Explicit reply-v1 composition beside the existing, pinned read receiver.

Loading these definitions never constructs TDLib. Native IDs never cross the
reply wire. A new worker opts into two fixed RPCs; the old worker is unchanged.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import runpy
import stat
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from satori.application.assistant.contracts import LocalPrincipal
from satori.application.assistant.telegram_reply import TelegramReplyOwner
from satori.application.assistant.telegram_reply_contracts import (
    DispatchGuard,
    ReplyApproval,
    ReplyClock,
    ReplyDenied,
    ReplyDraft,
    SelectedReplyScope,
)
from satori.desktop.telegram_task_native import (
    TASK_NATIVE_FUNCTIONS,
    TASK_SEARCH_FUNCTIONS,
    TelegramTaskExtension,
)
from satori.infrastructure.telegram_reply.journal import SQLiteReplyJournal
from satori.infrastructure.telegram_reply.tdlib import NativeReplyBinding, TDLibReplyAdapter

LEGACY_WORKER_SHA = "69235f84d83bc49a01fba8a4e342775027d2a1a433fbef5bb25e88c127bef910"
REPLY_COMMANDS = {
    "reply_preview": {"page_id", "message_handle", "text", "action_id"},
    "reply_approve": {"action_id", "payload_digest", "confirmed"},
    "reply_send": {"action_id", "approval_id"},
    "reply_reconcile": {"action_id"},
    "reply_discard": set(),
}


def _require(condition: bool) -> None:
    if not condition:
        raise ReplyDenied("reply_v1_rejected")


def legacy_definitions(path: Path) -> dict[str, Any]:
    """Only the exact public read implementation, not an arbitrary executable path."""
    _require(not path.is_symlink() and path.is_file())
    _require(hashlib.sha256(path.read_bytes()).hexdigest() == LEGACY_WORKER_SHA)
    return runpy.run_path(str(path), run_name="satori_telegram_readonly_v1")


class _OwnedRPC:
    reviewed_reply_enabled = True

    def __init__(self, extension: ReplyEngineExtension) -> None:
        self.extension = extension

    def call(self, query: dict[str, object], *, before_send: DispatchGuard) -> dict[str, object]:
        extension, engine = self.extension, self.extension.engine
        _require(query.get("@type") in {"sendMessage", "getMessage"})
        _require(extension.native_guard is None)
        extension.native_guard = before_send
        if query.get("@type") == "sendMessage":
            options = query.get("options")
            _require(extension.draft is not None and type(options) is dict)
            assert isinstance(options, dict)
            assert extension.draft is not None
            sending_id = options.get("sending_id")
            _require(type(sending_id) is int)
            assert isinstance(sending_id, int)
            extension._active_send = extension.draft, sending_id
        try:
            # The existing RPC owns @extra correlation, timeout and the sole receive loop.
            return cast(dict[str, object], engine.rpc(query, allowed_error=404))
        except extension.legacy["SnapshotStale"] as stale:
            engine.stale_page(stale)
            extension.invalidate()
            raise ReplyDenied("reply_source_changed") from None
        except Exception:
            raise ReplyDenied("reply_native_unknown") from None
        finally:
            extension.native_guard = None
            extension._active_send = None


class ReplyEngineExtension:
    """One ephemeral reply owner sharing the read owner's handles and RPC lane."""

    def __init__(
        self,
        engine: Any,
        legacy: dict[str, Any],
        principal: LocalPrincipal,
        generation: int,
        journal_path: Path,
    ) -> None:
        _require(generation >= 1 and principal.os_uid == os.getuid())
        self.engine, self.legacy = engine, legacy
        self.principal, self.generation, self.journal_path = principal, generation, journal_path
        self.auth_session_id, self.account_epoch = uuid4(), uuid4()
        self.native_guard: DispatchGuard | None = None
        self.journal: SQLiteReplyJournal | None = None
        self.owner: TelegramReplyOwner | None = None
        self.adapter = TDLibReplyAdapter(_OwnedRPC(self), self.resolve)
        self.draft: ReplyDraft | None = None
        self.approval: ReplyApproval | None = None
        self._scope_value: SelectedReplyScope | None = None
        self._scope_key: tuple[object, ...] | None = None
        self._terminal_updates: list[dict[str, object]] = []
        self._active_send: tuple[ReplyDraft, int] | None = None

    def clock(self) -> ReplyClock:
        return ReplyClock(
            wall=datetime.fromtimestamp(self.engine.wall(), UTC),
            monotonic=float(self.engine.clock()),
        )

    def selected(self) -> SelectedReplyScope | None:
        e = self.engine
        if (
            e.stopped
            or e.closing
            or e.output_lost
            or e.interrupted.is_set()
            or not e.account_confirmed
            or e.auth_state != "authorizationStateReady"
            or e.selected_chat is None
            or not e.page_id
            or not e.selected_token
            or not e.last_presence_active
            or e.clock() - e.last_presence_sample > 2
        ):
            return None
        deadline = min(e.session_until, e.grant_until, e.page_until)
        if not math.isfinite(deadline) or e.clock() >= deadline:
            return None
        key = (e.account_id, e.selected_token, e.page_id, deadline)
        if key != self._scope_key:
            self._scope_key = key
            self._scope_value = SelectedReplyScope(
                principal=self.principal,
                auth_session_id=self.auth_session_id,
                account_epoch=self.account_epoch,
                generation=self.generation,
                selection_id=uuid5(NAMESPACE_URL, str(key)),
                chat_handle=UUID(hex=e.selected_token),
                page_id=UUID(hex=e.page_id),
                expires_at=self.clock().wall + timedelta(seconds=deadline - e.clock()),
                monotonic_deadline=float(deadline),
            )
        return self._scope_value

    def resolve(self, scope: SelectedReplyScope, handle: UUID) -> NativeReplyBinding | None:
        if self.selected() != scope:
            return None
        e = self.engine
        row = e.messages.get(handle.hex)
        if row is None or row.sender_id != e.selected_chat["user_id"]:
            return None
        return NativeReplyBinding(
            scope=scope,
            reply_to_handle=handle,
            personal_chat_verified=True,
            chat_id=e.selected_chat["id"],
            reply_to_message_id=row.native_id,
        )

    def invalidate(self) -> None:
        if self.owner is not None:
            self.owner.revoke()
        self.owner, self.draft, self.approval = None, None, None
        self._scope_value, self._scope_key = None, None
        self.adapter.clear()
        self._terminal_updates.clear()

    def close(self) -> None:
        self.invalidate()
        if self.journal is not None:
            self.journal.close()
            self.journal = None

    def _owner(self) -> TelegramReplyOwner:
        _require(self.selected() is not None)
        if self.journal is None:
            parent = self.journal_path.parent
            _require(self.journal_path.is_absolute() and not parent.is_symlink())
            for path in (parent, *parent.parents):
                _require(not path.is_symlink())
            if not parent.exists():
                parent.mkdir(mode=0o700)
            st = parent.stat()
            _require(st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) == 0o700)
            self.journal = SQLiteReplyJournal(
                self.journal_path, create=not self.journal_path.exists()
            )
        if self.owner is None:
            self.owner = TelegramReplyOwner(
                selection=self.selected, clock=self.clock, journal=self.journal, port=self.adapter
            )
        return self.owner

    def _recheck_recipient(self) -> None:
        e = self.engine
        _require(self.selected() is not None)
        selected = dict(e.selected_chat)
        chat = e.rpc({"@type": "getChat", "chat_id": selected["id"]})
        _require(
            chat.get("@type") == "chat"
            and chat.get("id") == selected["id"]
            and e._supported_chat(chat)
            and chat["type"].get("user_id") == selected["user_id"]
            and type(chat.get("paid_message_star_count")) is int
            and chat["paid_message_star_count"] == 0
        )
        user = e.rpc({"@type": "getUser", "user_id": selected["user_id"]})
        _require(
            user.get("@type") == "user"
            and user.get("id") == selected["user_id"]
            and type(user.get("type")) is dict
            and user["type"].get("@type") == "userTypeRegular"
        )
        _require(self.selected() is not None)

    def observe(self, event: dict[str, Any]) -> bool:
        """Terminal updates only correlate; they never publish SENT."""
        if event.get("@type") in {"updateMessageSendSucceeded", "updateMessageSendFailed"}:
            draft = self.draft
            if draft is None or self.owner is None:
                return True
            binding = self.resolve(draft.scope, draft.reply_to_handle)
            prior, message = self.owner.receipt(draft.request_id), event.get("message")
            if binding is None or prior is None or type(message) is not dict:
                return True
            try:
                self.adapter._parse(draft, binding, prior.sending_id, message)
            except (ValueError, AssertionError, TypeError):
                return True
            if len(self._terminal_updates) >= 32:
                self.invalidate()
                return False
            self._terminal_updates.append(
                {
                    "@type": event["@type"],
                    "old_message_id": event.get("old_message_id"),
                    "message": {"id": message.get("id"), "chat_id": message.get("chat_id")},
                }
            )
            return True
        # The exact outgoing message created by our currently admitted send is
        # not an edit to the selected original page. Never exempt another message.
        if event.get("@type") == "updateNewMessage" and self._active_send is not None:
            draft, sending_id = self._active_send
            message = event.get("message")
            binding = self.resolve(draft.scope, draft.reply_to_handle)
            if type(message) is dict and binding is not None:
                try:
                    correlated = self.adapter._parse(draft, binding, sending_id, message)
                    if correlated.state == "unknown" and correlated.code == "native_pending":
                        return True
                except (ValueError, AssertionError, TypeError):
                    pass
        return False

    def _terminal_mappings(self) -> None:
        scope = self.selected()
        if scope is None:
            self.invalidate()
            raise ReplyDenied("reply_source_unavailable")
        for update in self._terminal_updates:
            try:
                self.adapter.observe_terminal_update(scope, update)
            except (ValueError, AssertionError, TypeError):
                continue
        self._terminal_updates.clear()

    def _emit(self, status: str, **fields: object) -> None:
        e = self.engine
        e.event_seq += 1
        value = {
            "v": 2,
            "capability": "reviewed_reply_v1",
            "event_seq": e.event_seq,
            "type": "reply",
            "request_id": e.current_request,
            "status": status,
            **fields,
        }
        _require(len(json.dumps(value, ensure_ascii=False).encode()) <= 32768)
        e.emit_sink(value)

    def command(self, command: dict[str, Any]) -> None:
        e = self.engine
        operation = command.get("command")
        _require(
            command.get("v") == 2
            and type(command.get("v")) is int
            and command.get("capability") == "reviewed_reply_v1"
            and type(operation) is str
            and operation in REPLY_COMMANDS
            and set(command)
            == {"v", "capability", "command", "request_id"} | REPLY_COMMANDS[operation]
        )
        ident = command["request_id"]
        _require(type(ident) is str and e.request_ids is not None and ident not in e.request_ids)
        _require(bool(self.legacy["REQUEST_ID"].fullmatch(ident)) and len(e.request_ids) < 4096)
        e.request_ids.add(ident)
        e.current_request, e.current_command = ident, None
        e.command_deadline = e.clock() + 30
        draft: ReplyDraft | None
        approval: ReplyApproval | None
        try:
            e.tick(allow_maintenance=False)
            if operation == "reply_discard":
                self.invalidate()
                self._emit("discarded")
                return
            _require(self.selected() is not None)
            if operation == "reply_preview":
                _require(command["page_id"] == e.page_id)
                self.invalidate()
                self._recheck_recipient()
                owner = self._owner()
                draft = owner.draft(
                    UUID(command["action_id"]), UUID(hex=command["message_handle"]), command["text"]
                )
                self.adapter.validate_recipient(draft)
                self.draft = draft
                self._emit(
                    "preview",
                    action_id=str(draft.request_id),
                    page_id=e.page_id,
                    message_handle=draft.reply_to_handle.hex,
                    payload_digest=draft.payload_digest,
                    text=draft.text,
                    chat_title=e.selected_chat["title"],
                    expires_at=draft.expires_at.isoformat(),
                )
            else:
                draft = self.draft
                _require(draft is not None and str(draft.request_id) == command["action_id"])
                assert draft is not None
                owner = self._owner()
                if operation == "reply_approve":
                    _require(
                        command["confirmed"] is True
                        and command["payload_digest"] == draft.payload_digest
                    )
                    self._recheck_recipient()
                    approval = owner.approve(draft, confirmed=True)
                    self.approval = approval
                    self._emit(
                        "approved",
                        action_id=str(draft.request_id),
                        approval_id=str(approval.approval_id),
                        payload_digest=approval.payload_digest,
                        expires_at=approval.expires_at.isoformat(),
                    )
                else:
                    if operation == "reply_send":
                        approval = self.approval
                        _require(
                            approval is not None
                            and str(approval.approval_id) == command["approval_id"]
                        )
                        assert approval is not None
                        self.approval = None
                        self._recheck_recipient()
                        receipt = owner.send(approval)
                    else:
                        self._terminal_mappings()
                        receipt = owner.reconcile(draft)
                    if self.selected() != draft.scope:
                        self._emit("unknown", action_id=str(draft.request_id))
                    else:
                        self._emit(
                            receipt.state, action_id=str(draft.request_id), code=receipt.code
                        )
        except Exception:
            # A failed publication is never a receipt of non-delivery.
            self._emit(
                "unknown" if operation in {"reply_send", "reply_reconcile"} else "unavailable"
            )
        finally:
            e.current_request, e.current_command, e.command_deadline = None, None, None


def build_reply_engine(
    legacy: dict[str, Any],
    library: Path,
    library_sha: str,
    session_root: Path,
    emit: Callable[[dict[str, Any]], None],
    *,
    principal: LocalPrincipal,
    generation: int,
    journal_path: Path,
    native_factory: Callable[..., Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
    wall: Callable[[], float] = time.time,
) -> Any:
    """Build one versioned engine without starting/loading its native client."""
    base, native_base = legacy["Engine"], legacy["NativeClient"]
    extension_box: list[ReplyEngineExtension] = []
    task_box: list[TelegramTaskExtension] = []

    def native_send(self: Any, query: dict[str, Any]) -> None:
        task = task_box[0] if task_box else None
        if query.get("@type") in TASK_SEARCH_FUNCTIONS:
            _require(task is not None and task.native_guard is not None)
            assert task is not None
            assert task.native_guard is not None
            task.native_guard()
            self.lib.td_send(
                self.client_id, json.dumps(query, ensure_ascii=True, allow_nan=False).encode()
            )
            return
        if task is not None and task.native_guard is not None:
            _require(query.get("@type") in TASK_NATIVE_FUNCTIONS)
            task.native_guard()
        if query.get("@type") not in {"sendMessage", "getMessage"}:
            native_base.send(self, query)
            return
        extension = extension_box[0]
        _require(extension.native_guard is not None)
        guard = extension.native_guard
        assert guard is not None
        # This exact callback is inside the only native send lane, after @extra
        # construction and immediately before td_send. No arbitrary RPC is exposed.
        guard()
        self.lib.td_send(
            self.client_id, json.dumps(query, ensure_ascii=True, allow_nan=False).encode()
        )

    native_type = type("ReviewedReplyNativeClientV1", (native_base,), {"send": native_send})

    def make_native(*args: Any, **kwargs: Any) -> Any:
        if native_factory is None:
            return native_type(*args, **kwargs)
        client = native_factory(*args, **kwargs)

        class GuardedFixtureClient:
            def send(self, query: dict[str, Any]) -> None:
                task = task_box[0] if task_box else None
                if query.get("@type") in TASK_SEARCH_FUNCTIONS:
                    _require(task is not None and task.native_guard is not None)
                    assert task is not None
                    assert task.native_guard is not None
                    task.native_guard()
                elif task is not None and task.native_guard is not None:
                    _require(query.get("@type") in TASK_NATIVE_FUNCTIONS)
                    task.native_guard()
                if query.get("@type") in {"sendMessage", "getMessage"}:
                    guard = extension_box[0].native_guard
                    _require(guard is not None)
                    assert guard is not None
                    guard()
                client.send(query)

            def receive(self, timeout: float) -> Any:
                return client.receive(timeout)

        return GuardedFixtureClient()

    def dispatch(self: Any, command: Any) -> None:
        extension = extension_box[0]
        if type(command) is dict and type(command.get("v")) is int and command.get("v") == 3:
            try:
                task_box[0].command(command)
            except Exception:
                raise legacy["Rejected"]("INPUT_REJECTED") from None
        elif type(command) is dict and command.get("v") == 2:
            extension.command(command)
        else:
            if type(command) is dict and command.get("command") not in {
                "viewport",
                "account_preview",
            }:
                extension.invalidate()
            base.command(self, command)

    def update(self: Any, event: dict[str, Any]) -> None:
        task_box[0].observe(event)
        if not extension_box[0].observe(event):
            base._update(self, event)

    def close(self: Any, *args: Any, **kwargs: Any) -> None:
        task_box[0].invalidate()
        extension_box[0].close()
        base.close(self, *args, **kwargs)

    def tick(self: Any, *, allow_maintenance: bool = True) -> None:
        if task_box:
            task_box[0].expire()
        base.tick(self, allow_maintenance=allow_maintenance)

    engine_type = type(
        "ReviewedReplyEngineV1",
        (base,),
        {"command": dispatch, "_update": update, "close": close, "tick": tick},
    )
    engine = engine_type(
        library, library_sha, session_root, emit, native_factory=make_native, clock=clock, wall=wall
    )
    extension = ReplyEngineExtension(engine, legacy, principal, generation, journal_path)
    extension_box.append(extension)
    engine.reply_extension = extension
    task = TelegramTaskExtension(engine, principal, generation)
    task_box.append(task)
    engine.task_extension = task
    return engine
