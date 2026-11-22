"""Neural speech worker contracts use synthetic PCM and no model, microphone or speaker."""

import base64
import importlib
import io
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from satori.desktop.voice import speech_worker
from satori.desktop.voice.expression import VoiceDelivery
from satori.desktop.voice.speech_contracts import (
    MAX_SPEECH_REQUEST_BYTES,
    AudioFrame,
    SpeechError,
    SpeechRequest,
)


def request_bytes(**updates: Any) -> bytes:
    return json.dumps({"text": "SYNTHETIC COMMITTED REPLY", **updates}).encode()


def records(output: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.getvalue().splitlines()]


def mock_chunks(
    monkeypatch: pytest.MonkeyPatch, chunks: list[bytes]
) -> list[tuple[SpeechRequest, Path]]:
    observed: list[tuple[SpeechRequest, Path]] = []

    def generate(request: SpeechRequest, directory: Path) -> Iterator[tuple[bytes, float]]:
        observed.append((request, directory))
        for pcm in chunks:
            yield pcm, 2.0

    monkeypatch.setattr(speech_worker, "_audio_chunks", generate)
    return observed


@pytest.mark.parametrize("rate", [16_000, 24_000, 44_100, 48_000])
def test_tiny_producer_packets_do_not_exhaust_sequence_before_audio_duration_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rate: int
) -> None:
    chunks = [sample.to_bytes(2, "little") for sample in range(1002)]
    mock_chunks(monkeypatch, chunks)
    output = io.StringIO()
    assert (
        speech_worker.run_worker(tmp_path, io.BytesIO(request_bytes(sample_rate=rate)), output) == 0
    )
    events = records(output)
    assert [event["event"] for event in events] == ["audio", "complete"]
    audio = AudioFrame.model_validate(events[0])
    assert audio.sequence == 0
    assert audio.sample_rate == rate
    assert audio.pcm() == b"".join(chunks)
    assert events[1]["total_pcm_bytes"] == 2004


def test_worker_streams_exact_binary_frames_in_order_and_completes_only_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pcm = bytes(range(256)) * 600
    observed = mock_chunks(monkeypatch, [pcm, b"\x00\xff"])
    output = io.StringIO()
    assert speech_worker.run_worker(tmp_path, io.BytesIO(request_bytes()), output) == 0
    events = records(output)
    audio = [event for event in events if event["event"] == "audio"]
    decoded = [AudioFrame.model_validate(event).pcm() for event in audio]
    assert b"".join(decoded) == pcm + b"\x00\xff"
    assert [event["sequence"] for event in audio] == list(range(len(audio)))
    assert all(len(block) <= 48_000 for block in decoded)
    assert len([event for event in events if event["event"] == "complete"]) == 1
    assert events[-1]["event"] == "complete"
    assert events[-1]["total_pcm_bytes"] == len(pcm) + 2
    assert events[-1]["model_load_ms"] == 2.0
    assert events[-1]["first_chunk_ms"] >= 0
    assert events[-1]["generation_ms"] >= 0
    assert observed[0][0].text == "SYNTHETIC COMMITTED REPLY"
    assert observed[0][0].delivery == VoiceDelivery()
    assert observed[0][1] == tmp_path
    assert "SYNTHETIC COMMITTED REPLY" not in output.getvalue()
    assert "text=" not in repr(observed[0][0])


@pytest.mark.parametrize("rate", [16_000, 24_000, 44_100, 48_000])
def test_worker_frames_each_supported_rate_to_at_most_one_second(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rate: int
) -> None:
    pcm = b"\x00\xff" * (rate + 1)
    mock_chunks(monkeypatch, [pcm])
    output = io.StringIO()
    assert (
        speech_worker.run_worker(tmp_path, io.BytesIO(request_bytes(sample_rate=rate)), output) == 0
    )
    events = records(output)
    assert all(event["sample_rate"] == rate for event in events[:-1])
    decoded = [AudioFrame.model_validate(event).pcm() for event in events[:-1]]
    assert b"".join(decoded) == pcm
    assert all(0 < len(block) <= rate * 2 for block in decoded)
    assert len(decoded) <= 5


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"not JSON",
        b"\xff",
        request_bytes(text=""),
        request_bytes(text="   "),
        request_bytes(text="\x00"),
        request_bytes(text="x" * 8001),
        request_bytes(text=4),
        request_bytes(protocol_version=2),
        request_bytes(sample_rate=8000),
        request_bytes(sample_rate="24000"),
        request_bytes(permission="unreviewed"),
        request_bytes(delivery={"instruction": "unreviewed instructions"}),
        b" " * (MAX_SPEECH_REQUEST_BYTES + 1),
    ],
)
def test_invalid_requests_are_rejected_before_any_synthesis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: bytes
) -> None:
    def forbidden(*args: Any) -> Iterator[tuple[bytes, float]]:
        del args
        raise AssertionError("synthesis must not run")

    monkeypatch.setattr(speech_worker, "_audio_chunks", forbidden)
    output = io.StringIO()
    assert speech_worker.run_worker(tmp_path, io.BytesIO(payload), output) == 1
    assert records(output) == [{"event": "error", "code": "configuration_invalid"}]


def test_relative_model_directory_is_rejected_before_synthesis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = mock_chunks(monkeypatch, [b"\x00\xff"])
    output = io.StringIO()
    assert speech_worker.run_worker(Path("relative"), io.BytesIO(request_bytes()), output) == 1
    assert observed == []
    assert records(output) == [{"event": "error", "code": "configuration_invalid"}]


def test_missing_pinned_resources_never_import_a_runtime_or_create_a_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unrelated = tmp_path / "keep.txt"
    unrelated.write_bytes(b"unrelated private fixture")

    def forbidden(_name: str) -> None:
        raise AssertionError("missing resources must not import or run an inference engine")

    monkeypatch.setattr(importlib, "import_module", forbidden)
    output = io.StringIO()
    assert speech_worker.run_worker(tmp_path, io.BytesIO(request_bytes()), output) == 1
    assert records(output) == [{"event": "error", "code": "speech_model_unavailable"}]
    assert list(tmp_path.iterdir()) == [unrelated]
    assert unrelated.read_bytes() == b"unrelated private fixture"


@pytest.mark.parametrize("chunks", [[], [b""], [b"\x00"]])
def test_empty_or_incomplete_pcm_never_produces_successful_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, chunks: list[bytes]
) -> None:
    mock_chunks(monkeypatch, chunks)
    output = io.StringIO()
    assert speech_worker.run_worker(tmp_path, io.BytesIO(request_bytes()), output) == 1
    assert records(output) == [{"event": "error", "code": "speech_audio_invalid"}]


def test_duration_limit_rejects_the_next_chunk_without_claiming_complete_speech(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(speech_worker, "MAX_SPEECH_SECONDS", 1)
    mock_chunks(monkeypatch, [b"\x00\xff" * 24_000, b"\x00\xff"])
    output = io.StringIO()
    assert speech_worker.run_worker(tmp_path, io.BytesIO(request_bytes()), output) == 1
    events = records(output)
    assert all(event["event"] == "audio" for event in events[:-1])
    assert events[-1] == {"event": "error", "code": "speech_too_long"}
    assert sum(len(AudioFrame.model_validate(event).pcm()) for event in events[:-1]) == 48_000


@pytest.mark.parametrize("typed", [False, True])
def test_provider_failure_exposes_only_a_static_protocol_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, typed: bool
) -> None:
    def fail(*args: Any) -> Iterator[tuple[bytes, float]]:
        del args
        if typed:
            raise SpeechError("speech_engine_unavailable")
        raise RuntimeError("PRIVATE TOKEN USER TEXT AND MODEL PATH")

    monkeypatch.setattr(speech_worker, "_audio_chunks", fail)
    output = io.StringIO()
    assert speech_worker.run_worker(tmp_path, io.BytesIO(request_bytes()), output) == 1
    assert records(output) == [
        {"event": "error", "code": "speech_engine_unavailable" if typed else "speech_failed"}
    ]
    assert "PRIVATE" not in output.getvalue()
    assert str(tmp_path) not in output.getvalue()


def test_real_owned_entrypoint_contains_native_and_late_vendor_output(tmp_path: Path) -> None:
    script = (
        "import atexit,os\n"
        "from satori.desktop.voice import speech_worker as w\n"
        "def chunks(request,directory):\n"
        " print('PRIVATE PYTHON OUTPUT',flush=True)\n"
        " os.write(1,b'PRIVATE NATIVE OUTPUT\\n')\n"
        " os.write(2,b'PRIVATE VENDOR ERROR\\n')\n"
        " atexit.register(lambda:os.write(1,b'PRIVATE LATE OUTPUT\\n'))\n"
        " yield bytes(range(256)),1.0\n"
        "w._audio_chunks=chunks\n"
        "raise SystemExit(w.main())\n"
    )
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [sys.executable, "-c", script, "--model-directory", str(tmp_path)],
        input=request_bytes(),
        capture_output=True,
        env=environment,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0
    assert result.stderr == b""
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert [event["event"] for event in events] == ["audio", "complete"]
    assert base64.b64decode(events[0]["pcm_base64"]) == bytes(range(256))
    assert b"PRIVATE" not in result.stdout
    assert list(tmp_path.iterdir()) == []
