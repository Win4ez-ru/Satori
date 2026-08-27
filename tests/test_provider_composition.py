"""Capability routing tests at the CLI composition root."""

from pathlib import Path

import pytest

from satori.__main__ import _configured_conversation_provider
from satori.config import Settings
from satori.infrastructure.providers.ollama import OllamaConversationAdapter
from satori.infrastructure.providers.openai import OpenAIConversationAdapter
from satori.infrastructure.providers.yandex_ai_studio import (
    YandexAIStudioConversationAdapter,
)


def test_conversation_provider_defaults_to_ollama(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert isinstance(_configured_conversation_provider(Settings()), OllamaConversationAdapter)


def test_conversation_provider_routes_only_foreground_to_yandex(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SATORI_CONVERSATION_PROVIDER", "yandex_ai_studio")
    monkeypatch.setenv("SATORI_CONVERSATION_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("SATORI_YANDEX_AI_STUDIO_FOLDER_ID", "folder-1")
    monkeypatch.setenv("SATORI_YANDEX_AI_STUDIO_API_KEY", "private-test-key")
    monkeypatch.setenv("SATORI_YANDEX_AI_STUDIO_REASONING_EFFORT", "low")
    settings = Settings()

    provider = _configured_conversation_provider(settings)

    assert isinstance(provider, YandexAIStudioConversationAdapter)
    assert provider.model == "gpt://folder-1/deepseek-v4-flash"
    assert provider.reasoning_effort == "low"
    assert settings.episode_formation_provider.value == "ollama"
    assert settings.semantic_formation_provider.value == "ollama"
    assert settings.affective_appraisal_provider.value == "ollama"
    assert settings.relationship_appraisal_provider.value == "ollama"


def test_conversation_provider_routes_only_foreground_to_openai(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SATORI_CONVERSATION_PROVIDER", "openai")
    monkeypatch.setenv("SATORI_CONVERSATION_MODEL", "gpt-5.6-terra")
    monkeypatch.setenv("SATORI_OPENAI_API_KEY", "private-test-key")
    monkeypatch.setenv("SATORI_OPENAI_REASONING_EFFORT", "low")
    monkeypatch.setenv("SATORI_OPENAI_REASONING_TOKEN_ALLOWANCE", "1536")
    settings = Settings()

    provider = _configured_conversation_provider(settings)

    assert isinstance(provider, OpenAIConversationAdapter)
    assert provider.model == "gpt-5.6-terra"
    assert provider.reasoning_effort == "low"
    assert provider.reasoning_token_allowance == 1536
    assert settings.episode_formation_provider.value == "ollama"
    assert settings.semantic_formation_provider.value == "ollama"
    assert settings.affective_appraisal_provider.value == "ollama"
    assert settings.relationship_appraisal_provider.value == "ollama"
