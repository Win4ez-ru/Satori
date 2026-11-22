"""One-shot owned local STT child; input is bounded WAV and output is safe JSON."""

import argparse
import contextlib
import logging
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO, TextIO

from satori.desktop.voice.contracts import (
    MAX_WAV_BYTES,
    VoiceError,
    VoiceWorkerResponse,
    decode_wav,
)
from satori.desktop.voice.engines import transcribe


@contextlib.contextmanager
def _quiet_engine() -> Iterator[None]:
    """This dedicated process reserves stdout for its one protocol record."""

    with open(os.devnull, "w") as sink:
        saved: list[tuple[int, int]] = []
        try:
            for stream in (sys.stdout, sys.stderr):
                stream.flush()
            for descriptor in (1, 2):
                saved.append((descriptor, os.dup(descriptor)))
                os.dup2(sink.fileno(), descriptor)
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                yield
        finally:
            for descriptor, original in saved:
                os.dup2(original, descriptor)
                os.close(original)


def run_worker(model_directory: Path, source: BinaryIO, destination: TextIO) -> int:
    """Read one clip, emit one result, then release transient audio with the process."""

    try:
        if not model_directory.is_absolute():
            raise VoiceError("configuration_invalid")
        payload = source.read(MAX_WAV_BYTES + 1)
        clip = decode_wav(payload)
        # Lazy model import/loading, progress, native libraries and arbitrary
        # provider exceptions are all contained behind this private process pipe.
        with _quiet_engine():
            result = transcribe(clip, model_directory)
        response = VoiceWorkerResponse(event="result", result=result)
        exit_code = 0
    except VoiceError as error:
        response = VoiceWorkerResponse(event="error", code=error.code)
        exit_code = 1
    except Exception:
        response = VoiceWorkerResponse(event="error", code="transcription_failed")
        exit_code = 1
    destination.write(response.model_dump_json(exclude_none=True) + "\n")
    destination.flush()
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Satori private local speech worker")
    parser.add_argument("--model-directory", type=Path, required=True)
    arguments = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)
    source = sys.stdin.buffer
    # Reserve a distinct pipe descriptor for the one protocol record. Native
    # libraries may buffer output until destruction/process exit, after a Python
    # redirect context has ended; their stdout/stderr remain discarded for this
    # dedicated child's entire remaining lifetime.
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
