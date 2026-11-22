"""Voice lifecycle checks use synthetic bytes, fake hardware and owned Python children."""

import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from time import monotonic
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QIODevice, QObject, QProcess, Qt, Signal
from PySide6.QtMultimedia import QAudioDevice, QAudioFormat, QMediaDevices, QtAudio
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from satori.desktop.voice import controller as voice_module
from satori.desktop.voice.controller import VoiceController, _PcmBuffer


class FakeSource(QObject):
    stateChanged = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.stream = QBuffer(self)
        self.stream.open(QIODevice.OpenModeFlag.ReadOnly)
        self.stopped = False

    def start(self) -> QIODevice:
        return self.stream

    def stop(self) -> None:
        self.stopped = True

    def error(self) -> QtAudio.Error:
        return QtAudio.Error.NoError


@pytest.fixture(scope="module")
def application() -> QApplication:
    from satori.desktop.ui import DesktopApplication

    return QApplication.instance() or DesktopApplication([])  # type: ignore[return-value]


@pytest.fixture
def controller(application: QApplication) -> Iterator[VoiceController]:
    result = VoiceController()
    yield result
    result.cancel()
    wait_until(lambda: not result._retired)
    application.processEvents()
    result.deleteLater()


def wait_until(predicate: Any) -> None:
    deadline = monotonic() + 5
    while not predicate() and monotonic() < deadline:
        QTest.qWait(10)
    assert predicate()


def hardware(
    controller: VoiceController,
    monkeypatch: pytest.MonkeyPatch,
    permission: Qt.PermissionStatus = Qt.PermissionStatus.Granted,
) -> list[FakeSource]:
    sources: list[FakeSource] = []
    audio_format = QAudioFormat()
    audio_format.setSampleRate(16_000)
    audio_format.setChannelCount(1)
    audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    monkeypatch.setattr(controller, "_permission_status", lambda: permission)
    monkeypatch.setattr(controller, "_audio_configuration", lambda: (QAudioDevice(), audio_format))

    def create(*args: Any) -> FakeSource:
        del args
        source = FakeSource()
        sources.append(source)
        return source

    monkeypatch.setattr(controller, "_create_audio_source", create)
    return sources


def synthetic_worker(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch, path: Path
) -> None:
    path.write_text(
        "import io,json,sys,time,wave\n"
        "data=sys.stdin.buffer.read()\n"
        "with wave.open(io.BytesIO(data)) as w:\n"
        " assert w.getframerate()==16000 and w.getsampwidth()==2 and w.getnchannels()==1\n"
        "time.sleep(float(sys.argv[2]))\n"
        "response={'event':'result','result':{'text':sys.argv[1],'language':'ru',"
        "'engine':'gigaam-v3-e2e-rnnt/int8','audio_duration_ms':20.,'model_load_ms':1.,"
        "'transcription_ms':2.}}\n"
        "print(json.dumps(response),flush=True)\n"
    )
    monkeypatch.setattr(
        controller, "_arguments", lambda kind, directory: [str(path), directory.name, "0.05"]
    )


def test_initialization_does_not_request_permission_or_capture(
    application: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    del application
    calls: list[str] = []
    monkeypatch.setattr(
        VoiceController, "_permission_status", lambda self: calls.append("permission")
    )
    monkeypatch.setattr(
        VoiceController, "_audio_configuration", lambda self: calls.append("device")
    )
    result = VoiceController()
    assert calls == []
    assert result.activity == "idle"
    assert result._capture is None
    assert result._transcription is result._speech is None
    result.deleteLater()


def test_denied_permission_never_opens_hardware(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sources = hardware(controller, monkeypatch, Qt.PermissionStatus.Denied)
    statuses: list[str] = []
    controller.status_changed.connect(statuses.append)
    controller.begin_capture("draft", tmp_path)
    assert sources == []
    assert statuses == ["microphone_permission_denied"]


@pytest.mark.parametrize("supported", [16_000, 48_000, 44_100, None])
def test_capture_negotiates_only_supported_mono_pcm16_without_opening_device(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch, supported: int | None
) -> None:
    checked: list[int] = []

    class Device:
        def isNull(self) -> bool:
            return False

        def isFormatSupported(self, audio_format: QAudioFormat) -> bool:
            assert audio_format.channelCount() == 1
            assert audio_format.sampleFormat() == QAudioFormat.SampleFormat.Int16
            checked.append(audio_format.sampleRate())
            return audio_format.sampleRate() == supported

    monkeypatch.setattr(QMediaDevices, "defaultAudioInput", lambda: Device())
    configuration = controller._audio_configuration()
    if supported is None:
        assert configuration is None
        assert checked == [16_000, 48_000, 44_100]
    else:
        assert configuration is not None
        assert configuration[1].sampleRate() == supported


def test_permission_grant_requires_a_new_physical_press(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sources = hardware(controller, monkeypatch, Qt.PermissionStatus.Undetermined)
    requested: list[int] = []

    def request(generation: int) -> None:
        controller._permission_request = generation
        requested.append(generation)

    monkeypatch.setattr(controller, "_request_permission", request)
    statuses: list[str] = []
    controller.status_changed.connect(statuses.append)
    controller.begin_capture("draft", tmp_path)
    assert len(requested) == 1
    permission = type("Granted", (), {"status": lambda self: Qt.PermissionStatus.Granted})()
    controller._permission_received(requested[0], permission)
    assert not sources
    assert statuses[-1] == "microphone_permission_granted_press_again"
    monkeypatch.setattr(controller, "_permission_status", lambda: Qt.PermissionStatus.Granted)
    controller.begin_capture("new-press", tmp_path)
    assert len(sources) == 1
    assert controller.activity == "capturing"


def test_cancelled_permission_callback_never_starts_or_reports_capture(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sources = hardware(controller, monkeypatch, Qt.PermissionStatus.Undetermined)
    monkeypatch.setattr(
        controller,
        "_request_permission",
        lambda generation: setattr(controller, "_permission_request", generation),
    )
    statuses: list[str] = []
    controller.status_changed.connect(statuses.append)
    controller.begin_capture("old", tmp_path)
    requested = controller._permission_request
    assert requested is not None
    controller.cancel()
    permission = type("Granted", (), {"status": lambda self: Qt.PermissionStatus.Granted})()
    controller._permission_received(requested, permission)
    assert statuses == ["microphone_permission_requested"]
    assert not sources
    assert controller.activity == "idle"


def test_capture_buffer_enforces_hard_bound_before_appending(controller: VoiceController) -> None:
    sink = _PcmBuffer(4, controller)
    limit: list[bool] = []
    sink.limit_reached.connect(lambda: limit.append(True))
    assert sink.append(b"1234") == 4
    assert sink.append(b"PRIVATE-AUDIO") == -1
    assert bytes(sink.pcm) == b"1234"
    assert sink.overflowed
    assert limit == [True]
    assert sink.append(b"5678") == -1
    assert limit == [True]


def test_native_qiodevice_reads_preserve_binary_pcm_including_null_and_non_utf8(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sources = hardware(controller, monkeypatch)
    controller.begin_capture("draft", tmp_path)
    payload = bytes(range(256)) * 2
    stream = sources[0].stream
    stream.close()
    stream.setData(payload)
    stream.open(QIODevice.OpenModeFlag.ReadOnly)
    stream.readyRead.emit()
    assert controller._capture is not None
    assert bytes(controller._capture.buffer.pcm) == payload


def test_capture_timeout_discards_audio_without_starting_recognition(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    hardware(controller, monkeypatch)
    controller.begin_capture("draft", tmp_path)
    job = controller._capture
    assert job is not None
    job.buffer.append(b"\x01\x00" * 100)
    job.timer.timeout.emit()
    controller.finish_capture()
    assert controller._transcription is None
    assert not job.buffer.pcm
    assert controller.activity == "idle"


def test_cancel_capture_immediately_discards_bytes_and_late_release(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sources = hardware(controller, monkeypatch)
    controller.begin_capture("old", tmp_path)
    job = controller._capture
    assert job is not None
    job.buffer.append(b"\x01\x00" * 100)
    controller.cancel()
    controller.finish_capture()
    assert sources[0].stopped
    assert not job.buffer.pcm
    assert controller._transcription is None


def test_revoked_permission_discards_capture_without_recognition(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sources = hardware(controller, monkeypatch)
    controller.begin_capture("draft", tmp_path)
    capture = controller._capture
    assert capture is not None
    capture.buffer.append(b"\x01\x00" * 320)
    monkeypatch.setattr(controller, "_permission_status", lambda: Qt.PermissionStatus.Denied)
    capture.permission_poll.timeout.emit()
    assert controller._capture is None
    assert controller._transcription is None
    assert sources[0].stopped
    assert not capture.buffer.pcm


def test_capture_device_failure_discards_audio_without_starting_stt(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sources = hardware(controller, monkeypatch)
    controller.begin_capture("draft", tmp_path)
    capture = controller._capture
    assert capture is not None
    capture.buffer.append(b"\x01\x00" * 320)
    monkeypatch.setattr(sources[0], "error", lambda: QtAudio.Error.IOError)
    sources[0].stateChanged.emit(QtAudio.State.StoppedState)
    assert controller._capture is None
    assert controller._transcription is None
    assert sources[0].stopped
    assert not capture.buffer.pcm


def test_released_capture_delivers_only_a_token_bound_draft_through_owned_pipe(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    hardware(controller, monkeypatch)
    helper = tmp_path / "synthetic_worker.py"
    synthetic_worker(controller, monkeypatch, helper)
    transcripts: list[tuple[str, str]] = []
    controller.transcript_ready.connect(lambda token, text: transcripts.append((token, text)))
    controller.begin_capture("draft-snapshot", tmp_path / "synthetic-text")
    capture = controller._capture
    assert capture is not None
    capture.buffer.append(b"\x01\x00" * 320)
    controller.finish_capture()
    job = controller._transcription
    assert job is not None
    assert not capture.buffer.pcm
    wait_until(lambda: bool(transcripts))
    assert transcripts == [("draft-snapshot", "synthetic-text")]
    assert controller.activity == "idle"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["synthetic_worker.py"]


def test_cancelled_result_cannot_clobber_new_capture_or_deliver_old_token(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    hardware(controller, monkeypatch)
    synthetic_worker(controller, monkeypatch, tmp_path / "synthetic_worker.py")
    transcripts: list[tuple[str, str]] = []
    controller.transcript_ready.connect(lambda token, text: transcripts.append((token, text)))
    controller.begin_capture("old", tmp_path / "old")
    capture = controller._capture
    assert capture is not None
    capture.buffer.append(b"\x01\x00" * 320)
    controller.finish_capture()
    old = controller._transcription
    assert old is not None
    controller.cancel()
    controller.begin_capture("new", tmp_path / "new")
    current = controller._capture
    controller._process_finished(old, 0, QProcess.ExitStatus.NormalExit)
    QTest.qWait(80)
    assert controller._capture is current
    assert controller.activity == "capturing"
    assert not transcripts


def test_invalid_or_oversized_worker_output_never_becomes_diagnostics_or_draft(
    controller: VoiceController,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    hardware(controller, monkeypatch)
    helper = tmp_path / "invalid_worker.py"
    helper.write_text(
        "import sys\nsys.stdin.buffer.read()\n"
        "sys.stderr.write('PRIVATE-ENGINE-EXCEPTION')\n"
        "sys.stdout.write('PRIVATE-TRANSCRIPT'*10000)\n"
    )
    monkeypatch.setattr(controller, "_arguments", lambda kind, directory: [str(helper)])
    statuses: list[str] = []
    transcripts: list[str] = []
    controller.status_changed.connect(statuses.append)
    controller.transcript_ready.connect(lambda token, text: transcripts.append(text))
    controller.begin_capture("draft", tmp_path)
    assert controller._capture is not None
    controller._capture.buffer.append(b"\x01\x00" * 320)
    controller.finish_capture()
    wait_until(lambda: controller.activity == "idle")
    assert not transcripts
    assert statuses[-1] == "transcription_invalid"
    assert not any("PRIVATE" in status for status in statuses)
    assert "PRIVATE" not in capsys.readouterr().out


@pytest.mark.parametrize("valid", [True, False])
def test_setup_requires_clean_exit_and_a_bounded_ready_record(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, valid: bool
) -> None:
    helper = tmp_path / "setup.py"
    records = [
        {"event": "progress", "downloaded_bytes": 1, "total_bytes": 2},
        {"event": "result", "ready": True},
    ]
    if not valid:
        records[0]["downloaded_bytes"] = "PRIVATE-UNTRUSTED-ERROR"
    helper.write_text("print(" + repr("\n".join(json.dumps(record) for record in records)) + ")\n")
    monkeypatch.setattr(controller, "_arguments", lambda kind, directory: [str(helper)])
    outcomes: list[bool] = []
    statuses: list[str] = []
    controller.setup_finished.connect(outcomes.append)
    controller.status_changed.connect(statuses.append)
    controller.prepare_model(tmp_path)
    wait_until(lambda: bool(outcomes))
    assert outcomes == [valid]
    assert not any("PRIVATE" in status for status in statuses)
    assert controller.activity == "idle"


def test_model_verification_uses_check_only_without_capture_download_or_transcript(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    helper = tmp_path / "verify.py"
    helper.write_text(
        "import json,sys\n"
        "assert sys.argv[1:]==['--check-only']\n"
        "print(json.dumps({'event':'result','ready':True}))\n"
    )
    monkeypatch.setattr(controller, "_arguments", lambda kind, directory: [str(helper)])
    outcomes: list[bool] = []
    statuses: list[str] = []
    controller.setup_finished.connect(outcomes.append)
    controller.status_changed.connect(statuses.append)
    controller.verify_model(tmp_path)
    assert controller._setup is not None
    assert controller._setup.process.arguments() == [str(helper), "--check-only"]
    assert controller._capture is None
    assert controller._speech is None
    assert statuses == ["checking_model"]
    wait_until(lambda: bool(outcomes))
    assert outcomes == [True]
    assert statuses[-1] == "model_ready"
    assert list(tmp_path.iterdir()) == [helper]


class FakeProcess(QProcess):
    """Capture speech arguments/stdin without ever starting an audible process."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.synthetic_state = QProcess.ProcessState.NotRunning
        self.input = bytearray()
        self.killed = False

    def start(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self.synthetic_state = QProcess.ProcessState.Running
        self.started.emit()

    def state(self) -> QProcess.ProcessState:
        return self.synthetic_state

    def write(self, data: Any, *args: Any) -> int:
        del args
        self.input.extend(bytes(data))
        return len(data)

    def kill(self) -> None:
        self.killed = True
        self.synthetic_state = QProcess.ProcessState.NotRunning
        self.finished.emit(0, QProcess.ExitStatus.CrashExit)


def test_speech_uses_stdin_and_stop_does_not_touch_capture_or_core(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(voice_module, "QProcess", FakeProcess)
    monkeypatch.setattr(sys, "platform", "darwin")
    controller.speak("PRIVATE-REPLY; $(touch should-never-run)")
    job = controller._speech
    assert job is not None
    process = job.process
    assert isinstance(process, FakeProcess)
    assert process.program() == "/usr/bin/say"
    assert process.arguments() == ["-v", "Milena", "-r", "165", "-f", "-"]
    assert bytes(process.input) == b"PRIVATE-REPLY; $(touch should-never-run)"
    assert job.payload == b""
    assert controller.activity == "speaking"
    generation = controller._generation
    controller.stop_speech()
    assert process.killed
    assert controller._generation == generation
    assert controller.activity == "idle"


def test_non_darwin_tts_is_unavailable_without_starting_a_process(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    statuses: list[str] = []
    controller.status_changed.connect(statuses.append)
    controller.speak("Привет")
    assert statuses == ["speech_unavailable"]
    assert controller._speech is None


def test_speech_projection_does_not_send_emoji_names_or_markdown_to_engine(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(voice_module, "QProcess", FakeProcess)
    monkeypatch.setattr(sys, "platform", "darwin")
    committed = {"text": "Привет 😊 **важный** [текст](https://example.org)"}
    controller.speak(committed["text"])
    job = controller._speech
    assert job is not None
    process = job.process
    assert isinstance(process, FakeProcess)
    assert bytes(process.input).decode() == "Привет важный текст"
    assert committed["text"] == "Привет 😊 **важный** [текст](https://example.org)"
    controller.stop_speech()


def test_emoji_only_reply_does_not_start_speech_process(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(voice_module, "QProcess", FakeProcess)
    monkeypatch.setattr(sys, "platform", "darwin")
    statuses: list[str] = []
    controller.status_changed.connect(statuses.append)
    controller.speak("😊 👩🏽‍🔬 ❤️")
    assert controller._speech is None
    assert controller.activity == "idle"
    assert statuses == ["speech_no_text"]


def test_stop_speech_leaves_in_progress_capture_unchanged(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    hardware(controller, monkeypatch)
    controller.begin_capture("draft", tmp_path)
    capture = controller._capture
    controller.stop_speech()
    assert controller._capture is capture
    assert controller.activity == "capturing"


def test_frozen_and_source_workers_receive_no_audio_or_text_in_arguments(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert controller._arguments("transcription", tmp_path) == [
        "-m",
        "satori.desktop.voice.worker",
        "--model-directory",
        str(tmp_path),
    ]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert controller._arguments("transcription", tmp_path) == [
        "--voice-worker",
        "--model-directory",
        str(tmp_path),
    ]
    assert controller._arguments("setup", tmp_path) == [
        "--voice-model-setup",
        "--model-directory",
        str(tmp_path),
    ]
