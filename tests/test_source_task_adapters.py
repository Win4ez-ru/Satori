"""Query-derived bodies and the real TDLib send override using public synthetic fixtures."""

import base64
import json
import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Any, ClassVar, Literal
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from pydantic import SecretStr

from satori.application.assistant.contracts import LocalPrincipal
from satori.application.assistant.mail import MailAccessCoordinator
from satori.application.assistant.mail_contracts import MailAccountRef, MailClock
from satori.application.assistant.source_answer_contracts import (
    SourceSearchPlan,
    SourceTaskRequest,
    SourceTaskScope,
    SourceTaskSnapshot,
)
from satori.application.assistant.source_task_reads import plan_mail_chat_read
from satori.desktop.telegram_reply_native import build_reply_engine, legacy_definitions
from satori.desktop.telegram_task_protocol import TASK_PROTOCOL_SHA256, verified_task_protocol
from satori.infrastructure.gmail.connector import GmailConnector, _Session
from satori.infrastructure.gmail.contracts import GmailAccount, GmailError
from satori.infrastructure.gmail.http import GoogleHTTP

NOW = datetime(2026, 11, 6, 12, tzinfo=UTC)
RESOURCES = Path(__file__).resolve().parents[1] / "src/satori/resources"


def task(
    principal: LocalPrincipal, now: datetime = NOW
) -> tuple[SourceTaskRequest, SourceSearchPlan]:
    request = SourceTaskRequest(
        request_id=uuid4(),
        scope=SourceTaskScope(
            identity_id=uuid4(),
            profile_id=principal.profile_id,
            session_id=uuid4(),
            generation=uuid4(),
            timezone="Europe/Moscow",
        ),
        user_text="Когда было последнее собеседование?",
        sources=("mail", "telegram"),
        created_at=now,
        expires_at=now + timedelta(seconds=120),
    )
    plan = SourceSearchPlan(
        status="search",
        mail_query="собеседование",
        telegram_query="собеседование",
        window_start=now - timedelta(days=30),
        window_end=now,
    )
    return request, plan


class MailWire(GoogleHTTP):
    def __init__(self, *, html: bool = False) -> None:
        self.ids = tuple(f"m{index}" for index in range(5))
        self.calls: list[str] = []
        self.html = html
        self.before: Any = None
        self.more = True

    def request(
        self,
        method: Literal["GET", "POST"],
        url: str,
        *,
        token: SecretStr | None = None,
        form: dict[str, str] | None = None,
        max_bytes: int = 524288,
        cancel: Event,
        timeout: float = 10,
    ) -> bytes:
        assert method == "GET"
        assert token is not None
        assert form is None
        assert max_bytes <= 524288
        assert not cancel.is_set()
        assert 0 < timeout <= 10
        self.calls.append(url)
        if self.before is not None:
            self.before(url)
        path, query = urlsplit(url).path, parse_qs(urlsplit(url).query)
        if path.endswith("/messages"):
            assert "after:" in query["q"][0]
            assert "before:" in query["q"][0]
            assert "labelIds" not in query
            return json.dumps(
                {"messages": [{"id": key} for key in self.ids]}
                | ({"nextPageToken": "PUBLIC-NEXT"} if self.more else {})
            ).encode()
        key = path.rsplit("/", 1)[1]
        date = str(int((NOW - timedelta(days=int(key[1:]) + 1)).timestamp() * 1000))
        payload: dict[str, Any]
        if query["format"] == ["metadata"]:
            payload = {
                "headers": [
                    {"name": "Subject", "value": "PUBLIC interview invitation"},
                    {"name": "From", "value": "PUBLIC recruiter"},
                ]
            }
        else:
            assert query["format"] == ["full"]
            text = (
                "<p>PUBLIC interview: 5 November, 16:00.</p>"
                if self.html
                else "PUBLIC interview: 5 November, 16:00."
            )
            mime = "text/html" if self.html else "text/plain"
            payload = {
                "partId": "0",
                "mimeType": mime,
                "headers": [{"name": "Subject", "value": "PUBLIC interview invitation"}],
                "body": {
                    "size": len(text.encode()),
                    "data": base64.urlsafe_b64encode(text.encode()).decode().rstrip("="),
                },
            }
        return json.dumps({"id": key, "internalDate": date, "payload": payload}).encode()


def mail_connector(html: bool = False) -> tuple[GmailConnector, MailWire, LocalPrincipal]:
    principal = LocalPrincipal(os_uid=os.getuid(), profile_id=uuid4(), session_id=uuid4())
    wire = MailWire(html=html)
    connector = GmailConnector(
        principal, http=wire, clock=lambda: MailClock(wall=NOW, monotonic=1000.0)
    )
    account = GmailAccount(
        account=MailAccountRef(subject="PUBLIC-ACCOUNT", client_registration="PUBLIC-CLIENT"),
        email_label="public@example.invalid",
    )
    connector._session = _Session(
        account, SecretStr("PUBLIC-TOKEN"), None, NOW + timedelta(hours=1), 4600, 0
    )
    return connector, wire, principal


@pytest.mark.parametrize("html", [False, True])
def test_task_mail_reads_three_exact_query_hits_without_human_selection(
    monkeypatch: pytest.MonkeyPatch, html: bool
) -> None:
    connector, wire, principal = mail_connector(html)
    request, plan = task(principal)
    monkeypatch.setattr(
        MailAccessCoordinator, "select", lambda *_a, **_k: pytest.fail("fake selection")
    )
    result = connector.task_search(request, plan, cancel=Event())
    assert result.request_id == request.request_id
    assert result.scope == request.scope
    assert result.plan_digest == plan.digest
    assert len(result.items) == 5
    assert len([url for url in wire.calls if "format=full" in url]) == 3
    assert [item.event_at for item in result.items] == [
        NOW - timedelta(days=i + 1) for i in range(5)
    ]
    assert all(item.text.startswith("PUBLIC interview") for item in result.items[:3])
    assert all(item.completeness == "metadata_only" for item in result.items[3:])
    assert result.coverage[0].status == "partial"
    assert "body_reads=3" in result.coverage[0].detail
    assert "PUBLIC-TOKEN" not in result.model_dump_json()
    assert all(item.source_ref.startswith("mail:") for item in result.items)
    assert connector._task_read_grant is None
    if html:
        assert all(item.completeness == "partial" for item in result.items[:3])
        assert "html_lossy" in result.coverage[0].detail


def test_task_mail_cancellation_after_first_body_prevents_further_related_reads() -> None:
    connector, wire, principal = mail_connector()
    request, plan = task(principal)
    cancel = Event()
    wire.before = lambda url: cancel.set() if "format=full" in url else None
    with pytest.raises(GmailError, match="cancelled"):
        connector.task_search(request, plan, cancel=cancel)
    assert len([url for url in wire.calls if "format=full" in url]) == 1
    assert connector._task_read_grant is None


def test_task_mail_other_profile_and_oversized_derived_query_do_not_access_provider() -> None:
    connector, wire, principal = mail_connector()
    request, plan = task(principal)
    wrong = request.model_copy(
        update={"scope": request.scope.model_copy(update={"profile_id": uuid4()})}
    )
    with pytest.raises(GmailError, match="account_mismatch"):
        connector.task_search(wrong, plan, cancel=Event())
    assert wire.calls == []
    long_plan = plan.model_copy(update={"mail_query": "я" * 512})
    with pytest.raises(ValueError, match="query"):
        connector.task_search(request, long_plan, cancel=Event())
    assert wire.calls == []


def test_direct_mail_chat_search_reads_metadata_only_and_reports_actual_coverage() -> None:
    connector, wire, principal = mail_connector()
    wire.more = False
    original, _ = task(principal)
    request = SourceTaskRequest.model_validate(
        original.model_dump() | {"sources": ("mail",), "user_text": "Найди письмо от Acme"}
    )
    plan = plan_mail_chat_read(request)
    assert plan is not None
    assert plan.mail_headers_only
    assert plan.mail_query == 'from:("Acme")'
    result = connector.task_search(request, plan, cancel=Event())
    assert len(result.items) == 5
    assert len([url for url in wire.calls if "format=metadata" in url]) == 5
    assert not any("format=full" in url for url in wire.calls)
    assert all(not item.text and item.completeness == "metadata_only" for item in result.items)
    assert result.coverage[0].status == "searched"
    assert "body_reads=0" in result.coverage[0].detail
    assert connector._task_read_grant is None
    wire.calls.clear()
    forged = plan.model_copy(update={"mail_query": "in:inbox"})
    with pytest.raises(ValueError, match="source_task_scope_invalid"):
        connector.task_search(request, forged, cancel=Event())
    assert wire.calls == []


class TaskNative:
    """Production native-send override calls this fake C boundary; no library loaded."""

    rows: ClassVar[list[dict[str, Any]]] = []
    protected = False
    before_response: Any = None
    interleave_before: str | None = None
    interleave_event: dict[str, Any] | None = None

    def __init__(self, *_args: Any) -> None:
        self.client_id = 1
        self.lib = self
        self.calls: list[dict[str, Any]] = []
        self.queue: list[dict[str, Any]] = []
        self.peer_titles: dict[int, str] = {}
        self.peer_handles: dict[int, tuple[str, ...]] = {}
        self.peer_protected: set[int] = set()
        self.peer_ids: tuple[int, ...] = ()
        self.peer_total: int | None = None
        self.peer_rows: list[dict[str, Any]] = []

    def td_send(self, _client: int, raw: bytes) -> None:
        self._answer(json.loads(raw))

    def send(self, value: dict[str, Any]) -> None:
        self._answer(value)

    def _answer(self, value: dict[str, Any]) -> None:
        self.calls.append(deepcopy(value))
        kind = value["@type"]
        if kind == "searchMessages":
            assert value["limit"] == 5
            assert value["chat_list"] is None
            assert value["chat_type_filter"] == {"@type": "searchMessagesChatTypeFilterPrivate"}
            answer = {
                "@type": "foundMessages",
                "messages": deepcopy(self.rows),
                "next_offset": "",
                "total_count": len(self.rows),
            }
        elif kind == "searchChatsOnServer":
            assert value["type_filter"] is None
            assert value["limit"] == 8
            answer = {
                "@type": "chats",
                "chat_ids": list(self.peer_ids),
                "total_count": self.peer_total
                if self.peer_total is not None
                else len(self.peer_ids),
            }
        elif kind == "getChat":
            chat_id = value["chat_id"]
            answer = {
                "@type": "chat",
                "id": chat_id,
                "title": self.peer_titles.get(chat_id, "PUBLIC recruiter"),
                "type": {"@type": "chatTypePrivate", "user_id": chat_id},
                "has_protected_content": self.protected or chat_id in self.peer_protected,
                "message_auto_delete_time": 0,
            }
        elif kind == "getUser":
            user_id = value["user_id"]
            answer = {
                "@type": "user",
                "id": user_id,
                "type": {"@type": "userTypeRegular"},
                "usernames": {"active_usernames": list(self.peer_handles.get(user_id, ()))},
            }
        elif kind == "getChatHistory" and value["from_message_id"] == 0:
            assert value["offset"] == 0
            assert value["limit"] == 12
            assert value["only_local"] is False
            answer = {
                "@type": "messages",
                "messages": deepcopy(self.peer_rows),
                "total_count": len(self.peer_rows),
            }
        elif kind == "getChatHistory":
            answer = {
                "@type": "messages",
                "messages": [
                    deepcopy(row) for row in self.rows if row["id"] == value["from_message_id"]
                ],
                "total_count": 1,
            }
        elif kind in {"closeChat", "setOption"}:
            answer = {"@type": "ok"}
        else:
            raise AssertionError("unexpected source-task RPC")
        if kind == self.interleave_before and self.interleave_event is not None:
            self.queue.append(deepcopy(self.interleave_event))
            self.interleave_before = None
        self.queue.append({**answer, "@extra": value["@extra"]})

    def receive(self, _timeout: float) -> dict[str, Any] | None:
        if self.before_response is not None:
            callback, self.before_response = self.before_response, None
            callback()
        return self.queue.pop(0) if self.queue else None


def message(*, age: int = 7, reply: bool = False, forward: bool = False) -> dict[str, Any]:
    return {
        "@type": "message",
        "id": 101,
        "chat_id": 42,
        "date": int((NOW - timedelta(days=age)).timestamp()),
        "can_be_saved": True,
        "auto_delete_in": 0,
        "self_destruct_type": None,
        "sender_id": {"@type": "messageSenderUser", "user_id": 42},
        "content": {
            "@type": "messageText",
            "text": {"text": "PUBLIC interview was on 29 October", "entities": []},
        },
        "reply_to": {"@type": "messageReplyToMessage", "chat_id": 42, "message_id": 100}
        if reply
        else None,
        "forward_info": {"PUBLIC": True} if forward else None,
    }


def task_engine(tmp_path: Path) -> tuple[Any, Any, LocalPrincipal, list[dict[str, Any]]]:
    legacy = legacy_definitions(RESOURCES / "telegram/backend/worker.py.source")
    legacy["NativeClient"] = TaskNative
    principal = LocalPrincipal(os_uid=os.getuid(), profile_id=uuid4(), session_id=uuid4())
    events: list[dict[str, Any]] = []
    engine = build_reply_engine(
        legacy,
        Path("/PUBLIC/unloaded.dylib"),
        "0" * 64,
        tmp_path / "PUBLIC-session",
        events.append,
        principal=principal,
        generation=1,
        journal_path=tmp_path / "PUBLIC-journal.sqlite3",
        clock=lambda: 1000.0,
        wall=lambda: NOW.timestamp(),
    )
    engine.native = engine.native_factory(Path("/PUBLIC/unloaded.dylib"), "0" * 64)
    engine.account_confirmed = True
    engine.auth_state = "authorizationStateReady"
    engine.account_id = 1
    engine.session_until = 1900
    return engine, engine.native, principal, events


def task_command(
    engine: Any, request: SourceTaskRequest, plan: SourceSearchPlan, operation: str = "task_search"
) -> None:
    engine.command(
        {
            "v": 3,
            "request_id": uuid4().hex,
            "command": operation,
            "request": request.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
        }
    )


@pytest.mark.parametrize("reply", [False, True])
def test_telegram_task_uses_same_client_and_can_read_week_old_direct_or_reply_text(
    tmp_path: Path, reply: bool
) -> None:
    TaskNative.rows, TaskNative.protected = [message(reply=reply)], False
    engine, native, principal, events = task_engine(tmp_path)
    request, plan = task(principal)
    task_command(engine, request, plan)
    result = SourceTaskSnapshot.model_validate_json(json.dumps(events[-1]["snapshot"]))
    assert len(result.items) == 1
    assert result.items[0].event_at == NOW - timedelta(days=7)
    assert result.items[0].text == "PUBLIC interview was on 29 October"
    assert result.coverage[0].status == "searched"
    assert engine.native is native
    assert {call["@type"] for call in native.calls} <= {
        "searchMessages",
        "getChat",
        "getUser",
        "getChatHistory",
    }
    assert engine.selected_chat is None
    assert engine.page_id is None
    assert "chat_id" not in result.model_dump_json()
    assert "message_id" not in result.model_dump_json()


@pytest.mark.parametrize("kind", ["protected", "forward", "older"])
def test_telegram_task_omits_private_ineligible_content_without_related_read(
    tmp_path: Path, kind: str
) -> None:
    TaskNative.rows = [message(age=31 if kind == "older" else 7, forward=kind == "forward")]
    TaskNative.protected = kind == "protected"
    engine, native, principal, events = task_engine(tmp_path)
    request, plan = task(principal)
    task_command(engine, request, plan)
    snapshot = SourceTaskSnapshot.model_validate_json(json.dumps(events[-1]["snapshot"]))
    assert snapshot.items == ()
    assert snapshot.coverage[0].status == "partial"
    assert all(call["@type"] != "getChatHistory" for call in native.calls)


def test_telegram_task_new_arrival_preserves_snapshot_but_referenced_edit_revokes(
    tmp_path: Path,
) -> None:
    TaskNative.rows, TaskNative.protected = [message()], False
    engine, _, principal, events = task_engine(tmp_path)
    request, plan = task(principal)
    task_command(engine, request, plan)
    count = len(events)
    engine._update(
        {"@type": "updateNewMessage", "message": {"@type": "message", "chat_id": 42, "id": 102}}
    )
    assert len(events) == count
    engine._update({"@type": "updateMessageEdited", "chat_id": 42, "message_id": 101})
    assert events[-1]["type"] == "task_invalidated"
    assert events[-1]["task_id"] == str(request.request_id)
    assert engine.task_extension.references == set()
    assert engine.account_confirmed
    assert not engine.stopped


def test_telegram_search_native_function_requires_task_guard_immediately_before_send(
    tmp_path: Path,
) -> None:
    _engine, native, _, _ = task_engine(tmp_path)
    with pytest.raises(ValueError, match="reply_v1_rejected"):
        native.send({"@type": "searchMessages", "query": "PUBLIC"})
    assert native.calls == []


def peer_plan(plan: SourceSearchPlan, query: str = "Алексей") -> SourceSearchPlan:
    return SourceSearchPlan.model_validate(
        {
            **plan.model_dump(),
            "telegram_query": query,
            "telegram_match": "peer",
            "window_start": NOW - timedelta(hours=2),
        }
    )


def peer_message(
    identifier: int, at: datetime, *, outgoing: bool = False, text: str = "Можно созвониться?"
) -> dict[str, Any]:
    row = message()
    row.update(
        id=identifier,
        date=int(at.timestamp()),
        is_outgoing=outgoing,
        sender_id={"@type": "messageSenderUser", "user_id": 1 if outgoing else 42},
        content={"@type": "messageText", "text": {"text": text, "entities": []}},
    )
    return row


@pytest.mark.parametrize("query", ["Алексей", "Алексей Вымышленный", "@public_alexey"])
def test_telegram_peer_task_reads_only_incoming_in_exact_window(tmp_path: Path, query: str) -> None:
    TaskNative.protected = False
    engine, native, principal, events = task_engine(tmp_path)
    request, plan = task(principal)
    plan = peer_plan(plan, query)
    native.peer_ids = (42, -7, 1)
    native.peer_titles = {42: "Алексей Вымышленный"}
    native.peer_handles = {42: ("public_alexey",)}
    native.peer_rows = [
        peer_message(104, plan.window_end, text="Outside upper boundary"),
        peer_message(103, NOW - timedelta(minutes=1), outgoing=True, text="My own reply"),
        peer_message(102, plan.window_start),
        peer_message(101, plan.window_start - timedelta(seconds=1), text="Previous day"),
    ]

    task_command(engine, request, plan)

    result = SourceTaskSnapshot.model_validate_json(json.dumps(events[-1]["snapshot"]))
    assert [item.text for item in result.items] == ["Можно созвониться?"]
    assert result.items[0].event_at == plan.window_start
    assert result.coverage[0].status == "searched"
    assert result.coverage[0].detail == "peer_incoming"
    assert [call["@type"] for call in native.calls] == [
        "searchChatsOnServer",
        "getChat",
        "getUser",
        "getChatHistory",
    ]
    assert native.calls[0]["query"] == query.removeprefix("@")
    assert engine.native is native
    assert engine.selected_chat is None
    assert engine.page_id is None


@pytest.mark.parametrize(
    ("titles", "total", "protected", "detail"),
    [
        ((), None, False, "peer_not_found"),
        (("Алексей", "Алексей Вымышленный"), None, False, "peer_ambiguous"),
        (("Алексей Вымышленный",), 9, False, "peer_lookup_incomplete"),
        (("Алексей",) * 8, None, False, "peer_lookup_incomplete"),
        (("Алексей Вымышленный",), None, True, "peer_unresolved"),
    ],
)
def test_telegram_peer_task_does_not_read_uncertain_candidate(
    tmp_path: Path,
    titles: tuple[str, ...],
    total: int | None,
    protected: bool,
    detail: str,
) -> None:
    TaskNative.protected = False
    engine, native, principal, events = task_engine(tmp_path)
    request, plan = task(principal)
    plan = peer_plan(plan)
    native.peer_titles = {i: title for i, title in enumerate(titles, start=42)}
    native.peer_ids = tuple(native.peer_titles)
    native.peer_protected = {42} if protected else set()
    native.peer_total = total

    task_command(engine, request, plan)

    result = SourceTaskSnapshot.model_validate_json(json.dumps(events[-1]["snapshot"]))
    assert result.items == ()
    assert result.coverage[0].status == "partial"
    assert result.coverage[0].detail == detail
    assert all(call["@type"] != "getChatHistory" for call in native.calls)


@pytest.mark.parametrize("reached_window_start", [False, True])
def test_telegram_peer_task_requires_boundary_before_reporting_no_incoming(
    tmp_path: Path, reached_window_start: bool
) -> None:
    TaskNative.protected = False
    engine, native, principal, events = task_engine(tmp_path)
    request, plan = task(principal)
    plan = peer_plan(plan)
    native.peer_ids = (42,)
    native.peer_titles = {42: "Алексей"}
    native.peer_rows = [peer_message(101, NOW - timedelta(minutes=1), outgoing=True)]
    if reached_window_start:
        native.peer_rows.append(peer_message(100, plan.window_start - timedelta(seconds=1)))
    task_command(engine, request, plan)

    result = SourceTaskSnapshot.model_validate_json(json.dumps(events[-1]["snapshot"]))
    assert result.items == ()
    assert result.coverage[0].status == ("searched" if reached_window_start else "partial")
    assert result.coverage[0].more is not reached_window_start
    engine._update({"@type": "updateChatTitle", "chat_id": 42, "title": "Другое имя"})
    assert events[-1]["type"] == "task_invalidated"


def test_telegram_peer_resolution_requires_the_task_native_guard(tmp_path: Path) -> None:
    _engine, native, _, _ = task_engine(tmp_path)
    with pytest.raises(ValueError, match="reply_v1_rejected"):
        native.send({"@type": "searchChatsOnServer", "query": "Алексей"})
    assert native.calls == []


@pytest.mark.parametrize(
    "event",
    [
        {"@type": "updateChatTitle", "chat_id": 42, "title": "Другое имя"},
        {"@type": "updateChatHasProtectedContent", "chat_id": 42, "has_protected_content": True},
    ],
)
def test_telegram_peer_task_drops_history_after_peer_changes_during_read(
    tmp_path: Path, event: dict[str, Any]
) -> None:
    TaskNative.protected = False
    engine, native, principal, events = task_engine(tmp_path)
    request, plan = task(principal)
    plan = peer_plan(plan)
    native.peer_ids = (42,)
    native.peer_titles = {42: "Алексей Вымышленный"}
    native.peer_rows = [peer_message(101, NOW - timedelta(minutes=1))]
    native.interleave_before = "getChatHistory"
    native.interleave_event = event

    task_command(engine, request, plan)

    result = SourceTaskSnapshot.model_validate_json(json.dumps(events[-1]["snapshot"]))
    assert result.items == ()
    assert result.coverage[0].status == "error"
    assert any(item["type"] == "task_invalidated" for item in events)
    assert engine.task_extension.references == set()


def test_task_protocol_is_fixed_and_legacy_resource_is_not_modified() -> None:
    import hashlib

    verified_task_protocol(RESOURCES)
    assert (
        hashlib.sha256((RESOURCES / "telegram/task_v1/protocol.json").read_bytes()).hexdigest()
        == TASK_PROTOCOL_SHA256
    )
    assert (
        hashlib.sha256((RESOURCES / "telegram/backend/worker.py.source").read_bytes()).hexdigest()
        == "69235f84d83bc49a01fba8a4e342775027d2a1a433fbef5bb25e88c127bef910"
    )


@pytest.mark.parametrize("before", ["searchMessages", "getChat", "getUser"])
@pytest.mark.parametrize(
    "event",
    [
        {"@type": "updateMessageEdited", "chat_id": 42, "message_id": 101},
        {
            "@type": "updateDeleteMessages",
            "chat_id": 42,
            "message_ids": [101],
            "is_permanent": True,
            "from_cache": False,
        },
        {"@type": "updateChatHasProtectedContent", "chat_id": 42, "has_protected_content": True},
    ],
)
def test_telegram_task_rejects_update_before_reference_or_during_nested_rpc(
    tmp_path: Path,
    before: str,
    event: dict[str, Any],
) -> None:
    TaskNative.rows, TaskNative.protected = [message()], False
    engine, native, principal, events = task_engine(tmp_path)
    native.interleave_before, native.interleave_event = before, event
    request, plan = task(principal)
    task_command(engine, request, plan)
    results = [item for item in events if item["type"] == "task_result"]
    snapshot = SourceTaskSnapshot.model_validate_json(json.dumps(results[-1]["snapshot"]))
    assert snapshot.items == ()
    assert snapshot.coverage[0].status == "error"
    assert any(item["type"] == "task_invalidated" for item in events)
    assert all(call["@type"] != "getChatHistory" for call in native.calls)
    assert engine.account_confirmed
    assert not engine.stopped


@pytest.mark.parametrize("field", ["self_destruct_type", "auto_delete_in", "is_secret"])
def test_telegram_task_omits_ttl_and_secret_messages(tmp_path: Path, field: str) -> None:
    row = message()
    row[field] = (
        {"@type": "messageSelfDestructTypeTimer", "self_destruct_time": 10}
        if field == "self_destruct_type"
        else True
        if field == "is_secret"
        else 10
    )
    TaskNative.rows, TaskNative.protected = [row], False
    engine, native, principal, events = task_engine(tmp_path)
    request, plan = task(principal)
    task_command(engine, request, plan)
    snapshot = SourceTaskSnapshot.model_validate_json(json.dumps(events[-1]["snapshot"]))
    assert snapshot.items == ()
    assert snapshot.coverage[0].status == "partial"
    assert not any(call["@type"] == "getChatHistory" for call in native.calls)


def test_telegram_task_sanitizes_plain_text_format_controls_with_partial_marker(
    tmp_path: Path,
) -> None:
    row = message()
    row["content"]["text"]["text"] = "PUBLIC\u202e spoof\u200b\nline\ttext"
    TaskNative.rows, TaskNative.protected = [row], False
    engine, _, principal, events = task_engine(tmp_path)
    request, plan = task(principal)
    task_command(engine, request, plan)
    snapshot = SourceTaskSnapshot.model_validate_json(json.dumps(events[-1]["snapshot"]))
    assert snapshot.items[0].text == "PUBLIC spoof\nline\ttext"
    assert snapshot.items[0].completeness == "partial"
    assert snapshot.coverage[0].status == "partial"


def test_task_mail_maximum_planned_query_preserves_exact_query_and_window() -> None:
    connector, wire, principal = mail_connector()
    request, plan = task(principal)
    query = "я" * 440
    plan = SourceSearchPlan.model_validate(plan.model_dump() | {"mail_query": query})
    connector.task_search(request, plan, cancel=Event())
    issued = parse_qs(urlsplit(wire.calls[0]).query)["q"][0]
    assert issued.startswith("(" + query + ") after:")
    assert len(issued) <= 512


def test_source_projection_caps_do_not_mask_bad_scope() -> None:
    from satori.application.assistant.source_answer_contracts import SourceObservation
    from satori.application.assistant.source_task_reads import task_snapshot

    principal = LocalPrincipal(os_uid=os.getuid(), profile_id=uuid4(), session_id=uuid4())
    request, plan = task(principal)
    observations = tuple(
        SourceObservation(
            source="mail",
            source_ref=f"PUBLIC:{index}",
            observed_at=NOW,
            event_at=NOW - timedelta(days=1),
            text="я" * 4000,
            title="я" * 500,
            sender="я" * 500,
            completeness="complete",
        )
        for index in range(20)
    )
    snapshot = task_snapshot(request, plan, "mail", NOW, observations, status="searched")
    assert sum(len(item.text.encode()) for item in snapshot.items) <= 32768
    assert len(snapshot.model_dump_json().encode()) <= 48 * 1024
    assert snapshot.coverage[0].status == "partial"
    assert snapshot.coverage[0].more
    assert any(item.completeness == "partial" for item in snapshot.items)
    with pytest.raises(ValueError, match="source_task_projection_scope_invalid"):
        task_snapshot(request, plan, "telegram", NOW, observations[:1], status="searched")
