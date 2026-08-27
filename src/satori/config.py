"""Typed runtime configuration for SATORI."""

from enum import StrEnum
from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class Environment(StrEnum):
    """Supported runtime environment labels."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    """Supported standard-library logging levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ConversationProviderKind(StrEnum):
    """Configured conversation provider adapter."""

    OLLAMA = "ollama"
    YANDEX_AI_STUDIO = "yandex_ai_studio"
    OPENAI = "openai"


class YandexReasoningEffort(StrEnum):
    """Explicit provider-local reasoning depth for supported Yandex-hosted models."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class OpenAIReasoningEffort(StrEnum):
    """Provider-local reasoning depth for OpenAI Responses API models."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class EmbeddingProviderKind(StrEnum):
    """Configured embedding provider adapter."""

    OLLAMA = "ollama"


class Settings(BaseSettings):
    """Application settings loaded from SATORI_* environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="SATORI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = Environment.DEVELOPMENT
    database_url: str = "sqlite+pysqlite:///./var/satori.db"
    log_level: LogLevel = LogLevel.INFO
    conversation_provider: ConversationProviderKind = ConversationProviderKind.OLLAMA
    conversation_model: str = "qwen3:4b-instruct"
    conversation_provider_base_url: str = "http://127.0.0.1:11434"
    conversation_timeout_seconds: float = Field(default=120.0, gt=0.0, le=600.0)
    conversation_temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    conversation_max_output_tokens: int = Field(default=768, ge=1, le=4096)
    conversation_max_input_chars: int = Field(default=8000, ge=1, le=100_000)
    conversation_max_context_chars: int = Field(default=12_000, ge=1000, le=100_000)
    conversation_max_response_chars: int = Field(default=12_000, ge=1, le=100_000)
    yandex_ai_studio_api_key: SecretStr | None = Field(default=None, repr=False)
    yandex_ai_studio_folder_id: str | None = None
    yandex_ai_studio_base_url: str = "https://ai.api.cloud.yandex.net/v1"
    yandex_ai_studio_reasoning_effort: YandexReasoningEffort | None = None
    openai_api_key: SecretStr | None = Field(default=None, repr=False)
    openai_base_url: str = "https://api.openai.com/v1"
    openai_reasoning_effort: OpenAIReasoningEffort = OpenAIReasoningEffort.LOW
    openai_reasoning_token_allowance: int = Field(default=1024, ge=0, le=4096)
    recent_conversation_max_turns: int = Field(default=8, ge=1, le=32)
    recent_conversation_max_chars: int = Field(default=6000, ge=256, le=40_000)
    ollama_keep_alive: str = "10m"
    ollama_serialize_inference: bool = True
    ollama_background_aging_seconds: float = Field(default=30.0, gt=0.0, le=600.0)
    ollama_background_grace_seconds: float = Field(default=2.0, ge=0.0, le=30.0)
    episode_formation_provider: ConversationProviderKind = ConversationProviderKind.OLLAMA
    episode_formation_model: str = "qwen3:4b-instruct"
    episode_formation_max_output_tokens: int = Field(default=512, ge=64, le=2048)
    semantic_formation_provider: ConversationProviderKind = ConversationProviderKind.OLLAMA
    semantic_formation_model: str = "qwen3:4b-instruct"
    semantic_formation_max_output_tokens: int = Field(default=768, ge=64, le=4096)
    model_formation_provider: ConversationProviderKind = ConversationProviderKind.OLLAMA
    model_formation_model: str = "qwen3:4b-instruct"
    model_formation_max_output_tokens: int = Field(default=512, ge=64, le=4096)
    model_formation_max_source_messages: int = Field(default=8, ge=1, le=32)
    model_formation_max_user_claims: int = Field(default=2, ge=1, le=8)
    model_formation_max_world_claims: int = Field(default=2, ge=1, le=8)
    model_backfill_limit: int = Field(default=100, ge=1, le=10_000)
    position_formation_provider: ConversationProviderKind = ConversationProviderKind.OLLAMA
    position_formation_model: str = "qwen3:4b-instruct"
    position_formation_max_output_tokens: int = Field(default=640, ge=64, le=4096)
    position_formation_max_source_messages: int = Field(default=8, ge=1, le=32)
    position_formation_max_positions: int = Field(default=3, ge=1, le=8)
    position_backfill_limit: int = Field(default=100, ge=1, le=10_000)
    position_context_top_k: int = Field(default=4, ge=1, le=12)
    position_context_max_chars: int = Field(default=1600, ge=256, le=12_000)
    reflection_provider: ConversationProviderKind = ConversationProviderKind.OLLAMA
    reflection_model: str = "qwen3:4b-instruct"
    reflection_provider_base_url: str = "http://127.0.0.1:11434"
    reflection_timeout_seconds: float = Field(default=180.0, gt=0.0, le=600.0)
    reflection_max_output_tokens: int = Field(default=768, ge=64, le=768)
    affective_appraisal_provider: ConversationProviderKind = ConversationProviderKind.OLLAMA
    affective_appraisal_model: str = "qwen3:4b-instruct"
    affective_appraisal_provider_base_url: str = "http://127.0.0.1:11434"
    affective_appraisal_timeout_seconds: float = Field(default=120.0, gt=0.0, le=600.0)
    affective_appraisal_max_output_tokens: int = Field(default=96, ge=32, le=1024)
    affective_appraisal_context_window: int = Field(default=4096, ge=512, le=32_768)
    relationship_appraisal_provider: ConversationProviderKind = ConversationProviderKind.OLLAMA
    relationship_appraisal_model: str = "qwen3:4b-instruct"
    relationship_appraisal_provider_base_url: str = "http://127.0.0.1:11434"
    relationship_appraisal_timeout_seconds: float = Field(default=120.0, gt=0.0, le=600.0)
    relationship_appraisal_max_output_tokens: int = Field(default=64, ge=32, le=512)
    relationship_appraisal_context_window: int = Field(default=4096, ge=512, le=32_768)
    default_counterparty_id: str = "local-default"
    semantic_max_claims_per_memory: int = Field(default=4, ge=1, le=16)
    semantic_max_source_memories: int = Field(default=6, ge=1, le=16)
    semantic_backfill_limit: int = Field(default=100, ge=1, le=10_000)
    semantic_retrieval_top_k: int = Field(default=4, ge=1, le=16)
    semantic_retrieval_max_context_chars: int = Field(default=2000, ge=256, le=20_000)
    embedding_provider: EmbeddingProviderKind = EmbeddingProviderKind.OLLAMA
    embedding_model: str = "embeddinggemma:300m"
    embedding_provider_base_url: str = "http://127.0.0.1:11434"
    embedding_dimensions: int = Field(default=768, ge=1, le=8192)
    embedding_timeout_seconds: float = Field(default=120.0, gt=0.0, le=600.0)
    retrieval_minimum_similarity: float = Field(default=0.55, ge=-1.0, le=1.0)
    retrieval_candidate_limit: int = Field(default=32, ge=1, le=1000)
    retrieval_top_k: int = Field(default=4, ge=1, le=32)
    retrieval_max_context_chars: int = Field(default=2400, ge=256, le=20_000)
    retrieval_semantic_weight: float = Field(default=0.80, ge=0.0, le=1.0)
    retrieval_importance_weight: float = Field(default=0.10, ge=0.0, le=1.0)
    retrieval_recency_weight: float = Field(default=0.10, ge=0.0, le=1.0)
    retrieval_recency_half_life_days: float = Field(default=30.0, gt=0.0, le=3650.0)
    chat_log_path: str = "./var/satori-runtime.jsonl"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Keep explicit test runtimes independent from an operator's local .env."""

        del settings_cls
        initial_environment = init_settings().get("environment")
        environment_environment = env_settings().get("environment")
        effective_environment = initial_environment or environment_environment
        if effective_environment in {Environment.TEST, Environment.TEST.value}:
            return init_settings, env_settings, file_secret_settings
        return init_settings, env_settings, dotenv_settings, file_secret_settings

    @field_validator("database_url")
    @classmethod
    def database_url_must_not_be_blank(cls, value: str) -> str:
        """Reject a configuration that cannot identify a persistence target."""

        normalized = value.strip()
        if not normalized:
            raise ValueError("database_url must not be blank")
        return normalized

    @field_validator(
        "conversation_model",
        "episode_formation_model",
        "semantic_formation_model",
        "model_formation_model",
        "position_formation_model",
        "reflection_model",
        "affective_appraisal_model",
        "relationship_appraisal_model",
        "default_counterparty_id",
        "embedding_model",
        "ollama_keep_alive",
        "chat_log_path",
    )
    @classmethod
    def conversation_model_must_not_be_blank(cls, value: str, info: ValidationInfo) -> str:
        """Require model selection at configuration, never inside domain logic."""

        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{info.field_name} must not be blank")
        return normalized

    @field_validator("yandex_ai_studio_folder_id")
    @classmethod
    def yandex_folder_id_must_be_an_opaque_segment(cls, value: str | None) -> str | None:
        """Keep the folder identifier out of URLs and reject ambiguous model-URI assembly."""

        if value is None:
            return None
        normalized = value.strip()
        if (
            not normalized
            or "/" in normalized
            or any(character.isspace() for character in normalized)
        ):
            raise ValueError("yandex_ai_studio_folder_id must be one non-blank path segment")
        return normalized

    @field_validator(
        "conversation_provider_base_url",
        "affective_appraisal_provider_base_url",
        "relationship_appraisal_provider_base_url",
        "reflection_provider_base_url",
        "embedding_provider_base_url",
        "yandex_ai_studio_base_url",
        "openai_base_url",
    )
    @classmethod
    def conversation_provider_base_url_must_be_http(
        cls,
        value: str,
        info: ValidationInfo,
    ) -> str:
        """Accept an explicit HTTP(S) provider origin without credentials or query data."""

        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        allowed_paths = {"", "/"}
        if info.field_name == "yandex_ai_studio_base_url":
            allowed_paths.add("/v1")
        if info.field_name == "openai_base_url":
            allowed_paths.add("/v1")
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in allowed_paths
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "conversation_provider_base_url must be an HTTP(S) URL without credentials, "
                "query, or fragment"
            )
        return normalized

    @model_validator(mode="after")
    def retrieval_policy_must_be_coherent(self) -> "Settings":
        """Reject ambiguous ranking and impossible selection policies at startup."""

        weights = (
            self.retrieval_semantic_weight,
            self.retrieval_importance_weight,
            self.retrieval_recency_weight,
        )
        if abs(sum(weights) - 1.0) > 1e-9:
            raise ValueError("retrieval weights must sum to 1")
        if self.retrieval_semantic_weight <= sum(weights[1:]):
            raise ValueError("retrieval semantic weight must dominate secondary weights")
        if self.retrieval_top_k > self.retrieval_candidate_limit:
            raise ValueError("retrieval_top_k cannot exceed retrieval_candidate_limit")

        background_providers = {
            "episode_formation_provider": self.episode_formation_provider,
            "semantic_formation_provider": self.semantic_formation_provider,
            "model_formation_provider": self.model_formation_provider,
            "position_formation_provider": self.position_formation_provider,
            "reflection_provider": self.reflection_provider,
            "affective_appraisal_provider": self.affective_appraisal_provider,
            "relationship_appraisal_provider": self.relationship_appraisal_provider,
        }
        unsupported = [
            name
            for name, provider in background_providers.items()
            if provider is not ConversationProviderKind.OLLAMA
        ]
        if unsupported:
            raise ValueError(
                "Cloud providers are authorized only for conversation_provider; unsupported "
                f"settings: {', '.join(unsupported)}"
            )

        if self.conversation_provider is ConversationProviderKind.YANDEX_AI_STUDIO:
            if (
                self.yandex_ai_studio_api_key is None
                or not self.yandex_ai_studio_api_key.get_secret_value().strip()
            ):
                raise ValueError(
                    "yandex_ai_studio_api_key is required when conversation_provider is "
                    "yandex_ai_studio"
                )
            parsed_yandex_url = urlsplit(self.yandex_ai_studio_base_url)
            if (
                parsed_yandex_url.scheme != "https"
                or parsed_yandex_url.hostname != "ai.api.cloud.yandex.net"
                or parsed_yandex_url.port is not None
                or parsed_yandex_url.path != "/v1"
            ):
                raise ValueError(
                    "yandex_ai_studio_base_url must be exactly "
                    "https://ai.api.cloud.yandex.net/v1 for credential safety"
                )
            if self.conversation_model.startswith("gpt://"):
                parsed_model = urlsplit(self.conversation_model)
                if (
                    parsed_model.scheme != "gpt"
                    or not parsed_model.hostname
                    or parsed_model.username is not None
                    or parsed_model.password is not None
                    or parsed_model.port is not None
                    or parsed_model.path in {"", "/"}
                    or parsed_model.query
                    or parsed_model.fragment
                    or any(character.isspace() for character in self.conversation_model)
                ):
                    raise ValueError("conversation_model must be a valid gpt:// model URI")
            else:
                if self.conversation_model == "qwen3:4b-instruct":
                    raise ValueError(
                        "conversation_model must select an explicit Yandex AI Studio model"
                    )
                if self.yandex_ai_studio_folder_id is None:
                    raise ValueError(
                        "yandex_ai_studio_folder_id is required when conversation_model is not "
                        "a complete gpt:// URI"
                    )
        if self.conversation_provider is ConversationProviderKind.OPENAI:
            if self.openai_api_key is None or not self.openai_api_key.get_secret_value().strip():
                raise ValueError("openai_api_key is required when conversation_provider is openai")
            parsed_openai_url = urlsplit(self.openai_base_url)
            if (
                parsed_openai_url.scheme != "https"
                or parsed_openai_url.hostname != "api.openai.com"
                or parsed_openai_url.port is not None
                or parsed_openai_url.path != "/v1"
            ):
                raise ValueError(
                    "openai_base_url must be exactly https://api.openai.com/v1 for credential "
                    "safety"
                )
            if self.conversation_model == "qwen3:4b-instruct":
                raise ValueError("conversation_model must select an explicit OpenAI model")
        if self.yandex_ai_studio_reasoning_effort is not None and (
            self.conversation_provider is not ConversationProviderKind.YANDEX_AI_STUDIO
            or "deepseek" not in self.conversation_model.casefold()
        ):
            raise ValueError(
                "yandex_ai_studio_reasoning_effort is supported only for a Yandex-hosted "
                "DeepSeek foreground model"
            )
        return self


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    """Load and cache process-wide settings."""

    return Settings()
