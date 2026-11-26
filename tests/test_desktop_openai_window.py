"""Actual Window GPT selection and bounded memory/source commands, using public fakes."""

# ruff: noqa: RUF001 - intentional Russian owner requests and interface labels.

import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from PySide6.QtCore import QProcess, QTimer
from PySide6.QtWidgets import QApplication

from satori.application.retrieval.adoption import (
    MemoryIndexAdoptionReport,
    MemoryIndexAdoptionRequest,
)
from satori.desktop import ui
from satori.desktop.call_inbox_ui import CallInboxDialog
from satori.desktop.settings import DesktopPreferences
from satori.infrastructure.providers.openai_http import OpenAIHttpClient
from tests.test_telegram_chat_handoff import NoChildProcess
from tests.test_telegram_chat_handoff import app as app
from tests.test_telegram_chat_handoff import window as window

QUERY = "привет, посмотри, написали ли мне по собесу последнему"


@pytest.fixture(autouse=True)
def no_real_api_or_children(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("gpt_window_fixture_attempted_real_api_or_child")

    monkeypatch.setattr(OpenAIHttpClient, "post_json", forbidden)
    monkeypatch.setattr(QProcess, "start", forbidden)


def context(w: Any) -> None:
    w._calendar_identity = str(uuid4())
    w._calendar_provider = "ollama:qwen3:4b-instruct"
    w.process = NoChildProcess(w)


def test_source_question_on_unqualified_runtime_stays_in_chat_and_requests_gpt(
    window: Any,
) -> None:
    w, requests = window
    context(w)
    before = w.transcript.toPlainText()
    w.editor.setPlainText(QUERY)
    w.send()
    assert requests == []
    assert w.source_answer.card is not None
    assert w.source_answer.card.status == "gpt_required"
    assert w.source_answer._request is None
    assert w.source_answer._lease is None
    assert w.source_answer_panel.connect_button.isVisible()
    assert not w.source_answer_panel.call_inbox_button.isVisible()
    assert w.tabs.currentWidget() is w.conversation_page
    assert w.transcript.toPlainText() == before
    assert w.mail_panel.controller._connector is None
    w.source_answer_panel.connect_button.click()
    assert w.tabs.currentWidget() is w._workspace_routes["general"]
    assert w.editor.toPlainText() == QUERY
    assert requests == []


def test_gpt_connection_action_preserves_a_newer_unsent_draft(window: Any) -> None:
    w, _ = window
    context(w)
    w.editor.setPlainText(QUERY)
    w.send()
    w.editor.setPlainText("PUBLIC newer draft")
    w._open_gpt_settings()
    assert w.editor.toPlainText() == "PUBLIC newer draft"


def test_calls_only_does_not_promise_gpt_will_connect_telephony(window: Any) -> None:
    w, requests = window
    context(w)
    w.editor.setPlainText("Кто мне звонил?")
    w.send()
    assert requests == []
    assert w.source_answer.card is not None
    assert w.source_answer.card.status == "unsupported"
    assert "нет доступа к журналу звонков оператора" in w.source_answer.card.text
    assert not w.source_answer_panel.connect_button.isVisible()
    assert not w.source_answer_panel.provider.isVisible()
    assert w.source_answer_panel.call_inbox_button.isVisible()
    dialogs = []

    def read_inbox() -> None:
        dialog = QApplication.activeModalWidget()
        if isinstance(dialog, CallInboxDialog):
            dialogs.append((dialog.calls.count(), dialog.message.placeholderText()))
            dialog.accept()

    QTimer.singleShot(0, read_inbox)
    w.source_answer_panel.call_inbox_button.click()
    assert dialogs == [(0, "Пока нет сводок звонков.")]
    assert requests == []
    assert w.transcript.document().isEmpty()


def test_configured_gpt_does_not_imply_a_connected_phone_route(window: Any) -> None:
    w, requests = window
    context(w)
    w.supports_source_answers = True
    w.editor.setPlainText("Кто мне звонил?")
    w.send()
    assert requests == []
    assert w.source_answer.card is not None
    assert w.source_answer.card.status == "unsupported"
    assert w.source_answer_panel.call_inbox_button.isVisible()
    assert w.source_answer._lease is None


@pytest.mark.parametrize("capability", [None, False, 1, "true", True])
def test_only_exact_worker_capability_admits_source_analysis(
    window: Any, capability: object
) -> None:
    w, _ = window
    context(w)
    operation = str(uuid4())
    w.pending[operation] = "status"
    w._receive(
        {
            "id": operation,
            "event": "result",
            "data": {
                "identity_id": w._calendar_identity,
                "activated": True,
                "session_id": w.session_id,
                "provider": "openai",
                "model": "PUBLIC-model-name-cannot-grant",
                "supports_source_answers": capability,
            },
        }
    )
    assert w.supports_source_answers is (capability is True)


def test_explicit_gpt_selection_saves_metadata_reference_and_preserves_identity_profile(
    window: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, _ = window
    saved: list[DesktopPreferences] = []
    restarted: list[bool] = []
    monkeypatch.setattr(ui, "save_preferences", saved.append)
    monkeypatch.setattr(w, "restart", lambda: restarted.append(True))
    original_profile, original_model = w.preferences.data_directory, w.preferences.model
    path = original_profile / "desktop-openai-runtime.json"
    assert w._configure_gpt_preferences(path)
    assert w.preferences.runtime_provider == "openai"
    assert w.preferences.openai_runtime_config == path
    assert w.preferences.data_directory == original_profile
    assert w.preferences.model == original_model
    assert saved == [w.preferences]
    assert restarted == [True]
    assert not path.exists()  # This callback never writes a credential or runtime metadata itself.
    assert not w._configure_gpt_preferences(original_profile / "other.json")
    assert len(saved) == 1


@pytest.mark.parametrize("provider", ["local", "openai"])
def test_launcher_uses_explicit_provider_and_never_passes_ambient_keys(
    window: Any, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    w, _ = window
    for key in (
        "OPENAI_API_KEY",
        "SATORI_OPENAI_API_KEY",
        "ELEVENLABS_API_KEY",
        "PUBLIC_ACCESS_TOKEN",
    ):
        monkeypatch.setenv(key, "PUBLIC-fake-secret-not-a-real-key")
    selected = {
        **w.preferences.model_dump(),
        "runtime_provider": provider,
        "openai_runtime_config": w.preferences.data_directory / "desktop-openai-runtime.json",
    }
    w.preferences = DesktopPreferences.model_validate(selected)
    started: list[bool] = []
    monkeypatch.setattr(w.process, "start", lambda: started.append(True))
    w.start()
    arguments = w.process.arguments()
    assert arguments[arguments.index("--runtime-provider") + 1] == provider
    if provider == "openai":
        assert "--model" not in arguments
        assert arguments[arguments.index("--openai-runtime-config") + 1] == str(
            w.preferences.openai_runtime_config
        )
    else:
        assert arguments[arguments.index("--model") + 1] == w.preferences.model
        assert "--openai-runtime-config" not in arguments
    assert started == [True]
    assert "PUBLIC-fake-secret" not in repr(arguments)
    environment = w.process.processEnvironment()
    for key in (
        "OPENAI_API_KEY",
        "SATORI_OPENAI_API_KEY",
        "ELEVENLABS_API_KEY",
        "PUBLIC_ACCESS_TOKEN",
    ):
        assert not environment.contains(key)


def test_missing_openai_config_never_silently_starts_local_model(
    window: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, _ = window
    w.preferences = DesktopPreferences.model_validate(
        {**w.preferences.model_dump(), "runtime_provider": "openai", "openai_runtime_config": None}
    )
    started: list[bool] = []
    monkeypatch.setattr(w.process, "start", lambda: started.append(True))
    w.start()
    assert started == []
    assert "подключить GPT" in w.state.text()


def test_memory_preparation_is_one_explicit_bounded_scoped_command(window: Any) -> None:
    w, calls = window
    context(w)
    w.supports_source_answers = True
    w.gpt_settings.set_runtime_status({"supports_source_answers": True})
    assert calls == []
    w.gpt_settings.prepare_memory_button.click()
    assert len(calls) == 1
    assert calls[0][0] == "memory_index_adopt"
    request = MemoryIndexAdoptionRequest.model_validate_json(
        json.dumps(calls[0][1]["memory_index_adoption"])
    )
    assert request.identity_id == UUID(w._calendar_identity)
    assert request.session_id == UUID(w.session_id)
    assert request.max_memories == 100
    assert request.confirmed is True
    w.gpt_settings.prepare_memory_button.click()
    assert len(calls) == 1
    report = MemoryIndexAdoptionReport(
        operation_id=request.operation_id,
        status="deferred",
        embedding_space="PUBLIC-openai-space",
        considered=100,
        indexed=100,
        failed=0,
        more_available=True,
    )
    w._memory_adoption_result(
        (request, w.preferences.data_directory), report.model_dump(mode="json")
    )
    assert "100 из 100" in w.gpt_settings.memory_status.text()
    assert "отдельным нажатием" in w.gpt_settings.memory_status.text()
    assert len(calls) == 1


def test_memory_result_from_another_profile_never_updates_current_ui(window: Any) -> None:
    w, _ = window
    context(w)
    w.supports_source_answers = True
    request = MemoryIndexAdoptionRequest(
        operation_id=uuid4(),
        identity_id=UUID(w._calendar_identity),
        session_id=UUID(w.session_id),
        confirmed=True,
    )
    before = w.gpt_settings.memory_status.text()
    w._memory_adoption_result((request, Path("/PUBLIC-another-profile")), {})
    assert w.gpt_settings.memory_status.text() == before
