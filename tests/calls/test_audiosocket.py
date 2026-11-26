"""Real loopback transport checks, with synthetic authority and PCM only."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from satori.application.assistant.contracts import LocalPrincipal
from satori.application.calls.intake import CallBinding, CallEndReceipt, CallIntakeError
from satori.infrastructure.calls.audiosocket import (
    MAX_PAYLOAD,
    AudioSocketError,
    AudioSocketGrant,
    AudioSocketLimits,
    AudioSocketPCM,
    AudioSocketReceipt,
    AudioSocketServer,
)
from satori.infrastructure.calls.busy import BusyCallHandler
from tests.busy_call_support import FixtureIntakeAuthority, FixtureSpeech, ReviewInboxes


@pytest.mark.parametrize(
    "greeting", [b"", bytes(32), b"\x01\x00"], ids=["empty", "silence", "quiet"]
)
def test_busy_intake_requires_new_greeting_audio(greeting: bytes) -> None:
    class GreetingSpeech(FixtureSpeech):
        async def speak(self, line: str, sink: AudioSocketPCM) -> None:
            if greeting:
                await sink.write(greeting)

    async def scenario() -> None:
        audio = grant()
        auth = FixtureIntakeAuthority(audio)
        speech = GreetingSpeech()
        inboxes = ReviewInboxes()
        handler = BusyCallHandler(
            auth.grant, authority=auth, speech=speech, deliver=inboxes.deliver
        )
        sink = AudioSocketPCM(
            MagicMock(spec=asyncio.StreamWriter), lambda: 30.0, AudioSocketLimits()
        )
        # Audio sent before this invitation cannot open message intake.
        await sink.write(b"\x01\x00")
        if any(greeting):
            await handler.on_open(audio, sink)
            await handler.on_pcm(audio, speech.render("Перезвони"), sink)
            assert speech.transcribed == 1
        else:
            with pytest.raises(CallIntakeError, match=r"^greeting_audio_missing$"):
                await handler.on_open(audio, sink)
            with pytest.raises(CallIntakeError, match=r"^session_stopped$"):
                await handler.on_pcm(audio, speech.render("Перезвони"), sink)
            assert speech.transcribed == 0
        assert inboxes.attempts == 0

    asyncio.run(scenario())


class Admission:
    def __init__(self, grant: AudioSocketGrant) -> None:
        self.grant = grant
        self.claimed = False
        self.current = True

    def claim(self, stream_id: UUID, now: datetime) -> AudioSocketGrant | None:
        if self.claimed or stream_id != self.grant.stream_id:
            return None
        self.claimed = True
        return self.grant

    def is_current(self, grant: AudioSocketGrant, now: datetime) -> bool:
        return self.current and grant == self.grant


class Handler:
    def __init__(self) -> None:
        self.frames: list[bytes] = []
        self.digits: list[str] = []
        self.receipts: list[AudioSocketReceipt] = []
        self.opened = asyncio.Event()
        self.closed = asyncio.Event()
        self.sink: AudioSocketPCM | None = None
        self.binding: CallBinding | None = None
        self.fail = False
        self.hang = False
        self.reply = False

    async def on_open(self, grant: AudioSocketGrant, sink: AudioSocketPCM) -> None:
        self.sink = sink
        self.binding = grant.binding
        self.opened.set()
        if self.fail:
            raise ValueError("PRIVATE_CALLER_SECRET_MUST_NOT_APPEAR")
        if self.hang:
            await asyncio.Event().wait()

    async def on_pcm(self, grant: AudioSocketGrant, pcm: bytes, sink: AudioSocketPCM) -> None:
        self.frames.append(pcm)
        if self.reply:
            await sink.write(b"\x02\x00" * 160)

    async def on_dtmf(self, grant: AudioSocketGrant, digit: str) -> None:
        self.digits.append(digit)

    async def on_close(self, receipt: AudioSocketReceipt) -> None:
        self.receipts.append(receipt)
        self.closed.set()


def grant(*, seconds: float = 120, principal: LocalPrincipal | None = None) -> AudioSocketGrant:
    now = datetime.now(UTC)
    return AudioSocketGrant(
        stream_id=uuid4(),
        binding=CallBinding(
            principal=principal
            or LocalPrincipal(os_uid=501, profile_id=uuid4(), session_id=uuid4()),
            route_id=uuid4(),
            provider_account_ref=uuid4(),
            call_id=uuid4(),
            leg_id=uuid4(),
            generation=1,
        ),
        issued_at=now,
        expires_at=now + timedelta(seconds=seconds),
    )


def frame(kind: int, payload: bytes = b"") -> bytes:
    return bytes([kind]) + len(payload).to_bytes(2, "big") + payload


async def connect(server: AudioSocketServer) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection("127.0.0.1", server.port)


async def eof(reader: asyncio.StreamReader) -> None:
    assert await asyncio.wait_for(reader.read(), 1) == b""


async def settled(server: AudioSocketServer) -> AudioSocketReceipt:
    for _ in range(100):
        if server.last_receipt is not None:
            return server.last_receipt
        await asyncio.sleep(0.001)
    raise AssertionError("receipt_not_settled")


def test_deployment_disabled_and_non_loopback_rejected() -> None:
    async def scenario() -> None:
        server = AudioSocketServer(Admission(grant()), Handler())
        with pytest.raises(RuntimeError, match="deployment_disabled"):
            await server.start()
        with pytest.raises(RuntimeError, match="not_started"):
            _ = server.port
        for host in ("0.0.0.0", "::", "192.0.2.1", "localhost"):
            with pytest.raises(ValueError, match=r"loopback|IP address"):
                AudioSocketServer(Admission(grant()), Handler(), host=host)

    asyncio.run(scenario())


def test_actual_fragmented_duplex_pcm_and_dtmf() -> None:
    async def scenario() -> None:
        authorized = grant()
        handler = Handler()
        handler.reply = True
        server = AudioSocketServer(Admission(authorized), handler)
        await server.start(enabled=True)
        reader, writer = await connect(server)
        try:
            handshake = frame(1, authorized.stream_id.bytes)
            for chunk in (handshake[:2], handshake[2:9], handshake[9:]):
                writer.write(chunk)
                await writer.drain()
            writer.write(frame(0x10, b"\x01\x00" * 160) + frame(3, b"5"))
            await writer.drain()
            assert await asyncio.wait_for(reader.readexactly(323), 1) == frame(
                0x10, b"\x02\x00" * 160
            )
            writer.write(frame(0))
            await writer.drain()
            await eof(reader)
            await asyncio.wait_for(handler.closed.wait(), 1)
            assert handler.frames == [b"\x01\x00" * 160]
            assert handler.digits == ["5"]
            assert handler.binding == authorized.binding
            receipt = handler.receipts[0]
            assert receipt.outcome == "peer_hangup"
            assert receipt.admitted is True
            assert receipt.pcm_frames == 1
            assert receipt.received_pcm_bytes == receipt.sent_pcm_bytes == 320
            assert receipt.remote_termination_confirmed is False
            assert handler.sink is not None
            with pytest.raises(AudioSocketError, match="stopped"):
                await handler.sink.write(b"\x00\x00")
        finally:
            writer.close()
            await writer.wait_closed()
            await server.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "wire",
    [
        frame(0x10, b"\x00\x00"),  # Audio before UUID.
        frame(1, b"short"),
        frame(1, UUID(int=0).bytes),
        frame(1, uuid4().bytes),  # Not pre-armed by trusted control plane.
    ],
)
def test_unarmed_wire_cannot_open_handler(wire: bytes) -> None:
    async def scenario() -> None:
        handler = Handler()
        server = AudioSocketServer(Admission(grant()), handler)
        await server.start(enabled=True)
        reader, writer = await connect(server)
        try:
            writer.write(wire)
            await writer.drain()
            await eof(reader)
            receipt = await settled(server)
            assert receipt.admitted is False
            assert not handler.opened.is_set()
            assert handler.frames == []
            assert handler.digits == []
        finally:
            writer.close()
            await writer.wait_closed()
            await server.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "bad_frame",
    [
        b"\x10" + (MAX_PAYLOAD + 1).to_bytes(2, "big"),  # Header rejected before body.
        frame(0x10, b"odd"),
        frame(0x10),
        frame(1, uuid4().bytes),  # A second UUID cannot replace admitted binding.
        frame(0, b"x"),
        frame(3, b"77"),
        frame(3, b"x"),
        frame(0xFF, b"PRIVATE_PROVIDER_ERROR"),
        frame(0x80, b"caller_id=trusted_grandmother"),
    ],
)
def test_bad_wire_closes_without_identity_or_payload_leak(bad_frame: bytes) -> None:
    async def scenario() -> None:
        authorized = grant()
        handler = Handler()
        server = AudioSocketServer(Admission(authorized), handler)
        await server.start(enabled=True)
        reader, writer = await connect(server)
        try:
            writer.write(frame(1, authorized.stream_id.bytes) + bad_frame)
            await writer.drain()
            await eof(reader)
            await asyncio.wait_for(handler.closed.wait(), 1)
            receipt = handler.receipts[0]
            assert receipt.outcome == "protocol_rejected"
            assert handler.frames == []
            assert handler.digits == []
            assert "PRIVATE" not in receipt.model_dump_json()
            assert "caller_id" not in receipt.model_dump_json()
        finally:
            writer.close()
            await writer.wait_closed()
            await server.close()

    asyncio.run(scenario())


def test_single_call_replay_rejected_and_shutdown_releases_socket() -> None:
    async def scenario() -> None:
        authorized = grant()
        admission = Admission(authorized)
        handler = Handler()
        server = AudioSocketServer(admission, handler)
        await server.start(enabled=True)
        reader, writer = await connect(server)
        writer.write(frame(1, authorized.stream_id.bytes))
        await writer.drain()
        await asyncio.wait_for(handler.opened.wait(), 1)
        second_reader, second_writer = await connect(server)
        await eof(second_reader)
        second_writer.close()
        await second_writer.wait_closed()
        await server.close()
        await eof(reader)
        assert (await settled(server)).outcome == "stopped"
        writer.close()
        await writer.wait_closed()
        # A new listener cannot replay consumed admission.
        await server.start(enabled=True)
        replay_reader, replay_writer = await connect(server)
        replay_writer.write(frame(1, authorized.stream_id.bytes))
        await replay_writer.drain()
        await eof(replay_reader)
        assert (await settled(server)).outcome == "binding_rejected"
        assert len(handler.receipts) == 1
        replay_writer.close()
        await replay_writer.wait_closed()
        await server.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["pcm_budget", "frame_budget", "dtmf_budget", "revoked"])
def test_stream_bounds_and_revoke(case: str) -> None:
    async def scenario() -> None:
        authorized = grant()
        admission = Admission(authorized)
        handler = Handler()
        limits = AudioSocketLimits(max_frames=2, max_pcm_bytes=4, max_dtmf=1)
        server = AudioSocketServer(admission, handler, limits=limits)
        await server.start(enabled=True)
        reader, writer = await connect(server)
        try:
            writer.write(frame(1, authorized.stream_id.bytes))
            await writer.drain()
            await asyncio.wait_for(handler.opened.wait(), 1)
            if case == "revoked":
                admission.current = False
                wire = frame(0x10, b"\x00\x00")
            elif case == "pcm_budget":
                wire = frame(0x10, b"\x00\x00" * 3)
            elif case == "dtmf_budget":
                wire = frame(3, b"1") * 2
            else:
                wire = frame(0x10, b"\x00\x00") * 3
            writer.write(wire)
            await writer.drain()
            await eof(reader)
            await asyncio.wait_for(handler.closed.wait(), 1)
            receipt = handler.receipts[0]
            assert receipt.outcome == (
                "authority_lost" if case == "revoked" else "protocol_rejected"
            )
            assert receipt.received_pcm_bytes <= 4
            assert receipt.dtmf_count <= 1
            if case == "revoked":
                assert handler.frames == []
        finally:
            writer.close()
            await writer.wait_closed()
            await server.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["handshake", "idle", "lifetime", "callback", "exception"])
def test_timeouts_and_body_free_handler_failure(case: str) -> None:
    async def scenario() -> None:
        authorized = grant(seconds=0.3 if case == "lifetime" else 120)
        handler = Handler()
        handler.hang = case in ("callback", "lifetime")
        handler.fail = case == "exception"
        limits = AudioSocketLimits(
            handshake_seconds=0.1,
            frame_seconds=0.1,
            callback_seconds=0.1 if case == "callback" else 1.0,
        )
        server = AudioSocketServer(Admission(authorized), handler, limits=limits)
        await server.start(enabled=True)
        reader, writer = await connect(server)
        try:
            if case != "handshake":
                writer.write(frame(1, authorized.stream_id.bytes))
                await writer.drain()
            await eof(reader)
            receipt = await settled(server)
            assert (
                receipt.outcome
                == {
                    "handshake": "idle_timeout",
                    "idle": "idle_timeout",
                    "lifetime": "deadline",
                    "callback": "handler_failed",
                    "exception": "handler_failed",
                }[case]
            )
            assert "PRIVATE" not in repr(receipt)
        finally:
            writer.close()
            await writer.wait_closed()
            await server.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("cause", ["backward", "expiry"])
def test_absolute_expiry_backward_clock_and_stale_output(cause: str) -> None:
    async def scenario() -> None:
        authorized = grant()
        now = authorized.issued_at
        admission = Admission(authorized)
        handler = Handler()
        server = AudioSocketServer(admission, handler, clock=lambda: now)
        await server.start(enabled=True)
        reader, writer = await connect(server)
        try:
            writer.write(frame(1, authorized.stream_id.bytes))
            await writer.drain()
            await asyncio.wait_for(handler.opened.wait(), 1)
            assert handler.sink is not None
            if cause == "backward":
                now -= timedelta(seconds=1)
                expected = "authority_lost"
            else:
                now = authorized.expires_at
                expected = "deadline"
            with pytest.raises(AudioSocketError, match=expected):
                await handler.sink.write(b"\x00\x00")
            # Clock correction cannot resurrect a revoked/expired media capability.
            now = authorized.issued_at
            writer.write(frame(0x10, b"\x00\x00"))
            await writer.drain()
            await eof(reader)
            assert (await settled(server)).outcome == expected
            assert handler.frames == []
        finally:
            writer.close()
            await writer.wait_closed()
            await server.close()

    asyncio.run(scenario())


def test_grants_cannot_extend_two_minutes_or_add_caller_authority() -> None:
    valid = grant()
    for update in (
        {"expires_at": valid.issued_at + timedelta(seconds=121)},
        {"expires_at": valid.issued_at},
        {"stream_id": UUID(int=0)},
        {"known_contact": True},
        {"caller_id": "+79990000000"},
    ):
        with pytest.raises(ValidationError):
            AudioSocketGrant.model_validate({**valid.model_dump(), **update})


@pytest.mark.parametrize("payload", [b"", b"odd", b"\x00" * (MAX_PAYLOAD + 2)])
def test_output_payload_validation(payload: bytes) -> None:
    async def scenario() -> None:
        authorized = grant()
        handler = Handler()
        server = AudioSocketServer(Admission(authorized), handler)
        await server.start(enabled=True)
        reader, writer = await connect(server)
        try:
            writer.write(frame(1, authorized.stream_id.bytes))
            await writer.drain()
            await asyncio.wait_for(handler.opened.wait(), 1)
            assert handler.sink is not None
            with pytest.raises(AudioSocketError, match="protocol_rejected"):
                await handler.sink.write(payload)
            assert handler.sink.sent_bytes == 0
            writer.write(frame(0))
            await writer.drain()
            await eof(reader)
        finally:
            writer.close()
            await writer.wait_closed()
            await server.close()

    asyncio.run(scenario())


def test_output_budget_and_revocation_prevent_more_audio() -> None:
    async def scenario() -> None:
        authorized = grant()
        admission = Admission(authorized)
        handler = Handler()
        server = AudioSocketServer(admission, handler, limits=AudioSocketLimits(max_pcm_bytes=2))
        await server.start(enabled=True)
        reader, writer = await connect(server)
        try:
            writer.write(frame(1, authorized.stream_id.bytes))
            await writer.drain()
            await asyncio.wait_for(handler.opened.wait(), 1)
            assert handler.sink is not None
            await handler.sink.write(b"\x00\x00")
            assert await asyncio.wait_for(reader.readexactly(5), 1) == frame(0x10, b"\x00\x00")
            with pytest.raises(AudioSocketError, match="protocol_rejected"):
                await handler.sink.write(b"\x00\x00")
            admission.current = False
            with pytest.raises(AudioSocketError, match="authority_lost"):
                await handler.sink.write(b"\x00\x00")
            assert handler.sink.sent_bytes == 2
            writer.write(frame(0))
            await writer.drain()
            await eof(reader)
        finally:
            writer.close()
            await writer.wait_closed()
            await server.close()

    asyncio.run(scenario())


def test_partial_body_timeout_and_peer_eof_release_admission() -> None:
    async def scenario() -> None:
        for partial in (False, True):
            authorized = grant()
            handler = Handler()
            server = AudioSocketServer(
                Admission(authorized), handler, limits=AudioSocketLimits(frame_seconds=0.1)
            )
            await server.start(enabled=True)
            reader, writer = await connect(server)
            writer.write(frame(1, authorized.stream_id.bytes))
            await writer.drain()
            await asyncio.wait_for(handler.opened.wait(), 1)
            if partial:
                writer.write(b"\x10\x00\x04\x01")
                await writer.drain()
                await eof(reader)
            else:
                writer.write_eof()
                await eof(reader)
            await asyncio.wait_for(handler.closed.wait(), 1)
            assert handler.receipts[0].outcome == ("idle_timeout" if partial else "peer_eof")
            assert handler.frames == []
            writer.close()
            await writer.wait_closed()
            await server.close()

    asyncio.run(scenario())


def test_concurrent_start_cannot_leave_an_extra_listener() -> None:
    async def scenario() -> None:
        server = AudioSocketServer(Admission(grant()), Handler())
        first, second = await asyncio.gather(
            server.start(enabled=True), server.start(enabled=True), return_exceptions=True
        )
        assert first is None
        assert isinstance(second, RuntimeError)
        assert str(second) == "audiosocket_already_started"
        port = server.port
        await server.close()
        await server.close()
        with pytest.raises(OSError, match=r"Connect call failed|refused"):
            await asyncio.open_connection("127.0.0.1", port)

    asyncio.run(scenario())


_CLEAR = "Это Илья, доставка будет в восемь."
_DETAIL = "Доставка будет в восемь."
_THANKS = "Хорошо, передам."
_ASK = "Подскажите, по какому вы вопросу?"


@pytest.mark.parametrize(
    ("turns", "summary", "lost", "confirm_first"),
    [
        pytest.param(((_CLEAR, _THANKS),), _CLEAR, False, False, id="one-clear-answer"),
        pytest.param((("Перезвони", _THANKS),), "Перезвони", False, False, id="short-request"),
        pytest.param(
            (("меня зовут илья", _ASK), ("Такси", _THANKS)),
            "\n".join(("меня зовут илья", "Такси")),
            False,
            False,
            id="brief-clarification",
        ),
        pytest.param(
            (("Перенесли встречу", _THANKS),), "Перенесли встречу", False, True, id="no-name"
        ),
        pytest.param(
            (("Илья.", _ASK), (_DETAIL, _THANKS)),
            "Илья.\n" + _DETAIL,
            False,
            False,
            id="name-then-message",
        ),
        pytest.param(
            (("Добрый день", _ASK), (_DETAIL, _THANKS)),
            _DETAIL,
            False,
            False,
            id="greeting-then-message",
        ),
        pytest.param(
            (("Алло", _ASK), ("Да", "До свидания.")),
            None,
            False,
            False,
            id="one-clarification-only",
        ),
        pytest.param((), None, False, False, id="caller-hangs-up-without-speaking"),
        pytest.param((("Это Илья", _ASK),), None, False, False, id="intro-only"),
        pytest.param(
            (("Ничего не передавайте", "До свидания."),), None, False, False, id="declined"
        ),
        pytest.param(((_CLEAR, _THANKS),), None, True, False, id="disconnect"),
        pytest.param(((_CLEAR, _THANKS),), None, True, True, id="confirmed-before-disconnect"),
    ],
)
def test_busy_call_private_delivery(
    turns: tuple[tuple[str, str], ...], summary: str | None, lost: bool, confirm_first: bool
) -> None:
    async def scenario() -> None:
        authorized = grant()
        owner = authorized.binding.principal
        other_profile = owner.model_copy(update={"profile_id": uuid4()})
        speech, inboxes = FixtureSpeech(), ReviewInboxes()
        authority = FixtureIntakeAuthority(authorized)
        handler = BusyCallHandler(
            authority.grant,
            authority=authority,
            speech=speech,
            deliver=inboxes.deliver,
        )
        server = AudioSocketServer(Admission(authorized), handler)
        await server.start(enabled=True)
        try:
            reader, writer = await connect(server)

            async def hear() -> str:
                header = await reader.readexactly(3)
                assert header[0] == 0x10
                return speech.recognize(await reader.readexactly(int.from_bytes(header[1:], "big")))

            async def send(kind: int, payload: bytes) -> None:
                writer.write(frame(kind, payload))
                await writer.drain()

            try:
                await send(1, authorized.stream_id.bytes)
                greeting = await hear()
                assert greeting == (
                    "Здравствуйте! Я Сатори, ассистент Кирилла. Он сейчас занят. Что ему передать?"
                )
                for spoken, reply in turns:
                    await send(0x10, speech.render(spoken))
                    assert await hear() == reply
                assert inboxes.attempts == 0
                end = CallEndReceipt(
                    binding=authorized.binding,
                    receipt_id=uuid4(),
                    status="confirmed_ended",
                    observed_at=datetime.now(UTC),
                )
                if confirm_first:
                    handler.accept_call_end(end)
                    assert inboxes.attempts == 0
                if lost:
                    writer.transport.abort()  # Drop TCP without an AudioSocket hangup frame.
                else:
                    await send(0, b"")
                    await eof(reader)
            finally:
                writer.close()
                await writer.wait_closed()
            media = await settled(server)
        finally:
            await server.close()

        assert (media.pcm_frames, media.dtmf_count) == (len(turns), 0)
        assert speech.transcribed == len(turns)
        assert media.remote_termination_confirmed is False
        if lost:
            assert media.outcome in {"peer_eof", "transport_failed"}
            with pytest.raises(CallIntakeError, match=r"^session_stopped$"):
                handler.accept_call_end(end)
            assert inboxes.attempts == 0
            assert inboxes.read(owner) == inboxes.read(other_profile) == []
            return

        assert media.outcome == "peer_hangup"
        assert inboxes.attempts == int(confirm_first and summary is not None)
        expected = [summary] if summary is not None else []
        handler.accept_call_end(end)
        assert inboxes.attempts == len(expected)
        assert inboxes.read(owner) == expected
        handler.accept_call_end(end)
        assert inboxes.attempts == len(expected)
        assert inboxes.read(owner) == expected
        assert inboxes.read(other_profile) == []
        if expected:
            (entry,) = inboxes.entries(owner)
            with pytest.raises(CallIntakeError, match=r"^review_owner_mismatch$"):
                entry.read(other_profile, now=datetime.now(UTC))
            authority.current = False
            with pytest.raises(CallIntakeError, match=r"^authority_unavailable$"):
                inboxes.read(owner)

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))
