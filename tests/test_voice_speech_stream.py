"""Owned neural stdout streams use synthetic children and fake playback, never speakers."""

import base64
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from time import monotonic
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QProcess
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from satori.desktop.voice import controller as voice_module
from satori.desktop.voice.controller import VoiceController
from satori.desktop.voice.playback import PcmPlayback
from satori.desktop.voice.speech_contracts import MAX_SPEECH_RECORD_BYTES


class FakePlayback(PcmPlayback):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.rate = 24_000
        self.active = False
        self.chunks: list[bytes] = []
        self.started_rates: list[int] = []
        self.input_finished = 0
        self.cancel_count = 0

    def preferred_sample_rate(self) -> int | None:
        return self.rate

    def start(self, rate: int) -> bool:
        self.started_rates.append(rate)
        self.active = True
        return True

    def append(self, pcm: bytes) -> bool:
        if not self.active:
            return False
        self.chunks.append(pcm)
        if len(self.chunks) == 1:
            self.first_buffer_written.emit()
        return True

    def finish_input(self) -> None:
        self.input_finished += 1

    def cancel(self) -> None:
        self.cancel_count += 1
        self.active = False

    def drain(self) -> None:
        self.active = False
        self.finished.emit()


@pytest.fixture(scope="module")
def application() -> QApplication:
    from satori.desktop.ui import DesktopApplication

    return QApplication.instance() or DesktopApplication([])  # type: ignore[return-value]


@pytest.fixture
def controller(
    application: QApplication, monkeypatch: pytest.MonkeyPatch
) -> Iterator[VoiceController]:
    monkeypatch.setattr(voice_module, "PcmPlayback", FakePlayback)
    result = VoiceController()
    yield result
    result.cancel()
    wait_until(lambda: not result._retired)
    result.deleteLater()
    application.processEvents()


def output(controller: VoiceController) -> FakePlayback:
    playback = controller._playback
    assert isinstance(playback, FakePlayback)
    return playback


def wait_until(predicate: Any) -> None:
    deadline = monotonic() + 5
    while not predicate() and monotonic() < deadline:
        QTest.qWait(5)
    assert predicate()


def frame(sequence: int, pcm: bytes, rate: int = 24_000) -> dict[str, Any]:
    return {
        "event": "audio",
        "sequence": sequence,
        "sample_rate": rate,
        "pcm_base64": base64.b64encode(pcm).decode(),
    }


def complete(total: int) -> dict[str, Any]:
    return {
        "event": "complete",
        "total_pcm_bytes": total,
        "model_load_ms": 1.0,
        "first_chunk_ms": 2.0,
        "generation_ms": 3.0,
    }


def owned_child(
    controller: VoiceController,
    monkeypatch: pytest.MonkeyPatch,
    path: Path,
    events: list[dict[str, Any]],
    *,
    fragmented: bool = False,
    hold: bool = False,
    exit_code: int = 0,
    trailing: bytes = b"",
) -> None:
    payload = b"".join(
        json.dumps(event, separators=(",", ":")).encode() + b"\n" for event in events
    )
    script = (
        "import json,sys,time\n"
        "request=json.load(sys.stdin)\n"
        "assert request['text']=='SYNTHETIC REPLY', 'request must arrive on private stdin'\n"
        "assert request['delivery']['availability']=='unavailable'\n"
        f"payload={payload + trailing!r}\n"
        f"fragmented={fragmented!r}\n"
        "if fragmented:\n"
        " for offset in range(0,len(payload),1021):\n"
        "  sys.stdout.buffer.write(payload[offset:offset+1021]);sys.stdout.buffer.flush()\n"
        "  time.sleep(0.001)\n"
        "else:\n"
        " sys.stdout.buffer.write(payload);sys.stdout.buffer.flush()\n"
        f"if {hold!r}:time.sleep(10)\n"
        f"raise SystemExit({exit_code})\n"
    )
    path.write_text(script)
    monkeypatch.setattr(controller, "_arguments", lambda _kind, _directory: [str(path)])


def test_cloud_admission_code_survives_owned_child_controller_boundary_without_audio(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    owned_child(
        controller,
        monkeypatch,
        tmp_path / "guard-rejection.py",
        [{"event": "error", "code": "speech_text_unsupported"}],
        exit_code=1,
    )
    statuses: list[str] = []
    controller.status_changed.connect(statuses.append)
    start_stream = controller._start_speech_stream

    def private_child(
        request: Any, payload: bytes, directory: Path, *, cloud: bool = False
    ) -> None:
        assert cloud
        # Cloud arguments otherwise replace _arguments(); route this synthetic
        # protocol test explicitly to its owned helper, never the network worker.
        start_stream(request, payload, directory, cloud=False)

    monkeypatch.setattr(controller, "_start_speech_stream", private_child)
    controller.set_cloud_credentials("synthetic-session-key", "SyntheticVoiceID12345")
    controller.speak_cloud("SYNTHETIC REPLY")
    wait_until(lambda: controller._speech is None)
    assert statuses.count("speech_text_unsupported") == 1
    assert not output(controller).chunks
    assert controller.activity == "idle"


def test_worker_exit_waits_for_actual_playback_drain_before_finished(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pcm = bytes(range(256))
    owned_child(
        controller, monkeypatch, tmp_path / "helper.py", [frame(0, pcm), complete(len(pcm))]
    )
    statuses: list[str] = []
    controller.status_changed.connect(statuses.append)
    controller.speak_neural("SYNTHETIC REPLY 😊", tmp_path / "model")
    job = controller._speech
    assert job is not None
    assert "SYNTHETIC" not in " ".join(job.process.arguments())
    wait_until(lambda: job.worker_done)
    playback = output(controller)
    assert playback.chunks == [pcm]
    assert playback.input_finished == 1
    assert controller.activity == "speaking"
    assert controller._speech is job
    assert "speech_finished" not in statuses
    assert job.payload == b""
    assert job.process.state() == QProcess.ProcessState.NotRunning
    playback.drain()
    assert controller._speech is None
    assert controller.activity == "idle"
    assert statuses.count("speech_finished") == 1
    assert not controller._retired


@pytest.mark.parametrize("fragmented", [False, True])
def test_large_and_multiple_records_cross_pipe_boundaries_without_losing_pcm(
    controller: VoiceController,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fragmented: bool,
) -> None:
    pcm = bytes(range(256)) * 375
    events = [frame(index, pcm, 48_000) for index in range(3)] + [complete(len(pcm) * 3)]
    output(controller).rate = 48_000
    owned_child(
        controller,
        monkeypatch,
        tmp_path / "helper.py",
        events,
        fragmented=fragmented,
    )
    controller.speak_neural("SYNTHETIC REPLY", tmp_path / "model")
    job = controller._speech
    assert job is not None
    wait_until(lambda: job.worker_done)
    assert b"".join(output(controller).chunks) == pcm * 3
    assert job.pcm_bytes == len(pcm) * 3
    assert job.audio_frames == 3
    assert job.output == b""
    output(controller).drain()
    assert controller._speech is None


@pytest.mark.parametrize(
    "fault",
    [
        "first_sequence",
        "duplicate_sequence",
        "wrong_rate",
        "bad_base64",
        "odd_pcm",
        "oversized_frame",
        "wrong_total",
        "audio_after_complete",
        "duplicate_complete",
        "no_audio_complete",
        "extra_field",
        "unknown_error",
    ],
)
def test_malformed_streams_fail_closed_without_a_success_receipt_or_late_audio(
    controller: VoiceController,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fault: str,
) -> None:
    pcm = b"\x00\xff" * 10
    events = [frame(0, pcm), complete(len(pcm))]
    if fault == "first_sequence":
        events[0]["sequence"] = 1
    elif fault == "duplicate_sequence":
        events.insert(1, frame(0, pcm))
    elif fault == "wrong_rate":
        events[0]["sample_rate"] = 16_000
    elif fault == "bad_base64":
        events[0]["pcm_base64"] = "PRIVATE RAW VENDOR BODY"
    elif fault == "odd_pcm":
        events[0] = frame(0, b"\x00")
    elif fault == "oversized_frame":
        events[0] = frame(0, b"\x00\xff" * 24_001)
    elif fault == "wrong_total":
        events[1]["total_pcm_bytes"] = len(pcm) + 2
    elif fault == "audio_after_complete":
        events.append(frame(1, pcm))
    elif fault == "duplicate_complete":
        events.append(complete(len(pcm)))
    elif fault == "no_audio_complete":
        events = [complete(len(pcm))]
    elif fault == "extra_field":
        events[0]["text"] = "PRIVATE TEXT"
    else:
        events = [{"event": "error", "code": "PRIVATE UNKNOWN ERROR"}]
    owned_child(controller, monkeypatch, tmp_path / "helper.py", events)
    statuses: list[str] = []
    controller.status_changed.connect(statuses.append)
    controller.speak_neural("SYNTHETIC REPLY", tmp_path / "model")
    wait_until(lambda: controller._speech is None and not controller._retired)
    accepted = list(output(controller).chunks)
    QTest.qWait(20)
    assert output(controller).chunks == accepted
    assert "speech_invalid" in statuses
    assert "speech_finished" not in statuses
    assert not output(controller).active
    assert not any("PRIVATE" in code for code in statuses)


@pytest.mark.parametrize(
    "ending", ["missing_complete", "truncated_record", "trailing_after_complete", "failed_exit"]
)
def test_incomplete_process_results_never_report_successful_speech(
    controller: VoiceController,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ending: str,
) -> None:
    pcm = b"\x00\xff" * 10
    events = [frame(0, pcm)]
    if ending in {"failed_exit", "trailing_after_complete"}:
        events.append(complete(len(pcm)))
    owned_child(
        controller,
        monkeypatch,
        tmp_path / "helper.py",
        events,
        trailing=b'{"event":' if ending in {"truncated_record", "trailing_after_complete"} else b"",
        exit_code=1 if ending == "failed_exit" else 0,
    )
    statuses: list[str] = []
    controller.status_changed.connect(statuses.append)
    controller.speak_neural("SYNTHETIC REPLY", tmp_path / "model")
    wait_until(lambda: controller._speech is None and not controller._retired)
    assert "speech_failed" in statuses
    assert "speech_finished" not in statuses
    assert output(controller).input_finished == 0


def test_stream_duration_bound_is_enforced_before_appending_an_excess_frame(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(voice_module, "MAX_SPEECH_SECONDS", 1)
    pcm = b"\x00\xff" * 24_000
    owned_child(
        controller,
        monkeypatch,
        tmp_path / "helper.py",
        [frame(0, pcm), frame(1, pcm), complete(len(pcm) * 2)],
    )
    statuses: list[str] = []
    controller.status_changed.connect(statuses.append)
    controller.speak_neural("SYNTHETIC REPLY", tmp_path / "model")
    wait_until(lambda: controller._speech is None and not controller._retired)
    assert output(controller).chunks == [pcm]
    assert "speech_invalid" in statuses
    assert "speech_finished" not in statuses


def test_explicit_cloud_route_uses_only_private_stdin_and_forgets_owned_payload(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(QProcess, "start", lambda *_args: None)
    key = "sk_SYNTHETIC_ONLY_SESSION_CREDENTIAL"
    voice_id = "ymDCYd8puC7gYjxIamPt"
    controller.set_cloud_credentials(key, voice_id)
    assert controller.has_cloud_credentials
    controller.set_cloud_credentials("", voice_id)
    original = "Сохранённый **ответ** 😊"
    controller.speak_cloud(original)
    job = controller._speech
    assert job is not None
    assert job.neural
    assert job.process.program() == sys.executable
    assert job.process.arguments() == ["-m", "satori.desktop.voice.cloud_worker"]
    assert key not in repr(job)
    assert key not in " ".join(job.process.arguments())
    payload = json.loads(job.payload)
    assert payload["api_key"] == key
    assert payload["voice_id"] == voice_id
    assert payload["text"] == "Сохранённый ответ"
    assert original == "Сохранённый **ответ** 😊"
    assert payload["delivery"]["availability"] == "unavailable"
    controller.stop_speech()
    assert job.payload == b""
    assert not job.output
    assert controller._speech is None
    controller.clear_cloud_credentials()
    assert not controller.has_cloud_credentials


@pytest.mark.parametrize("invalid_id", ["", "../redirect", "x" * 129])
def test_cloud_missing_or_invalid_credentials_never_launch_or_fall_back(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch, invalid_id: str
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(QProcess, "start", lambda *_args: calls.append("process"))
    statuses: list[str] = []
    controller.status_changed.connect(statuses.append)
    controller.speak_cloud("Публичная тестовая фраза.")
    assert statuses[-1] == "speech_credentials_required"
    controller.set_cloud_credentials("sk_SYNTHETIC_ONLY_SESSION_CREDENTIAL", invalid_id)
    controller.speak_cloud("Публичная тестовая фраза.")
    assert statuses[-1] in {"configuration_invalid", "speech_credentials_required"}
    assert controller._speech is None
    assert calls == []
    assert output(controller).started_rates == []


def test_record_without_a_newline_cannot_grow_past_the_transport_bound(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    owned_child(
        controller,
        monkeypatch,
        tmp_path / "helper.py",
        [],
        trailing=b"x" * (MAX_SPEECH_RECORD_BYTES + 1),
    )
    statuses: list[str] = []
    controller.status_changed.connect(statuses.append)
    controller.speak_neural("SYNTHETIC REPLY", tmp_path / "model")
    wait_until(lambda: controller._speech is None and not controller._retired)
    assert output(controller).chunks == []
    assert "speech_invalid" in statuses
    assert "speech_finished" not in statuses


def test_known_worker_failure_remains_a_static_failure_and_never_finishes_playback(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    owned_child(
        controller,
        monkeypatch,
        tmp_path / "helper.py",
        [{"event": "error", "code": "speech_model_unavailable"}],
        exit_code=1,
    )
    statuses: list[str] = []
    controller.status_changed.connect(statuses.append)
    controller.speak_neural("SYNTHETIC REPLY", tmp_path / "model")
    wait_until(lambda: controller._speech is None and not controller._retired)
    assert output(controller).chunks == []
    assert output(controller).input_finished == 0
    assert "speech_model_unavailable" in statuses
    assert "speech_finished" not in statuses


def test_owned_stop_does_not_kill_another_process_or_accept_late_pcm(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pcm = b"\x00\xff" * 10
    owned_child(controller, monkeypatch, tmp_path / "helper.py", [frame(0, pcm)], hold=True)
    unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(10)"])
    try:
        controller.speak_neural("SYNTHETIC REPLY", tmp_path / "model")
        job = controller._speech
        assert job is not None
        wait_until(lambda: bool(output(controller).chunks))
        previous = list(output(controller).chunks)
        controller.stop_speech()
        wait_until(lambda: not controller._retired)
        assert controller._speech is None
        assert unrelated.poll() is None
        QTest.qWait(20)
        assert output(controller).chunks == previous
        assert controller.activity == "idle"
        assert not output(controller).active
    finally:
        unrelated.kill()
        unrelated.wait(timeout=5)


def test_playback_failure_cancels_its_owned_inference_child(
    controller: VoiceController, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    owned_child(controller, monkeypatch, tmp_path / "helper.py", [frame(0, b"\x00\xff")], hold=True)
    statuses: list[str] = []
    controller.status_changed.connect(statuses.append)
    controller.speak_neural("SYNTHETIC REPLY", tmp_path / "model")
    wait_until(lambda: bool(output(controller).chunks))
    output(controller).failed.emit("playback_failed")
    wait_until(lambda: controller._speech is None and not controller._retired)
    assert "playback_failed" in statuses
    assert "speech_finished" not in statuses
    assert not output(controller).active
