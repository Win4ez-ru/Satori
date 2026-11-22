"""Transient push-to-talk and owned speech processes; recognition creates drafts only."""

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from pydantic import SecretStr, ValidationError
from PySide6.QtCore import (
    QCoreApplication,
    QIODevice,
    QMicrophonePermission,
    QObject,
    QPermission,
    QProcess,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtMultimedia import QAudioDevice, QAudioFormat, QAudioSource, QMediaDevices, QtAudio

from satori.desktop.voice.contracts import (
    MAX_AUDIO_BYTES,
    MAX_DURATION_SECONDS,
    MAX_TRANSCRIPT_CHARS,
    AudioClip,
    VoiceWorkerResponse,
    wav_bytes,
)
from satori.desktop.voice.expression import VoiceDelivery
from satori.desktop.voice.models import MODEL_SETUP_ERROR_CODES
from satori.desktop.voice.playback import PcmPlayback
from satori.desktop.voice.speech_contracts import (
    MAX_SPEECH_RECORD_BYTES,
    MAX_SPEECH_SECONDS,
    SPEECH_RECORD,
    AudioFrame,
    SpeechComplete,
    SpeechFailure,
    SpeechRate,
    SpeechRequest,
)
from satori.desktop.voice.speech_text import render_speech_text

MAX_PROCESS_OUTPUT_BYTES = 65_536
MAX_SETUP_RECORDS = 1024
ProcessKind = Literal["transcription", "setup", "speech"]


class _PcmBuffer(QObject):
    """Hard-bounded PCM storage fed by reads from Qt's native audio device."""

    limit_reached = Signal()

    def __init__(self, limit: int, parent: QObject) -> None:
        super().__init__(parent)
        self.pcm = bytearray()
        self.limit = limit
        self.overflowed = False

    def append(self, payload: bytes) -> int:
        if self.overflowed or len(self.pcm) + len(payload) > self.limit:
            if not self.overflowed:
                self.overflowed = True
                self.limit_reached.emit()
            return -1
        self.pcm.extend(payload)
        return len(payload)


@dataclass
class _Capture:
    generation: int
    token: str
    model_directory: Path
    sample_rate: int
    source: QAudioSource
    buffer: _PcmBuffer
    timer: QTimer
    permission_poll: QTimer
    input: QIODevice | None = None


@dataclass(eq=False)
class _ProcessJob:
    kind: ProcessKind
    generation: int
    process: QProcess
    timer: QTimer
    token: str = ""
    payload: bytes = field(default=b"", repr=False)
    output: bytearray = field(default_factory=bytearray, repr=False)
    setup_ready: bool = False
    records: int = 0
    neural: bool = False
    sample_rate: int = 0
    pcm_bytes: int = 0
    audio_frames: int = 0
    speech_complete: bool = False
    worker_done: bool = False


class VoiceController(QObject):
    """Keep microphone consent, draft delivery and playback separate from the Core."""

    transcript_ready = Signal(str, str)
    status_changed = Signal(str)
    activity_changed = Signal(str)
    setup_finished = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.activity = "idle"
        self._generation = 0
        self._capture: _Capture | None = None
        self._transcription: _ProcessJob | None = None
        self._setup: _ProcessJob | None = None
        self._speech: _ProcessJob | None = None
        self._retired: set[_ProcessJob] = set()
        self._permission_request: int | None = None
        self._cloud_api_key = SecretStr("")
        self._cloud_voice_id = ""
        self._playback = PcmPlayback(self)
        self._playback.failed.connect(self._playback_failed)
        self._playback.finished.connect(self._playback_finished)
        self._playback.first_buffer_written.connect(self._playback_started)

    def _update_activity(self) -> None:
        value = (
            "capturing"
            if self._capture is not None
            else "transcribing"
            if self._transcription is not None
            else "preparing_model"
            if self._setup is not None
            else "speaking"
            if self._speech is not None
            else "idle"
        )
        if value != self.activity:
            self.activity = value
            self.activity_changed.emit(value)

    def _permission_status(self) -> Qt.PermissionStatus:
        application = QCoreApplication.instance()
        if application is None:
            return Qt.PermissionStatus.Denied
        return application.checkPermission(QMicrophonePermission())

    def _request_permission(self, generation: int) -> None:
        application = QCoreApplication.instance()
        if application is None:
            self.status_changed.emit("microphone_permission_denied")
            return
        self._permission_request = generation
        application.requestPermission(
            QMicrophonePermission(),
            self,
            lambda permission: self._permission_received(generation, permission),
        )

    def _permission_received(self, generation: int, permission: QPermission) -> None:
        if self._permission_request != generation:
            return
        self._permission_request = None
        if generation != self._generation:
            return
        self.status_changed.emit(
            "microphone_permission_granted_press_again"
            if permission.status() == Qt.PermissionStatus.Granted
            else "microphone_permission_denied"
        )

    def begin_capture(self, token: str, model_directory: Path) -> None:
        """Start only an explicitly held PTT with an already granted permission."""

        self.cancel()
        if not token or len(token) > 256 or not model_directory.is_absolute():
            self.status_changed.emit("configuration_invalid")
            return
        if self._permission_request is not None:
            self.status_changed.emit("microphone_permission_pending")
            return
        permission = self._permission_status()
        if permission == Qt.PermissionStatus.Undetermined:
            self.status_changed.emit("microphone_permission_requested")
            self._request_permission(self._generation)
            return
        if permission != Qt.PermissionStatus.Granted:
            self.status_changed.emit("microphone_permission_denied")
            return
        try:
            configuration = self._audio_configuration()
        except (RuntimeError, OSError):
            self.status_changed.emit("capture_failed")
            return
        if configuration is None:
            self.status_changed.emit("audio_unsupported")
            return
        device, audio_format = configuration
        source = self._create_audio_source(device, audio_format)
        sample_rate = audio_format.sampleRate()
        sink = _PcmBuffer(min(MAX_AUDIO_BYTES, sample_rate * 2 * MAX_DURATION_SECONDS), self)
        timer = QTimer(self)
        timer.setSingleShot(True)
        permission_poll = QTimer(self)
        permission_poll.setInterval(250)
        job = _Capture(
            self._generation,
            token,
            model_directory,
            sample_rate,
            source,
            sink,
            timer,
            permission_poll,
        )
        self._capture = job
        timer.timeout.connect(lambda: self._capture_failed(job, "capture_timeout_discarded"))
        permission_poll.timeout.connect(lambda: self._capture_permission(job))
        sink.limit_reached.connect(
            lambda: self._capture_failed(job, "audio_too_large"),
            Qt.ConnectionType.QueuedConnection,
        )
        source.stateChanged.connect(lambda state: self._capture_state(job, state))
        self._update_activity()
        self.status_changed.emit("capturing")
        timer.start(MAX_DURATION_SECONDS * 1000)
        permission_poll.start()
        try:
            stream = source.start()
            if self._capture is not job:
                return
            if stream is None:
                self._capture_failed(job, "capture_failed")
                return
            job.input = stream
            stream.readyRead.connect(lambda: self._read_audio(job))
            self._read_audio(job)
        except (RuntimeError, OSError):
            self._capture_failed(job, "capture_failed")

    def _audio_configuration(self) -> tuple[QAudioDevice, QAudioFormat] | None:
        device = QMediaDevices.defaultAudioInput()
        if device.isNull():
            return None
        for sample_rate in (16_000, 48_000, 44_100):
            audio_format = QAudioFormat()
            audio_format.setChannelCount(1)
            audio_format.setSampleRate(sample_rate)
            audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            if device.isFormatSupported(audio_format):
                return device, audio_format
        return None

    def _create_audio_source(
        self, device: QAudioDevice, audio_format: QAudioFormat
    ) -> QAudioSource:
        return QAudioSource(device, audio_format, self)

    def _capture_state(self, job: _Capture, state: QtAudio.State) -> None:
        if (
            self._capture is job
            and state == QtAudio.State.StoppedState
            and job.source.error() != QtAudio.Error.NoError
        ):
            self._capture_failed(job, "capture_failed")

    def _capture_permission(self, job: _Capture) -> None:
        if self._capture is job and self._permission_status() != Qt.PermissionStatus.Granted:
            self._capture_failed(job, "microphone_permission_denied")

    def _read_audio(self, job: _Capture) -> None:
        if self._capture is not job or job.input is None:
            return
        while job.input.bytesAvailable() > 0:
            remaining = job.buffer.limit - len(job.buffer.pcm)
            payload = bytes(job.input.read(min(remaining + 1, 65_536)).data())
            if not payload:
                break
            if job.buffer.append(payload) < 0:
                self._capture_failed(job, "audio_too_large")
                return

    def _release_capture(self) -> _Capture | None:
        job, self._capture = self._capture, None
        if job is not None:
            job.timer.stop()
            job.permission_poll.stop()
            job.source.stop()
            job.source.deleteLater()
            job.timer.deleteLater()
            job.permission_poll.deleteLater()
            job.buffer.deleteLater()
        return job

    def _capture_failed(self, job: _Capture, code: str) -> None:
        if self._capture is not job or job.generation != self._generation:
            return
        self.cancel()
        self.status_changed.emit(code)

    def finish_capture(self) -> None:
        """A release creates one recognition job; timeouts/cancel never submit audio."""

        current = self._capture
        if current is not None:
            self._capture_permission(current)
            self._read_audio(current)
        job = self._release_capture()
        if job is None:
            return
        try:
            if job.buffer.overflowed:
                raise ValueError("capture_overflow")
            clip = AudioClip(pcm16le=bytes(job.buffer.pcm), sample_rate=job.sample_rate)
            payload = wav_bytes(clip)
        except (ValueError, ValidationError):
            self.status_changed.emit("audio_invalid")
            self._update_activity()
            return
        finally:
            job.buffer.pcm.clear()
        process_job = self._new_process_job("transcription", job.model_directory)
        process_job.token = job.token
        process_job.payload = payload
        self._transcription = process_job
        self._update_activity()
        self.status_changed.emit("transcribing")
        process_job.process.start()

    def _arguments(self, kind: ProcessKind, model_directory: Path) -> list[str]:
        flag, module = {
            "transcription": ("--voice-worker", "satori.desktop.voice.worker"),
            "setup": ("--voice-model-setup", "satori.desktop.voice.models"),
            "speech": ("--speech-worker", "satori.desktop.voice.speech_worker"),
        }[kind]
        prefix = [flag] if getattr(sys, "frozen", False) else ["-m", module]
        return [*prefix, "--model-directory", str(model_directory)]

    def _new_process_job(self, kind: ProcessKind, model_directory: Path) -> _ProcessJob:
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.setStandardErrorFile(QProcess.nullDevice())
        process.setProgram(sys.executable)
        process.setArguments(self._arguments(kind, model_directory))
        timer = QTimer(self)
        timer.setSingleShot(True)
        job = _ProcessJob(kind, self._generation, process, timer)
        process.started.connect(lambda: self._process_started(job))
        process.readyReadStandardOutput.connect(lambda: self._read_output(job))
        process.finished.connect(lambda code, status: self._process_finished(job, code, status))
        process.errorOccurred.connect(lambda error: self._process_error(job, error))
        timer.timeout.connect(lambda: self._job_failed(job, f"{kind}_timeout"))
        timer.start(
            120_000 if kind == "transcription" else 1_800_000 if kind == "setup" else 300_000
        )
        return job

    def _current(self, job: _ProcessJob) -> bool:
        return (
            job.generation == self._generation
            and job
            is {
                "transcription": self._transcription,
                "setup": self._setup,
                "speech": self._speech,
            }[job.kind]
        )

    def _process_started(self, job: _ProcessJob) -> None:
        if not self._current(job):
            job.payload = b""
            job.process.kill()
            return
        if job.payload:
            written = job.process.write(job.payload)
            if written != len(job.payload):
                self._job_failed(job, f"{job.kind}_failed")
                return
        job.payload = b""
        job.process.closeWriteChannel()

    def _read_output(self, job: _ProcessJob) -> None:
        if not self._current(job):
            return
        if job.kind == "speech" and job.neural:
            while job.process.bytesAvailable() and self._current(job):
                job.output.extend(job.process.read(65_536).data())
                self._read_speech_records(job)
                if len(job.output) > MAX_SPEECH_RECORD_BYTES:
                    self._job_failed(job, "speech_invalid")
            return
        remaining = MAX_PROCESS_OUTPUT_BYTES - len(job.output)
        chunk = job.process.read(remaining + 1).data()
        if len(chunk) > remaining:
            self._job_failed(job, f"{job.kind}_invalid")
            return
        job.output.extend(chunk)
        if job.kind == "setup":
            self._read_setup_records(job)

    def _read_setup_records(self, job: _ProcessJob) -> None:
        while b"\n" in job.output and self._current(job):
            raw, _, remainder = job.output.partition(b"\n")
            job.output[:] = remainder
            job.records += 1
            if job.records > MAX_SETUP_RECORDS:
                self._job_failed(job, "setup_invalid")
                return
            try:
                record = json.loads(raw)
                if not isinstance(record, dict):
                    raise ValueError("setup_record_invalid")
                event = record.get("event")
                if event == "progress":
                    downloaded, total = record.get("downloaded_bytes"), record.get("total_bytes")
                    if (
                        type(downloaded) is not int
                        or type(total) is not int
                        or not 0 <= downloaded <= total <= 3_000_000_000
                        or job.setup_ready
                    ):
                        raise ValueError("setup_progress_invalid")
                    self.status_changed.emit("model_download_progress")
                elif event == "result" and record.get("ready") is True and not job.setup_ready:
                    job.setup_ready = True
                elif event == "error":
                    code = record.get("code")
                    self._job_failed(
                        job,
                        code
                        if isinstance(code, str) and code in MODEL_SETUP_ERROR_CODES
                        else "model_setup_failed",
                    )
                    return
                else:
                    raise ValueError("setup_event_invalid")
            except (ValueError, UnicodeError):
                self._job_failed(job, "setup_invalid")
                return

    def _read_speech_records(self, job: _ProcessJob) -> None:
        while b"\n" in job.output and self._current(job):
            raw, _, remainder = job.output.partition(b"\n")
            job.output[:] = remainder
            try:
                if len(raw) > MAX_SPEECH_RECORD_BYTES or job.speech_complete:
                    raise ValueError("speech_record_invalid")
                record = SPEECH_RECORD.validate_json(raw)
                if isinstance(record, SpeechFailure):
                    self._job_failed(job, record.code)
                    return
                if isinstance(record, SpeechComplete):
                    if record.total_pcm_bytes != job.pcm_bytes or not job.audio_frames:
                        raise ValueError("speech_total_invalid")
                    job.speech_complete = True
                    continue
                if not isinstance(record, AudioFrame) or (
                    record.sample_rate != job.sample_rate or record.sequence != job.audio_frames
                ):
                    raise ValueError("speech_frame_invalid")
                pcm = record.pcm()
                job.pcm_bytes += len(pcm)
                job.audio_frames += 1
                if job.pcm_bytes > job.sample_rate * 2 * MAX_SPEECH_SECONDS:
                    raise ValueError("speech_total_invalid")
                if not self._playback.append(pcm):
                    if self._current(job):
                        self._job_failed(job, "playback_failed")
                    return
            except (ValueError, UnicodeError):
                self._job_failed(job, "speech_invalid")
                return

    def _process_error(self, job: _ProcessJob, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._job_failed(job, f"{job.kind}_unavailable")
            self._dispose_job(job)

    def _process_finished(self, job: _ProcessJob, code: int, status: QProcess.ExitStatus) -> None:
        if not self._current(job):
            self._dispose_job(job)
            return
        self._read_output(job)
        if not self._current(job):
            self._dispose_job(job)
            return
        normal = code == 0 and status == QProcess.ExitStatus.NormalExit
        if job.kind == "transcription":
            try:
                response = VoiceWorkerResponse.model_validate_json(job.output)
            except ValidationError:
                self._job_failed(job, "transcription_invalid")
            else:
                if normal and response.event == "result" and response.result is not None:
                    self._transcription = None
                    self._update_activity()
                    self.status_changed.emit("transcription_ready")
                    self.transcript_ready.emit(job.token, response.result.text)
                else:
                    self._job_failed(job, response.code or "transcription_failed")
        elif job.kind == "setup":
            if normal and job.setup_ready and not job.output.strip():
                self._setup = None
                self._update_activity()
                self.status_changed.emit("model_ready")
                self.setup_finished.emit(True)
            else:
                self._job_failed(job, "model_setup_failed")
        else:
            if job.neural:
                if normal and job.speech_complete and not job.output:
                    job.worker_done = True
                    job.timer.stop()
                    self._playback.finish_input()
                else:
                    self._job_failed(job, "speech_failed")
                return
            self._speech = None
            self._update_activity()
            self.status_changed.emit("speech_finished" if normal else "speech_failed")
        self._dispose_job(job)

    def _dispose_job(self, job: _ProcessJob) -> None:
        job.payload = b""
        job.output.clear()
        job.timer.stop()
        self._retired.discard(job)
        if job.process.state() == QProcess.ProcessState.NotRunning:
            job.timer.deleteLater()
            job.process.deleteLater()

    def _retire_job(self, job: _ProcessJob) -> None:
        job.payload = b""
        job.output.clear()
        job.timer.stop()
        self._retired.add(job)
        if job.process.state() == QProcess.ProcessState.NotRunning:
            self._dispose_job(job)
        elif job.kind == "setup":
            job.process.terminate()
            QTimer.singleShot(
                2000,
                self,
                lambda: job.process.kill() if job in self._retired else None,
            )
        else:
            job.process.kill()

    def _job_failed(self, job: _ProcessJob, code: str) -> None:
        if not self._current(job):
            return
        if job.kind == "transcription":
            self._transcription = None
        elif job.kind == "setup":
            self._setup = None
            self.setup_finished.emit(False)
        else:
            self._speech = None
            if job.neural:
                self._playback.cancel()
        self._retire_job(job)
        self._update_activity()
        self.status_changed.emit(code)

    def prepare_model(self, model_directory: Path) -> None:
        """An explicit setup command, isolated from recording and transcript submission."""

        self._setup_model(model_directory, check_only=False)

    def verify_model(self, model_directory: Path) -> None:
        """Check existing public weights in an owned child without downloading anything."""

        self._setup_model(model_directory, check_only=True)

    def prepare_speech_model(self, model_directory: Path) -> None:
        self._setup_model(model_directory, check_only=False, speech=True)

    def verify_speech_model(self, model_directory: Path) -> None:
        self._setup_model(model_directory, check_only=True, speech=True)

    def _setup_model(
        self, model_directory: Path, *, check_only: bool, speech: bool = False
    ) -> None:
        self.cancel()
        if not model_directory.is_absolute():
            self.status_changed.emit("configuration_invalid")
            self.setup_finished.emit(False)
            return
        job = self._new_process_job("setup", model_directory)
        if speech:
            prefix = (
                ["--speech-model-setup"]
                if getattr(sys, "frozen", False)
                else ["-m", "satori.desktop.voice.neural_models"]
            )
            job.process.setArguments([*prefix, "--model-directory", str(model_directory)])
        if check_only:
            job.process.setArguments([*job.process.arguments(), "--check-only"])
        self._setup = job
        self._update_activity()
        self.status_changed.emit("checking_model" if check_only else "preparing_model")
        job.process.start()

    def speak_neural(
        self, text: str, model_directory: Path, delivery: VoiceDelivery | None = None
    ) -> None:
        """Synthesize an explicitly requested committed reply with its selected delivery."""

        self.stop_speech()
        if not model_directory.is_absolute():
            self.status_changed.emit("configuration_invalid")
            return
        request = self._speech_request(text, delivery)
        if request is not None:
            self._start_speech_stream(
                request, request.model_dump_json().encode("utf-8"), model_directory
            )

    @property
    def has_cloud_credentials(self) -> bool:
        return bool(self._cloud_api_key.get_secret_value())

    def set_cloud_credentials(self, api_key: str, voice_id: str) -> None:
        """Retain an explicitly entered key only in this controller's process memory."""

        self._cloud_voice_id = voice_id
        if api_key:
            self._cloud_api_key = SecretStr(api_key)

    def clear_cloud_credentials(self) -> None:
        self.stop_speech()
        self._cloud_api_key = SecretStr("")

    def speak_cloud(self, text: str, delivery: VoiceDelivery | None = None) -> None:
        """Only the explicitly selected cloud route may disclose this speech copy."""

        from satori.desktop.voice.cloud import CloudSpeechRequest

        self.stop_speech()
        if not self.has_cloud_credentials or not self._cloud_voice_id:
            self.status_changed.emit("speech_credentials_required")
            return
        request = self._speech_request(text, delivery)
        if request is None:
            return
        try:
            cloud_request = CloudSpeechRequest(
                **request.model_dump(),
                api_key=self._cloud_api_key,
                voice_id=self._cloud_voice_id,
            )
        except ValidationError:
            self.status_changed.emit("configuration_invalid")
            return
        data = cloud_request.model_dump(mode="json", exclude={"api_key"})
        data["api_key"] = self._cloud_api_key.get_secret_value()
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._start_speech_stream(request, payload, Path("/"), cloud=True)

    def _speech_request(self, text: str, delivery: VoiceDelivery | None) -> SpeechRequest | None:
        if self._capture is not None or self._transcription is not None or self._setup is not None:
            self.status_changed.emit("speech_busy")
            return None
        if len(text) > MAX_TRANSCRIPT_CHARS:
            self.status_changed.emit("configuration_invalid")
            return None
        rendered = render_speech_text(text)
        if not rendered:
            self.status_changed.emit("speech_no_text")
            return None
        rate = self._playback.preferred_sample_rate()
        if rate not in (16_000, 24_000, 44_100, 48_000):
            self.status_changed.emit("playback_format_unsupported")
            return None
        try:
            request = SpeechRequest(
                text=rendered,
                sample_rate=cast(SpeechRate, rate),
                delivery=delivery or VoiceDelivery(),
            )
        except ValidationError:
            self.status_changed.emit("configuration_invalid")
            return None
        return request

    def _start_speech_stream(
        self,
        request: SpeechRequest,
        payload: bytes,
        model_directory: Path,
        *,
        cloud: bool = False,
    ) -> None:
        if not self._playback.start(request.sample_rate):
            return
        job = self._new_process_job("speech", model_directory)
        if cloud:
            job.process.setArguments(
                ["--cloud-speech-worker"]
                if getattr(sys, "frozen", False)
                else ["-m", "satori.desktop.voice.cloud_worker"]
            )
        job.neural = True
        job.sample_rate = request.sample_rate
        job.payload = payload
        self._speech = job
        self._update_activity()
        self.status_changed.emit("speech_preparing")
        job.process.start()

    def _playback_started(self) -> None:
        if self._speech is not None and self._speech.neural:
            self.status_changed.emit("speaking")

    def _playback_failed(self, code: str) -> None:
        job = self._speech
        if job is not None and job.neural:
            self._job_failed(job, code)
        else:
            self.status_changed.emit(code)

    def _playback_finished(self) -> None:
        job = self._speech
        if job is not None and job.neural and job.worker_done:
            self._speech = None
            self._dispose_job(job)
            self._update_activity()
            self.status_changed.emit("speech_finished")

    def speak(self, text: str, voice: str = "Milena", rate: int = 165) -> None:
        """Speak only at the caller's request; no audio/history replay on startup."""

        self.stop_speech()
        if sys.platform != "darwin":
            self.status_changed.emit("speech_unavailable")
            return
        if self._capture is not None or self._transcription is not None or self._setup is not None:
            self.status_changed.emit("speech_busy")
            return
        if (
            not text.strip()
            or len(text) > MAX_TRANSCRIPT_CHARS
            or not voice
            or len(voice) > 128
            or any(value in voice for value in ("\n", "\r", "\x00"))
            or not 80 <= rate <= 350
        ):
            self.status_changed.emit("configuration_invalid")
            return
        spoken_text = render_speech_text(text)
        if not spoken_text:
            self.status_changed.emit("speech_no_text")
            return
        job = self._new_process_job("speech", Path("/"))
        job.process.setProgram("/usr/bin/say")
        job.process.setArguments(["-v", voice, "-r", str(rate), "-f", "-"])
        job.payload = spoken_text.encode("utf-8")
        self._speech = job
        self._update_activity()
        self.status_changed.emit("speaking")
        job.process.start()

    def stop_speech(self) -> None:
        self._playback.cancel()
        job, self._speech = self._speech, None
        if job is not None:
            self._retire_job(job)
            self.status_changed.emit("speech_stopped")
        self._update_activity()

    def cancel(self) -> None:
        """Invalidate callbacks immediately and stop only this controller's resources."""

        self._generation += 1
        self._playback.cancel()
        capture = self._release_capture()
        if capture is not None:
            capture.buffer.pcm.clear()
        jobs = (self._transcription, self._setup, self._speech)
        self._transcription = self._setup = self._speech = None
        for job in jobs:
            if job is not None:
                self._retire_job(job)
        if jobs[1] is not None:
            self.setup_finished.emit(False)
        self._update_activity()
        if capture is not None or any(job is not None for job in jobs):
            self.status_changed.emit("voice_cancelled")
