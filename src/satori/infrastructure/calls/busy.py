"""Opt-in AudioSocket dialogue for one owner-authorized busy call."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, Protocol
from uuid import UUID, uuid4

from satori.application.assistant.contracts import LocalPrincipal
from satori.application.calls.intake import (
    BusyMessageGrant,
    CallEndReceipt,
    CallFollowupProposal,
    CallIntakeError,
    CallIntakeSession,
    ExternalCallSegment,
    IntakeAuthority,
)
from satori.infrastructure.calls.audiosocket import (
    AudioSocketGrant,
    AudioSocketPCM,
    AudioSocketReceipt,
)

GREETING = "Здравствуйте! Я Сатори, ассистент Кирилла. Он сейчас занят. Что ему передать?"
NOTICE_DIGEST = sha256(GREETING.encode()).hexdigest()
CLARIFICATION = "Подскажите, по какому вы вопросу?"
ACKNOWLEDGEMENT = "Хорошо, передам."


def _reply_kind(
    text: str, *, clarifying: bool
) -> Literal["declined", "unclear", "intro", "message"]:
    words = re.findall(r"[\w-]+", text.casefold())
    phrase = " ".join(words)
    if phrase in {
        "ничего",
        "нет",
        "не нужно",
        "не надо",
        "нет спасибо",
        "до свидания",
        "не передавайте",
        "ничего не передавайте",
        "не надо ничего передавать",
        "не хочу оставлять сообщение",
        "ошибся номером",
        "ошиблась номером",
    }:
        return "declined"
    if phrase in {
        "",
        "алло",
        "да",
        "эм",
        "здравствуйте",
        "добрый день",
        "не расслышал",
        "не расслышала",
        "меня слышно",
        "вы меня слышите",
    }:
        return "unclear"
    # A single possible name, or a standalone self-introduction, can precede the message.
    if (
        not clarifying
        and len(words) == 1
        and phrase
        not in {
            "перезвони",
            "перезвоните",
            "перезвонить",
            "опаздываю",
            "задерживаюсь",
            "выезжаю",
            "приехал",
            "приехала",
        }
    ):
        return "intro"
    introduction = re.fullmatch(
        r"(?i:(?:(?:здравствуйте|добрый день)[, ]+)?(?:меня зовут|это(?: я)?|я)) "
        r"(?P<name>[\w-]+(?: [\w-]+)?)",
        text.strip(" .!?"),
    )
    if introduction and (
        introduction["name"].istitle()
        or phrase.startswith("меня зовут ")
        or (len(words) == 2 and words[0] == "это")
    ):
        return "intro"
    return "message"


class BusyCallSpeech(Protocol):
    """Per-call audio adapter. Return text only for a completed utterance."""

    async def speak(self, line: str, sink: AudioSocketPCM) -> None: ...

    async def transcribe(self, pcm: bytes) -> str | None: ...


@dataclass(frozen=True, slots=True)
class BusyCallReview:
    """Local inbox reference. Resolve the transient source again when the owner reads it."""

    owner: LocalPrincipal = field(repr=False)
    request_id: UUID
    _session: CallIntakeSession = field(repr=False)

    def resolve(self, principal: LocalPrincipal, *, now: datetime) -> CallFollowupProposal:
        if principal != self.owner:
            raise CallIntakeError("review_owner_mismatch")
        review = self._session.resolve_proposal(self.request_id, now=now)
        if principal != review.binding.principal:
            raise CallIntakeError("review_owner_mismatch")
        return review

    def read(self, principal: LocalPrincipal, *, now: datetime) -> str:
        return "\n".join(self.resolve(principal, now=now).untrusted_fragments)


class BusyCallHandler:
    """One owner-authorized invitation, an optional clarification and a private inbox review."""

    def __init__(
        self,
        grant: BusyMessageGrant,
        *,
        authority: IntakeAuthority,
        speech: BusyCallSpeech,
        deliver: Callable[[BusyCallReview], None],
    ) -> None:
        if grant.notice_digest != NOTICE_DIGEST:
            raise CallIntakeError("notice_mismatch")
        self._grant = grant
        self._session = CallIntakeSession(grant, now=datetime.now(UTC), authority=authority)
        self._speech = speech
        self._deliver = deliver
        self._audio: AudioSocketGrant | None = None
        self._sink: AudioSocketPCM | None = None
        self._turn: Literal["initial", "clarifying", "complete", "dismissed"] = "initial"
        self._segments: list[UUID] = []
        self._clean_close = False
        self._confirmed: UUID | None = None
        self._delivered = False

    async def on_open(self, grant: AudioSocketGrant, sink: AudioSocketPCM) -> None:
        if self._audio is not None or grant.binding != self._grant.binding:
            raise CallIntakeError("binding_mismatch")
        if self._session.metadata(now=datetime.now(UTC))["phase"] != "awaiting_greeting":
            raise CallIntakeError("session_stopped")
        self._audio, self._sink = grant, sink
        previous_frames = sink.non_silent_frames
        await self._speech.speak(GREETING, sink)
        if sink.non_silent_frames == previous_frames:
            self._session.stop()
            raise CallIntakeError("greeting_audio_missing")
        self._session.open_busy_message(grant.binding, now=datetime.now(UTC))

    async def on_dtmf(self, grant: AudioSocketGrant, digit: str) -> None:
        pass  # The conversational route has no keypad commands.

    async def on_pcm(self, grant: AudioSocketGrant, pcm: bytes, sink: AudioSocketPCM) -> None:
        if grant != self._audio or sink is not self._sink:
            return
        if (
            self._turn in {"complete", "dismissed"}
            or self._session.metadata(now=datetime.now(UTC))["phase"] != "intake"
        ):
            return
        text = await self._speech.transcribe(pcm)
        if text is None:
            return
        now = datetime.now(UTC)
        if self._session.metadata(now=now)["phase"] != "intake":
            return
        kind = _reply_kind(text, clarifying=self._turn == "clarifying")
        if kind == "declined" or (self._turn == "clarifying" and kind != "message"):
            self._turn = "dismissed"
            self._session.stop()
            await self._speech.speak("До свидания.", sink)
            return
        if kind != "unclear":
            segment = ExternalCallSegment(
                binding=grant.binding,
                participant_ref=self._grant.participant_ref,
                segment_id=uuid4(),
                sequence=len(self._segments) + 1,
                observed_at=now,
                text=text,
            )
            self._session.admit_segment(segment, now=now)
            self._segments.append(segment.segment_id)
        if kind == "message":
            self._turn = "complete"
            reply = ACKNOWLEDGEMENT
        else:
            self._turn = "clarifying"
            reply = CLARIFICATION
        await self._speech.speak(reply, sink)

    async def on_close(self, receipt: AudioSocketReceipt) -> None:
        if self._sink is None:
            return
        self._sink = None
        if receipt.outcome != "peer_hangup" or not receipt.admitted:
            self._session.stop()
            return
        self._clean_close = True
        self._offer_review(datetime.now(UTC))

    def accept_call_end(self, receipt: CallEndReceipt) -> None:
        """Called by the trusted control plane independently of AudioSocket closure."""
        if self._turn == "dismissed":
            return
        now = datetime.now(UTC)
        self._session.accept_end(receipt, now=now)
        if receipt.status == "unknown":
            self._session.stop()
            return
        self._confirmed = receipt.receipt_id
        self._offer_review(now)

    def _offer_review(self, now: datetime) -> None:
        if not self._clean_close or self._confirmed is None or self._turn != "complete":
            return
        if not self._delivered:
            request = self._session.propose_followup(
                self._confirmed, "owner_callback_review", tuple(self._segments), now=now
            )
            self._deliver(BusyCallReview(self._grant.binding.principal, request, self._session))
            self._delivered = True
