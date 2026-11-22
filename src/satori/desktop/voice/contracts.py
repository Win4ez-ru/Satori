"""Bounded transient audio and speech results at the desktop boundary."""

import io
import struct
import wave
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

MAX_AUDIO_BYTES = 3_000_000
MAX_WAV_BYTES = 3_010_000
MAX_DURATION_SECONDS = 30
MAX_TRANSCRIPT_CHARS = 8000

VoiceErrorCode = Literal[
    "audio_invalid",
    "audio_too_large",
    "audio_too_long",
    "audio_unsupported",
    "no_speech",
    "model_unavailable",
    "engine_unavailable",
    "transcription_failed",
    "transcription_invalid",
    "configuration_invalid",
]


class VoiceError(ValueError):
    """A closed, content-free failure code; never a vendor exception string."""

    def __init__(self, code: VoiceErrorCode) -> None:
        self.code = code
        super().__init__(code)


class AudioClip(BaseModel):
    """Mono PCM16LE, held in memory for one explicitly initiated operation."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    pcm16le: bytes = Field(min_length=2, max_length=MAX_AUDIO_BYTES, repr=False, exclude=True)
    sample_rate: int = Field(ge=16_000, le=48_000)
    channels: Literal[1] = 1
    sample_width_bytes: Literal[2] = 2

    @model_validator(mode="after")
    def validate_frames(self) -> "AudioClip":
        if len(self.pcm16le) % 2:
            raise ValueError("audio_incomplete_frame")
        if self.frame_count > self.sample_rate * MAX_DURATION_SECONDS:
            raise ValueError("audio_too_long")
        return self

    @property
    def frame_count(self) -> int:
        return len(self.pcm16le) // 2

    @property
    def duration_ms(self) -> float:
        return self.frame_count / self.sample_rate * 1000.0


class TranscriptionResult(BaseModel):
    """A hypothesis for the editable draft, never an instruction to send it."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)

    text: str = Field(min_length=1, max_length=MAX_TRANSCRIPT_CHARS)
    language: Literal["ru"] = "ru"
    confidence: float | None = Field(default=None, ge=0, le=1)
    engine: Literal["gigaam-v3-e2e-rnnt/int8"] = "gigaam-v3-e2e-rnnt/int8"
    audio_duration_ms: float = Field(gt=0, le=MAX_DURATION_SECONDS * 1000)
    model_load_ms: float = Field(ge=0)
    transcription_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def nonblank_text(self) -> "TranscriptionResult":
        if not self.text.strip():
            raise ValueError("transcription_blank")
        return self


class VoiceWorkerResponse(BaseModel):
    """One bounded result or safe failure on the private speech process pipe."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    protocol_version: Literal[1] = 1
    event: Literal["result", "error"]
    result: TranscriptionResult | None = None
    code: VoiceErrorCode | None = None

    @model_validator(mode="after")
    def exclusive_outcome(self) -> "VoiceWorkerResponse":
        if self.event == "result" and (self.result is None or self.code is not None):
            raise ValueError("voice_result_required")
        if self.event == "error" and (self.code is None or self.result is not None):
            raise ValueError("voice_error_required")
        return self


def decode_wav(payload: bytes) -> AudioClip:
    """Validate a small complete PCM WAV without creating an audio file."""

    if len(payload) > MAX_WAV_BYTES:
        raise VoiceError("audio_too_large")
    if (
        len(payload) < 12
        or payload[:4] != b"RIFF"
        or payload[8:12] != b"WAVE"
        or struct.unpack_from("<I", payload, 4)[0] + 8 != len(payload)
    ):
        raise VoiceError("audio_invalid")
    try:
        with wave.open(io.BytesIO(payload), "rb") as source:
            if (
                source.getcomptype() != "NONE"
                or source.getnchannels() != 1
                or source.getsampwidth() != 2
                or not 16_000 <= source.getframerate() <= 48_000
            ):
                raise VoiceError("audio_unsupported")
            frames, sample_rate = source.getnframes(), source.getframerate()
            if frames > sample_rate * MAX_DURATION_SECONDS:
                raise VoiceError("audio_too_long")
            if frames * 2 > MAX_AUDIO_BYTES:
                raise VoiceError("audio_too_large")
            pcm = source.readframes(frames)
            if not frames or len(pcm) != frames * 2 or source.readframes(1):
                raise VoiceError("audio_invalid")
            return AudioClip(pcm16le=pcm, sample_rate=sample_rate)
    except VoiceError:
        raise
    except (EOFError, OSError, ValueError, wave.Error, ValidationError) as error:
        raise VoiceError("audio_invalid") from error


def wav_bytes(clip: AudioClip) -> bytes:
    """Create transient WAV input so the engine decodes/resamples its actual rate."""

    destination = io.BytesIO()
    with wave.open(destination, "wb") as output:
        output.setnchannels(clip.channels)
        output.setsampwidth(clip.sample_width_bytes)
        output.setframerate(clip.sample_rate)
        output.writeframes(clip.pcm16le)
    return destination.getvalue()
