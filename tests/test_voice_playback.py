"""PCM output uses fake hardware; no test opens a real speaker or microphone."""

import os
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QIODevice, QObject, Signal
from PySide6.QtMultimedia import QAudioFormat, QtAudio
from PySide6.QtWidgets import QApplication

from satori.desktop.voice import playback as playback_module
from satori.desktop.voice.playback import PcmPlayback


class FakeDevice:
    def __init__(self, rates: set[int] | None = None) -> None:
        self.rates = {24_000, 48_000, 44_100, 16_000} if rates is None else rates
        self.missing = False
        self.queries: list[int] = []

    def isNull(self) -> bool:
        return self.missing

    def isFormatSupported(self, audio_format: QAudioFormat) -> bool:
        assert audio_format.channelCount() == 1
        assert audio_format.sampleFormat() == QAudioFormat.SampleFormat.Int16
        self.queries.append(audio_format.sampleRate())
        return audio_format.sampleRate() in self.rates


class FakeStream:
    def __init__(self, sink: "FakeSink") -> None:
        self.sink = sink
        self.accepted = bytearray()
        self.write_limit: int | None = None
        self.result_override: int | None = None

    def write(self, pcm: bytes) -> int:
        assert isinstance(pcm, bytes)
        if self.result_override is not None:
            return self.result_override
        amount = min(len(pcm), self.sink.bytesFree())
        if self.write_limit is not None:
            amount = min(amount, self.write_limit)
        if amount:
            self.accepted.extend(pcm[:amount])
            self.sink.buffered += amount
            self.sink.change_state(QtAudio.State.ActiveState)
        return amount


class FakeSink(QObject):
    stateChanged = Signal(object)

    def __init__(self, audio_format: QAudioFormat, parent: QObject) -> None:
        super().__init__(parent)
        self.audio_format = audio_format
        self.stream = FakeStream(self)
        self.native_stream: QBuffer | None = None
        self.capacity = 65_536
        self.buffered = 0
        self.started = 0
        self.stopped = 0
        self.reset_count = 0
        self.requested_buffer_size: int | None = None
        self.null = False
        self.open_fails = False
        self.return_no_stream = False
        self.current_state = QtAudio.State.StoppedState
        self.current_error = QtAudio.Error.NoError

    def isNull(self) -> bool:
        return self.null

    def setBufferSize(self, size: int) -> None:
        self.requested_buffer_size = size

    def start(self) -> FakeStream | QBuffer | None:
        self.started += 1
        if self.open_fails:
            self.current_error = QtAudio.Error.OpenError
            self.change_state(QtAudio.State.StoppedState)
        else:
            self.change_state(QtAudio.State.IdleState)
        if self.return_no_stream:
            return None
        return self.native_stream or self.stream

    def stop(self) -> None:
        self.stopped += 1
        self.buffered = 0
        self.current_error = QtAudio.Error.NoError
        self.change_state(QtAudio.State.StoppedState)

    def reset(self) -> None:
        self.reset_count += 1
        self.buffered = 0
        self.current_error = QtAudio.Error.NoError
        self.change_state(QtAudio.State.StoppedState)

    def state(self) -> QtAudio.State:
        return self.current_state

    def error(self) -> QtAudio.Error:
        return self.current_error

    def bytesFree(self) -> int:
        return max(0, self.capacity - self.buffered)

    def change_state(self, state: QtAudio.State) -> None:
        self.current_state = state
        self.stateChanged.emit(state)

    def drain(self) -> None:
        self.buffered = 0
        self.change_state(QtAudio.State.IdleState)


class Hardware:
    def __init__(self) -> None:
        self.device = FakeDevice()
        self.sinks: list[FakeSink] = []

    def create_sink(
        self, device: FakeDevice, audio_format: QAudioFormat, parent: QObject
    ) -> FakeSink:
        assert device is self.device
        sink = FakeSink(audio_format, parent)
        self.sinks.append(sink)
        return sink


@pytest.fixture(scope="module")
def application() -> QApplication:
    from satori.desktop.ui import DesktopApplication

    return QApplication.instance() or DesktopApplication([])  # type: ignore[return-value]


@pytest.fixture
def hardware(monkeypatch: pytest.MonkeyPatch) -> Hardware:
    result = Hardware()
    monkeypatch.setattr(
        playback_module,
        "QMediaDevices",
        SimpleNamespace(defaultAudioOutput=lambda: result.device),
    )
    monkeypatch.setattr(playback_module, "QAudioSink", result.create_sink)
    return result


@pytest.fixture
def playback(application: QApplication, hardware: Hardware) -> Iterator[PcmPlayback]:
    del hardware
    result = PcmPlayback()
    yield result
    result.cancel()
    result.deleteLater()
    application.processEvents()


@pytest.mark.parametrize(
    ("rates", "preferred"),
    [
        ({24_000, 48_000, 44_100, 16_000}, 24_000),
        ({48_000, 44_100, 16_000}, 48_000),
        ({44_100, 16_000}, 44_100),
        ({16_000}, 16_000),
        (set(), None),
    ],
)
def test_preferred_rate_only_queries_supported_mono_pcm_without_opening_output(
    playback: PcmPlayback, hardware: Hardware, rates: set[int], preferred: int | None
) -> None:
    hardware.device.rates = rates
    assert playback.preferred_sample_rate() == preferred
    assert hardware.sinks == []


def test_prepare_and_empty_append_do_not_start_a_speaker(
    playback: PcmPlayback, hardware: Hardware
) -> None:
    assert playback.start(24_000)
    sink = hardware.sinks[0]
    assert sink.started == 0
    assert playback.append(b"")
    assert sink.started == 0
    assert sink.audio_format.sampleRate() == 24_000
    assert sink.requested_buffer_size == 9600


def test_empty_input_finishes_without_opening_output(
    playback: PcmPlayback, hardware: Hardware
) -> None:
    done: list[bool] = []
    playback.finished.connect(lambda: done.append(True))
    assert playback.start(24_000)
    playback.finish_input()
    playback.finish_input()
    assert done == [True]
    assert hardware.sinks[0].started == 0
    assert playback._playback is None


def test_partial_writes_and_backpressure_preserve_every_binary_byte(
    playback: PcmPlayback, hardware: Hardware
) -> None:
    first: list[bool] = []
    done: list[bool] = []
    playback.first_buffer_written.connect(lambda: first.append(True))
    playback.finished.connect(lambda: done.append(True))
    assert playback.start(24_000)
    sink = hardware.sinks[0]
    sink.capacity = 64
    sink.stream.write_limit = 3
    pcm = bytes(range(256))
    assert playback.append(pcm)
    job = playback._playback
    assert job is not None
    assert job.timer.isActive()
    assert job.timer.interval() == 20
    assert job.queued_bytes > 0
    playback.finish_input()
    for _ in range(20):
        sink.drain()
        if playback._playback is None:
            break
        job.timer.timeout.emit()
    assert bytes(sink.stream.accepted) == pcm
    assert first == [True]
    assert done == [True]
    assert sink.stopped == 1
    assert sink.reset_count == 0
    assert not job.pending
    assert job.queued_bytes == 0


def test_real_native_qbuffer_write_preserves_null_and_non_ascii_pcm(
    playback: PcmPlayback, hardware: Hardware
) -> None:
    assert playback.start(24_000)
    sink = hardware.sinks[0]
    stream = QBuffer(sink)
    assert stream.open(QIODevice.OpenModeFlag.ReadWrite)
    sink.native_stream = stream
    pcm = bytes(range(256))
    assert playback.append(pcm)
    assert stream.data().data() == pcm
    playback.finish_input()
    assert playback._playback is None


def test_idle_underrun_does_not_finish_before_input_eos(
    playback: PcmPlayback, hardware: Hardware
) -> None:
    done: list[bool] = []
    playback.finished.connect(lambda: done.append(True))
    assert playback.start(24_000)
    sink = hardware.sinks[0]
    assert playback.append(b"\x00\x01" * 4)
    sink.current_error = QtAudio.Error.UnderrunError
    sink.drain()
    assert done == []
    assert playback.append(b"\x02\x03" * 4)
    sink.drain()
    assert done == []
    playback.finish_input()
    assert done == [True]
    assert bytes(sink.stream.accepted) == b"\x00\x01" * 4 + b"\x02\x03" * 4


def test_eos_waits_for_native_drain_before_finished(
    playback: PcmPlayback, hardware: Hardware
) -> None:
    order: list[str] = []
    playback.finished.connect(lambda: order.append("finished"))
    assert playback.start(24_000)
    sink = hardware.sinks[0]
    assert playback.append(b"\x00\x01" * 4)
    playback.finish_input()
    assert order == []
    assert sink.stopped == 0
    sink.drain()
    assert order == ["finished"]
    assert sink.stopped == 1
    assert playback._playback is None


def test_zero_write_retries_without_premature_first_buffer_or_completion(
    playback: PcmPlayback, hardware: Hardware
) -> None:
    events: list[str] = []
    playback.first_buffer_written.connect(lambda: events.append("first"))
    playback.finished.connect(lambda: events.append("finished"))
    assert playback.start(24_000)
    sink = hardware.sinks[0]
    sink.stream.result_override = 0
    assert playback.append(b"\x01\x02")
    playback.finish_input()
    assert events == []
    job = playback._playback
    assert job is not None
    sink.stream.result_override = None
    job.timer.timeout.emit()
    assert events == ["first"]
    sink.drain()
    assert events == ["first", "finished"]


@pytest.mark.parametrize("rate", [0, 8000, 48_001, True])
def test_invalid_rate_has_a_static_failure_without_a_sink(
    playback: PcmPlayback, hardware: Hardware, rate: int
) -> None:
    errors: list[str] = []
    playback.failed.connect(errors.append)
    assert not playback.start(rate)
    assert errors == ["playback_format_unsupported"]
    assert hardware.sinks == []


def test_absent_or_unsupported_device_never_starts_output(
    playback: PcmPlayback, hardware: Hardware
) -> None:
    errors: list[str] = []
    playback.failed.connect(errors.append)
    hardware.device.missing = True
    assert playback.preferred_sample_rate() is None
    assert not playback.start(24_000)
    hardware.device.missing = False
    hardware.device.rates = {16_000}
    assert not playback.start(24_000)
    assert errors == ["playback_unavailable", "playback_format_unsupported"]
    assert hardware.sinks == []


@pytest.mark.parametrize("fault", ["open_error", "no_stream", "write_error", "hardware_error"])
def test_native_errors_fail_closed_without_exposing_vendor_data(
    playback: PcmPlayback, hardware: Hardware, fault: str
) -> None:
    errors: list[str] = []
    done: list[bool] = []
    playback.failed.connect(errors.append)
    playback.finished.connect(lambda: done.append(True))
    assert playback.start(24_000)
    sink = hardware.sinks[0]
    if fault == "open_error":
        sink.open_fails = True
    elif fault == "no_stream":
        sink.return_no_stream = True
    elif fault == "write_error":
        sink.stream.result_override = -1
    assert playback.append(b"\x00\x01") is (fault == "hardware_error")
    if fault == "hardware_error":
        sink.current_error = QtAudio.Error.IOError
        sink.change_state(QtAudio.State.StoppedState)
    assert errors == ["playback_unavailable" if fault == "no_stream" else "playback_failed"]
    assert done == []
    assert playback._playback is None
    assert sink.reset_count == 1


def test_cancel_discards_native_buffer_instead_of_synchronously_playing_it(
    playback: PcmPlayback, hardware: Hardware
) -> None:
    events: list[str] = []
    playback.failed.connect(events.append)
    playback.finished.connect(lambda: events.append("finished"))
    assert playback.start(24_000)
    sink = hardware.sinks[0]
    sink.capacity = 2
    assert playback.append(b"\x01\x02" * 100)
    job = playback._playback
    assert job is not None
    assert job.pending
    playback.cancel()
    assert sink.reset_count == 1
    assert sink.stopped == 0
    assert not job.pending
    assert not job.timer.isActive()
    assert job.stream is None
    assert playback._playback is None
    assert not playback.append(b"\x01\x02")
    assert events == []


def test_replacement_ignores_old_native_and_timer_callbacks(
    playback: PcmPlayback, hardware: Hardware
) -> None:
    events: list[str] = []
    playback.failed.connect(events.append)
    playback.finished.connect(lambda: events.append("finished"))
    assert playback.start(24_000)
    assert playback.append(b"\x00\x01")
    old_job = playback._playback
    assert old_job is not None
    old_sink = hardware.sinks[0]
    assert playback.start(48_000)
    assert old_sink.reset_count == 1
    current = playback._playback
    old_sink.current_error = QtAudio.Error.FatalError
    old_sink.change_state(QtAudio.State.StoppedState)
    old_job.timer.timeout.emit()
    assert playback._playback is current
    assert events == []
    assert playback.append(b"\x02\x03")
    assert bytes(hardware.sinks[1].stream.accepted) == b"\x02\x03"


def test_cancellation_from_first_buffer_callback_cannot_continue_writing(
    playback: PcmPlayback, hardware: Hardware
) -> None:
    assert playback.start(24_000)
    sink = hardware.sinks[0]
    sink.stream.write_limit = 2
    playback.first_buffer_written.connect(playback.cancel)
    assert not playback.append(b"\x01\x02" * 100)
    assert bytes(sink.stream.accepted) == b"\x01\x02"
    assert sink.reset_count == 1
    assert playback._playback is None


def test_total_duration_cap_includes_audio_already_written_and_drained(
    playback: PcmPlayback, hardware: Hardware, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(playback_module, "MAX_PLAYBACK_SECONDS", 1)
    errors: list[str] = []
    playback.failed.connect(errors.append)
    assert playback.start(16_000)
    sink = hardware.sinks[0]
    assert playback.append(b"\x01\x02" * 16_000)
    sink.drain()
    job = playback._playback
    assert job is not None
    assert job.queued_bytes == 0
    assert not playback.append(b"\x01\x02")
    assert errors == ["playback_overflow"]
    assert playback._playback is None


def test_resident_queue_cap_is_independent_of_output_duration(
    playback: PcmPlayback, hardware: Hardware, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(playback_module, "MAX_PCM_BYTES", 8)
    errors: list[str] = []
    playback.failed.connect(errors.append)
    assert playback.start(24_000)
    assert not playback.append(b"\x01\x02" * 5)
    assert errors == ["playback_overflow"]
    assert hardware.sinks[0].started == 0


@pytest.mark.parametrize("invalid", [b"\x01", "private text", None])
def test_invalid_pcm_cannot_be_sent_to_the_native_device(
    playback: PcmPlayback, hardware: Hardware, invalid: Any
) -> None:
    errors: list[str] = []
    playback.failed.connect(errors.append)
    assert playback.start(24_000)
    assert not playback.append(invalid)
    assert errors == ["playback_audio_invalid"]
    assert hardware.sinks[0].started == 0


def test_data_after_eos_is_rejected_without_replaying_the_committed_reply(
    playback: PcmPlayback, hardware: Hardware
) -> None:
    errors: list[str] = []
    playback.failed.connect(errors.append)
    assert playback.start(24_000)
    assert playback.append(b"\x01\x02")
    playback.finish_input()
    assert not playback.append(b"\x03\x04")
    assert errors == ["playback_audio_invalid"]
    assert bytes(hardware.sinks[0].stream.accepted) == b"\x01\x02"


def test_suspended_or_stalled_hardware_cannot_keep_a_stream_alive_forever(
    playback: PcmPlayback, hardware: Hardware, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [100.0]
    monkeypatch.setattr(playback_module, "monotonic", lambda: clock[0])
    errors: list[str] = []
    playback.failed.connect(errors.append)
    assert playback.start(24_000)
    sink = hardware.sinks[0]
    assert playback.append(b"\x01\x02")
    job = playback._playback
    assert job is not None
    sink.change_state(QtAudio.State.SuspendedState)
    clock[0] += playback_module.MAX_PLAYBACK_WALL_SECONDS + 1
    job.timer.timeout.emit()
    assert errors == ["playback_failed"]
    assert playback._playback is None
    assert sink.reset_count == 1


def test_missing_native_backend_releases_its_prepared_sink(
    playback: PcmPlayback, hardware: Hardware, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(FakeSink, "isNull", lambda self: True)
    errors: list[str] = []
    playback.failed.connect(errors.append)
    assert not playback.start(24_000)
    assert errors == ["playback_unavailable"]
    assert hardware.sinks[0].started == 0
    assert hardware.sinks[0].reset_count == 1
    assert playback._playback is None


def test_constructor_failure_never_exposes_vendor_error_text(
    playback: PcmPlayback, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(*args: Any) -> None:
        del args
        raise RuntimeError("private vendor exception must not reach the interface")

    monkeypatch.setattr(playback_module, "QAudioSink", unavailable)
    errors: list[str] = []
    playback.failed.connect(errors.append)
    assert not playback.start(24_000)
    assert errors == ["playback_unavailable"]
    assert playback._playback is None


def test_failure_to_drain_is_not_reported_as_successful_completion(
    playback: PcmPlayback, hardware: Hardware, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    playback.failed.connect(events.append)
    playback.finished.connect(lambda: events.append("finished"))
    assert playback.start(24_000)
    sink = hardware.sinks[0]
    assert playback.append(b"\x01\x02")

    def cannot_drain() -> None:
        raise RuntimeError("private vendor exception")

    monkeypatch.setattr(sink, "stop", cannot_drain)
    playback.finish_input()
    sink.drain()
    assert events == ["playback_failed"]
    assert playback._playback is None
    assert sink.reset_count == 1
