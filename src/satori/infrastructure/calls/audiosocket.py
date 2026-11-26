"""Opt-in loopback AudioSocket media seam, not a SIP server or call assistant.

A trusted control-plane owner must admit an exact, single-use stream UUID and
CallBinding before media callbacks can run. The protocol carries no caller identity
or source permission. PCM callbacks do not authorize ASR, model disclosure, TTS,
Calendar actions or canonical learning. No callback implementation is supplied.

Closing TCP proves only local transport closure, never remote PSTN termination.
No service starts on import or application startup. Deployment is disabled by
default and requires an explicit start(enabled=True) by a future composition owner.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from satori.application.assistant.contracts import require_utc
from satori.application.calls.intake import CallBinding

MAX_LIFETIME = 120.0
MAX_PCM_BYTES = 120 * 8000 * 2
MAX_PAYLOAD = 3200  # 200 ms, signed PCM16LE mono at 8 kHz.
Outcome = Literal[
    "peer_hangup",
    "peer_eof",
    "protocol_rejected",
    "binding_rejected",
    "authority_lost",
    "deadline",
    "idle_timeout",
    "handler_failed",
    "transport_failed",
    "stopped",
]
Clock = Callable[[], datetime]


class AudioSocketError(Exception):
    """A finite metadata code; no payload, original error or caller data."""

    def __init__(self, code: Outcome) -> None:
        super().__init__(code)
        self.code = code


class _DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class AudioSocketGrant(_DTO):
    """Trusted admission assertion, not a bearer token or user permission grant."""

    stream_id: UUID = Field(repr=False)
    binding: CallBinding = Field(repr=False)
    issued_at: datetime
    expires_at: datetime

    _utc = field_validator("issued_at", "expires_at")(require_utc)

    @model_validator(mode="after")
    def bounded(self) -> "AudioSocketGrant":
        if self.stream_id.int == 0 or not timedelta(
            0
        ) < self.expires_at - self.issued_at <= timedelta(seconds=MAX_LIFETIME):
            raise ValueError("bounded_audio_binding_required")
        return self


class AudioSocketLimits(_DTO):
    handshake_seconds: float = Field(default=2.0, gt=0, le=5)
    frame_seconds: float = Field(default=3.0, gt=0, le=10)
    callback_seconds: float = Field(default=5.0, gt=0, le=15)
    close_seconds: float = Field(default=0.5, gt=0, le=2)
    max_frames: int = Field(default=8000, ge=1, le=8000)
    max_pcm_bytes: int = Field(default=MAX_PCM_BYTES, ge=2, le=MAX_PCM_BYTES)
    max_dtmf: int = Field(default=32, ge=0, le=32)


class AudioSocketReceipt(_DTO):
    """Local metadata only. Does not satisfy CallEndReceipt.confirmed_ended."""

    outcome: Outcome
    admitted: bool
    pcm_frames: int = Field(ge=0, le=8000)
    received_pcm_bytes: int = Field(ge=0, le=MAX_PCM_BYTES)
    sent_pcm_bytes: int = Field(ge=0, le=MAX_PCM_BYTES)
    dtmf_count: int = Field(ge=0, le=32)
    remote_termination_confirmed: Literal[False] = False


class AudioSocketAdmission(Protocol):
    """Local authenticated control plane, never an adapter over caller-supplied ID.

    claim consumes one pre-armed UUID exactly once, including on later failure.
    is_current rechecks the exact binding/generation and revocation. Neither may
    block or call network services. Caller ID alone must never satisfy this port.
    """

    def claim(self, stream_id: UUID, now: datetime) -> AudioSocketGrant | None: ...

    def is_current(self, grant: AudioSocketGrant, now: datetime) -> bool: ...


class AudioSocketHandler(Protocol):
    """Future bounded ASR/TTS seam. Callbacks must cooperate with cancellation.

    Per-frame work is awaited, so there is no unbounded internal task/audio queue.
    The sink accepts at most 200 ms per write and paces generated PCM in real time.
    Separate source/disclosure/intake permissions belong to the composition owner.
    """

    async def on_open(self, grant: AudioSocketGrant, sink: "AudioSocketPCM") -> None: ...

    async def on_pcm(self, grant: AudioSocketGrant, pcm: bytes, sink: "AudioSocketPCM") -> None: ...

    async def on_dtmf(self, grant: AudioSocketGrant, digit: str) -> None: ...

    async def on_close(self, receipt: AudioSocketReceipt) -> None: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AudioSocketPCM:
    """One call's bounded output; retaining this object cannot extend its lease."""

    def __init__(
        self,
        writer: asyncio.StreamWriter,
        guard: Callable[[], float],
        limits: AudioSocketLimits,
    ) -> None:
        self._writer = writer
        self._guard = guard
        self._limits = limits
        self._lock = asyncio.Lock()
        self._closed = False
        self._next_audio = 0.0
        self._sent_bytes = 0
        self._non_silent_frames = 0

    @property
    def sent_bytes(self) -> int:
        return self._sent_bytes

    @property
    def non_silent_frames(self) -> int:
        """Drained frames containing at least one nonzero PCM sample."""
        return self._non_silent_frames

    def close(self) -> None:
        self._closed = True

    async def write(self, pcm: bytes) -> None:
        if type(pcm) is not bytes or not 0 < len(pcm) <= MAX_PAYLOAD or len(pcm) % 2:
            raise AudioSocketError("protocol_rejected")
        async with self._lock:
            if self._closed:
                raise AudioSocketError("stopped")
            self._guard()
            if self._sent_bytes + len(pcm) > self._limits.max_pcm_bytes:
                raise AudioSocketError("protocol_rejected")
            loop = asyncio.get_running_loop()
            await asyncio.sleep(max(0.0, self._next_audio - loop.time()))
            if self._closed:
                raise AudioSocketError("stopped")
            if self._guard() < len(pcm) / 16000:
                raise AudioSocketError("deadline")
            self._writer.write(b"\x10" + len(pcm).to_bytes(2, "big") + pcm)
            async with asyncio.timeout(self._limits.frame_seconds):
                await self._writer.drain()
            self._sent_bytes += len(pcm)
            if any(pcm):
                self._non_silent_frames += 1
            self._next_audio = loop.time() + len(pcm) / 16000


class AudioSocketServer:
    """One active loopback connection; disabled unless explicitly started.

    The caller must independently authenticate/authorize the SIP channel before
    issuing its stream UUID through AudioSocketAdmission. No SIP listener or paid
    provider client is installed or configured by this class.
    """

    def __init__(
        self,
        admission: AudioSocketAdmission,
        handler: AudioSocketHandler,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        limits: AudioSocketLimits | None = None,
        clock: Clock = _utc_now,
    ) -> None:
        try:
            address = ip_address(host)
        except ValueError:
            raise ValueError("numeric_loopback_and_valid_port_required") from None
        if not address.is_loopback or type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("numeric_loopback_and_valid_port_required")
        self._admission = admission
        self._handler = handler
        self._host = host
        self._port = port
        self._limits = limits or AudioSocketLimits()
        self._clock = clock
        self._server: asyncio.Server | None = None
        self._active: asyncio.Task[None] | None = None
        self._lifecycle = asyncio.Lock()
        self._accepting = False
        self.last_receipt: AudioSocketReceipt | None = None

    @property
    def port(self) -> int:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("audiosocket_not_started")
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self, *, enabled: bool = False) -> None:
        if enabled is not True:
            raise RuntimeError("audiosocket_deployment_disabled")
        async with self._lifecycle:
            if self._server is not None:
                raise RuntimeError("audiosocket_already_started")
            self._server = await asyncio.start_server(
                self._connected,
                host=self._host,
                port=self._port,
                limit=MAX_PAYLOAD + 3,
                backlog=1,
            )
            self._accepting = True

    def _connected(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        if (
            not self._accepting
            or not peer
            or not ip_address(peer[0]).is_loopback
            or self._active is not None
        ):
            writer.close()
            return
        task = asyncio.create_task(self._serve(reader, writer))
        self._active = task
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task[None]) -> None:
        if self._active is task:
            self._active = None
        if not task.cancelled():
            task.exception()  # Retrieve a callback failure without logging its body.

    async def close(self) -> None:
        async with self._lifecycle:
            self._accepting = False
            server = self._server
            self._server = None
            if server is not None:
                server.close()
            task = self._active
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if server is not None:
                await server.wait_closed()

    async def _frame(self, reader: asyncio.StreamReader, timeout: float) -> tuple[int, bytes]:
        async with asyncio.timeout(timeout):
            header = await reader.readexactly(3)
            length = int.from_bytes(header[1:], "big")
            if length > MAX_PAYLOAD:
                raise AudioSocketError("protocol_rejected")
            return header[0], await reader.readexactly(length)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        grant: AudioSocketGrant | None = None
        sink: AudioSocketPCM | None = None
        outcome: Outcome = "transport_failed"
        admitted = False
        frames = pcm_frames = pcm_bytes = dtmf = 0
        try:
            kind, payload = await self._frame(reader, self._limits.handshake_seconds)
            if kind != 1 or len(payload) != 16:
                raise AudioSocketError("protocol_rejected")
            now = require_utc(self._clock())
            stream_id = UUID(bytes=payload)
            grant = self._admission.claim(stream_id, now)
            if grant is None or grant.stream_id != stream_id:
                grant = None
                raise AudioSocketError("binding_rejected")
            if not grant.issued_at <= now < grant.expires_at:
                raise AudioSocketError("binding_rejected")
            loop = asyncio.get_running_loop()
            deadline = loop.time() + min(MAX_LIFETIME, (grant.expires_at - now).total_seconds())
            last_now = now
            accepted = grant
            failure: Outcome | None = None

            def guard() -> float:
                nonlocal last_now, failure
                if failure is not None:
                    raise AudioSocketError(failure)
                try:
                    observed = require_utc(self._clock())
                    if observed < last_now:
                        failure = "authority_lost"
                    elif (
                        loop.time() >= deadline
                        or not accepted.issued_at <= observed < accepted.expires_at
                    ):
                        failure = "deadline"
                    elif self._admission.is_current(accepted, observed) is not True:
                        failure = "authority_lost"
                except Exception:
                    failure = "authority_lost"
                    raise AudioSocketError(failure) from None
                last_now = observed
                if failure is not None:
                    raise AudioSocketError(failure)
                return min((accepted.expires_at - observed).total_seconds(), deadline - loop.time())

            guard()
            admitted = True
            sink = AudioSocketPCM(writer, guard, self._limits)
            async with asyncio.timeout_at(deadline):
                try:
                    async with asyncio.timeout(self._limits.callback_seconds):
                        await self._handler.on_open(grant, sink)
                    while True:
                        guard()
                        try:
                            kind, payload = await self._frame(reader, self._limits.frame_seconds)
                        except TimeoutError:
                            raise AudioSocketError("idle_timeout") from None
                        guard()
                        if kind == 0 and not payload:
                            outcome = "peer_hangup"
                            break
                        frames += 1
                        if frames > self._limits.max_frames:
                            raise AudioSocketError("protocol_rejected")
                        if kind == 0x10 and payload and len(payload) % 2 == 0:
                            if pcm_bytes + len(payload) > self._limits.max_pcm_bytes:
                                raise AudioSocketError("protocol_rejected")
                            pcm_bytes += len(payload)
                            pcm_frames += 1
                            async with asyncio.timeout(self._limits.callback_seconds):
                                await self._handler.on_pcm(grant, payload, sink)
                        elif kind == 3 and len(payload) == 1 and payload in b"0123456789*#ABCD":
                            if dtmf >= self._limits.max_dtmf:
                                raise AudioSocketError("protocol_rejected")
                            dtmf += 1
                            async with asyncio.timeout(self._limits.callback_seconds):
                                await self._handler.on_dtmf(grant, payload.decode("ascii"))
                        else:
                            raise AudioSocketError("protocol_rejected")
                except TimeoutError:
                    raise AudioSocketError("handler_failed") from None
        except AudioSocketError as error:
            outcome = error.code
        except TimeoutError:
            outcome = "deadline" if grant is not None else "idle_timeout"
        except asyncio.IncompleteReadError:
            outcome = "peer_eof"
        except asyncio.CancelledError:
            outcome = "stopped"
        except (ConnectionError, OSError):
            outcome = "transport_failed"
        except Exception:
            outcome = "handler_failed"
        finally:
            if sink is not None:
                sink.close()
            receipt = AudioSocketReceipt(
                outcome=outcome,
                admitted=admitted,
                pcm_frames=pcm_frames,
                received_pcm_bytes=pcm_bytes,
                sent_pcm_bytes=sink.sent_bytes if sink is not None else 0,
                dtmf_count=dtmf,
            )
            self.last_receipt = receipt
            writer.close()
            try:
                async with asyncio.timeout(self._limits.close_seconds):
                    await writer.wait_closed()
            except (TimeoutError, ConnectionError, OSError):
                pass
            finally:
                # Server shutdown can cancel wait_closed after the terminal frame was read.
                if admitted:
                    try:
                        async with asyncio.timeout(self._limits.close_seconds):
                            await self._handler.on_close(receipt)
                    except Exception:
                        pass
