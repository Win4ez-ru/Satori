"""Synthetic speech, consent authority and mailboxes for local busy-call acceptance."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from satori.application.assistant.contracts import LocalPrincipal
from satori.application.calls.intake import BusyMessageGrant, CallBinding
from satori.infrastructure.calls.audiosocket import AudioSocketGrant, AudioSocketPCM
from satori.infrastructure.calls.busy import NOTICE_DIGEST, BusyCallReview


class FixtureSpeech:
    """Each synthetic utterance travels over the socket as a distinct 20 ms PCM frame."""

    def __init__(self) -> None:
        self._lines: dict[bytes, str] = {}
        self.transcribed = 0

    def render(self, line: str) -> bytes:
        pcm = (len(self._lines) + 1).to_bytes(2, "little") * 160
        self._lines[pcm] = line
        return pcm

    def recognize(self, pcm: bytes) -> str:
        return self._lines[pcm]

    async def speak(self, line: str, sink: AudioSocketPCM) -> None:
        await sink.write(self.render(line))

    async def transcribe(self, pcm: bytes) -> str:
        self.transcribed += 1
        return self.recognize(pcm)


class ReviewInboxes:
    """An idempotent in-memory sink with a separate count of delivery attempts."""

    def __init__(self) -> None:
        self._entries: dict[LocalPrincipal, dict[UUID, BusyCallReview]] = {}
        self.attempts = 0

    def deliver(self, review: BusyCallReview) -> None:
        self.attempts += 1
        self._entries.setdefault(review.owner, {}).setdefault(review.request_id, review)

    def entries(self, owner: LocalPrincipal) -> list[BusyCallReview]:
        return list(self._entries.get(owner, {}).values())

    def read(self, owner: LocalPrincipal) -> list[str]:
        return [entry.read(owner, now=datetime.now(UTC)) for entry in self.entries(owner)]


class FixtureIntakeAuthority:
    def __init__(self, audio: AudioSocketGrant) -> None:
        self.grant = BusyMessageGrant(
            grant_id=uuid4(),
            binding=audio.binding,
            issued_at=audio.issued_at,
            expires_at=audio.expires_at,
            notice_digest=NOTICE_DIGEST,
            participant_ref=uuid4(),
        )
        self.current = True

    def is_current(self, binding: CallBinding, grant_id: UUID, now: datetime) -> bool:
        return self.current and binding == self.grant.binding and grant_id == self.grant.grant_id
