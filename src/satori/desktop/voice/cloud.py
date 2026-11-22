"""Explicit, single-request cloud speech; only transient PCM and session credentials.

The caller owns disclosure consent and canonical reply correlation. Provider retention is
not controlled by this adapter. There is no retry after an uncertain delivery.
"""

import contextlib
import http.client
import importlib
import json
import signal
import ssl
import threading
from collections.abc import Iterator
from time import monotonic
from types import FrameType

import certifi
from pydantic import ConfigDict, Field, SecretStr, field_validator

from satori.desktop.voice.cloud_text import admit_cloud_speech_text
from satori.desktop.voice.expression import VoiceQualifier
from satori.desktop.voice.speech_contracts import (
    MAX_SPEECH_SECONDS,
    SpeechError,
    SpeechRequest,
)

_HOST = "api.elevenlabs.io"
_INPUT_RATE = 24_000
_READ_BYTES = 16_384
_DEADLINE_SECONDS = 180.0
_SOCKET_TIMEOUT_SECONDS = 15.0
_TAGS: dict[VoiceQualifier, str] = {
    "neutral": "",
    "precise": "[measured]",
    "direct": "[confident]",
    "gentle_amusement": "[lightly amused]",
    "contained_strain": "[restrained]",
    "reflective": "[thoughtful]",
    "bright": "[upbeat]",
    "warm": "[warmly]",
    "supportive": "[reassuring]",
    "reserved": "[reserved]",
    "engaged": "[curious]",
}


class CloudSpeechRequest(SpeechRequest):
    """Session-only credential boundary; default serialization deliberately masks the key."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    api_key: SecretStr = Field(min_length=20, max_length=512, repr=False)
    voice_id: str = Field(min_length=20, max_length=128, pattern=r"^[A-Za-z0-9]+$")

    @field_validator("api_key", mode="before")
    @classmethod
    def strict_key(cls, value: object) -> object:
        if not isinstance(value, str | SecretStr):
            raise ValueError("cloud_credentials_invalid")
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if not 20 <= len(raw) <= 512 or any(not 33 <= ord(char) <= 126 for char in raw):
            raise ValueError("cloud_credentials_invalid")
        return value


def speech_body(request: CloudSpeechRequest) -> dict[str, str | int]:
    """Admit rendered content before prefixing closed, owned audio directions."""

    admit_cloud_speech_text(request.text)
    tags = " ".join(_TAGS[item] for item in request.delivery.qualifiers if _TAGS[item])
    text = f"{tags} {request.text}" if tags else request.text
    return {"text": text, "model_id": "eleven_v4", "language_code": "ru", "seed": 42}


@contextlib.contextmanager
def _deadline() -> Iterator[float]:
    """Bound even a trickling HTTP header/DNS operation in this owned POSIX child.

    The adapter runs on its private worker's main thread. Refuse to replace another
    owner's timer or silently weaken the wall deadline on an unsupported platform.
    """

    if threading.current_thread() is not threading.main_thread() or not hasattr(
        signal, "setitimer"
    ):
        raise SpeechError("speech_engine_unavailable")
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    if previous_timer != (0.0, 0.0):
        raise SpeechError("speech_engine_unavailable")
    previous_handler = signal.getsignal(signal.SIGALRM)

    def expired(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        raise SpeechError("speech_network_failed")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, _DEADLINE_SECONDS)
    try:
        yield monotonic() + _DEADLINE_SECONDS
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _remaining(deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise SpeechError("speech_network_failed")
    return min(_SOCKET_TIMEOUT_SECONDS, remaining)


def cloud_audio_chunks(request: CloudSpeechRequest) -> Iterator[tuple[bytes, float]]:
    """Yield PCM16LE mono at the requested rate from one fixed HTTPS REST stream."""

    body = speech_body(request)
    try:
        av = importlib.import_module("av")
        np = importlib.import_module("numpy")
    except ImportError:
        raise SpeechError("speech_engine_unavailable") from None
    resampler = av.AudioResampler(format="s16", layout="mono", rate=request.sample_rate)
    payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    context = ssl.create_default_context(cafile=certifi.where())
    # A direct HTTPSConnection has no ambient proxy, netrc, cookie, auth, redirect or retry handler.
    connection = http.client.HTTPSConnection(
        _HOST, timeout=_SOCKET_TIMEOUT_SECONDS, context=context
    )
    response: http.client.HTTPResponse | None = None
    try:
        with _deadline() as deadline:
            connection.timeout = _remaining(deadline)
            connection.request(
                "POST",
                f"/v1/text-to-speech/{request.voice_id}/stream?output_format=pcm_24000",
                body=payload,
                headers={
                    "xi-api-key": request.api_key.get_secret_value(),
                    "Content-Type": "application/json",
                    "Accept": "audio/pcm, application/octet-stream",
                    "Accept-Encoding": "identity",
                },
            )
            response = connection.getresponse()
            if response.status in {401, 403}:
                raise SpeechError("speech_credentials_rejected")
            if response.status in {402, 429}:
                raise SpeechError("speech_quota_exceeded")
            if response.status >= 500:
                raise SpeechError("speech_provider_unavailable")
            if response.status != 200:
                raise SpeechError("speech_provider_rejected")
            content_type = (
                (response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
            )
            if (
                content_type not in {"audio/pcm", "application/octet-stream"}
                or (response.getheader("Content-Encoding", "identity") or "identity").lower()
                != "identity"
            ):
                raise SpeechError("speech_provider_rejected")
            expected_length: int | None = None
            declared = response.getheader("Content-Length")
            maximum = _INPUT_RATE * 2 * MAX_SPEECH_SECONDS
            if declared is not None:
                if not declared.isascii() or not declared.isdecimal() or len(declared) > 12:
                    raise SpeechError("speech_provider_rejected")
                expected_length = int(declared)
                if expected_length > maximum:
                    raise SpeechError("speech_too_long")
                if expected_length == 0 or expected_length % 2:
                    raise SpeechError("speech_audio_invalid")
            received = 0
            produced = 0
            carry = b""
            while True:
                timeout = _remaining(deadline)
                if connection.sock is not None:
                    connection.sock.settimeout(timeout)
                chunk = response.read1(_READ_BYTES)
                _remaining(deadline)
                if not chunk:
                    break
                received += len(chunk)
                if received > maximum:
                    raise SpeechError("speech_too_long")
                if len(chunk) > _READ_BYTES:
                    raise SpeechError("speech_audio_invalid")
                aligned = carry + chunk
                aligned_size = len(aligned) - len(aligned) % 2
                carry = aligned[aligned_size:]
                if not aligned_size:
                    continue
                values = np.frombuffer(aligned[:aligned_size], dtype="<i2").reshape(1, -1)
                frame = av.AudioFrame.from_ndarray(values, format="s16", layout="mono")
                frame.sample_rate = _INPUT_RATE
                for converted in resampler.resample(frame):
                    pcm = converted.to_ndarray().astype("<i2", copy=False).tobytes()
                    produced += len(pcm)
                    if produced > request.sample_rate * 2 * MAX_SPEECH_SECONDS:
                        raise SpeechError("speech_too_long")
                    if pcm:
                        yield pcm, 0.0
            if (
                carry
                or received == 0
                or (expected_length is not None and received != expected_length)
            ):
                raise SpeechError("speech_audio_invalid")
            for converted in resampler.resample(None):
                _remaining(deadline)
                pcm = converted.to_ndarray().astype("<i2", copy=False).tobytes()
                produced += len(pcm)
                if produced > request.sample_rate * 2 * MAX_SPEECH_SECONDS:
                    raise SpeechError("speech_too_long")
                if pcm:
                    yield pcm, 0.0
    except SpeechError:
        raise
    except (OSError, http.client.HTTPException):
        raise SpeechError("speech_network_failed") from None
    except Exception:
        raise SpeechError("speech_failed") from None
    finally:
        if response is not None:
            response.close()
        connection.close()
