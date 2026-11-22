"""Private, bounded neural synthesis protocol; no canonical state or audio persistence."""

import base64
import binascii
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from satori.desktop.voice.expression import VoiceDelivery

MAX_SPEECH_SECONDS = 120
MAX_SPEECH_RECORD_BYTES = 140_000
MAX_SPEECH_REQUEST_BYTES = 64_000
MAX_FRAME_BYTES = 96_000
SpeechRate = Literal[16_000, 24_000, 44_100, 48_000]
SpeechErrorCode = Literal[
    "configuration_invalid",
    "speech_model_unavailable",
    "speech_engine_unavailable",
    "speech_failed",
    "speech_audio_invalid",
    "speech_too_long",
    "speech_credentials_required",
    "speech_credentials_rejected",
    "speech_quota_exceeded",
    "speech_network_failed",
    "speech_provider_unavailable",
    "speech_provider_rejected",
    "speech_text_unsupported",
]


class SpeechRequest(BaseModel):
    """Only a rendered copy of a committed reply and a read-only Core projection."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    protocol_version: Literal[1] = 1
    text: str = Field(min_length=1, max_length=8000, repr=False)
    sample_rate: SpeechRate = 24_000
    delivery: VoiceDelivery = Field(default_factory=VoiceDelivery)

    @field_validator("text")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("speech_text_invalid")
        return value


class AudioFrame(BaseModel):
    """At most one second of PCM16LE mono per record, in sequence."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    event: Literal["audio"] = "audio"
    sequence: int = Field(ge=0, le=1000)
    sample_rate: SpeechRate
    pcm_base64: str = Field(min_length=4, max_length=128_000, repr=False)

    def pcm(self) -> bytes:
        try:
            payload = base64.b64decode(self.pcm_base64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("speech_audio_invalid") from error
        if (
            not payload
            or len(payload) % 2
            or len(payload) > min(MAX_FRAME_BYTES, self.sample_rate * 2)
        ):
            raise ValueError("speech_audio_invalid")
        return payload


class SpeechComplete(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)

    event: Literal["complete"] = "complete"
    total_pcm_bytes: int = Field(gt=0, le=MAX_SPEECH_SECONDS * 48_000 * 2)
    model_load_ms: float = Field(ge=0)
    first_chunk_ms: float = Field(ge=0)
    generation_ms: float = Field(ge=0)


class SpeechFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    event: Literal["error"] = "error"
    code: SpeechErrorCode


SpeechRecord = Annotated[AudioFrame | SpeechComplete | SpeechFailure, Field(discriminator="event")]
SPEECH_RECORD: TypeAdapter[SpeechRecord] = TypeAdapter(SpeechRecord)


class SpeechError(ValueError):
    def __init__(self, code: SpeechErrorCode) -> None:
        self.code = code
        super().__init__(code)
