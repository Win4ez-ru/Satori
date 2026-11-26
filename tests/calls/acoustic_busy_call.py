"""Manual Mac loopback acceptance with local speech and a one- or two-turn caller recording.

Run `python -m tests.calls.acoustic_busy_call CALLER.wav NEW_OUTPUT_DIRECTORY` from work
with the pinned desktop dependencies and GigaAM weights already installed.
"""

import argparse
import asyncio
import json
import re
import subprocess
import wave
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import numpy as np

from satori.application.calls.intake import CallEndReceipt
from satori.desktop.settings import default_voice_model_directory
from satori.desktop.voice.contracts import AudioClip
from satori.desktop.voice.engines import transcribe
from satori.infrastructure.calls.audiosocket import (
    AudioSocketLimits,
    AudioSocketPCM,
    AudioSocketServer,
)
from satori.infrastructure.calls.inbox import CallInbox
from tests.busy_call_support import FixtureIntakeAuthority
from tests.calls.test_audiosocket import Admission, connect, eof, frame, grant, settled

RATE = 8000
FRAME = 320  # 20 ms of mono PCM16LE
SILENT_FRAME = bytes(FRAME)


def read_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as f:
        if (f.getnchannels(), f.getsampwidth(), f.getframerate()) != (1, 2, RATE):
            raise ValueError("expected 8 kHz mono PCM16LE")
        if not 0 < f.getnframes() <= 15 * RATE:
            raise ValueError("expected at most 15 seconds of audio")
        return f.readframes(f.getnframes())


def save_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as f:
        f.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        f.writeframes(pcm)


def synthesize(line: str) -> bytes:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "line.wav"
        subprocess.run(
            [
                "/usr/bin/say",
                "-v",
                "Milena",
                "-r",
                "165",
                "-o",
                str(path),
                "--file-format=WAVE",
                "--data-format=LEI16@8000",
                line,
            ],
            timeout=12,
            check=True,
        )
        return read_wav(path)


def recognize(pcm: bytes) -> str:
    # GigaAM accepts 16 kHz clips. Preserve the telephone-rate waveform and its duration.
    samples = np.frombuffer(pcm, dtype="<i2")
    up = np.interp(np.arange(samples.size * 2) / 2, np.arange(samples.size), samples)
    clip = AudioClip(sample_rate=16000, pcm16le=up.astype("<i2").tobytes())
    return transcribe(clip, default_voice_model_directory()).text


def rms(pcm: bytes) -> float:
    signal = np.frombuffer(pcm, dtype="<i2").astype(float)
    return float(np.sqrt(np.mean(signal**2)))


class TelephoneSpeech:
    def __init__(self) -> None:
        self.transcripts: list[str] = []
        self.buffer = bytearray()
        self.in_speech = False
        self.quiet_bytes = 0

    async def speak(self, line: str, sink: AudioSocketPCM) -> None:
        pcm = await asyncio.to_thread(synthesize, line)
        for start in range(0, len(pcm), FRAME):
            await sink.write(pcm[start : start + FRAME])

    async def transcribe(self, pcm: bytes) -> str | None:
        self.buffer.extend(pcm)
        if len(self.buffer) > 15 * RATE * 2:
            raise ValueError("utterance exceeds acoustic test limit")
        if rms(pcm) >= 75:
            self.in_speech = True
            self.quiet_bytes = 0
        else:
            self.quiet_bytes += len(pcm)
        if not self.in_speech or self.quiet_bytes < 25 * FRAME:
            return None
        utterance = bytes(self.buffer)
        self.buffer.clear()
        self.quiet_bytes = 0
        self.in_speech = False
        line = await asyncio.to_thread(recognize, utterance)
        self.transcripts.append(line)
        return line


def caller_turns(recording: bytes) -> tuple[bytes, ...]:
    # Pause the recording for Satori's follow-up at the first inter-utterance silence.
    active = False
    quiet = 0
    for i in range(0, len(recording), FRAME):
        chunk = recording[i : i + FRAME]
        if rms(chunk) >= 75:
            active = True
            quiet = 0
        elif active:
            quiet += len(chunk)
            if quiet >= 35 * FRAME:
                end = i + len(chunk)
                tail = recording[end:]
                if any(rms(tail[j : j + FRAME]) >= 75 for j in range(0, len(tail), FRAME)):
                    return recording[:end], tail
                break
    return (recording,)


async def hear(reader: asyncio.StreamReader) -> bytes:
    # Only bytes received by the caller go into the return-channel evidence.
    pcm = bytearray()
    while len(pcm) < 15 * RATE * 2:
        try:
            header = await asyncio.wait_for(reader.readexactly(3), 0.65 if pcm else 14)
        except TimeoutError:
            if pcm:
                return bytes(pcm)
            raise
        size = int.from_bytes(header[1:], "big")
        if header[0] != 0x10 or not 0 < size <= 3200 or size % 2:
            raise ValueError("invalid return audio")
        pcm.extend(await asyncio.wait_for(reader.readexactly(size), 1))
    raise ValueError("return audio exceeds limit")


def words(text: str) -> str:
    # Acceptance only: all three spellings mean eight o'clock. Keep the preposition mandatory.
    text = re.sub(r"\b0?8:00\b", "восемь", text.casefold())
    return " ".join("восемь" if word == "8" else word for word in re.findall(r"\w+", text))


async def run(recording_path: Path, output: Path) -> None:
    recording = read_wav(recording_path)
    turns = caller_turns(recording)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    save_wav(output / "caller.wav", recording)
    inbox = CallInbox(output / "profile")
    other = CallInbox(output / "other-profile")
    authorized = grant(principal=inbox.principal)
    speech = TelephoneSpeech()
    authority = FixtureIntakeAuthority(authorized)
    handler = inbox.busy_handler(
        authority.grant,
        authority=authority,
        speech=speech,
    )
    server = AudioSocketServer(
        Admission(authorized), handler, limits=AudioSocketLimits(callback_seconds=15)
    )
    replies: list[bytes] = []
    await server.start(enabled=True)
    try:
        reader, writer = await connect(server)

        async def send(kind: int, payload: bytes) -> None:
            writer.write(frame(kind, payload))
            await writer.drain()

        try:
            await send(1, authorized.stream_id.bytes)
            replies.append(await hear(reader))
            # The final quiet frames are telephone idle time after the recording ends.
            for pos, turn in enumerate(turns):
                answer = turn + SILENT_FRAME * 30 if pos == len(turns) - 1 else turn
                for i in range(0, len(answer), FRAME):
                    await send(0x10, answer[i : i + FRAME])
                    await asyncio.sleep(0.020)
                replies.append(await hear(reader))
            await send(0, b"")
            await eof(reader)
        finally:
            writer.close()
            await writer.wait_closed()
        media = await settled(server)
    finally:
        await server.close()
    before = len(inbox.entries())
    end = CallEndReceipt(
        binding=authorized.binding,
        receipt_id=uuid4(),
        status="confirmed_ended",
        observed_at=datetime.now(UTC),
    )
    handler.accept_call_end(end)
    first_delivery = len(inbox.entries())
    handler.accept_call_end(end)
    summaries = [item.text for item in inbox.entries()]
    other_count = len(other.entries())
    conversation = [replies[0]]
    for turn, reply in zip(turns, replies[1:], strict=True):
        conversation.extend((turn, reply))
    save_wav(output / "conversation.wav", (SILENT_FRAME * 12).join(conversation))
    readback = [await asyncio.to_thread(recognize, pcm) for pcm in replies]
    levels = [round(20 * float(np.log10(max(rms(pcm), 0.01) / 32768)), 1) for pcm in replies]
    checks = {
        "media": media.outcome == "peer_hangup"
        and not media.remote_termination_confirmed
        and media.dtmf_count == 0
        and media.received_pcm_bytes == len(recording) + 30 * FRAME
        and media.sent_pcm_bytes == sum(map(len, replies)),
        "caller_words": words(" ".join(speech.transcripts)) == "илья доставка будет в восемь",
        "owner_summary": len(summaries) == 1
        and words(summaries[0]) == "илья доставка будет в восемь",
        "private_once": (before, first_delivery, len(summaries), other_count) == (0, 1, 1, 0),
        "return_speech": all(level > -40 for level in levels)
        and all(token in words(readback[0]) for token in ("ассистент", "занят", "передать"))
        and (len(turns) == 1 or "вопрос" in words(readback[1]))
        and "передам" in words(readback[-1]),
    }
    receipt = {
        "source": "local authored WAV",
        "pcm_sha256": sha256(recording).hexdigest(),
        "wire": "AudioSocket TCP loopback, mono PCM16LE/8000",
        "asr": "local GigaAM v3 RNNT int8",
        "tts": "macOS Milena",
        "caller_asr": speech.transcripts,
        "return_channel_asr": readback,
        "return_channel_rms_dbfs": levels,
        "owner_inbox": summaries,
        "other_inbox_count": other_count,
        "durable_entry_counts": [before, first_delivery, len(summaries)],
        "media": media.model_dump(),
        "checks": checks,
    }
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    (output / "result.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    asyncio.run(asyncio.wait_for(run(args.recording, args.output), 110))
