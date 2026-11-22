"""Voice drafts and playback cross the UI boundary without admitting audio to Core."""

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QProcess, Qt
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from satori.application.conversation.character_delivery_contracts import (
    CharacterDeliveryGoal,
    CharacterDeliveryVoice,
)
from satori.application.conversation.character_expression import CharacterPressureLevel
from satori.desktop.settings import DesktopPreferences
from satori.desktop.ui import DesktopApplication, DesktopWindow
from satori.desktop.voice import models, neural_models
from satori.desktop.voice.auditions import PUBLIC_VOICE_AUDITIONS, public_voice_audition
from satori.desktop.voice.controller import VoiceController
from satori.desktop.voice.expression import VoiceDelivery
from satori.desktop.voice_ui import VoiceContext, VoicePanel


class FakeVoice(VoiceController):
    def __init__(self) -> None:
        super().__init__()
        self.capture_tokens: list[str] = []
        self.speeches: list[str] = []
        self.cancels = 0
        self.preparations: list[Path] = []
        self.verifications: list[Path] = []
        self.neural_speeches: list[tuple[str, Path, VoiceDelivery | None]] = []
        self.speech_preparations: list[Path] = []
        self.speech_verifications: list[Path] = []
        self.cloud_speeches: list[tuple[str, VoiceDelivery | None]] = []

    def begin_capture(self, token: str, model_directory: Path) -> None:
        del model_directory
        self.capture_tokens.append(token)
        self.activity = "capturing"

    def cancel(self) -> None:
        self.cancels += 1
        self.activity = "idle"

    def speak(self, text: str, voice: str = "Milena", rate: int = 175) -> None:
        del voice, rate
        self.speeches.append(text)

    def prepare_model(self, model_directory: Path) -> None:
        self.preparations.append(model_directory)

    def verify_model(self, model_directory: Path) -> None:
        self.verifications.append(model_directory)

    def prepare_speech_model(self, model_directory: Path) -> None:
        self.speech_preparations.append(model_directory)

    def verify_speech_model(self, model_directory: Path) -> None:
        self.speech_verifications.append(model_directory)

    def speak_neural(
        self, text: str, model_directory: Path, delivery: VoiceDelivery | None = None
    ) -> None:
        self.neural_speeches.append((text, model_directory, delivery))

    def speak_cloud(self, text: str, delivery: VoiceDelivery | None = None) -> None:
        self.cloud_speeches.append((text, delivery))


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or DesktopApplication([])  # type: ignore[return-value]


@pytest.fixture
def window(application: QApplication, tmp_path: Path) -> Iterator[DesktopWindow]:
    del application
    result = DesktopWindow(
        DesktopPreferences(
            data_directory=tmp_path,
            repository_directory=tmp_path,
            voice_model_directory=tmp_path / "weights",
            speech_model_directory=tmp_path / "speech-weights",
        )
    )
    result.poll.stop()
    yield result
    result.quitting = True
    result.tray.hide()
    result.close()
    result.deleteLater()


def test_legacy_preferences_do_not_enable_microphone_or_playback(tmp_path: Path) -> None:
    old = {"data_directory": str(tmp_path), "repository_directory": str(tmp_path), "model": "qwen"}
    preferences = DesktopPreferences.model_validate_json(json.dumps(old))
    assert not preferences.speak_replies
    assert preferences.speech_voice == "Milena"
    assert preferences.speech_rate == 165
    assert preferences.speech_backend == "elevenlabs"
    assert "audio" not in preferences.model_dump_json()


def test_full_voice_presentation_stays_available_without_starting_voice_work(
    application: QApplication, tmp_path: Path
) -> None:
    del application
    voice = FakeVoice()
    preferences = DesktopPreferences(
        data_directory=tmp_path, voice_model_directory=tmp_path / "absent"
    )
    before = preferences.model_dump_json()
    panel = VoicePanel(
        preferences,
        context=lambda: VoiceContext(tmp_path, "session", "PRIVATE DRAFT", True),
        apply_draft=lambda _: pytest.fail("presentation cannot write a draft"),
        toggle_speech=lambda _: pytest.fail("presentation cannot change autoplay"),
        controller=voice,
    )
    assert panel.microphone.text() == "Зажми, чтобы говорить"
    assert panel.cancel_button.text() == "Отменить голос / остановить звук"
    assert not panel.cancel_button.isHidden()
    assert panel.settings_cancel_button.isHidden()
    assert panel.settings_status.isHidden()
    assert not panel.settings_controls.isHidden()
    panel.set_compact_presentation(True)
    assert panel.microphone.text() == "Голос"
    assert panel.cancel_button.isHidden()
    panel.set_compact_presentation(False)
    assert panel.microphone.text() == "Зажми, чтобы говорить"
    assert panel.cancel_button.text() == "Отменить голос / остановить звук"
    assert not panel.cancel_button.isHidden()
    assert panel.microphone.toolTip() == ""
    assert panel.controls_layout.alignment() == Qt.AlignmentFlag(0)
    assert not voice.capture_tokens
    assert not voice.preparations
    assert not voice.cloud_speeches
    assert voice.cancels == 0
    assert panel.preferences.model_dump_json() == before
    panel.cancel()
    panel.deleteLater()


@pytest.mark.parametrize(
    "activity", ["idle", "capturing", "transcribing", "preparing_model", "speaking"]
)
def test_compact_stop_is_visible_for_each_active_voice_operation(
    application: QApplication, tmp_path: Path, activity: str
) -> None:
    del application
    voice = FakeVoice()
    panel = VoicePanel(
        DesktopPreferences(data_directory=tmp_path, voice_model_directory=tmp_path / "absent"),
        context=lambda: VoiceContext(tmp_path, "session", "", True),
        apply_draft=lambda _: pytest.fail("stop visibility cannot write a draft"),
        toggle_speech=lambda _: pytest.fail("stop visibility cannot change autoplay"),
        controller=voice,
    )
    panel.set_compact_presentation(True)
    voice.activity = activity
    voice.activity_changed.emit(activity)
    assert panel.cancel_button.text() == "Стоп"
    assert panel.cancel_button.isHidden() == (activity == "idle")
    assert not panel.settings_cancel_button.isHidden()
    assert panel.settings_cancel_button.isEnabled() == (activity != "idle")
    assert not voice.capture_tokens
    assert not voice.speeches
    if activity != "idle":
        panel.settings_cancel_button.click()
        assert voice.cancels == 1
        assert voice.activity == "idle"
        panel.refresh()
        assert panel.cancel_button.isHidden()
    panel.cancel()
    panel.deleteLater()


@pytest.mark.parametrize(
    ("ready", "model_ready", "hint"),
    [
        (False, False, "активном разговоре"),
        (True, False, "Настройки → Голос → Подготовить распознавание"),
        (True, True, "Проверь текст и нажми «Отправить»"),
    ],
)
def test_compact_microphone_explains_readiness_without_preparing_or_requesting_access(
    application: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready: bool,
    model_ready: bool,
    hint: str,
) -> None:
    del application
    voice = FakeVoice()
    panel = VoicePanel(
        DesktopPreferences(data_directory=tmp_path, voice_model_directory=tmp_path / "absent"),
        context=lambda: VoiceContext(tmp_path, "session", "", ready),
        apply_draft=lambda _: pytest.fail("tooltip cannot write a draft"),
        toggle_speech=lambda _: pytest.fail("tooltip cannot change autoplay"),
        controller=voice,
    )
    monkeypatch.setattr(panel, "_model_ready", lambda: model_ready)
    panel.set_compact_presentation(True)
    assert hint in panel.microphone.toolTip()
    assert panel.microphone.isEnabled() == (ready and model_ready)
    assert not voice.capture_tokens
    assert not voice.preparations
    assert not voice.verifications
    assert not voice.cloud_speeches
    panel.cancel()
    panel.deleteLater()


def test_settings_reparent_keeps_one_controller_and_ptt_only_creates_a_reviewable_draft(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del application
    voice = FakeVoice()
    releases: list[str] = []
    drafts: list[str] = []
    monkeypatch.setattr(voice, "finish_capture", lambda: releases.append("release"))
    panel = VoicePanel(
        DesktopPreferences(data_directory=tmp_path, voice_model_directory=tmp_path / "absent"),
        context=lambda: VoiceContext(tmp_path, "session", "Typed draft", True),
        apply_draft=drafts.append,
        toggle_speech=lambda _: pytest.fail("moving settings cannot change autoplay"),
        controller=voice,
    )
    owner_parent = voice.parent()
    settings_host = QWidget()
    settings_layout = QVBoxLayout(settings_host)
    settings_layout.addWidget(panel.settings_controls)
    panel.settings_controls.show()
    panel.set_compact_presentation(True)
    assert panel.controller is voice
    assert voice.parent() is owner_parent
    assert panel.settings_controls.parentWidget() is settings_host
    for control in (
        panel.automatic_speech,
        panel.prepare,
        panel.prepare_speech,
        panel.settings_cancel_button,
        panel.preview_style,
        panel.preview_button,
        panel.preview_phrase,
        panel.voice_id_help,
        panel.cloud_notice,
    ):
        assert panel.settings_controls.isAncestorOf(control)
    for control in (panel.status, panel.microphone, panel.cancel_button):
        assert not panel.settings_controls.isAncestorOf(control)
    assert not voice.capture_tokens
    assert not voice.preparations
    assert not voice.cloud_speeches
    monkeypatch.setattr(panel, "_model_ready", lambda: True)
    panel.refresh()
    panel.microphone.pressed.emit()
    assert len(voice.capture_tokens) == 1
    token = voice.capture_tokens[0]
    voice.activity_changed.emit("capturing")
    assert panel.microphone.text() == "Отпусти для распознавания"
    panel.microphone.released.emit()
    assert releases == ["release"]
    voice.transcript_ready.emit(token, "Recognized words")
    assert drafts == ["Typed draft\nRecognized words"]
    assert not voice.speeches
    assert not voice.neural_speeches
    assert not voice.cloud_speeches
    assert not panel.automatic_speech.isChecked()
    panel.cancel()
    panel.deleteLater()
    settings_host.deleteLater()


def test_settings_stop_retires_an_idle_pending_token_and_rejects_its_late_transcript(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del application
    voice = FakeVoice()
    drafts: list[str] = []
    panel = VoicePanel(
        DesktopPreferences(data_directory=tmp_path, voice_model_directory=tmp_path / "absent"),
        context=lambda: VoiceContext(tmp_path, "session", "Original draft", True),
        apply_draft=drafts.append,
        toggle_speech=lambda _: None,
        controller=voice,
    )
    monkeypatch.setattr(panel, "_model_ready", lambda: True)
    panel.set_compact_presentation(True)
    panel.microphone.pressed.emit()
    token = voice.capture_tokens[-1]
    voice.activity = "idle"
    panel.refresh()
    assert not panel.cancel_button.isHidden()
    assert panel.settings_cancel_button.isEnabled()
    before = voice.cancels
    panel.settings_cancel_button.click()
    panel.refresh()
    assert voice.cancels == before + 1
    assert panel.cancel_button.isHidden()
    voice.transcript_ready.emit(token, "Late words must not appear")
    assert not drafts
    panel.cancel()
    panel.deleteLater()


def test_compact_controls_fit_capture_caption_without_stretching_across_the_composer(
    application: QApplication, tmp_path: Path
) -> None:
    voice = FakeVoice()
    panel = VoicePanel(
        DesktopPreferences(data_directory=tmp_path, voice_model_directory=tmp_path / "absent"),
        context=lambda: VoiceContext(tmp_path, "session", "", True),
        apply_draft=lambda _: None,
        toggle_speech=lambda _: None,
        controller=voice,
    )
    settings_host = QWidget()
    settings_layout = QVBoxLayout(settings_host)
    settings_layout.addWidget(panel.settings_controls)
    panel.set_compact_presentation(True)
    panel.resize(700, 110)
    panel.show()
    application.processEvents()
    assert panel.controls_layout.alignment() == Qt.AlignmentFlag.AlignLeft
    assert panel.microphone.width() < panel.width() // 2
    voice.activity = "capturing"
    voice.activity_changed.emit("capturing")
    application.processEvents()
    assert panel.microphone.text() == "Отпусти для распознавания"
    assert panel.microphone.width() >= panel.microphone.sizeHint().width()
    assert panel.rect().contains(
        panel.microphone.mapTo(panel, panel.microphone.rect().bottomRight())
    )
    assert panel.rect().contains(
        panel.cancel_button.mapTo(panel, panel.cancel_button.rect().bottomRight())
    )
    assert not voice.capture_tokens
    panel.set_compact_presentation(False)
    assert panel.controls_layout.alignment() == Qt.AlignmentFlag(0)
    panel.cancel()
    panel.deleteLater()
    settings_host.deleteLater()


@pytest.mark.parametrize(
    ("code", "hint"),
    [
        ("preparing_model", "Загружаю модель распознавания"),
        ("speech_network_failed", "Соединение ElevenLabs недоступно"),
        ("<img src=provider-secret> UNKNOWN-RAW-CODE", "Голосовая операция не завершена"),
    ],
)
def test_settings_status_mirrors_closed_progress_errors_and_unknown_fallback(
    application: QApplication, tmp_path: Path, code: str, hint: str
) -> None:
    del application
    voice = FakeVoice()
    panel = VoicePanel(
        DesktopPreferences(data_directory=tmp_path, voice_model_directory=tmp_path / "absent"),
        context=lambda: VoiceContext(tmp_path, "session", "PRIVATE DRAFT", True),
        apply_draft=lambda _: pytest.fail("status cannot write a draft"),
        toggle_speech=lambda _: pytest.fail("status cannot change autoplay"),
        controller=voice,
    )
    panel.set_compact_presentation(True)
    assert panel.status.text() == panel.settings_status.text()
    voice.status_changed.emit(code)
    assert panel.status.text() == panel.settings_status.text()
    assert hint in panel.settings_status.text()
    assert "provider-secret" not in panel.settings_status.text()
    assert "UNKNOWN-RAW-CODE" not in panel.settings_status.text()
    assert "PRIVATE" not in panel.settings_status.text()
    assert (
        panel.status.textFormat() == panel.settings_status.textFormat() == Qt.TextFormat.PlainText
    )
    assert not voice.capture_tokens
    assert not voice.preparations
    assert not voice.cloud_speeches
    panel.cancel()
    panel.deleteLater()


def test_settings_status_shows_the_same_model_setup_guidance_as_the_chat(
    application: QApplication, tmp_path: Path
) -> None:
    del application
    voice = FakeVoice()
    panel = VoicePanel(
        DesktopPreferences(data_directory=tmp_path, voice_model_directory=tmp_path / "absent"),
        context=lambda: VoiceContext(tmp_path, "session", "", True),
        apply_draft=lambda _: None,
        toggle_speech=lambda _: None,
        controller=voice,
    )
    panel.set_compact_presentation(True)
    panel._begin()
    assert panel.status.text() == panel.settings_status.text()
    assert panel.settings_status.text() == "Настройки → Голос → Подготовить распознавание."
    assert not voice.capture_tokens
    assert not voice.preparations
    panel.cancel()
    panel.deleteLater()


@pytest.mark.parametrize(
    "transcript", ["Recognized words", "x" * 8001], ids=["success", "oversized"]
)
def test_settings_status_mirrors_transcription_success_or_size_rejection(
    application: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transcript: str,
) -> None:
    del application
    voice = FakeVoice()
    drafts: list[str] = []
    panel = VoicePanel(
        DesktopPreferences(data_directory=tmp_path, voice_model_directory=tmp_path / "absent"),
        context=lambda: VoiceContext(tmp_path, "session", "Original draft", True),
        apply_draft=drafts.append,
        toggle_speech=lambda _: None,
        controller=voice,
    )
    monkeypatch.setattr(panel, "_model_ready", lambda: True)
    panel.set_compact_presentation(True)
    panel._begin()
    voice.transcript_ready.emit(voice.capture_tokens[-1], transcript)
    assert panel.status.text() == panel.settings_status.text()
    if len(transcript) > 8000:
        assert "не помещается" in panel.settings_status.text()
        assert not drafts
    else:
        assert "Проверь текст" in panel.settings_status.text()
        assert drafts == ["Original draft\nRecognized words"]
    assert "Original draft" not in panel.settings_status.text()
    assert "Recognized words" not in panel.settings_status.text()
    assert not voice.cloud_speeches
    panel.cancel()
    panel.deleteLater()


def test_default_cloud_does_not_prepare_local_tts_or_play_commits_automatically(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    voice = FakeVoice()
    voice.set_cloud_credentials("synthetic-session-key", "ymDCYd8puC7gYjxIamPt")
    panel = VoicePanel(
        DesktopPreferences(
            data_directory=tmp_path,
            voice_model_directory=tmp_path / "asr",
            speech_model_directory=tmp_path / "tts",
        ),
        context=lambda: VoiceContext(tmp_path, "session", "", True),
        apply_draft=lambda _: pytest.fail("default voice cannot change the editor"),
        toggle_speech=lambda _: None,
        controller=voice,
    )
    monkeypatch.setattr(panel, "_model_fingerprint", lambda **_: ((1, 2, 3, 4),))
    panel._verify_existing_model()
    voice.setup_finished.emit(True)
    application.processEvents()
    assert panel.preferences.speech_backend == "elevenlabs"
    assert panel.prepare_speech.isHidden()
    assert not panel.automatic_speech.isChecked()
    assert not voice.speech_preparations
    assert not voice.speech_verifications
    assert not (tmp_path / "tts").exists()
    panel.committed_reply("new-default", "No automatic paid speech.", replayed=False)
    assert not voice.cloud_speeches
    panel.cancel()
    panel.deleteLater()


def test_cloud_route_requires_explicit_choice_credentials_and_new_commit(
    application: QApplication, tmp_path: Path
) -> None:
    del application
    voice = FakeVoice()
    voice.set_cloud_credentials("synthetic-session-key", "ymDCYd8puC7gYjxIamPt")
    panel = VoicePanel(
        DesktopPreferences(
            data_directory=tmp_path,
            voice_model_directory=tmp_path / "asr",
            speech_model_directory=tmp_path / "tts",
            speech_backend="elevenlabs",
            speak_replies=True,
        ),
        context=lambda: VoiceContext(tmp_path, "session", "PRIVATE DRAFT", True),
        apply_draft=lambda _: pytest.fail("speech cannot change the editor"),
        toggle_speech=lambda _: None,
        controller=voice,
    )
    assert not voice.cloud_speeches
    assert not panel.cloud_notice.isHidden()
    delivery = VoiceDelivery()
    panel.committed_reply("one", "exact committed answer", replayed=False, delivery=delivery)
    panel.committed_reply("one", "duplicate", replayed=False)
    panel.committed_reply("two", "history", replayed=True)
    assert voice.cloud_speeches == [("exact committed answer", delivery)]
    assert not voice.neural_speeches
    assert not voice.speeches
    voice.clear_cloud_credentials()
    panel.committed_reply("three", "cannot upload", replayed=False)
    assert len(voice.cloud_speeches) == 1
    assert "API-ключ" in panel.status.text()
    assert not voice.neural_speeches
    assert not voice.speeches
    panel.cancel()
    panel.deleteLater()


@pytest.mark.parametrize("style", ["neutral", "curious", "subtle_humor", "reserved"])
def test_manual_preview_uses_only_fixed_public_phrase_and_does_not_consume_commits(
    application: QApplication, tmp_path: Path, style: str
) -> None:
    del application
    voice = FakeVoice()
    voice.set_cloud_credentials("synthetic-session-key", "ymDCYd8puC7gYjxIamPt")
    panel = VoicePanel(
        DesktopPreferences(
            data_directory=tmp_path,
            voice_model_directory=tmp_path / "asr",
            speech_model_directory=tmp_path / "tts",
            speech_backend="elevenlabs",
        ),
        context=lambda: VoiceContext(tmp_path, "session", "PRIVATE DRAFT", False),
        apply_draft=lambda _: pytest.fail("preview cannot create a draft"),
        toggle_speech=lambda _: None,
        controller=voice,
    )
    panel.preview_style.setCurrentIndex(panel.preview_style.findData(style))
    assert not voice.cloud_speeches
    panel.preview_button.click()
    assert len(voice.cloud_speeches) == 1
    text, delivery = voice.cloud_speeches[0]
    audition = public_voice_audition(style)
    assert audition is not None
    assert text == audition.text
    assert text in panel.preview_phrase.text()
    assert "PRIVATE" not in text
    assert delivery is not None
    assert (
        delivery.qualifiers
        == {
            "neutral": ("neutral",),
            "curious": ("engaged", "reflective"),
            "subtle_humor": ("gentle_amusement",),
            "reserved": ("reserved",),
        }[style]
    )
    assert not panel._spoken
    assert voice.cancels == 1
    assert not voice.capture_tokens
    assert not voice.speeches
    assert not voice.neural_speeches
    panel.cancel()
    panel.deleteLater()


def test_cloud_preview_cannot_interrupt_capture_or_invent_missing_credentials(
    application: QApplication, tmp_path: Path
) -> None:
    del application
    voice = FakeVoice()
    panel = VoicePanel(
        DesktopPreferences(
            data_directory=tmp_path,
            voice_model_directory=tmp_path / "asr",
            speech_model_directory=tmp_path / "tts",
            speech_backend="elevenlabs",
        ),
        context=lambda: VoiceContext(tmp_path, None, "", False),
        apply_draft=lambda _: None,
        toggle_speech=lambda _: None,
        controller=voice,
    )
    voice.activity = "capturing"
    panel.refresh()
    assert not panel.preview_button.isEnabled()
    panel.preview("neutral")
    assert voice.cancels == 0
    voice.activity = "idle"
    panel.preview("neutral")
    assert not voice.cloud_speeches
    assert "API-ключ" in panel.status.text()
    assert not voice.speeches
    assert not voice.neural_speeches
    panel.cancel()
    panel.deleteLater()


def test_public_preview_selection_preserves_voice_preferences_and_never_autoplays(
    application: QApplication, tmp_path: Path
) -> None:
    del application
    voice = FakeVoice()
    selected_id = "gedzfqL7OGdPbwm0ynTP"
    voice.set_cloud_credentials("synthetic-session-key", selected_id)
    preferences = DesktopPreferences(
        data_directory=tmp_path,
        voice_model_directory=tmp_path / "absent-asr",
        elevenlabs_voice_id=selected_id,
        speak_replies=False,
    )
    before = preferences.model_dump_json()
    panel = VoicePanel(
        preferences,
        context=lambda: VoiceContext(tmp_path, "session", "PRIVATE DRAFT", False),
        apply_draft=lambda _: pytest.fail("auditions cannot change draft"),
        toggle_speech=lambda _: pytest.fail("auditions cannot change autoplay"),
        controller=voice,
    )
    assert not panel.preview_style.isEditable()
    assert panel.preview_style.count() == 4
    assert "Voice ID" in panel.voice_id_help.text()
    assert "платная квота" in panel.cloud_notice.text()
    for audition in PUBLIC_VOICE_AUDITIONS:
        panel.preview_style.setCurrentIndex(panel.preview_style.findData(audition.id))
        assert not voice.cloud_speeches
        assert audition.text in panel.preview_phrase.text()
    panel.preview_button.click()
    assert voice.cloud_speeches == [(audition.text, audition.delivery)]
    assert preferences.model_dump_json() == before
    assert voice._cloud_voice_id == selected_id
    assert not panel._spoken
    assert not panel.automatic_speech.isChecked()
    panel.cancel()
    panel.deleteLater()


@pytest.mark.parametrize("invalid", ["[shouting]", "playful", {}, VoiceDelivery()])
def test_preview_only_admits_catalogue_ids(
    application: QApplication, tmp_path: Path, invalid: object
) -> None:
    del application
    voice = FakeVoice()
    voice.set_cloud_credentials("synthetic-session-key", "gedzfqL7OGdPbwm0ynTP")
    panel = VoicePanel(
        DesktopPreferences(data_directory=tmp_path, voice_model_directory=tmp_path / "absent"),
        context=lambda: VoiceContext(tmp_path, None, "PRIVATE", False),
        apply_draft=lambda _: pytest.fail("preview cannot access draft"),
        toggle_speech=lambda _: None,
        controller=voice,
    )
    panel.preview(invalid)
    assert not voice.cloud_speeches
    assert not voice.cancels
    assert "недопустимы" in panel.status.text()
    panel.cancel()
    panel.deleteLater()


def test_cloud_admission_status_explains_local_text_only_outcome(
    application: QApplication, tmp_path: Path
) -> None:
    del application
    panel = VoicePanel(
        DesktopPreferences(data_directory=tmp_path, voice_model_directory=tmp_path / "absent"),
        context=lambda: VoiceContext(tmp_path, None, "", False),
        apply_draft=lambda _: None,
        toggle_speech=lambda _: None,
        controller=FakeVoice(),
    )
    panel.controller.status_changed.emit("speech_text_unsupported")
    assert "оставлен текстом" in panel.status.text()
    assert "не передан" in panel.status.text()
    panel.cancel()
    panel.deleteLater()


@pytest.mark.parametrize("voice", ["", " bad", "-f", "Milena\n-f /private"])
def test_voice_configuration_rejects_invalid_names(tmp_path: Path, voice: str) -> None:
    with pytest.raises(ValidationError):
        DesktopPreferences(data_directory=tmp_path, speech_voice=voice)


def test_transcription_only_updates_reviewed_scope_draft(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del application
    state = VoiceContext(tmp_path, "one", "Typed first", True)
    drafts: list[str] = []
    voice = FakeVoice()
    panel = VoicePanel(
        DesktopPreferences(data_directory=tmp_path),
        context=lambda: state,
        apply_draft=drafts.append,
        toggle_speech=lambda _enabled: None,
        controller=voice,
    )
    monkeypatch.setattr(panel, "_model_ready", lambda: True)
    panel._begin()
    token = voice.capture_tokens[-1]
    voice.transcript_ready.emit(token, "Recognized text")
    voice.transcript_ready.emit(token, "duplicate")
    assert drafts == ["Typed first\nRecognized text"]
    assert not voice.speeches
    panel.cancel()
    panel.deleteLater()


@pytest.mark.parametrize("change", ["profile", "session", "draft", "cancel"])
def test_late_transcript_never_overwrites_new_context(
    application: QApplication, tmp_path: Path, change: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    del application
    state = VoiceContext(tmp_path, "one", "Draft", True)
    drafts: list[str] = []
    voice = FakeVoice()
    panel = VoicePanel(
        DesktopPreferences(data_directory=tmp_path),
        context=lambda: state,
        apply_draft=drafts.append,
        toggle_speech=lambda _enabled: None,
        controller=voice,
    )
    monkeypatch.setattr(panel, "_model_ready", lambda: True)
    panel._begin()
    token = voice.capture_tokens[-1]
    if change == "profile":
        state = VoiceContext(tmp_path / "other", "one", "Draft", True)
    elif change == "session":
        state = VoiceContext(tmp_path, "two", "Draft", True)
    elif change == "draft":
        state = VoiceContext(tmp_path, "one", "User edit", True)
        panel.editor_changed()
    else:
        panel.cancel()
    voice.transcript_ready.emit(token, "Must not appear")
    assert not drafts
    panel.cancel()
    panel.deleteLater()


def test_unrelated_core_read_does_not_discard_valid_draft(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del application
    state = VoiceContext(tmp_path, "one", "Draft", True)
    drafts: list[str] = []
    voice = FakeVoice()
    panel = VoicePanel(
        DesktopPreferences(data_directory=tmp_path),
        context=lambda: state,
        apply_draft=drafts.append,
        toggle_speech=lambda _enabled: None,
        controller=voice,
    )
    monkeypatch.setattr(panel, "_model_ready", lambda: True)
    panel._begin()
    state = VoiceContext(tmp_path, "one", "Draft", False)
    voice.transcript_ready.emit(voice.capture_tokens[-1], "Recognized")
    assert drafts == ["Draft\nRecognized"]
    panel.cancel()
    panel.deleteLater()


def test_model_requires_verification_before_recording_and_changes_invalidate_it(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del application
    directory = tmp_path / "weights"
    directory.mkdir()
    files = tuple(models.ModelFile(name, 4, "0" * 64) for name in ("model.bin", "config.json"))
    monkeypatch.setattr(models, "MODEL_FILES", files)
    for item in files:
        (directory / item.name).write_bytes(b"test")
    voice = FakeVoice()
    panel = VoicePanel(
        DesktopPreferences(data_directory=tmp_path, voice_model_directory=directory),
        context=lambda: VoiceContext(tmp_path, "one", "", True),
        apply_draft=lambda _draft: None,
        toggle_speech=lambda _enabled: None,
        controller=voice,
    )
    panel._begin()
    assert not panel.microphone.isEnabled()
    assert not voice.capture_tokens
    panel._prepare()
    assert voice.preparations == [directory]
    voice.setup_finished.emit(True)
    assert panel.microphone.isEnabled()
    before = (directory / "model.bin").stat()
    (directory / "model.bin").write_bytes(b"bad!")
    os.utime(directory / "model.bin", ns=(before.st_atime_ns, before.st_mtime_ns + 1))
    panel.refresh()
    panel._begin()
    assert not panel.microphone.isEnabled()
    assert not voice.capture_tokens
    panel.cancel()
    panel.deleteLater()


def test_startup_only_checks_complete_existing_weights_without_preparing_model(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del application
    directory = tmp_path / "weights"
    voice = FakeVoice()
    panel = VoicePanel(
        DesktopPreferences(data_directory=tmp_path, voice_model_directory=directory),
        context=lambda: VoiceContext(tmp_path, "one", "", True),
        apply_draft=lambda _draft: None,
        toggle_speech=lambda _enabled: None,
        controller=voice,
    )
    panel._verify_existing_model()
    assert not voice.preparations
    assert not voice.verifications
    directory.mkdir()
    monkeypatch.setattr(models, "MODEL_FILES", (models.ModelFile("model.bin", 4, "0" * 64),))
    (directory / "model.bin").write_bytes(b"test")
    panel._verify_existing_model()
    assert voice.verifications == [directory]
    assert not voice.preparations
    assert not panel.microphone.isEnabled()
    voice.setup_finished.emit(True)
    assert panel.microphone.isEnabled()
    panel._verify_existing_model()
    assert voice.verifications == [directory]
    panel.cancel()
    panel.deleteLater()


def test_only_new_committed_reply_plays_and_stop_preserves_history(
    window: DesktopWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    window.activated = True
    window.session_id = "session"
    window.voice_panel.preferences = window.preferences.model_copy(
        update={"speak_replies": True, "speech_backend": "system"}
    )
    monkeypatch.setattr(window.process, "state", lambda: QProcess.ProcessState.Running)
    monkeypatch.setattr(window, "isVisible", lambda: True)
    calls: list[str] = []
    monkeypatch.setattr(
        window.voice_panel.controller, "speak", lambda text, **_kw: calls.append(text)
    )
    monkeypatch.setattr(window, "request", lambda *_args, **_kw: None)
    window.pending["send-one"] = "send"
    window.send_scopes["send-one"] = (window.preferences.data_directory, "session")
    window.send_client_requests["send-one"] = "client-one"
    message: dict[str, Any] = {
        "id": "send-one",
        "event": "result",
        "data": {
            "session_id": "session",
            "interaction_id": "interaction-one",
            "text": "Сохранённый ответ",
            "replayed": False,
            "client_request_id": "client-one",
        },
    }
    window._receive(message)
    window._receive(message)
    window.pending["old"] = "history"
    window._receive({"id": "old", "event": "result", "data": {"sessions": [], "interactions": []}})
    assert calls == ["Сохранённый ответ"]
    window.voice_panel.cancel()
    assert window.session_id == "session"


@pytest.mark.parametrize(
    "reason", ["replay", "other-profile", "quitting", "other-session", "wrong-client"]
)
def test_replay_or_wrong_scope_reply_remains_silent(
    window: DesktopWindow, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    window.activated = True
    window.session_id = "session"
    window.voice_panel.preferences = window.preferences.model_copy(
        update={"speak_replies": True, "speech_backend": "system"}
    )
    monkeypatch.setattr(window.process, "state", lambda: QProcess.ProcessState.Running)
    monkeypatch.setattr(window, "isVisible", lambda: True)
    calls: list[str] = []
    monkeypatch.setattr(
        window.voice_panel.controller, "speak", lambda text, **_kw: calls.append(text)
    )
    monkeypatch.setattr(window, "request", lambda *_args, **_kw: None)
    window.pending["result"] = "send"
    scope = window.preferences.data_directory
    window.send_scopes["result"] = (
        scope / "other" if reason == "other-profile" else scope,
        "session",
    )
    window.send_client_requests["result"] = "expected-client"
    window.quitting = reason == "quitting"
    window.editor.setPlainText("Preserved current draft")
    window._receive(
        {
            "id": "result",
            "event": "result",
            "data": {
                "session_id": "other" if reason == "other-session" else "session",
                "interaction_id": "known",
                "text": "Do not speak",
                "replayed": reason == "replay",
                "client_request_id": "wrong" if reason == "wrong-client" else "expected-client",
            },
        }
    )
    assert not calls
    if reason in {"other-profile", "other-session", "wrong-client"}:
        assert window.editor.toPlainText() == "Preserved current draft"
        assert window.session_id == "session"


@pytest.mark.parametrize("malformed_delivery", [False, True])
def test_neural_reply_keeps_the_same_committed_text_and_validated_delivery(
    window: DesktopWindow, monkeypatch: pytest.MonkeyPatch, malformed_delivery: bool
) -> None:
    delivery = VoiceDelivery(
        availability="selected",
        source_policy_version=35,
        delivery_voice=CharacterDeliveryVoice.PLAYFUL_EDGE,
        delivery_goal=CharacterDeliveryGoal.CHALLENGE_CLAIM,
        delivery_pressure=CharacterPressureLevel.NONE,
    )
    packet = delivery.model_dump(mode="json")
    if malformed_delivery:
        packet["instruction"] = "PRIVATE arbitrary prompt"
    window.activated = True
    window.session_id = "session"
    window.voice_panel.preferences = window.preferences.model_copy(
        update={"speak_replies": True, "speech_backend": "neural"}
    )
    monkeypatch.setattr(window.process, "state", lambda: QProcess.ProcessState.Running)
    monkeypatch.setattr(window, "isVisible", lambda: True)
    monkeypatch.setattr(window, "request", lambda *_args, **_kw: None)
    monkeypatch.setattr(window.voice_panel, "_speech_model_ready", lambda: True)
    calls: list[tuple[str, Path, VoiceDelivery | None]] = []
    monkeypatch.setattr(
        window.voice_panel.controller,
        "speak_neural",
        lambda text, model_directory, delivery=None: calls.append(
            (text, model_directory, delivery)
        ),
    )

    def no_fallback(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Neural delivery must never fall back to the system voice")

    monkeypatch.setattr(window.voice_panel.controller, "speak", no_fallback)
    window.pending["neural-send"] = "send"
    window.send_scopes["neural-send"] = (window.preferences.data_directory, "session")
    window.send_client_requests["neural-send"] = "client-neural"
    message = {
        "id": "neural-send",
        "event": "result",
        "data": {
            "session_id": "session",
            "client_request_id": "client-neural",
            "interaction_id": "interaction-neural",
            "text": "Exact owner-committed words.",
            "replayed": False,
            "voice_delivery": packet,
        },
    }
    window._receive(message)
    window._receive(message)
    assert calls == [
        (
            "Exact owner-committed words.",
            window.preferences.speech_model_directory,
            VoiceDelivery() if malformed_delivery else delivery,
        )
    ]


def test_missing_neural_weights_keep_text_available_without_system_fallback(
    application: QApplication, tmp_path: Path
) -> None:
    del application
    voice = FakeVoice()
    panel = VoicePanel(
        DesktopPreferences(
            data_directory=tmp_path,
            voice_model_directory=tmp_path / "asr",
            speech_model_directory=tmp_path / "tts",
            speech_backend="neural",
            speak_replies=True,
        ),
        context=lambda: VoiceContext(tmp_path, "one", "", True),
        apply_draft=lambda _draft: None,
        toggle_speech=lambda _enabled: None,
        controller=voice,
    )
    panel.committed_reply("new-reply", "Committed text remains available.", replayed=False)
    assert not voice.neural_speeches
    assert not voice.speeches
    assert "Экспериментальный локальный голос" in panel.status.text()
    assert not (tmp_path / "tts").exists()
    panel.cancel()
    panel.deleteLater()


@pytest.mark.parametrize("cancel_first", [False, True])
def test_existing_recognition_and_nested_speech_models_are_checked_serially(
    application: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cancel_first: bool,
) -> None:
    recognition = tmp_path / "recognition"
    speech = tmp_path / "speech"
    recognition.mkdir()
    (speech / "speech_tokenizer").mkdir(parents=True)
    recognition_files = (models.ModelFile("acoustic.onnx", 4, "0" * 64),)
    speech_files = (
        models.ModelFile("model.safetensors", 4, "0" * 64),
        models.ModelFile("speech_tokenizer/config.json", 4, "0" * 64),
    )
    monkeypatch.setattr(models, "MODEL_FILES", recognition_files)
    monkeypatch.setattr(neural_models, "MODEL_FILES", speech_files)
    for directory, files in ((recognition, recognition_files), (speech, speech_files)):
        for item in files:
            (directory / item.name).write_bytes(b"test")
    voice = FakeVoice()
    panel = VoicePanel(
        DesktopPreferences(
            data_directory=tmp_path,
            voice_model_directory=recognition,
            speech_model_directory=speech,
            speech_backend="neural",
        ),
        context=lambda: VoiceContext(tmp_path, "one", "", True),
        apply_draft=lambda _draft: None,
        toggle_speech=lambda _enabled: None,
        controller=voice,
    )
    panel._verify_existing_model()
    panel._verify_existing_model()
    assert voice.verifications == [recognition]
    assert not voice.speech_verifications
    if cancel_first:
        panel.cancel()
    voice.setup_finished.emit(True)
    application.processEvents()
    if cancel_first:
        assert not voice.speech_verifications
        assert not panel._model_ready()
    else:
        assert panel._model_ready()
        assert voice.speech_verifications == [speech]
        assert not panel._speech_model_ready()
        voice.setup_finished.emit(True)
        assert panel._speech_model_ready()
        nested = speech / "speech_tokenizer/config.json"
        metadata = nested.stat()
        nested.write_bytes(b"bad!")
        os.utime(nested, ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1))
        assert not panel._speech_model_ready()
        assert panel._model_ready()
    assert not voice.preparations
    assert not voice.speech_preparations
    assert not voice.capture_tokens
    panel.cancel()
    panel.deleteLater()
