"""Reviewed one-message reply owner; no Core, model, account or receive loop construction."""

from collections.abc import Callable
from datetime import timedelta
from uuid import UUID, uuid4

from satori.application.assistant.telegram_reply_contracts import (
    ReplyApproval,
    ReplyClock,
    ReplyDenied,
    ReplyDraft,
    ReplyJournal,
    ReplyPort,
    ReplyReceipt,
    SelectedReplyScope,
)


class TelegramReplyOwner:
    """Use on the existing serialized host lane, with a trusted current-selection reader.

    ``approve`` is a trusted foreground approval boundary, not a DTO/network handler.
    An existing read selection alone never calls it and never authorizes sending.
    """

    def __init__(
        self,
        *,
        selection: Callable[[], SelectedReplyScope | None],
        clock: Callable[[], ReplyClock],
        journal: ReplyJournal,
        port: ReplyPort,
    ) -> None:
        self._selection = selection
        self._clock = clock
        self._journal = journal
        self._port = port
        self._last_clock: ReplyClock | None = None
        self._drafts: dict[UUID, ReplyDraft] = {}
        self._approvals: dict[UUID, ReplyApproval] = {}
        self._revoked = False

    def revoke(self) -> None:
        self._revoked = True
        self._drafts.clear()
        self._approvals.clear()

    def _now(self) -> ReplyClock:
        now = ReplyClock.model_validate(self._clock().model_dump())
        if self._last_clock is not None and (
            now.wall < self._last_clock.wall or now.monotonic < self._last_clock.monotonic
        ):
            self.revoke()
            raise ReplyDenied("reply_clock_rollback")
        self._last_clock = now
        return now

    def _scope(self, scope: SelectedReplyScope) -> ReplyClock:
        now = self._now()
        current = self._selection()
        if (
            self._revoked
            or current is None
            or SelectedReplyScope.model_validate(current.model_dump()) != scope
            or now.wall >= scope.expires_at
            or now.monotonic >= scope.monotonic_deadline
        ):
            self._approvals.clear()
            raise ReplyDenied("reply_scope_revoked_or_expired")
        return now

    def draft(self, request_id: UUID, reply_to_handle: UUID, text: str) -> ReplyDraft:
        current = self._selection()
        if current is None:
            raise ReplyDenied("reply_selection_required")
        current = SelectedReplyScope.model_validate(current.model_dump())
        now = self._scope(current)
        draft = ReplyDraft(
            request_id=request_id,
            scope=current,
            reply_to_handle=reply_to_handle,
            text=text,
            created=now,
            expires_at=min(current.expires_at, now.wall + timedelta(minutes=5)),
            monotonic_deadline=min(current.monotonic_deadline, now.monotonic + 300),
        )
        previous = self._drafts.get(request_id)
        receipt = self._journal.get(request_id)
        if (previous is not None and previous.payload_digest != draft.payload_digest) or (
            receipt is not None and receipt.payload_digest != draft.payload_digest
        ):
            raise ReplyDenied("reply_request_payload_conflict")
        if previous is not None:
            return previous  # Repaint never renews the original expiry.
        if len(self._drafts) >= 32:
            raise ReplyDenied("reply_draft_capacity")
        self._drafts[request_id] = draft
        return draft

    def _draft(self, draft: ReplyDraft, *, require_fresh: bool) -> ReplyClock:
        draft = ReplyDraft.model_validate(draft.model_dump())
        if self._drafts.get(draft.request_id) != draft:
            raise ReplyDenied("reply_foreign_draft")
        now = self._scope(draft.scope)
        if require_fresh and (
            now.wall >= draft.expires_at or now.monotonic >= draft.monotonic_deadline
        ):
            raise ReplyDenied("reply_draft_expired")
        return now

    def approve(self, draft: ReplyDraft, *, confirmed: bool) -> ReplyApproval:
        """Mint one NEW recipient/text-bound send grant after exact preview approval."""
        now = self._draft(draft, require_fresh=True)
        if confirmed is not True:
            raise ReplyDenied("reply_approval_required")
        if self._journal.get(draft.request_id) is not None:
            raise ReplyDenied("reply_already_attempted")
        self._port.validate_recipient(draft)
        self._draft(draft, require_fresh=True)
        # A replacement approval invalidates an earlier UI revision for this request.
        approval = ReplyApproval(
            approval_id=uuid4(),
            request_id=draft.request_id,
            payload_digest=draft.payload_digest,
            approved_at=now.wall,
            expires_at=min(draft.expires_at, now.wall + timedelta(seconds=60)),
            monotonic_deadline=min(draft.monotonic_deadline, now.monotonic + 60),
        )
        self._approvals[draft.request_id] = approval
        return approval

    def send(self, approval: ReplyApproval) -> ReplyReceipt:
        approval = ReplyApproval.model_validate(approval.model_dump())
        draft = self._drafts.get(approval.request_id)
        if draft is None or self._approvals.get(approval.request_id) != approval:
            raise ReplyDenied("reply_foreign_or_consumed_approval")
        now = self._draft(draft, require_fresh=True)
        if (
            approval.payload_digest != draft.payload_digest
            or now.wall >= approval.expires_at
            or now.monotonic >= approval.monotonic_deadline
        ):
            self._approvals.pop(approval.request_id, None)
            raise ReplyDenied("reply_approval_expired")
        self._approvals.pop(approval.request_id)
        # Commit the unknown liability BEFORE any transport may dispatch. The
        # application sending_id is correlation only, never server deduplication.
        sending_id = (uuid4().int % ((1 << 31) - 1)) + 1
        claimed = self._journal.claim(draft, approval, sending_id, now.wall)

        def guard() -> None:
            current = self._draft(draft, require_fresh=True)
            if (
                current.wall >= approval.expires_at
                or current.monotonic >= approval.monotonic_deadline
            ):
                raise ReplyDenied("reply_approval_expired")

        try:
            result = self._port.send_once(draft, sending_id, guard)
            self._draft(draft, require_fresh=False)
            return self._accept(claimed, result)
        except Exception:
            # Timeout, parsing failure, revoked publication and uncertain journal
            # acknowledgement all retain the already claimed unknown attempt.
            return self._journal.get(draft.request_id) or claimed

    def _accept(self, previous: ReplyReceipt, receipt: ReplyReceipt) -> ReplyReceipt:
        receipt = ReplyReceipt.model_validate(receipt.model_dump())
        if (
            receipt.request_id != previous.request_id
            or receipt.payload_digest != previous.payload_digest
            or receipt.sending_id != previous.sending_id
            or (
                previous.message_id is not None
                and receipt.state == "unknown"
                and receipt.message_id != previous.message_id
            )
        ):
            raise ReplyDenied("reply_foreign_receipt")
        return self._journal.record(receipt, self._now().wall)

    def receipt(self, request_id: UUID) -> ReplyReceipt | None:
        """Local metadata only; never reconnect, read Telegram or repeat a send."""
        return self._journal.get(request_id)

    def reconcile(self, draft: ReplyDraft) -> ReplyReceipt:
        self._draft(draft, require_fresh=False)
        previous = self._journal.get(draft.request_id)
        if previous is None or previous.payload_digest != draft.payload_digest:
            raise ReplyDenied("reply_attempt_unknown")
        if previous.state != "unknown" or previous.message_id is None:
            return previous

        def guard() -> None:
            self._draft(draft, require_fresh=False)

        try:
            result = self._port.reconcile(draft, previous, guard)
            guard()
            return self._accept(previous, result)
        except Exception:
            return self._journal.get(draft.request_id) or previous
