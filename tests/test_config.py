"""Typed configuration tests."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from satori.config import (
    ConversationProviderKind,
    EmbeddingProviderKind,
    Environment,
    LogLevel,
    OpenAIReasoningEffort,
    Settings,
    YandexReasoningEffort,
)


def test_configuration_has_safe_development_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Tests and imports do not require a .env file."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SATORI_ENVIRONMENT", raising=False)
    monkeypatch.delenv("SATORI_DATABASE_URL", raising=False)
    monkeypatch.delenv("SATORI_LOG_LEVEL", raising=False)

    settings = Settings()

    assert settings.environment is Environment.DEVELOPMENT
    assert settings.database_url == "sqlite+pysqlite:///./var/satori.db"
    assert settings.log_level is LogLevel.INFO
    assert settings.conversation_provider is ConversationProviderKind.OLLAMA
    assert settings.conversation_model == "qwen3:4b-instruct"
    assert settings.conversation_provider_base_url == "http://127.0.0.1:11434"
    assert settings.conversation_timeout_seconds == 120.0
    assert settings.conversation_temperature == 0.3
    assert settings.yandex_ai_studio_reasoning_effort is None
    assert settings.openai_reasoning_effort is OpenAIReasoningEffort.LOW
    assert settings.openai_reasoning_token_allowance == 1024
    assert settings.affective_appraisal_max_output_tokens == 96
    assert settings.affective_appraisal_context_window == 4096
    assert settings.affective_appraisal_model == "qwen3:4b-instruct"
    assert settings.episode_formation_model == "qwen3:4b-instruct"
    assert settings.semantic_formation_model == "qwen3:4b-instruct"
    assert settings.model_formation_max_output_tokens == 512
    assert settings.model_formation_max_user_claims == 2
    assert settings.model_formation_max_world_claims == 2
    assert settings.reflection_provider is ConversationProviderKind.OLLAMA
    assert settings.reflection_model == "qwen3:4b-instruct"
    assert settings.reflection_provider_base_url == "http://127.0.0.1:11434"
    assert settings.reflection_timeout_seconds == 180.0
    assert settings.reflection_max_output_tokens == 768
    assert settings.ollama_keep_alive == "10m"
    assert settings.ollama_serialize_inference is True
    assert settings.ollama_background_aging_seconds == 30.0
    assert settings.ollama_background_grace_seconds == 2.0
    assert settings.recent_conversation_max_turns == 8
    assert settings.recent_conversation_max_chars == 6000
    assert settings.embedding_provider is EmbeddingProviderKind.OLLAMA
    assert settings.embedding_model == "embeddinggemma:300m"
    assert settings.embedding_dimensions == 768
    assert settings.retrieval_minimum_similarity == 0.55
    assert settings.retrieval_top_k == 4


def test_configuration_loads_prefixed_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Runtime configuration is typed and environment-driven."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SATORI_ENVIRONMENT", "test")
    monkeypatch.setenv("SATORI_DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("SATORI_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("SATORI_CONVERSATION_MODEL", "custom:4b")
    monkeypatch.setenv("SATORI_CONVERSATION_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("SATORI_AFFECTIVE_APPRAISAL_MAX_OUTPUT_TOKENS", "640")
    monkeypatch.setenv("SATORI_REFLECTION_MODEL", "reflection:7b")
    monkeypatch.setenv("SATORI_REFLECTION_TIMEOUT_SECONDS", "90")
    monkeypatch.setenv("SATORI_REFLECTION_MAX_OUTPUT_TOKENS", "512")

    settings = Settings()

    assert settings.environment is Environment.TEST
    assert settings.database_url == "sqlite+pysqlite:///:memory:"
    assert settings.log_level is LogLevel.DEBUG
    assert settings.conversation_model == "custom:4b"
    assert settings.conversation_timeout_seconds == 45.0
    assert settings.affective_appraisal_max_output_tokens == 640
    assert settings.reflection_model == "reflection:7b"
    assert settings.reflection_timeout_seconds == 90.0
    assert settings.reflection_max_output_tokens == 512


def test_explicit_test_environment_ignores_local_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Repository tests cannot inherit a developer's paid foreground selection."""

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "SATORI_CONVERSATION_PROVIDER=openai\nSATORI_CONVERSATION_MODEL=gpt-5.6-terra\n",
        encoding="utf-8",
    )

    settings = Settings(environment=Environment.TEST)

    assert settings.conversation_provider is ConversationProviderKind.OLLAMA
    assert settings.conversation_model == "qwen3:4b-instruct"


def test_process_environment_overrides_dotenv_for_provider_selection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Operator environment variables keep their documented priority over .env."""

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "SATORI_CONVERSATION_PROVIDER=openai\n"
        "SATORI_CONVERSATION_MODEL=gpt-5.6-terra\n"
        "SATORI_OPENAI_API_KEY=dotenv-test-key\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SATORI_CONVERSATION_PROVIDER", "ollama")
    monkeypatch.setenv("SATORI_CONVERSATION_MODEL", "qwen3:4b-instruct")

    settings = Settings()

    assert settings.conversation_provider is ConversationProviderKind.OLLAMA
    assert settings.conversation_model == "qwen3:4b-instruct"


def test_configuration_rejects_blank_database_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An unusable persistence target fails at the typed boundary."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SATORI_DATABASE_URL", "   ")

    with pytest.raises(ValidationError, match="database_url must not be blank"):
        Settings()


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("SATORI_CONVERSATION_MODEL", "   ", "conversation_model must not be blank"),
        (
            "SATORI_CONVERSATION_PROVIDER_BASE_URL",
            "ftp://localhost",
            "must be an HTTP\\(S\\) URL",
        ),
        (
            "SATORI_CONVERSATION_PROVIDER_BASE_URL",
            "http://user:secret@localhost",
            "must be an HTTP\\(S\\) URL",
        ),
        (
            "SATORI_CONVERSATION_PROVIDER_BASE_URL",
            "http://localhost/custom/path",
            "must be an HTTP\\(S\\) URL",
        ),
        ("SATORI_CONVERSATION_TIMEOUT_SECONDS", "0", "greater than 0"),
        (
            "SATORI_OPENAI_REASONING_TOKEN_ALLOWANCE",
            "4097",
            "less than or equal to 4096",
        ),
        ("SATORI_REFLECTION_MODEL", "   ", "reflection_model must not be blank"),
        (
            "SATORI_REFLECTION_PROVIDER_BASE_URL",
            "http://user:secret@localhost",
            "must be an HTTP\\(S\\) URL",
        ),
        ("SATORI_REFLECTION_TIMEOUT_SECONDS", "0", "greater than 0"),
        ("SATORI_REFLECTION_MAX_OUTPUT_TOKENS", "769", "less than or equal to 768"),
    ],
)
def test_configuration_rejects_invalid_conversation_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
    value: str,
    message: str,
) -> None:
    """Provider selection and timeout fail at the typed configuration boundary."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError, match=message):
        Settings()


def test_configuration_rejects_incoherent_retrieval_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SATORI_RETRIEVAL_SEMANTIC_WEIGHT", "0.4")
    monkeypatch.setenv("SATORI_RETRIEVAL_IMPORTANCE_WEIGHT", "0.3")
    monkeypatch.setenv("SATORI_RETRIEVAL_RECENCY_WEIGHT", "0.3")

    with pytest.raises(ValidationError, match="semantic weight must dominate"):
        Settings()


def test_configuration_loads_yandex_foreground_without_exposing_secret(
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

    assert settings.conversation_provider is ConversationProviderKind.YANDEX_AI_STUDIO
    assert settings.yandex_ai_studio_api_key is not None
    assert settings.yandex_ai_studio_api_key.get_secret_value() == "private-test-key"
    assert settings.yandex_ai_studio_base_url == "https://ai.api.cloud.yandex.net/v1"
    assert settings.yandex_ai_studio_reasoning_effort is YandexReasoningEffort.LOW
    assert "private-test-key" not in repr(settings)


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({}, "api_key is required"),
        (
            {"SATORI_YANDEX_AI_STUDIO_API_KEY": "key"},
            "conversation_model must select an explicit",
        ),
        (
            {
                "SATORI_YANDEX_AI_STUDIO_API_KEY": "key",
                "SATORI_CONVERSATION_MODEL": "deepseek-v4-flash",
            },
            "folder_id is required",
        ),
    ],
)
def test_configuration_requires_complete_yandex_foreground_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    environment: dict[str, str],
    message: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SATORI_CONVERSATION_PROVIDER", "yandex_ai_studio")
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError, match=message):
        Settings()


def test_configuration_accepts_complete_yandex_model_uri_without_folder(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SATORI_CONVERSATION_PROVIDER", "yandex_ai_studio")
    monkeypatch.setenv("SATORI_CONVERSATION_MODEL", "gpt://folder/yandexgpt/latest")
    monkeypatch.setenv("SATORI_YANDEX_AI_STUDIO_API_KEY", "key")

    assert Settings().yandex_ai_studio_folder_id is None


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("ollama", "qwen3:4b-instruct"),
        ("yandex_ai_studio", "yandexgpt/latest"),
    ],
)
def test_reasoning_effort_is_restricted_to_yandex_hosted_deepseek(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    provider: str,
    model: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SATORI_CONVERSATION_PROVIDER", provider)
    monkeypatch.setenv("SATORI_CONVERSATION_MODEL", model)
    monkeypatch.setenv("SATORI_YANDEX_AI_STUDIO_REASONING_EFFORT", "low")
    if provider == "yandex_ai_studio":
        monkeypatch.setenv("SATORI_YANDEX_AI_STUDIO_FOLDER_ID", "folder-1")
        monkeypatch.setenv("SATORI_YANDEX_AI_STUDIO_API_KEY", "key")

    with pytest.raises(ValidationError, match=r"supported only.*DeepSeek"):
        Settings()


def test_configuration_rejects_yandex_for_background_capabilities(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SATORI_EPISODE_FORMATION_PROVIDER", "yandex_ai_studio")

    with pytest.raises(ValidationError, match="authorized only for conversation_provider"):
        Settings()


def test_configuration_pins_yandex_credentials_to_canonical_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SATORI_CONVERSATION_PROVIDER", "yandex_ai_studio")
    monkeypatch.setenv("SATORI_CONVERSATION_MODEL", "gpt://folder/model")
    monkeypatch.setenv("SATORI_YANDEX_AI_STUDIO_API_KEY", "key")
    monkeypatch.setenv("SATORI_YANDEX_AI_STUDIO_BASE_URL", "https://example.com/v1")

    with pytest.raises(
        ValidationError,
        match=r"exactly https://ai\.api\.cloud\.yandex\.net/v1",
    ):
        Settings()


def test_configuration_loads_openai_foreground_without_exposing_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SATORI_CONVERSATION_PROVIDER", "openai")
    monkeypatch.setenv("SATORI_CONVERSATION_MODEL", "gpt-5.6-terra")
    monkeypatch.setenv("SATORI_OPENAI_API_KEY", "private-test-key")
    monkeypatch.setenv("SATORI_OPENAI_REASONING_EFFORT", "medium")
    monkeypatch.setenv("SATORI_OPENAI_REASONING_TOKEN_ALLOWANCE", "1536")

    settings = Settings()

    assert settings.conversation_provider is ConversationProviderKind.OPENAI
    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "private-test-key"
    assert settings.openai_base_url == "https://api.openai.com/v1"
    assert settings.openai_reasoning_effort is OpenAIReasoningEffort.MEDIUM
    assert settings.openai_reasoning_token_allowance == 1536
    assert "private-test-key" not in repr(settings)


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({}, "openai_api_key is required"),
        ({"SATORI_OPENAI_API_KEY": "key"}, "explicit OpenAI model"),
    ],
)
def test_configuration_requires_complete_openai_foreground_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    environment: dict[str, str],
    message: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SATORI_CONVERSATION_PROVIDER", "openai")
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError, match=message):
        Settings()


def test_configuration_pins_openai_credentials_to_canonical_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SATORI_CONVERSATION_PROVIDER", "openai")
    monkeypatch.setenv("SATORI_CONVERSATION_MODEL", "gpt-5.6-terra")
    monkeypatch.setenv("SATORI_OPENAI_API_KEY", "key")
    monkeypatch.setenv("SATORI_OPENAI_BASE_URL", "https://example.com/v1")

    with pytest.raises(
        ValidationError,
        match=r"exactly https://api\.openai\.com/v1",
    ):
        Settings()
