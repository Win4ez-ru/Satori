"""One owned, offline neural-speech child; PCM exists only in private process pipes."""

import argparse
import base64
import contextlib
import importlib
import logging
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from time import monotonic
from typing import BinaryIO, TextIO

from pydantic import ValidationError

from satori.desktop.voice.neural_models import validate_model_directory
from satori.desktop.voice.speech_contracts import (
    MAX_FRAME_BYTES,
    MAX_SPEECH_REQUEST_BYTES,
    MAX_SPEECH_SECONDS,
    AudioFrame,
    SpeechComplete,
    SpeechError,
    SpeechFailure,
    SpeechRate,
    SpeechRequest,
)


def _offline_runtime() -> None:
    os.environ.update(
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        HF_HUB_DISABLE_TELEMETRY="1",
        DO_NOT_TRACK="1",
        TOKENIZERS_PARALLELISM="false",
    )

    def deny_network(event: str, arguments: tuple[object, ...]) -> None:
        if event == "socket.connect" and len(arguments) > 1 and isinstance(arguments[1], tuple):
            raise SpeechError("speech_engine_unavailable")

    sys.addaudithook(deny_network)


def _audio_chunks(request: SpeechRequest, directory: Path) -> Iterator[tuple[bytes, float]]:
    """Load pinned safetensors locally, keeping one speaker across every delivery style."""

    if not validate_model_directory(directory):
        raise SpeechError("speech_model_unavailable")
    _offline_runtime()
    try:
        mx = importlib.import_module("mlx.core")
        np = importlib.import_module("numpy")
        av = importlib.import_module("av")
        load_model = importlib.import_module("mlx_audio.tts.utils").load_model
    except ImportError as error:
        raise SpeechError("speech_engine_unavailable") from error
    mx.set_cache_limit(128 * 1024 * 1024)
    mx.set_memory_limit(3 * 1024 * 1024 * 1024)
    started = monotonic()
    model = load_model(str(directory))
    if (
        model.config.tts_model_type != "custom_voice"
        or model.config.tts_model_size != "1b7"
        or model.sample_rate != 24_000
    ):
        raise SpeechError("speech_engine_unavailable")
    load_ms = (monotonic() - started) * 1000
    resampler = av.AudioResampler(format="s16", layout="mono", rate=request.sample_rate)
    mx.random.seed(42)
    samples = 0
    tokens = 0
    # Fixed global sampling settings; no text-specific pronunciation or content rewriting.
    for result in model.generate(
        request.text,
        voice="Serena",
        instruct=request.delivery.instruction,
        lang_code="russian",
        stream=True,
        streaming_interval=1.0,
        max_tokens=1500,
        temperature=0.6,
        repetition_penalty=1.1,
        top_k=50,
        top_p=1.0,
        verbose=False,
    ):
        mx.eval(result.audio)
        block = np.asarray(result.audio, dtype=np.float32).reshape(-1)
        if not block.size:
            continue
        if not np.isfinite(block).all() or int(result.sample_rate) != 24_000:
            raise SpeechError("speech_audio_invalid")
        samples += int(block.size)
        tokens += int(result.token_count)
        if samples > 24_000 * MAX_SPEECH_SECONDS:
            raise SpeechError("speech_too_long")
        frame = av.AudioFrame.from_ndarray(block.reshape(1, -1), format="flt", layout="mono")
        frame.sample_rate = 24_000
        for converted in resampler.resample(frame):
            yield converted.to_ndarray().tobytes(), load_ms
    # Reaching the token budget is an incomplete synthesis, never a successful full reply.
    if tokens >= 1500:
        raise SpeechError("speech_too_long")
    for converted in resampler.resample(None):
        yield converted.to_ndarray().tobytes(), load_ms


def _frames(pcm: bytes, rate: SpeechRate, sequence: int) -> Iterator[AudioFrame]:
    if len(pcm) % 2:
        raise SpeechError("speech_audio_invalid")
    frame_size = min(MAX_FRAME_BYTES, rate * 2)
    for offset in range(0, len(pcm), frame_size):
        block = pcm[offset : offset + frame_size]
        yield AudioFrame(
            sequence=sequence,
            sample_rate=rate,
            pcm_base64=base64.b64encode(block).decode("ascii"),
        )
        sequence += 1


def run_worker(directory: Path, source: BinaryIO, destination: TextIO) -> int:
    """Stream bounded records, discarding vendor exceptions and all transient text on exit."""

    try:
        if not directory.is_absolute():
            raise SpeechError("configuration_invalid")
        payload = source.read(MAX_SPEECH_REQUEST_BYTES + 1)
        if len(payload) > MAX_SPEECH_REQUEST_BYTES:
            raise SpeechError("configuration_invalid")
        request = SpeechRequest.model_validate_json(payload)
    except (ValueError, UnicodeError):
        destination.write(SpeechFailure(code="configuration_invalid").model_dump_json() + "\n")
        destination.flush()
        return 1

    def chunks() -> Iterator[tuple[bytes, float]]:
        yield from _audio_chunks(request, directory)

    return emit_audio_stream(request, chunks(), destination)


def emit_audio_stream(
    request: SpeechRequest,
    chunks: Iterator[tuple[bytes, float]],
    destination: TextIO,
) -> int:
    """Share framing and closed outcomes across explicitly selected speech engines."""

    try:
        began = monotonic()
        first_ms: float | None = None
        load_ms = 0.0
        total = 0
        sequence = 0
        pending = bytearray()
        packet_bytes = request.sample_rate * 2 // 4

        def send(pcm: bytes) -> None:
            nonlocal first_ms, sequence
            for frame in _frames(pcm, request.sample_rate, sequence):
                if first_ms is None:
                    first_ms = (monotonic() - began) * 1000
                destination.write(frame.model_dump_json() + "\n")
                destination.flush()
                sequence += 1

        for pcm, chunk_load_ms in chunks:
            load_ms = chunk_load_ms
            if len(pcm) % 2:
                raise SpeechError("speech_audio_invalid")
            total += len(pcm)
            if total > request.sample_rate * 2 * MAX_SPEECH_SECONDS:
                raise SpeechError("speech_too_long")
            pending.extend(pcm)
            # Transport/native chunk boundaries must not determine protocol record counts.
            # 250 ms packets bound a full 120-second stream to 480 records plus residual.
            while len(pending) >= packet_bytes:
                send(bytes(pending[:packet_bytes]))
                del pending[:packet_bytes]
        if pending:
            send(bytes(pending))
        if first_ms is None:
            raise SpeechError("speech_audio_invalid")
        complete = SpeechComplete(
            total_pcm_bytes=total,
            model_load_ms=load_ms,
            first_chunk_ms=first_ms,
            generation_ms=(monotonic() - began) * 1000,
        )
        destination.write(complete.model_dump_json() + "\n")
        destination.flush()
        return 0
    except (ValidationError, UnicodeError):
        code = SpeechFailure(code="configuration_invalid")
    except SpeechError as error:
        code = SpeechFailure(code=error.code)
    except Exception:
        code = SpeechFailure(code="speech_failed")
    destination.write(code.model_dump_json() + "\n")
    destination.flush()
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Satori private local neural speech worker")
    parser.add_argument("--model-directory", type=Path, required=True)
    arguments = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)
    source = sys.stdin.buffer
    # Native libraries can flush late: keep both normal descriptors discarded until exit.
    with (
        os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8") as destination,
        open(os.devnull, "w") as sink,
    ):
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            return run_worker(arguments.model_directory, source, destination)


if __name__ == "__main__":
    raise SystemExit(main())
