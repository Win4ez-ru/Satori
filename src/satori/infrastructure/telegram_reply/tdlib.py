"""Fixed plain-text TDLib functions over an injected, single owned RPC receive lane.

No client, credentials, sockets or receiver is created here. The existing read-only
helper is intentionally incompatible. Its allowlist is neither patched nor bypassed.
Schema: local public TDLib commit bc9c263e2bfee06aaab41e82db51a103376030bc.
"""

from collections.abc import Callable
from typing import Literal, Protocol
from uuid import UUID

from pydantic import Field

from satori.application.assistant.telegram_reply_contracts import (
    DispatchGuard,
    ReplyDenied,
    ReplyDraft,
    ReplyModel,
    ReplyReceipt,
    SelectedReplyScope,
)

INT53 = (1 << 53) - 1


class NativeReplyBinding(ReplyModel):
    """Trusted native mapping, re-resolved at dispatch; never a frontend DTO."""

    scope: SelectedReplyScope
    reply_to_handle: UUID
    personal_chat_verified: Literal[True]
    chat_id: int = Field(ge=1, le=INT53, repr=False)
    reply_to_message_id: int = Field(ge=1, le=INT53, repr=False)


class OwnedReplyRPC(Protocol):
    @property
    def reviewed_reply_enabled(self) -> bool: ...

    def call(self, query: dict[str, object], *, before_send: DispatchGuard) -> dict[str, object]:
        """Serialize; correlate @extra; call guard immediately before native.send.

        Exactly one receiver owns responses/updates. No retries or callback replay.
        Exceptions carry no private provider payload. Native framing remains bounded.
        """
        ...


def _require(value: bool) -> None:
    if not value:
        raise ReplyDenied("reply_native_response_invalid")


def _object(value: object) -> dict[str, object]:
    _require(type(value) is dict)
    assert isinstance(value, dict)
    return value


def _message_id(value: object) -> int:
    _require(type(value) is int and value != 0 and abs(value) <= INT53)
    assert isinstance(value, int)
    return value


def _integer_equal(value: object, expected: int) -> bool:
    return type(value) is int and value == expected


class TDLibReplyAdapter:
    def __init__(
        self,
        rpc: OwnedReplyRPC,
        resolve: Callable[[SelectedReplyScope, UUID], NativeReplyBinding | None],
    ) -> None:
        if rpc.reviewed_reply_enabled is not True:
            raise ReplyDenied("reply_transport_unavailable")
        self._rpc = rpc
        self._resolve = resolve
        # Metadata only; receiving these updates is the existing native owner's job.
        self._pending: dict[UUID, tuple[NativeReplyBinding, int]] = {}
        self._terminal: dict[UUID, int] = {}

    def clear(self) -> None:
        self._pending.clear()
        self._terminal.clear()

    def _binding(self, draft: ReplyDraft) -> NativeReplyBinding:
        binding = self._resolve(draft.scope, draft.reply_to_handle)
        if binding is None:
            raise ReplyDenied("reply_native_binding_unavailable")
        binding = NativeReplyBinding.model_validate(binding.model_dump())
        if binding.scope != draft.scope or binding.reply_to_handle != draft.reply_to_handle:
            raise ReplyDenied("reply_native_binding_mismatch")
        return binding

    def _guard(
        self, draft: ReplyDraft, expected: NativeReplyBinding, parent: DispatchGuard
    ) -> None:
        parent()
        if self._binding(draft) != expected:
            raise ReplyDenied("reply_native_binding_changed")

    def validate_recipient(self, draft: ReplyDraft) -> None:
        self._binding(draft)

    def send_once(
        self, draft: ReplyDraft, sending_id: int, before_send: DispatchGuard
    ) -> ReplyReceipt:
        binding = self._binding(draft)
        if draft.request_id in self._pending or len(self._pending) >= 512:
            raise ReplyDenied("reply_adapter_attempt_bound")
        query: dict[str, object] = {
            "@type": "sendMessage",
            "chat_id": binding.chat_id,
            "topic_id": None,
            "reply_to": {
                "@type": "inputMessageReplyToMessage",
                "message_id": binding.reply_to_message_id,
                "quote": None,
                "checklist_task_id": 0,
                "poll_option_id": "",
            },
            "options": {
                "@type": "messageSendOptions",
                "suggested_post_info": None,
                "disable_notification": False,
                "from_background": False,
                "protect_content": False,
                "allow_paid_broadcast": False,
                "paid_message_star_count": 0,
                "update_order_of_installed_sticker_sets": False,
                "scheduling_state": None,
                "effect_id": 0,
                "sending_id": sending_id,
                "only_preview": False,
            },
            "reply_markup": None,
            "input_message_content": {
                "@type": "inputMessageText",
                "text": {"@type": "formattedText", "text": draft.text, "entities": []},
                "link_preview_options": {
                    "@type": "linkPreviewOptions",
                    "is_disabled": True,
                    "url": "",
                    "force_small_media": False,
                    "force_large_media": False,
                    "show_above_text": False,
                },
                "clear_draft": False,
            },
        }
        # Consume adapter dispatch authority before calling the lane too. A timeout
        # before the first message ID is fundamentally unreconciled, never retried.
        self._pending[draft.request_id] = (binding, 0)
        result = self._rpc.call(query, before_send=lambda: self._guard(draft, binding, before_send))
        self._guard(draft, binding, before_send)
        receipt = self._parse(draft, binding, sending_id, result)
        if receipt.message_id is not None:
            self._pending[draft.request_id] = (binding, receipt.message_id)
        return receipt

    def observe_terminal_update(self, scope: SelectedReplyScope, event: dict[str, object]) -> bool:
        """Trusted receive-owner hook, never a network/UI command or send receipt.

        A correlated mapping only enables a subsequent explicit getMessage read;
        it does not by itself publish success or write an action outcome.
        """
        if event.get("@type") not in {"updateMessageSendSucceeded", "updateMessageSendFailed"}:
            return False
        old_id = _message_id(event.get("old_message_id"))
        message = _object(event.get("message"))
        final_id = _message_id(message.get("id"))
        for request_id, (binding, pending_id) in self._pending.items():
            if (
                pending_id == old_id
                and binding.scope == scope
                and _integer_equal(message.get("chat_id"), binding.chat_id)
                and self._resolve(scope, binding.reply_to_handle) == binding
            ):
                self._terminal[request_id] = final_id
                return True
        return False

    def reconcile(
        self, draft: ReplyDraft, prior: ReplyReceipt, before_read: DispatchGuard
    ) -> ReplyReceipt:
        binding = self._binding(draft)
        if prior.message_id is None:
            return prior
        message_id = self._terminal.get(draft.request_id, prior.message_id)
        result = self._rpc.call(
            {"@type": "getMessage", "chat_id": binding.chat_id, "message_id": message_id},
            before_send=lambda: self._guard(draft, binding, before_read),
        )
        self._guard(draft, binding, before_read)
        # A missing local message or timeout is not proof that it was never sent.
        if result.get("@type") == "error":
            return prior
        _require(_integer_equal(result.get("id"), message_id))
        return self._parse(draft, binding, prior.sending_id, result)

    @staticmethod
    def _parse(
        draft: ReplyDraft,
        binding: NativeReplyBinding,
        sending_id: int,
        result: dict[str, object],
    ) -> ReplyReceipt:
        if result.get("@type") == "error":
            return ReplyReceipt(
                request_id=draft.request_id,
                payload_digest=draft.payload_digest,
                sending_id=sending_id,
                state="failed",
                code="native_failed",
            )
        _require(result.get("@type") == "message")
        message_id = _message_id(result.get("id"))
        _require(
            _integer_equal(result.get("chat_id"), binding.chat_id)
            and result.get("is_outgoing") is True
        )
        _require(_integer_equal(result.get("paid_message_star_count"), 0))
        _require("sending_state" in result and result.get("scheduling_state") is None)
        content = _object(result.get("content"))
        text = _object(content.get("text"))
        _require(content.get("@type") == "messageText")
        _require(text.get("text") == draft.text and text.get("entities") == [])
        reply = _object(result.get("reply_to"))
        _require(
            reply.get("@type") == "messageReplyToMessage"
            and _integer_equal(reply.get("chat_id"), binding.chat_id)
            and _integer_equal(reply.get("message_id"), binding.reply_to_message_id)
        )
        state = result["sending_state"]
        if state is None:
            _require(message_id > 0)
            return ReplyReceipt(
                request_id=draft.request_id,
                payload_digest=draft.payload_digest,
                sending_id=sending_id,
                message_id=message_id,
                state="sent",
                code="native_sent",
            )
        sending = _object(state)
        if sending.get("@type") == "messageSendingStateFailed":
            return ReplyReceipt(
                request_id=draft.request_id,
                payload_digest=draft.payload_digest,
                sending_id=sending_id,
                message_id=message_id,
                state="failed",
                code="native_failed",
            )
        _require(
            sending.get("@type") == "messageSendingStatePending"
            and _integer_equal(sending.get("sending_id"), sending_id)
        )
        return ReplyReceipt(
            request_id=draft.request_id,
            payload_digest=draft.payload_digest,
            sending_id=sending_id,
            message_id=message_id,
            state="unknown",
            code="native_pending",
        )
