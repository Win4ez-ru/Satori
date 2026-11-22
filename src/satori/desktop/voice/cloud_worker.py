"""One explicit cloud-speech child; credentials arrive only through private stdin."""

import argparse
import contextlib
import logging
import os
import sys
from collections.abc import Iterator
from typing import BinaryIO, TextIO

from pydantic import ValidationError

from satori.desktop.voice.cloud import CloudSpeechRequest, cloud_audio_chunks
from satori.desktop.voice.speech_contracts import (
    MAX_SPEECH_REQUEST_BYTES,
    SpeechFailure,
)
from satori.desktop.voice.speech_worker import emit_audio_stream


def run_worker(source: BinaryIO, destination: TextIO) -> int:
    """Validate before connecting; emit only shared bounded PCM/static outcome records."""

    try:
        payload = source.read(MAX_SPEECH_REQUEST_BYTES + 1)
        if len(payload) > MAX_SPEECH_REQUEST_BYTES:
            raise ValueError("configuration_invalid")
        request = CloudSpeechRequest.model_validate_json(payload)
    except (ValidationError, ValueError, UnicodeError):
        destination.write(SpeechFailure(code="configuration_invalid").model_dump_json() + "\n")
        destination.flush()
        return 1

    def chunks() -> Iterator[tuple[bytes, float]]:
        # Keep even an eager iterator-factory exception inside shared static handling.
        yield from cloud_audio_chunks(request)

    return emit_audio_stream(request, chunks(), destination)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Satori private explicit cloud speech worker")
    parser.parse_args(argv)
    logging.disable(logging.CRITICAL)
    source = sys.stdin.buffer
    # Vendor/native/atexit output cannot reach the protocol or reveal a session credential.
    with (
        os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8") as destination,
        open(os.devnull, "w") as sink,
    ):
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            return run_worker(source, destination)


if __name__ == "__main__":
    raise SystemExit(main())
