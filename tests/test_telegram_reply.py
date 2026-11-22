"""Public synthetic policy/TDLib-wire checks; no account, native client or network."""

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from satori.application.assistant.contracts import LocalPrincipal
from satori.application.assistant.telegram_reply import TelegramReplyOwner
from satori.application.assistant.telegram_reply_contracts import (
    ReplyClock,
    ReplyDenied,
    ReplyReceipt,
    SelectedReplyScope,
)
from satori.infrastructure.telegram_reply.journal import SQLiteReplyJournal
from satori.infrastructure.telegram_reply.tdlib import NativeReplyBinding, TDLibReplyAdapter


class Harness:
    def __init__(self, tmp_path: Path, *, capacity: int = 512) -> None:
        tmp_path.chmod(0o700)
        self.path = tmp_path / "reply.sqlite"
        self.wall = datetime(2026, 11, 3, tzinfo=UTC)
        self.monotonic = 100.0
        self.scope: SelectedReplyScope | None = SelectedReplyScope(
            principal=LocalPrincipal(os_uid=501, profile_id=uuid4(), session_id=uuid4()),
            auth_session_id=uuid4(),
            account_epoch=uuid4(),
            generation=1,
            selection_id=uuid4(),
            chat_handle=uuid4(),
            page_id=uuid4(),
            expires_at=self.wall + timedelta(minutes=15),
            monotonic_deadline=1000.0,
        )
        self.handle = uuid4()
        self.journal = SQLiteReplyJournal(self.path, create=True, capacity=capacity)
        self.calls: list[dict[str, object]] = []
        self.sent_state: str = "pending"
        self.message_id = 17
        self.sending_id = 0
        self.before_dispatch: Any = None
        self.mutate_response: Any = None
        self.timeout = False
        self.adapter = TDLibReplyAdapter(self, self.resolve)
        self.owner = self.new_owner()

    @property
    def reviewed_reply_enabled(self) -> bool:
        return True

    def now(self) -> ReplyClock:
        return ReplyClock(wall=self.wall, monotonic=self.monotonic)

    def advance(self, seconds: int) -> None:
        self.wall += timedelta(seconds=seconds)
        self.monotonic += seconds

    def new_owner(self) -> TelegramReplyOwner:
        return TelegramReplyOwner(
            selection=lambda: self.scope, clock=self.now, journal=self.journal, port=self.adapter
        )

    def resolve(self, scope: SelectedReplyScope, handle: Any) -> NativeReplyBinding | None:
        if scope != self.scope or handle != self.handle:
            return None
        return NativeReplyBinding(
            scope=scope,
            reply_to_handle=handle,
            personal_chat_verified=True,
            chat_id=111,
            reply_to_message_id=22,
        )

    def response(self) -> dict[str, object]:
        state = (
            None
            if self.sent_state == "sent"
            else {"@type": "messageSendingStatePending", "sending_id": self.sending_id}
            if self.sent_state == "pending"
            else {"@type": "messageSendingStateFailed", "error": {"code": 400}}
        )
        return {
            "@type": "message",
            "id": self.message_id,
            "chat_id": 111,
            "is_outgoing": True,
            "paid_message_star_count": 0,
            "sending_state": state,
            "scheduling_state": None,
            "content": {
                "@type": "messageText",
                "text": {"@type": "formattedText", "text": "Public reply", "entities": []},
            },
            "reply_to": {"@type": "messageReplyToMessage", "chat_id": 111, "message_id": 22},
        }

    def call(self, query: dict[str, object], *, before_send: Any) -> dict[str, object]:
        if self.before_dispatch is not None:
            self.before_dispatch()
        before_send()
        self.calls.append(query)
        if self.timeout:
            raise TimeoutError("PRIVATE PROVIDER DETAIL MUST NOT ESCAPE")
        if query["@type"] == "sendMessage":
            assert isinstance(query["options"], dict)
            self.sending_id = query["options"]["sending_id"]
        result = self.response()
        if self.mutate_response is not None:
            self.mutate_response(result)
        return result

    def draft(self) -> Any:
        return self.owner.draft(uuid4(), self.handle, "Public reply")


@pytest.fixture
def harness(tmp_path: Path) -> Any:
    value = Harness(tmp_path)
    yield value
    value.journal.close()


def test_reviewed_reply_sends_exact_fixed_plain_text_once(harness: Harness) -> None:
    harness.sent_state = "sent"
    draft = harness.draft()
    approval = harness.owner.approve(draft, confirmed=True)
    result = harness.owner.send(approval)
    assert result.state == "sent"
    assert len(harness.calls) == 1
    query = harness.calls[0]
    assert query["@type"] == "sendMessage"
    assert query["chat_id"] == 111
    assert query["topic_id"] is None
    assert query["reply_markup"] is None
    options = query["options"]
    assert isinstance(options, dict)
    assert options["allow_paid_broadcast"] is False
    assert options["paid_message_star_count"] == 0
    assert options["scheduling_state"] is None
    assert options["from_background"] is False
    assert options["only_preview"] is False
    assert "Public reply" not in repr(draft)
    with pytest.raises(ReplyDenied, match="consumed"):
        harness.owner.send(approval)
    assert len(harness.calls) == 1


def test_read_selection_draft_is_not_send_authority(harness: Harness) -> None:
    draft = harness.draft()
    assert harness.calls == []
    with pytest.raises(ReplyDenied, match="approval_required"):
        harness.owner.approve(draft, confirmed=False)
    assert harness.calls == []


def test_claim_is_durable_before_transport(harness: Harness) -> None:
    draft = harness.draft()

    def check() -> None:
        value = harness.journal.get(draft.request_id)
        assert value is not None
        assert value.state == "unknown"
        assert value.code == "dispatch_claimed"

    harness.before_dispatch = check
    result = harness.owner.send(harness.owner.approve(draft, confirmed=True))
    assert result.state == "unknown"
    assert result.code == "native_pending"


def test_metadata_journal_explains_authority_and_time_without_body(harness: Harness) -> None:
    draft = harness.draft()
    approval = harness.owner.approve(draft, confirmed=True)
    harness.owner.send(approval)
    with sqlite3.connect(harness.path) as connection:
        admission, updated = connection.execute(
            "SELECT admission, updated_at FROM reply_attempts WHERE request_id=?",
            (str(draft.request_id),),
        ).fetchone()
    metadata = json.loads(admission)
    assert metadata["operation"] == "telegram.reply_text"
    assert metadata["authority"] == "explicit_review"
    assert metadata["approval"]["approval_id"] == str(approval.approval_id)
    assert metadata["approval"]["approved_at"] == approval.approved_at.isoformat().replace(
        "+00:00", "Z"
    )
    assert metadata["principal"]["profile_id"] == str(draft.scope.principal.profile_id)
    assert updated == harness.wall.isoformat()
    assert draft.text not in admission


def test_timeout_remains_unknown_and_never_retries(harness: Harness) -> None:
    draft = harness.draft()
    harness.timeout = True
    result = harness.owner.send(harness.owner.approve(draft, confirmed=True))
    assert result.state == "unknown"
    assert result.message_id is None
    assert harness.owner.reconcile(draft) == result
    assert len(harness.calls) == 1
    with pytest.raises(ReplyDenied, match="already_attempted"):
        harness.owner.approve(draft, confirmed=True)


def test_restart_reads_journal_without_replaying(harness: Harness) -> None:
    draft = harness.draft()
    first = harness.owner.send(harness.owner.approve(draft, confirmed=True))
    harness.journal.close()
    harness.journal = SQLiteReplyJournal(harness.path)
    harness.adapter = TDLibReplyAdapter(harness, harness.resolve)
    harness.owner = harness.new_owner()
    restored = harness.owner.draft(draft.request_id, draft.reply_to_handle, draft.text)
    assert harness.owner.receipt(draft.request_id) == first
    with pytest.raises(ReplyDenied, match="already_attempted"):
        harness.owner.approve(restored, confirmed=True)
    harness.sent_state = "sent"
    assert harness.owner.reconcile(restored).state == "sent"
    assert [q["@type"] for q in harness.calls] == ["sendMessage", "getMessage"]


def test_terminal_mapping_needs_explicit_correlated_read(harness: Harness) -> None:
    draft = harness.draft()
    pending = harness.owner.send(harness.owner.approve(draft, confirmed=True))
    harness.message_id = 44
    harness.sent_state = "sent"
    assert harness.adapter.observe_terminal_update(
        draft.scope,
        {
            "@type": "updateMessageSendSucceeded",
            "old_message_id": 17,
            "message": harness.response(),
        },
    )
    assert harness.owner.receipt(draft.request_id) == pending
    assert harness.owner.reconcile(draft).state == "sent"
    assert harness.calls[-1] == {"@type": "getMessage", "chat_id": 111, "message_id": 44}


@pytest.mark.parametrize(
    "field",
    [
        "generation",
        "account_epoch",
        "auth_session_id",
        "page_id",
        "selection_id",
        "chat_handle",
        "principal",
    ],
)
def test_changed_scope_blocks_effect(harness: Harness, field: str) -> None:
    draft = harness.draft()
    approval = harness.owner.approve(draft, confirmed=True)
    assert harness.scope is not None
    value = (
        2
        if field == "generation"
        else LocalPrincipal(os_uid=501, profile_id=uuid4(), session_id=uuid4())
        if field == "principal"
        else uuid4()
    )
    harness.scope = harness.scope.model_copy(update={field: value})
    with pytest.raises(ReplyDenied, match="scope_revoked"):
        harness.owner.send(approval)
    assert harness.calls == []


def test_revoke_while_queued_blocks_actual_native_send(harness: Harness) -> None:
    draft = harness.draft()
    harness.before_dispatch = harness.owner.revoke
    result = harness.owner.send(harness.owner.approve(draft, confirmed=True))
    assert result.state == "unknown"
    assert harness.calls == []


@pytest.mark.parametrize("seconds", [60, 300, 901])
def test_expiry_never_renews_or_dispatches(harness: Harness, seconds: int) -> None:
    draft = harness.draft()
    approval = harness.owner.approve(draft, confirmed=True)
    harness.advance(seconds)
    with pytest.raises(ReplyDenied):
        harness.owner.send(approval)
    assert harness.calls == []


def test_clock_rollback_revokes_owner(harness: Harness) -> None:
    draft = harness.draft()
    harness.monotonic -= 1
    with pytest.raises(ReplyDenied, match="clock_rollback"):
        harness.owner.approve(draft, confirmed=True)
    assert harness.calls == []


def test_edit_or_forged_approval_cannot_change_recipient_or_text(harness: Harness) -> None:
    draft = harness.draft()
    approval = harness.owner.approve(draft, confirmed=True)
    with pytest.raises(ReplyDenied, match="foreign_draft"):
        harness.owner.approve(draft.model_copy(update={"text": "Changed"}), confirmed=True)
    with pytest.raises(ReplyDenied, match="foreign_or_consumed"):
        harness.owner.send(approval.model_copy(update={"approval_id": uuid4()}))
    with pytest.raises(ReplyDenied, match="payload_conflict"):
        harness.owner.draft(draft.request_id, draft.reply_to_handle, "Changed")
    assert harness.calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("chat_id", 222),
        ("is_outgoing", False),
        ("paid_message_star_count", 1),
        ("sending_state", {}),
        ("content", {}),
        ("reply_to", {}),
    ],
)
def test_malformed_or_foreign_native_result_keeps_unknown(
    harness: Harness, field: str, value: object
) -> None:
    harness.mutate_response = lambda result: result.update({field: value})
    draft = harness.draft()
    result = harness.owner.send(harness.owner.approve(draft, confirmed=True))
    assert result.state == "unknown"
    assert result.code == "dispatch_claimed"
    assert len(harness.calls) == 1


def test_native_failure_is_not_resent(harness: Harness) -> None:
    harness.sent_state = "failed"
    draft = harness.draft()
    failed = harness.owner.send(harness.owner.approve(draft, confirmed=True))
    assert failed.state == "failed"
    assert harness.owner.reconcile(draft) == failed
    assert len(harness.calls) == 1


def test_journal_capacity_fails_before_effect(tmp_path: Path) -> None:
    harness = Harness(tmp_path, capacity=1)
    try:
        first = harness.draft()
        harness.owner.send(harness.owner.approve(first, confirmed=True))
        second = harness.draft()
        with pytest.raises(ReplyDenied, match="capacity"):
            harness.owner.send(harness.owner.approve(second, confirmed=True))
        assert len(harness.calls) == 1
        assert b"Public reply" not in harness.path.read_bytes()
    finally:
        harness.journal.close()


def test_readonly_helper_is_not_a_reply_transport(harness: Harness) -> None:
    class ReadOnly:
        reviewed_reply_enabled = False

    with pytest.raises(ReplyDenied, match="transport_unavailable"):
        TDLibReplyAdapter(ReadOnly(), harness.resolve)  # type: ignore[arg-type]


def test_exact_json_contains_no_secret_or_arbitrary_native_function(harness: Harness) -> None:
    draft = harness.draft()
    harness.owner.send(harness.owner.approve(draft, confirmed=True))
    encoded = json.dumps(harness.calls[0])
    assert "api_hash" not in encoded
    assert "parse_mode" not in encoded
    assert "sendMessage" in encoded
    assert "message_thread_id" not in encoded


def test_reapproval_invalidates_previous_text_review(harness: Harness) -> None:
    draft = harness.draft()
    old = harness.owner.approve(draft, confirmed=True)
    current = harness.owner.approve(draft, confirmed=True)
    with pytest.raises(ReplyDenied, match="foreign_or_consumed"):
        harness.owner.send(old)
    harness.owner.send(current)
    assert len(harness.calls) == 1


def test_unknown_native_target_cannot_receive_approval(harness: Harness) -> None:
    draft = harness.owner.draft(uuid4(), uuid4(), "Public reply")
    with pytest.raises(ReplyDenied, match="binding_unavailable"):
        harness.owner.approve(draft, confirmed=True)
    assert harness.owner.receipt(draft.request_id) is None
    assert harness.calls == []


def test_scope_loss_after_dispatch_does_not_publish_sent(harness: Harness) -> None:
    harness.sent_state = "sent"

    def changed(result: dict[str, object]) -> None:
        harness.scope = None

    harness.mutate_response = changed
    draft = harness.draft()
    result = harness.owner.send(harness.owner.approve(draft, confirmed=True))
    assert result.state == "unknown"
    assert len(harness.calls) == 1


def test_readonly_reconcile_respects_revoke(harness: Harness) -> None:
    draft = harness.draft()
    harness.owner.send(harness.owner.approve(draft, confirmed=True))
    harness.scope = None
    with pytest.raises(ReplyDenied, match="scope_revoked"):
        harness.owner.reconcile(draft)
    assert len(harness.calls) == 1


def test_reconcile_not_found_is_not_failure_or_resend(harness: Harness) -> None:
    draft = harness.draft()
    pending = harness.owner.send(harness.owner.approve(draft, confirmed=True))

    def missing(result: dict[str, object]) -> None:
        result.clear()
        result.update({"@type": "error", "code": 404, "message": "Private omitted"})

    harness.mutate_response = missing
    assert harness.owner.reconcile(draft) == pending
    assert [call["@type"] for call in harness.calls] == ["sendMessage", "getMessage"]


@pytest.mark.parametrize("old_id", [99, 0, True])
def test_foreign_or_invalid_terminal_mapping_is_not_success(
    harness: Harness, old_id: object
) -> None:
    draft = harness.draft()
    pending = harness.owner.send(harness.owner.approve(draft, confirmed=True))
    event = {
        "@type": "updateMessageSendSucceeded",
        "old_message_id": old_id,
        "message": harness.response(),
    }
    if old_id == 99:
        assert harness.adapter.observe_terminal_update(draft.scope, event) is False
    else:
        with pytest.raises(ReplyDenied, match="native_response_invalid"):
            harness.adapter.observe_terminal_update(draft.scope, event)
    assert harness.owner.receipt(draft.request_id) == pending


def test_independent_connection_cannot_claim_same_attempt_twice(harness: Harness) -> None:
    draft = harness.draft()
    approval = harness.owner.approve(draft, confirmed=True)
    harness.journal.claim(draft, approval, 123, harness.wall)
    other = SQLiteReplyJournal(harness.path)
    try:
        with pytest.raises(ReplyDenied, match="already_attempted"):
            other.claim(draft, approval, 456, harness.wall)
    finally:
        other.close()
    assert harness.calls == []


@pytest.mark.parametrize("mode", [0o644, 0o666])
def test_unsafe_journal_file_permissions_reject(harness: Harness, mode: int) -> None:
    harness.path.chmod(mode)
    try:
        with pytest.raises(ReplyDenied, match="journal_file"):
            SQLiteReplyJournal(harness.path)
    finally:
        harness.path.chmod(0o600)


def test_hardlinked_journal_rejects(harness: Harness) -> None:
    linked = harness.path.with_name("second.sqlite")
    os.link(harness.path, linked)
    try:
        with pytest.raises(ReplyDenied, match="journal_file"):
            SQLiteReplyJournal(harness.path)
    finally:
        linked.unlink()


def test_native_bool_star_count_is_not_integer_zero(harness: Harness) -> None:
    harness.mutate_response = lambda result: result.update({"paid_message_star_count": False})
    draft = harness.draft()
    assert harness.owner.send(harness.owner.approve(draft, confirmed=True)).state == "unknown"


@pytest.mark.parametrize(
    ("state", "code", "message_id"),
    [
        ("sent", "native_sent", None),
        ("sent", "unknown", 123),
        ("unknown", "native_sent", 123),
        ("failed", "unknown", None),
        ("unknown", "native_pending", None),
    ],
)
def test_receipts_cannot_claim_incoherent_outcomes(
    state: str, code: str, message_id: int | None
) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ReplyReceipt.model_validate(
            {
                "request_id": uuid4(),
                "payload_digest": "a" * 64,
                "state": state,
                "sending_id": 1,
                "message_id": message_id,
                "code": code,
            }
        )
