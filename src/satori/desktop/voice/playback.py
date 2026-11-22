"""Owned, bounded transient PCM delivery through Qt's native writable device."""

from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from time import monotonic
from typing import Literal

from PySide6.QtCore import QIODevice, QObject, QTimer, Signal
from PySide6.QtMultimedia import QAudioDevice, QAudioFormat, QAudioSink, QMediaDevices, QtAudio

MAX_PLAYBACK_SECONDS = 120
MAX_PCM_BYTES = MAX_PLAYBACK_SECONDS * 48_000 * 2
MAX_PLAYBACK_WALL_SECONDS = MAX_PLAYBACK_SECONDS * 2 + 30
_PREFERRED_RATES = (24_000, 48_000, 44_100, 16_000)
_PUMP_INTERVAL_MS = 20
_MAX_WRITES_PER_PUMP = 16
_MAX_WRITE_BYTES = 65_536
PlaybackErrorCode = Literal[
    "playback_unavailable",
    "playback_format_unsupported",
    "playback_audio_invalid",
    "playback_overflow",
    "playback_failed",
]


def _format(sample_rate: int) -> QAudioFormat:
    result = QAudioFormat()
    result.setSampleRate(sample_rate)
    result.setChannelCount(1)
    result.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    return result


@dataclass(eq=False)
class _Playback:
    generation: int
    sample_rate: int
    sink: QAudioSink
    timer: QTimer
    stream: QIODevice | None = None
    pending: deque[bytes] = field(default_factory=deque, repr=False)
    offset: int = 0
    queued_bytes: int = 0
    total_bytes: int = 0
    input_done: bool = False
    first_written: bool = False
    pumping: bool = False
    started_at: float | None = None


class PcmPlayback(QObject):
    """Play committed speech only when supplied PCM; never create files or capture audio."""

    failed = Signal(str)
    finished = Signal()
    first_buffer_written = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._generation = 0
        self._playback: _Playback | None = None

    def preferred_sample_rate(self) -> int | None:
        """Inspect supported mono PCM16 output formats without opening an audio stream."""

        try:
            device = QMediaDevices.defaultAudioOutput()
            if not device.isNull():
                for sample_rate in _PREFERRED_RATES:
                    if device.isFormatSupported(_format(sample_rate)):
                        return sample_rate
        except (OSError, RuntimeError):
            pass
        return None

    def start(self, rate: int) -> bool:
        """Prepare one fresh output owner; native playback waits for nonempty append()."""

        self.cancel()
        if type(rate) is not int or not 16_000 <= rate <= 48_000:
            self.failed.emit("playback_format_unsupported")
            return False
        try:
            device: QAudioDevice = QMediaDevices.defaultAudioOutput()
            if device.isNull():
                self.failed.emit("playback_unavailable")
                return False
            audio_format = _format(rate)
            if not device.isFormatSupported(audio_format):
                self.failed.emit("playback_format_unsupported")
                return False
            sink = QAudioSink(device, audio_format, self)
        except (OSError, RuntimeError):
            self.failed.emit("playback_unavailable")
            return False
        timer = QTimer(self)
        job = _Playback(self._generation, rate, sink, timer)
        self._playback = job
        try:
            if sink.isNull():
                self._fail(job, "playback_unavailable")
                return False
            sink.setBufferSize(rate * 2 // 5)
            timer.setInterval(_PUMP_INTERVAL_MS)
            timer.timeout.connect(lambda: self._pump(job))
            sink.stateChanged.connect(lambda state: self._state_changed(job, state))
        except (OSError, RuntimeError):
            self._fail(job, "playback_unavailable")
            return False
        return True

    def append(self, pcm: bytes) -> bool:
        """Queue PCM16LE mono frames, preserving bytes through partial native writes."""

        job = self._playback
        if job is None:
            return False
        if job.input_done or not isinstance(pcm, bytes) or len(pcm) % 2:
            self._fail(job, "playback_audio_invalid")
            return False
        if not pcm:
            return True
        if (
            job.total_bytes + len(pcm) > job.sample_rate * 2 * MAX_PLAYBACK_SECONDS
            or job.queued_bytes + len(pcm) > MAX_PCM_BYTES
        ):
            self._fail(job, "playback_overflow")
            return False
        job.pending.append(pcm)
        job.queued_bytes += len(pcm)
        job.total_bytes += len(pcm)
        if job.stream is None:
            try:
                stream = job.sink.start()
                if not self._current(job):
                    return False
                if stream is None:
                    self._fail(job, "playback_unavailable")
                    return False
                job.stream = stream
                job.started_at = monotonic()
                job.timer.start()
            except (OSError, RuntimeError):
                self._fail(job, "playback_unavailable")
                return False
        self._pump(job)
        return self._current(job)

    def finish_input(self) -> None:
        """Mark end of synthesis; finished means both the queue and native sink drained."""

        job = self._playback
        if job is not None:
            job.input_done = True
            if job.stream is None:
                self._complete(job)
            else:
                self._pump(job)

    def cancel(self) -> None:
        """Invalidate callbacks and immediately discard queued and native buffered audio."""

        job = self._playback
        if job is not None:
            self._release(job, drain=False)
        else:
            self._generation += 1

    def _current(self, job: _Playback) -> bool:
        return self._playback is job and job.generation == self._generation

    def _state_changed(self, job: _Playback, state: QtAudio.State) -> None:
        if not self._current(job):
            return
        try:
            if job.sink.error() not in {QtAudio.Error.NoError, QtAudio.Error.UnderrunError} or (
                state == QtAudio.State.StoppedState and job.stream is not None
            ):
                self._fail(job, "playback_failed")
            elif state in {QtAudio.State.ActiveState, QtAudio.State.IdleState}:
                self._pump(job)
        except (OSError, RuntimeError):
            self._fail(job, "playback_failed")

    def _pump(self, job: _Playback) -> None:
        if not self._current(job) or job.stream is None or job.pumping:
            return
        job.pumping = True
        try:
            if job.started_at is not None and (
                monotonic() - job.started_at > MAX_PLAYBACK_WALL_SECONDS
            ):
                self._fail(job, "playback_failed")
                return
            if job.sink.error() not in {QtAudio.Error.NoError, QtAudio.Error.UnderrunError}:
                self._fail(job, "playback_failed")
                return
            state = job.sink.state()
            if state == QtAudio.State.StoppedState:
                self._fail(job, "playback_failed")
                return
            if state == QtAudio.State.SuspendedState:
                return
            for _ in range(_MAX_WRITES_PER_PUMP):
                if not job.pending:
                    break
                free = job.sink.bytesFree()
                if free <= 0:
                    break
                chunk = job.pending[0]
                payload = chunk[job.offset : job.offset + min(free, _MAX_WRITE_BYTES)]
                written = job.stream.write(payload)
                if not self._current(job):
                    return
                if written < 0 or written > len(payload):
                    self._fail(job, "playback_failed")
                    return
                if written == 0:
                    break
                job.offset += written
                job.queued_bytes -= written
                if job.offset == len(chunk):
                    job.pending.popleft()
                    job.offset = 0
                if not job.first_written:
                    job.first_written = True
                    self.first_buffer_written.emit()
                    if not self._current(job):
                        return
            if job.input_done and not job.pending and job.sink.state() == QtAudio.State.IdleState:
                self._complete(job)
        except (OSError, RuntimeError):
            self._fail(job, "playback_failed")
        finally:
            job.pumping = False

    def _release(self, job: _Playback, *, drain: bool) -> bool:
        if not self._current(job):
            return False
        self._playback = None
        self._generation += 1
        job.pending.clear()
        job.offset = job.queued_bytes = job.total_bytes = 0
        job.timer.stop()
        job.timer.deleteLater()
        success = True
        try:
            if drain and job.stream is not None:
                # On Darwin stop() drains the native buffer; reset() would truncate its tail.
                job.sink.stop()
            else:
                # stop() can synchronously drain on Darwin; cancellation must discard instead.
                job.sink.reset()
        except (OSError, RuntimeError):
            success = False
            with suppress(OSError, RuntimeError):
                job.sink.reset()
        job.stream = None
        job.sink.deleteLater()
        return success

    def _complete(self, job: _Playback) -> None:
        if self._current(job):
            if self._release(job, drain=True):
                self.finished.emit()
            else:
                self.failed.emit("playback_failed")

    def _fail(self, job: _Playback, code: PlaybackErrorCode) -> None:
        if self._current(job):
            self._release(job, drain=False)
            self.failed.emit(code)
