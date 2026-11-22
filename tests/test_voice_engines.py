"""Local speech contracts with synthetic PCM and mocked engines; no mic or model."""

import importlib
import io
import json
import os
import struct
import subprocess
import sys
import wave
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from satori.desktop.voice import engines
from satori.desktop.voice.contracts import (
    MAX_AUDIO_BYTES,
    MAX_WAV_BYTES,
    AudioClip,
    TranscriptionResult,
    VoiceError,
    VoiceWorkerResponse,
    decode_wav,
    wav_bytes,
)
from satori.desktop.voice.worker import run_worker


def make_wav(
    *, rate: int = 16_000, channels: int = 1, width: int = 2, frames: bytes = b"\x01\x00" * 1600
) -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(width)
        output.setframerate(rate)
        output.writeframes(frames)
    return stream.getvalue()


@pytest.mark.parametrize("rate", [16_000, 22_050, 44_100, 48_000])
def test_pcm_wave_round_trip_preserves_rate_and_frames(rate: int) -> None:
    clip = decode_wav(make_wav(rate=rate))
    assert clip.sample_rate == rate
    assert clip.frame_count == 1600
    assert clip.duration_ms == 1600 / rate * 1000
    assert decode_wav(wav_bytes(clip)) == clip
    assert "pcm16le" not in clip.model_dump()
    assert "pcm16le" not in repr(clip)


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b"", "audio_invalid"),
        (b"private-not-audio", "audio_invalid"),
        (make_wav()[:-1], "audio_invalid"),
        (make_wav() + b"private-trailing-content", "audio_invalid"),
        (make_wav(rate=8000), "audio_unsupported"),
        (make_wav(rate=96_000), "audio_unsupported"),
        (make_wav(channels=2), "audio_unsupported"),
        (make_wav(width=1), "audio_unsupported"),
        (make_wav(frames=b""), "audio_invalid"),
        (make_wav(frames=b"\x01\x00" * (16_000 * 30 + 1)), "audio_too_long"),
        (b"x" * (MAX_WAV_BYTES + 1), "audio_too_large"),
    ],
)
def test_invalid_audio_is_rejected_before_any_engine(payload: bytes, code: str) -> None:
    with pytest.raises(VoiceError) as caught:
        decode_wav(payload)
    assert caught.value.code == code
    assert str(caught.value) == code


def test_declared_long_audio_is_bounded_before_reading_frames() -> None:
    payload = bytearray(make_wav())
    struct.pack_into("<I", payload, 40, 2**31)
    with pytest.raises(VoiceError, match="audio_too_long"):
        decode_wav(bytes(payload))


def test_truncated_frame_data_with_adjusted_riff_size_is_not_accepted() -> None:
    payload = bytearray(make_wav()[:-2])
    struct.pack_into("<I", payload, 4, len(payload) - 8)
    with pytest.raises(VoiceError, match="audio_invalid"):
        decode_wav(bytes(payload))


@pytest.mark.parametrize(
    "fields",
    [
        {"pcm16le": b"\x00", "sample_rate": 16_000},
        {"pcm16le": b"\x00\x00", "sample_rate": 15_999},
        {"pcm16le": b"\x00\x00", "sample_rate": 48_001},
        {"pcm16le": b"\x00\x00", "sample_rate": 16_000, "channels": 2},
        {"pcm16le": b"\x00\x00", "sample_rate": 16_000, "sample_width_bytes": 1},
        {"pcm16le": b"\x00\x00", "sample_rate": 16_000, "unexpected": True},
        {"pcm16le": b"\x00\x00", "sample_rate": "16000"},
        {"pcm16le": b"\x00" * (MAX_AUDIO_BYTES + 2), "sample_rate": 48_000},
    ],
)
def test_audio_clip_is_a_strict_bounded_boundary(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AudioClip.model_validate(fields)


def test_full_thirty_second_mono_clip_is_accepted() -> None:
    clip = decode_wav(make_wav(rate=48_000, frames=b"\x01\x00" * (48_000 * 30)))
    assert clip.duration_ms == 30_000


@pytest.mark.parametrize("rate", [16_000, 44_100, 48_000])
def test_real_vendor_resampler_preserves_duration_and_frequency_without_a_model(rate: int) -> None:
    _, _, numpy = engines._engine_modules()
    resampler_type = importlib.import_module("onnx_asr.preprocessors.resampler").Resampler
    resampler = resampler_type(16_000, {"providers": ["CPUExecutionProvider"]})
    waveform = (0.25 * numpy.sin(2 * numpy.pi * 1000 * numpy.arange(rate // 10) / rate)).astype(
        numpy.float32
    )
    decoded, lengths = resampler(
        waveform[None, :], numpy.array([len(waveform)], dtype=numpy.int64), rate
    )
    assert lengths.tolist() == [1600]
    actual = decoded[0, : lengths[0]]
    expected = 0.25 * numpy.sin(2 * numpy.pi * 1000 * numpy.arange(1600) / 16_000)
    assert actual.dtype.name == "float32"
    # Disregard only the sinc filter's edge transient, not duration or frequency.
    assert float(numpy.max(numpy.abs(actual[50:-50] - expected[50:-50]))) < 0.003


def test_speech_import_is_lazy_and_missing_model_does_not_load_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[object] = []
    monkeypatch.setenv("ORT_DISABLE_TELEMETRY", "0")
    monkeypatch.setattr(engines, "_engine_modules", lambda: calls.append("model"))
    monkeypatch.setattr(engines, "validate_model_directory", lambda _: False)
    clip = decode_wav(make_wav())
    assert calls == []
    assert os.environ["ORT_DISABLE_TELEMETRY"] == "0"
    with pytest.raises(VoiceError, match="model_unavailable"):
        engines.transcribe(clip, tmp_path)
    assert calls == []
    assert os.environ["ORT_DISABLE_TELEMETRY"] == "0"


def test_silence_never_loads_an_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setenv("ORT_DISABLE_TELEMETRY", "0")
    monkeypatch.setattr(engines, "_engine_modules", lambda: calls.append("model"))
    monkeypatch.setattr(engines, "validate_model_directory", lambda _: True)
    with pytest.raises(VoiceError, match="no_speech"):
        engines.transcribe(decode_wav(make_wav(frames=b"\x00\x00" * 1600)), tmp_path)
    assert calls == []
    assert os.environ["ORT_DISABLE_TELEMETRY"] == "0"


def test_module_import_keeps_native_runtime_and_telemetry_configuration_lazy() -> None:
    helper = (
        "import os, sys\n"
        "os.environ['ORT_DISABLE_TELEMETRY'] = '0'\n"
        "from satori.desktop.voice import engines\n"
        "assert 'onnxruntime' not in sys.modules\n"
        "assert 'onnx_asr' not in sys.modules\n"
        "assert os.environ['ORT_DISABLE_TELEMETRY'] == '0'\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", helper], capture_output=True, timeout=10, check=False
    )
    assert completed.returncode == 0
    assert completed.stdout == completed.stderr == b""


@pytest.mark.parametrize("inherited", [None, "0", "1"])
def test_vendor_boundary_disables_telemetry_before_vendor_initialization(
    monkeypatch: pytest.MonkeyPatch, inherited: str | None
) -> None:
    if inherited is None:
        monkeypatch.delenv("ORT_DISABLE_TELEMETRY", raising=False)
    else:
        monkeypatch.setenv("ORT_DISABLE_TELEMETRY", inherited)
    calls: list[str] = []

    def disable_telemetry() -> None:
        calls.append("disable")

    runtime = SimpleNamespace(disable_telemetry_events=disable_telemetry)
    manager = object()
    numpy = object()
    modules = {
        "onnxruntime": runtime,
        "onnx_asr.loader": SimpleNamespace(Manager=manager),
        "numpy": numpy,
    }

    def vendor_import(name: str) -> object:
        assert os.environ["ORT_DISABLE_TELEMETRY"] == "1"
        calls.append(name)
        if name == "onnx_asr.loader":
            assert calls[-2] == "disable"
        return modules[name]

    monkeypatch.setattr(importlib, "import_module", vendor_import)
    for _ in range(2):
        assert engines._engine_modules() == (manager, runtime, numpy)
    assert calls == ["onnxruntime", "disable", "onnx_asr.loader", "numpy"] * 2


@pytest.mark.parametrize("failure", ["missing_api", "runtime_error", "os_error"])
def test_telemetry_opt_out_failure_prevents_any_asr_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    calls: list[str] = []

    def disable_telemetry() -> None:
        calls.append("disable")
        error_type = OSError if failure == "os_error" else RuntimeError
        raise error_type("PRIVATE-RUNTIME-DETAILS")

    runtime = (
        SimpleNamespace()
        if failure == "missing_api"
        else SimpleNamespace(disable_telemetry_events=disable_telemetry)
    )

    def vendor_import(name: str) -> object:
        assert os.environ["ORT_DISABLE_TELEMETRY"] == "1"
        calls.append(name)
        assert name == "onnxruntime", "ASR must not initialize after opt-out failure"
        return runtime

    monkeypatch.setattr(importlib, "import_module", vendor_import)
    monkeypatch.setattr(engines, "validate_model_directory", lambda _: True)
    with pytest.raises(VoiceError, match=r"^engine_unavailable$") as caught:
        engines.transcribe(decode_wav(make_wav()), tmp_path)
    assert str(caught.value) == "engine_unavailable"
    assert calls == (["onnxruntime"] if failure == "missing_api" else ["onnxruntime", "disable"])


class FakeSessionOptions:
    """Configuration-only double; native session lifetime has a separate fixture."""

    intra_op_num_threads = 0
    inter_op_num_threads = 0
    log_severity_level = 0


def test_model_validation_failure_never_exposes_local_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(_path: Path) -> bool:
        raise OSError("PRIVATE-MODEL-PATH")

    monkeypatch.setattr(engines, "validate_model_directory", fail)
    with pytest.raises(VoiceError, match=r"^model_unavailable$"):
        engines.transcribe(decode_wav(make_wav()), tmp_path)


def fake_engine(
    monkeypatch: pytest.MonkeyPatch,
    result: object,
    *,
    failure: Exception | None = None,
) -> list[tuple[str, object, object]]:
    calls: list[tuple[str, object, object]] = []

    class FakeManager:
        def __init__(self, *args: object, **kwargs: object) -> None:
            calls.append(("manager", args, kwargs))

        def create_asr(self, *args: object, **kwargs: object) -> "FakeModel":
            calls.append(("load", args, kwargs))
            return FakeModel()

    class FakeModel:
        def recognize(self, *args: object, **kwargs: object) -> object:
            calls.append(("recognize", args, kwargs))
            if failure is not None:
                raise failure
            return result

    runtime = SimpleNamespace(SessionOptions=FakeSessionOptions)
    numpy = importlib.import_module("numpy")
    monkeypatch.setattr(engines, "_engine_modules", lambda: (FakeManager, runtime, numpy))
    monkeypatch.setattr(engines, "validate_model_directory", lambda _: True)
    return calls


@pytest.mark.parametrize("rate", [16_000, 44_100, 48_000])
def test_explicit_transcription_is_local_russian_and_preserves_actual_pcm_rate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rate: int
) -> None:
    calls = fake_engine(monkeypatch, " Привет, Сатори. ")
    assert calls == []
    clip = decode_wav(make_wav(rate=rate, frames=struct.pack("<hhhh", -32768, -1, 0, 32767) * 400))
    result = engines.transcribe(clip, tmp_path)
    assert result.text == "Привет, Сатори."
    assert result.language == "ru"
    assert result.engine == "gigaam-v3-e2e-rnnt/int8"
    assert result.confidence is None
    assert result.audio_duration_ms == clip.duration_ms
    assert result.model_load_ms >= 0
    assert result.transcription_ms >= 0
    assert calls[0][0] == "manager"
    assert calls[0][1] == ()
    configuration = calls[0][2]
    assert isinstance(configuration, dict)
    assert configuration["providers"] == ["CPUExecutionProvider"]
    assert configuration["preprocessor_config"] == {
        "use_numpy_preprocessors": True,
        "max_concurrent_workers": 1,
    }
    options = configuration["sess_options"]
    assert isinstance(options, FakeSessionOptions)
    assert options.intra_op_num_threads == 2
    assert options.inter_op_num_threads == 1
    assert options.log_severity_level == 3
    assert calls[1] == (
        "load",
        ("gigaam-v3-e2e-rnnt", tmp_path),
        {"quantization": "int8", "offline": True},
    )
    assert calls[2][0] == "recognize"
    assert calls[2][2] == {"sample_rate": rate}
    args = calls[2][1]
    assert isinstance(args, tuple)
    input_audio = args[0]
    numpy = importlib.import_module("numpy")
    assert isinstance(input_audio, numpy.ndarray)
    assert input_audio.dtype.name == "float32"
    assert input_audio.shape == (clip.frame_count,)
    assert input_audio[:4].tolist() == [-1.0, -1 / 32768, 0.0, 32767 / 32768]
    assert "pcm16le" not in result.model_dump_json()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("output", "code"),
    [
        ("", "no_speech"),
        ("   ", "no_speech"),
        (None, "transcription_invalid"),
        ("x" * 8001, "transcription_invalid"),
        ([], "transcription_invalid"),
    ],
)
def test_empty_or_malformed_engine_output_is_never_a_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: object, code: str
) -> None:
    fake_engine(monkeypatch, output)
    with pytest.raises(VoiceError) as caught:
        engines.transcribe(decode_wav(make_wav()), tmp_path)
    assert caught.value.code == code


def test_vendor_exception_is_mapped_to_a_content_free_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_engine(monkeypatch, [], failure=RuntimeError("PRIVATE-AUDIO-OR-TEXT"))
    with pytest.raises(VoiceError, match=r"^transcription_failed$") as caught:
        engines.transcribe(decode_wav(make_wav()), tmp_path)
    assert "PRIVATE" not in str(caught.value)


def test_iterator_is_not_consumed_or_promoted_to_a_partial_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_iterator() -> Iterator[str]:
        pytest.fail("Malformed model output must not be iterated")
        yield "Частичный текст"

    fake_engine(monkeypatch, unexpected_iterator())
    with pytest.raises(VoiceError, match=r"^transcription_invalid$"):
        engines.transcribe(decode_wav(make_wav()), tmp_path)


def test_nonstandard_rate_is_rejected_without_loading_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORT_DISABLE_TELEMETRY", "0")
    calls = fake_engine(monkeypatch, "Unexpected draft")
    with pytest.raises(VoiceError, match=r"^audio_unsupported$"):
        engines.transcribe(decode_wav(make_wav(rate=17_000)), tmp_path)
    assert calls == []
    assert os.environ["ORT_DISABLE_TELEMETRY"] == "0"


def test_missing_optional_engine_has_a_safe_distinct_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engines, "validate_model_directory", lambda _: True)

    def absent(_name: str) -> object:
        raise ImportError("PRIVATE-IMPORT-PATH")

    monkeypatch.setattr(importlib, "import_module", absent)
    with pytest.raises(VoiceError, match=r"^engine_unavailable$"):
        engines.transcribe(decode_wav(make_wav()), tmp_path)


def test_worker_keeps_vendor_python_and_native_output_off_protocol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    from satori.desktop.voice import worker

    def noisy_transcribe(clip: AudioClip, _path: Path) -> TranscriptionResult:
        print("PRIVATE-VENDOR-OUTPUT")
        print("PRIVATE-VENDOR-ERROR", file=sys.stderr)
        os.write(1, b"PRIVATE-NATIVE-OUTPUT")
        os.write(2, b"PRIVATE-NATIVE-ERROR")
        return TranscriptionResult(
            text="Проверяемый черновик",
            audio_duration_ms=clip.duration_ms,
            model_load_ms=1.0,
            transcription_ms=2.0,
        )

    monkeypatch.setattr(worker, "transcribe", noisy_transcribe)
    destination = io.StringIO()
    assert run_worker(tmp_path, io.BytesIO(make_wav()), destination) == 0
    result = VoiceWorkerResponse.model_validate_json(destination.getvalue())
    assert result.event == "result"
    assert result.result is not None
    assert result.result.text == "Проверяемый черновик"
    assert "PRIVATE" not in destination.getvalue()
    captured = capfd.readouterr()
    assert "PRIVATE" not in captured.out + captured.err


def test_worker_masks_arbitrary_exceptions_and_does_not_persist_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from satori.desktop.voice import worker

    def fail(_clip: AudioClip, _path: Path) -> TranscriptionResult:
        raise RuntimeError("PRIVATE-TRANSCRIPT-AND-PCM")

    monkeypatch.setattr(worker, "transcribe", fail)
    destination = io.StringIO()
    assert run_worker(tmp_path, io.BytesIO(make_wav()), destination) == 1
    assert json.loads(destination.getvalue()) == {
        "protocol_version": 1,
        "event": "error",
        "code": "transcription_failed",
    }
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("payload", "code"),
    [(b"PRIVATE-NOT-WAV", "audio_invalid"), (b"x" * (MAX_WAV_BYTES + 1), "audio_too_large")],
)
def test_worker_rejects_input_without_running_speech(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: bytes, code: str
) -> None:
    from satori.desktop.voice import worker

    def forbidden(_clip: AudioClip, _path: Path) -> TranscriptionResult:
        raise AssertionError("must-not-transcribe")

    monkeypatch.setattr(worker, "transcribe", forbidden)
    destination = io.StringIO()
    assert run_worker(tmp_path, io.BytesIO(payload), destination) == 1
    assert json.loads(destination.getvalue())["code"] == code
    assert "PRIVATE" not in destination.getvalue()


def test_cli_child_returns_one_safe_record_without_loading_model(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "satori.desktop.voice.worker", "--model-directory", str(tmp_path)],
        input=b"PRIVATE-NOT-WAV",
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 1
    assert len(completed.stdout.splitlines()) == 1
    assert json.loads(completed.stdout)["code"] == "audio_invalid"
    assert completed.stderr == b""
    assert list(tmp_path.iterdir()) == []


def test_cli_discards_native_output_buffered_until_child_exit(tmp_path: Path) -> None:
    helper = (
        "import ctypes\n"
        "from satori.desktop.voice import worker\n"
        "from satori.desktop.voice.contracts import TranscriptionResult\n"
        "def noisy(clip,path):\n"
        " ctypes.CDLL(None).printf(b'PRIVATE-BUFFERED-NATIVE-OUTPUT')\n"
        " return TranscriptionResult(text='Test draft',audio_duration_ms=clip.duration_ms,"
        "model_load_ms=0.0,transcription_ms=1.0)\n"
        "worker.transcribe=noisy\n"
        "raise SystemExit(worker.main())\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", helper, "--model-directory", str(tmp_path)],
        input=make_wav(),
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0
    assert len(completed.stdout.splitlines()) == 1
    assert json.loads(completed.stdout)["result"]["text"] == "Test draft"
    assert b"PRIVATE" not in completed.stdout
    assert completed.stderr == b""


def test_response_has_one_outcome_and_no_nonfinite_metrics() -> None:
    with pytest.raises(ValidationError):
        VoiceWorkerResponse(event="result")
    with pytest.raises(ValidationError):
        VoiceWorkerResponse.model_validate({"event": "result", "code": "no_speech"})
    with pytest.raises(ValidationError):
        TranscriptionResult(
            text="Привет",
            audio_duration_ms=100.0,
            model_load_ms=float("nan"),
            transcription_ms=1.0,
        )
