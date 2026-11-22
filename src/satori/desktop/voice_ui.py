"""Voice presentation: transient hypotheses become drafts only after scope checks."""

import re
import stat
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from satori.desktop.settings import DesktopPreferences
from satori.desktop.voice import models, neural_models
from satori.desktop.voice.auditions import PUBLIC_VOICE_AUDITIONS, public_voice_audition
from satori.desktop.voice.controller import VoiceController
from satori.desktop.voice.expression import VoiceDelivery


@dataclass(frozen=True)
class VoiceContext:
    profile: Path
    session_id: str | None
    draft: str
    ready: bool


_STATUSES = {
    "microphone_permission_requested": "Разреши микрофон в системном окне. Запись ещё не началась.",
    "microphone_permission_denied": "Нет доступа к микрофону. Текстовый чат доступен.",
    "microphone_permission_pending": "Ожидаю решение в системном окне. Микрофон выключен.",
    "microphone_permission_granted_press_again": "Доступ разрешён. Зажми кнопку, чтобы говорить.",
    "microphone_unavailable": "Микрофон недоступен. Проверь подключение и системные разрешения.",
    "microphone_format_unsupported": "Формат этого микрофона пока не поддерживается.",
    "capturing": "● Микрофон включён. Говори; отпусти кнопку для распознавания.",
    "transcribing": "Распознаю локально. Отмена доступна; сообщение ещё не отправлено.",
    "transcription_ready": "Текст распознан локально. Проверь текст и нажми «Отправить».",
    "preparing_model": "Загружаю модель распознавания. Аудио не передаётся.",
    "checking_model": "Проверяю локальную модель распознавания. Микрофон выключен.",
    "model_download_progress": "Загружаю модель распознавания. Можно отменить.",
    "model_ready": "Модель готова. Зажми кнопку, чтобы говорить.",
    "voice_cancelled": "Голосовая операция отменена. Микрофон выключен.",
    "capture_timeout_discarded": "Запись отменена: лимит 30 секунд. Зажми кнопку ещё раз.",
    "capture_failed": "Запись прервана. Микрофон выключен; черновик сохранён.",
    "audio_unsupported": "Формат этого микрофона пока не поддерживается.",
    "audio_too_large": "Запись превышает лимит и отменена. Черновик сохранён.",
    "audio_invalid": "Запись недоступна для распознавания. Попробуй ещё раз.",
    "no_speech": "Речь неразборчива. Черновик сохранён; попробуй ещё раз.",
    "model_unavailable": "Подготовь модель распознавания кнопкой ниже.",
    "engine_unavailable": "Локальное распознавание недоступно. Текстовый чат работает.",
    "transcription_failed": "Распознавание завершилось ошибкой. Черновик сохранён.",
    "transcription_invalid": "Распознавание не дало пригодного текста. Попробуй ещё раз.",
    "transcription_timeout": "Распознавание остановлено по лимиту времени. Черновик сохранён.",
    "model_destination_exists": "Каталог модели занят другими файлами. Выбери другой в настройках.",
    "model_network_rejected": "Сервис загрузки недоступен. Модель не изменена.",
    "model_integrity_failed": "Проверка модели не прошла. Неполная модель не используется.",
    "model_setup_failed": "Подготовка модели завершилась ошибкой. Текстовый чат доступен.",
    "setup_timeout": "Загрузка остановлена по лимиту времени. Можно повторить подготовку.",
    "speech_started": "Озвучиваю сохранённый ответ. Звук можно остановить.",
    "speaking": "Озвучиваю сохранённый ответ. Звук можно остановить.",
    "speech_stopped": "Звук остановлен. Сохранённый ответ остаётся в истории.",
    "speech_finished": "Озвучивание завершено. Микрофон выключен.",
    "speech_no_text": "Ответ не содержит текста для озвучивания.",
    "speech_unavailable": "Системный голос недоступен. Ответ доступен текстом.",
    "speech_failed": "Ошибка озвучивания. Ответ доступен текстом.",
    "speech_preparing": "Готовлю локальное озвучивание сохранённого ответа. Звук можно остановить.",
    "speech_model_unavailable": (
        "Экспериментальный локальный голос не подготовлен. "
        "Естественность речи не прошла проверку; основной вариант — ElevenLabs в настройках."
    ),
    "speech_engine_unavailable": (
        "Экспериментальный локальный голос недоступен. Ответ есть текстом."
    ),
    "speech_busy": "Голос занят текущей операцией. Ответ доступен текстом.",
    "playback_failed": "Ошибка воспроизведения звука. Ответ доступен текстом.",
    "playback_format_unsupported": "Формат звукового устройства не поддерживается.",
    "speech_credentials_required": (
        "Открой «Настройки» и введи API-ключ ElevenLabs. "
        "Ключ хранится только до закрытия приложения; Voice ID можно изменить там же."
    ),
    "speech_credentials_rejected": "ElevenLabs отклонил ключ. Проверь ключ в настройках голоса.",
    "speech_quota_exceeded": "Лимит ElevenLabs исчерпан. Проверь тариф и доступную квоту.",
    "speech_network_failed": "Соединение ElevenLabs недоступно. Ответ доступен текстом.",
    "speech_provider_unavailable": "ElevenLabs временно недоступен. Ответ доступен текстом.",
    "speech_provider_rejected": "ElevenLabs не выполнил озвучивание. Ответ доступен текстом.",
    "speech_text_unsupported": (
        "Этот ответ оставлен текстом: разметка или символы не подходят для облачного голоса. "
        "Текст не передан провайдеру."
    ),
    "configuration_invalid": "Настройки голосовой операции недопустимы. Текстовый чат доступен.",
}


class VoicePanel(QWidget):
    """Own voice controls, scope fencing and opt-in playback, without Core access."""

    def __init__(
        self,
        preferences: DesktopPreferences,
        *,
        context: Callable[[], VoiceContext],
        apply_draft: Callable[[str], None],
        toggle_speech: Callable[[bool], None],
        parent: QWidget | None = None,
        controller: VoiceController | None = None,
    ) -> None:
        super().__init__(parent)
        self.preferences = preferences
        self.context = context
        self.apply_draft = apply_draft
        self.controller = controller if controller is not None else VoiceController(self)
        self._token: str | None = None
        self._snapshot: VoiceContext | None = None
        self._spoken: deque[str] = deque(maxlen=128)
        self._setup_directory: Path | None = None
        self._setup_kind: Literal["recognition", "speech"] | None = None
        self._verified_model: tuple[Path, tuple[tuple[int, int, int, int], ...]] | None = None
        self._verified_speech: tuple[Path, tuple[tuple[int, int, int, int], ...]] | None = None
        self._verification_attempted: set[tuple[str, Path]] = set()
        self._auto_verify_allowed = True
        self._compact_presentation = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.controls_layout = QHBoxLayout()
        self._default_controls_alignment = self.controls_layout.alignment()
        self.microphone = QPushButton("Зажми, чтобы говорить")
        self.microphone.pressed.connect(self._begin)
        self.microphone.released.connect(self.controller.finish_capture)
        self.controls_layout.addWidget(self.microphone)
        self.cancel_button = QPushButton("Отменить голос / остановить звук")
        self.cancel_button.clicked.connect(self.cancel)
        self.controls_layout.addWidget(self.cancel_button)
        layout.addLayout(self.controls_layout)
        self.settings_controls = QWidget(self)
        settings_layout = QVBoxLayout(self.settings_controls)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        self.settings_status = QLabel()
        self.settings_status.setTextFormat(Qt.TextFormat.PlainText)
        self.settings_status.setWordWrap(True)
        settings_layout.addWidget(self.settings_status)
        self._set_status_text("Микрофон выключен · распознавание локальное · отправка вручную")
        self.automatic_speech = QCheckBox("Озвучивать новые ответы")
        self.automatic_speech.setChecked(preferences.speak_replies)
        self.automatic_speech.toggled.connect(toggle_speech)
        settings_layout.addWidget(self.automatic_speech)
        self.prepare = QPushButton("Подготовить распознавание речи · 226 МБ")
        self.prepare.clicked.connect(self._prepare)
        settings_layout.addWidget(self.prepare)
        self.prepare_speech = QPushButton("Подготовить экспериментальный голос · 2,31 ГБ")
        self.prepare_speech.clicked.connect(self._prepare_speech)
        settings_layout.addWidget(self.prepare_speech)
        self.settings_cancel_button = QPushButton("Отменить голос / остановить звук")
        self.settings_cancel_button.clicked.connect(self.cancel)
        settings_layout.addWidget(self.settings_cancel_button)
        previews = QHBoxLayout()
        self.preview_style = QComboBox()
        for audition in PUBLIC_VOICE_AUDITIONS:
            self.preview_style.addItem(audition.label, audition.id)
        previews.addWidget(self.preview_style)
        self.preview_button = QPushButton("Прослушать голос")
        self.preview_button.clicked.connect(self._preview_selected)
        previews.addWidget(self.preview_button)
        settings_layout.addLayout(previews)
        self.preview_phrase = QLabel()
        self.preview_phrase.setWordWrap(True)
        settings_layout.addWidget(self.preview_phrase)
        self.preview_style.currentIndexChanged.connect(self._preview_changed)
        self._preview_changed()
        self.voice_id_help = QLabel(
            "Пробы используют сохранённый Voice ID из настроек. Выбор подачи сохраняет Voice ID."
        )
        self.voice_id_help.setWordWrap(True)
        settings_layout.addWidget(self.voice_id_help)
        self.cloud_notice = QLabel(
            "ElevenLabs: текст озвучиваемого ответа или тестовой фразы передаётся провайдеру. "
            "Используется платная квота; хранение определяется правилами провайдера. "
            "Микрофон и история чата не передаются."
        )
        self.cloud_notice.setWordWrap(True)
        settings_layout.addWidget(self.cloud_notice)
        layout.addWidget(self.settings_controls)
        self.controller.transcript_ready.connect(self._transcribed)
        self.controller.status_changed.connect(self._status)
        self.controller.activity_changed.connect(lambda _activity: self.refresh())
        self.controller.setup_finished.connect(self._model_checked)
        self.refresh()
        QTimer.singleShot(0, self._verify_existing_model)

    def refresh(self) -> None:
        context = self.context()
        activity = self.controller.activity
        present = self.preferences.voice_model_directory.exists()
        model_ready = context.ready and self._model_ready()
        self.microphone.setEnabled(model_ready and activity in {"idle", "capturing", "speaking"})
        self.microphone.setText(
            "Отпусти для распознавания"
            if activity == "capturing"
            else "Голос"
            if self._compact_presentation
            else "Зажми, чтобы говорить"
        )
        self.cancel_button.setText(
            "Стоп" if self._compact_presentation else "Отменить голос / остановить звук"
        )
        operation_pending = (
            activity != "idle" or self._token is not None or self._setup_kind is not None
        )
        self.cancel_button.setVisible(not self._compact_presentation or operation_pending)
        self.settings_status.setVisible(self._compact_presentation)
        self.settings_cancel_button.setVisible(self._compact_presentation)
        self.settings_cancel_button.setEnabled(operation_pending)
        if self._compact_presentation:
            self.microphone.setToolTip(
                "Голосовой ввод доступен в активном разговоре."
                if not context.ready
                else "Настройки → Голос → Подготовить распознавание. "
                "Модель загружается только по твоему действию."
                if not model_ready
                else "Удерживай, чтобы говорить; отпусти для распознавания. "
                "Проверь текст и нажми «Отправить»."
            )
        else:
            self.microphone.setToolTip("")
        self.prepare.setEnabled(activity in {"idle", "speaking"})
        self.prepare_speech.setVisible(self.preferences.speech_backend == "neural")
        self.prepare_speech.setEnabled(activity in {"idle", "speaking"})
        self.preview_button.setEnabled(activity in {"idle", "speaking"})
        self.preview_style.setEnabled(
            activity in {"idle", "speaking"} and self.preferences.speech_backend != "system"
        )
        self.cloud_notice.setVisible(self.preferences.speech_backend == "elevenlabs")
        self.voice_id_help.setVisible(self.preferences.speech_backend == "elevenlabs")
        self.prepare.setText(
            "Проверить / подготовить модель распознавания"
            if present
            else "Подготовить распознавание речи · 226 МБ"
        )
        self.prepare_speech.setText(
            "Проверить / подготовить экспериментальный голос"
            if self.preferences.speech_model_directory.exists()
            else "Подготовить экспериментальный голос · 2,31 ГБ"
        )

    def set_compact_presentation(self, compact: bool) -> None:
        """Change chat controls without replacing the owner or starting voice work.

        The persistent settings_controls may be placed in a separate settings page.
        Its visibility and lifetime remain with that page, independent of this mode.
        """
        self._compact_presentation = compact
        self.controls_layout.setAlignment(
            Qt.AlignmentFlag.AlignLeft if compact else self._default_controls_alignment
        )
        self.cancel_button.setToolTip(
            "Отменить запись, распознавание или озвучивание" if compact else ""
        )
        self.refresh()

    def _set_status_text(self, message: str) -> None:
        """Mirror one local presentation message in both persistent surfaces."""
        self.status.setText(message)
        self.settings_status.setText(message)

    def set_preferences(self, preferences: DesktopPreferences) -> None:
        recognition_changed = (
            self.preferences.voice_model_directory != preferences.voice_model_directory
        )
        speech_changed = (
            self.preferences.speech_model_directory != preferences.speech_model_directory
        )
        backend_changed = self.preferences.speech_backend != preferences.speech_backend
        cloud_voice_changed = (
            self.preferences.elevenlabs_voice_id != preferences.elevenlabs_voice_id
        )
        if recognition_changed or speech_changed or backend_changed or cloud_voice_changed:
            self.cancel()
            self._verification_attempted.clear()
            self._auto_verify_allowed = True
            QTimer.singleShot(0, self._verify_existing_model)
        if recognition_changed:
            self._verified_model = None
        if speech_changed:
            self._verified_speech = None
        self.preferences = preferences
        self.automatic_speech.blockSignals(True)
        self.automatic_speech.setChecked(preferences.speak_replies)
        self.automatic_speech.blockSignals(False)
        if not preferences.speak_replies:
            self.controller.stop_speech()
        self.refresh()

    def _begin(self) -> None:
        snapshot = self.context()
        if not snapshot.ready:
            return
        if not self._model_ready():
            self._set_status_text(
                "Настройки → Голос → Подготовить распознавание."
                if self._compact_presentation
                else "Сначала проверь или подготовь модель кнопкой ниже."
            )
            return
        self.cancel()
        self._token = str(uuid4())
        self._snapshot = snapshot
        self.controller.begin_capture(self._token, self.preferences.voice_model_directory)

    def editor_changed(self) -> None:
        if self._token is not None:
            self.cancel()

    def cancel(self) -> None:
        self._auto_verify_allowed = False
        self._token = None
        self._snapshot = None
        self.controller.cancel()
        self._setup_kind = None
        self._setup_directory = None

    def _prepare(self) -> None:
        self.cancel()
        self._setup_directory = self.preferences.voice_model_directory
        self._setup_kind = "recognition"
        self._verified_model = None
        self.controller.prepare_model(self.preferences.voice_model_directory)

    def _prepare_speech(self) -> None:
        self.cancel()
        self._setup_directory = self.preferences.speech_model_directory
        self._setup_kind = "speech"
        self._verified_speech = None
        self.controller.prepare_speech_model(self.preferences.speech_model_directory)

    def _verify_existing_model(self) -> None:
        if (
            not self._auto_verify_allowed
            or self.controller.activity != "idle"
            or self._setup_kind is not None
        ):
            return
        for kind, directory, ready in (
            ("recognition", self.preferences.voice_model_directory, self._model_ready()),
            ("speech", self.preferences.speech_model_directory, self._speech_model_ready()),
        ):
            if kind == "speech" and self.preferences.speech_backend != "neural":
                continue
            if (
                ready
                or (kind, directory) in self._verification_attempted
                or self._model_fingerprint(speech=kind == "speech") is None
            ):
                continue
            self._verification_attempted.add((kind, directory))
            self._setup_directory = directory
            if kind == "speech":
                self._setup_kind = "speech"
                self.controller.verify_speech_model(directory)
            else:
                self._setup_kind = "recognition"
                self.controller.verify_model(directory)
            return

    def _model_fingerprint(
        self, *, speech: bool = False
    ) -> tuple[tuple[int, int, int, int], ...] | None:
        """Cheap change detection after an owned worker verified all model hashes."""

        directory = (
            self.preferences.speech_model_directory
            if speech
            else self.preferences.voice_model_directory
        )
        files = neural_models.MODEL_FILES if speech else models.MODEL_FILES
        try:
            expected_files = {Path(item.name) for item in files}
            if any(path.is_absolute() or ".." in path.parts for path in expected_files):
                return None
            directories = {Path(".")}
            for path in expected_files:
                directories.update(path.parents)
            expected_paths = expected_files | (directories - {Path(".")})
            fingerprint = []
            for relative in sorted(directories):
                folder = directory / relative
                metadata = folder.lstat()
                if not stat.S_ISDIR(metadata.st_mode):
                    return None
                expected = {path.name for path in expected_paths if path.parent == relative}
                if {entry.name for entry in folder.iterdir()} != expected:
                    return None
                fingerprint.append(
                    (metadata.st_dev, metadata.st_ino, metadata.st_mtime_ns, metadata.st_size)
                )
            for item in files:
                metadata = (directory / item.name).lstat()
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != item.size:
                    return None
                fingerprint.append(
                    (metadata.st_dev, metadata.st_ino, metadata.st_mtime_ns, metadata.st_size)
                )
            return tuple(fingerprint)
        except OSError:
            return None

    def _model_ready(self) -> bool:
        fingerprint = self._model_fingerprint()
        return fingerprint is not None and self._verified_model == (
            self.preferences.voice_model_directory,
            fingerprint,
        )

    def _speech_model_ready(self) -> bool:
        fingerprint = self._model_fingerprint(speech=True)
        return fingerprint is not None and self._verified_speech == (
            self.preferences.speech_model_directory,
            fingerprint,
        )

    def _model_checked(self, ready: bool) -> None:
        kind = self._setup_kind
        if kind is None:
            return
        speech = kind == "speech"
        directory = (
            self.preferences.speech_model_directory
            if speech
            else self.preferences.voice_model_directory
        )
        fingerprint = self._model_fingerprint(speech=speech) if ready else None
        verified = (
            (directory, fingerprint)
            if fingerprint is not None and self._setup_directory == directory
            else None
        )
        if speech:
            self._verified_speech = verified
        else:
            self._verified_model = verified
        self._setup_directory = None
        self._setup_kind = None
        self.refresh()
        if self._auto_verify_allowed:
            QTimer.singleShot(0, self._verify_existing_model)

    def _status(self, code: str) -> None:
        # Vendor output never reaches this presentation boundary as an error message.
        message = _STATUSES.get(code)
        if code == "speech_preparing" and self.preferences.speech_backend == "elevenlabs":
            message = "Передаю текст в ElevenLabs для озвучивания. Звук можно остановить."
        if self._setup_kind == "speech":
            message = {
                "checking_model": "Проверяю локальный нейронный голос. Микрофон выключен.",
                "preparing_model": "Загружаю нейронный голос. Сообщения и аудио не передаются.",
                "model_download_progress": "Загружаю нейронный голос. Можно отменить.",
                "model_ready": (
                    "Экспериментальная модель проверена. Естественность речи не прошла проверку."
                ),
            }.get(code, message)
        if message is None:
            message = "Голосовая операция не завершена. Текстовый чат доступен."
        self._set_status_text(message)
        self.refresh()

    def _transcribed(self, token: str, text: str) -> None:
        snapshot = self._snapshot
        current = self.context()
        if (
            token != self._token
            or snapshot is None
            or (current.profile, current.session_id, current.draft)
            != (snapshot.profile, snapshot.session_id, snapshot.draft)
        ):
            return
        self._token = None
        self._snapshot = None
        draft = f"{snapshot.draft}\n{text}" if snapshot.draft else text
        if not text.strip() or len(draft) > 8000:
            self._set_status_text(
                "Распознанный текст не помещается в сообщение. Черновик сохранён."
            )
            return
        self.apply_draft(draft)
        self._set_status_text(
            "Распознано локально, русский · уверенность неизвестна. "
            "Проверь текст и нажми «Отправить»."
        )

    def committed_reply(
        self,
        interaction_id: str,
        text: str,
        *,
        replayed: bool,
        delivery: VoiceDelivery | None = None,
    ) -> None:
        if (
            replayed
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", interaction_id)
            or interaction_id in self._spoken
        ):
            return
        self._spoken.append(interaction_id)
        if self.preferences.speak_replies and self.context().ready:
            self._speak(text, delivery)

    def _speak(self, text: str, delivery: VoiceDelivery | None) -> None:
        if self.preferences.speech_backend == "elevenlabs":
            if (
                not self.preferences.elevenlabs_voice_id
                or not self.controller.has_cloud_credentials
            ):
                self._status("speech_credentials_required")
                return
            self.controller.speak_cloud(text, delivery=delivery)
        elif self.preferences.speech_backend == "neural":
            if not self._speech_model_ready():
                self._status("speech_model_unavailable")
                return
            self.controller.speak_neural(
                text, self.preferences.speech_model_directory, delivery=delivery
            )
        else:
            self.controller.speak(
                text, voice=self.preferences.speech_voice, rate=self.preferences.speech_rate
            )

    def _preview_selected(self) -> None:
        self.preview(self.preview_style.currentData())

    def _preview_changed(self) -> None:
        audition = public_voice_audition(self.preview_style.currentData())
        self.preview_phrase.setText(
            f"Публичная проба: {audition.text}" if audition else "Публичная проба не выбрана."
        )

    def preview(self, audition_id: object = "neutral") -> None:
        """Send one catalogue phrase to the selected engine after a button gesture."""

        if self.controller.activity not in {"idle", "speaking"}:
            self._status("speech_busy")
            return
        audition = public_voice_audition(audition_id)
        if audition is None:
            self._status("configuration_invalid")
            return
        self.cancel()
        self._speak(audition.text, audition.delivery)
