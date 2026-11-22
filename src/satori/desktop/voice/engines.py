"""Lazy, local-only Russian speech recognition for an explicitly supplied clip."""

import importlib
import os
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import ValidationError

from satori.desktop.voice.contracts import (
    MAX_TRANSCRIPT_CHARS,
    AudioClip,
    TranscriptionResult,
    VoiceError,
)
from satori.desktop.voice.models import validate_model_directory

SUPPORTED_SAMPLE_RATES = frozenset({16_000, 22_050, 24_000, 32_000, 44_100, 48_000})


def _engine_modules() -> tuple[Any, Any, Any]:
    """Keep optional vendor imports and their dynamic types at this boundary."""

    try:
        # The POSIX opt-out prevents uploader initialization during native import.
        os.environ["ORT_DISABLE_TELEMETRY"] = "1"
        runtime = importlib.import_module("onnxruntime")
        # Also suppress future events if a caller already imported the runtime.
        runtime.disable_telemetry_events()
        return (
            importlib.import_module("onnx_asr.loader").Manager,
            runtime,
            importlib.import_module("numpy"),
        )
    except (ImportError, OSError, AttributeError, RuntimeError) as error:
        raise VoiceError("engine_unavailable") from error


def transcribe(clip: AudioClip, model_directory: Path) -> TranscriptionResult:
    """Return a draft hypothesis; no Core calls, download, file audio or state write."""

    try:
        model_ready = model_directory.is_absolute() and validate_model_directory(model_directory)
    except Exception as error:
        raise VoiceError("model_unavailable") from error
    if not model_ready:
        raise VoiceError("model_unavailable")
    if clip.sample_rate not in SUPPORTED_SAMPLE_RATES:
        raise VoiceError("audio_unsupported")
    # Exact digital silence is not a speech sample. Do not invent a confidence
    # threshold that might reject quiet but real speech or treat silence as text.
    if not any(clip.pcm16le):
        raise VoiceError("no_speech")
    load_started = perf_counter()
    try:
        manager_type, runtime, numpy = _engine_modules()
        options = runtime.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        manager = manager_type(
            sess_options=options,
            providers=["CPUExecutionProvider"],
            preprocessor_config={"use_numpy_preprocessors": True, "max_concurrent_workers": 1},
        )
        model = manager.create_asr(
            "gigaam-v3-e2e-rnnt",
            model_directory,
            quantization="int8",
            offline=True,
        )
        loaded = perf_counter()
        waveform = numpy.frombuffer(clip.pcm16le, dtype="<i2").astype(numpy.float32) / 32768.0
        # The vendor's bundled resampler consumes the actual device rate. The
        # model receives only this transient array, without a filename or prompt.
        text = model.recognize(waveform, sample_rate=clip.sample_rate)
        if not isinstance(text, str) or len(text) > MAX_TRANSCRIPT_CHARS:
            raise VoiceError("transcription_invalid")
        text = text.strip()
        if not text:
            raise VoiceError("no_speech")
        return TranscriptionResult(
            text=text,
            audio_duration_ms=clip.duration_ms,
            model_load_ms=(loaded - load_started) * 1000.0,
            transcription_ms=(perf_counter() - loaded) * 1000.0,
        )
    except VoiceError:
        raise
    except ValidationError as error:
        raise VoiceError("transcription_invalid") from error
    except Exception as error:
        raise VoiceError("transcription_failed") from error
