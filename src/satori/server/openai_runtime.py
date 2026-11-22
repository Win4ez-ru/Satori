"""Explicit all-role OpenAI composition with caller-owned admitted transports.

Construction performs no I/O. The caller must supply a budgeted transport for
all nine roles and owns its credential custody and close lifecycle. This module
never constructs a raw HTTP client, a local provider or an API fallback.
"""

from collections.abc import Mapping
from pathlib import Path

from pydantic import SecretStr

from satori.config import (
    ConversationProviderKind,
    EmbeddingProviderKind,
    OpenAIModelProfile,
    OpenAIReasoningEffort,
    Settings,
)
from satori.desktop.backend import DesktopProviders
from satori.infrastructure.providers.openai import OpenAIConversationAdapter
from satori.infrastructure.providers.openai_embedding import OpenAIEmbeddingAdapter
from satori.infrastructure.providers.openai_roles import (
    OpenAIAffectiveAppraisalAdapter,
    OpenAIEpisodeFormationAdapter,
    OpenAIModelFormationAdapter,
    OpenAIPositionFormationAdapter,
    OpenAIReflectionAdapter,
    OpenAIRelationshipAppraisalAdapter,
    OpenAISemanticFormationAdapter,
)
from satori.infrastructure.providers.openai_structured import (
    OpenAIStructuredClient,
    OpenAIStructuredTransport,
)
from satori.server.usage import Model, Role

OPENAI_GENERATION_MODEL = "gpt-6-luna"
OPENAI_FOREGROUND_TERRA_MODEL = "gpt-6.1-terra"
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENAI_OUTPUT_TOKEN_LIMIT_FLOOR = 128
OPENAI_ROLES = frozenset(
    {
        Role.CONVERSATION,
        Role.EPISODE,
        Role.EMBEDDING,
        Role.SEMANTIC,
        Role.APPRAISAL,
        Role.RELATIONSHIP,
        Role.MODELS,
        Role.POSITIONS,
        Role.REFLECTION,
    }
)


class OpenAIRuntimeConfigurationError(ValueError):
    """Finite configuration failure without credentials or input echoes."""

    def __init__(self) -> None:
        super().__init__("openai_runtime_configuration_rejected")


def build_openai_settings(
    data_directory: Path,
    api_key: SecretStr,
    *,
    embedding_dimensions: int = 1536,
    model_profile: OpenAIModelProfile = OpenAIModelProfile.ALL_LUNA,
) -> Settings:
    """Build exact settings from explicit values; ignore .env and inherited SATORI_*.

    A new embedding space is explicit, not permission to relabel existing vectors.
    This does not initialize, migrate or activate the supplied directory.
    """
    if not isinstance(api_key, SecretStr) or not api_key.get_secret_value().strip():
        raise OpenAIRuntimeConfigurationError()
    if not isinstance(model_profile, OpenAIModelProfile):
        raise OpenAIRuntimeConfigurationError()
    if not data_directory.is_absolute() or ".." in data_directory.parts:
        raise OpenAIRuntimeConfigurationError()
    root = data_directory.resolve()
    values = {
        name: field.get_default(call_default_factory=True)
        for name, field in Settings.model_fields.items()
    }
    values.update(
        all_openai_enabled=True,
        openai_model_profile=model_profile,
        database_url=f"sqlite+pysqlite:///{root / 'satori.db'}",
        source_policy_directory=root / "source-policy",
        chat_log_path=str(root / "satori-runtime.jsonl"),
        conversation_provider=ConversationProviderKind.OPENAI,
        conversation_model=(
            OPENAI_FOREGROUND_TERRA_MODEL
            if model_profile is OpenAIModelProfile.FOREGROUND_TERRA
            else OPENAI_GENERATION_MODEL
        ),
        conversation_output_token_limit_floor=OPENAI_OUTPUT_TOKEN_LIMIT_FLOOR,
        conversation_provider_base_url=OPENAI_BASE_URL,
        episode_formation_provider=ConversationProviderKind.OPENAI,
        episode_formation_model=OPENAI_GENERATION_MODEL,
        semantic_formation_provider=ConversationProviderKind.OPENAI,
        semantic_formation_model=OPENAI_GENERATION_MODEL,
        model_formation_provider=ConversationProviderKind.OPENAI,
        model_formation_model=OPENAI_GENERATION_MODEL,
        position_formation_provider=ConversationProviderKind.OPENAI,
        position_formation_model=OPENAI_GENERATION_MODEL,
        reflection_provider=ConversationProviderKind.OPENAI,
        reflection_model=OPENAI_GENERATION_MODEL,
        reflection_provider_base_url=OPENAI_BASE_URL,
        affective_appraisal_provider=ConversationProviderKind.OPENAI,
        affective_appraisal_model=OPENAI_GENERATION_MODEL,
        affective_appraisal_provider_base_url=OPENAI_BASE_URL,
        relationship_appraisal_provider=ConversationProviderKind.OPENAI,
        relationship_appraisal_model=OPENAI_GENERATION_MODEL,
        relationship_appraisal_provider_base_url=OPENAI_BASE_URL,
        embedding_provider=EmbeddingProviderKind.OPENAI,
        embedding_model=OPENAI_EMBEDDING_MODEL,
        embedding_provider_base_url=OPENAI_BASE_URL,
        embedding_dimensions=embedding_dimensions,
        openai_base_url=OPENAI_BASE_URL,
        openai_api_key=api_key,
        openai_reasoning_effort=OpenAIReasoningEffort.NONE,
        openai_reasoning_token_allowance=0,
    )
    try:
        return Settings(_env_file=None, **values)  # type: ignore[call-arg]
    except ValueError:
        raise OpenAIRuntimeConfigurationError() from None


def budget_model_for_role(settings: Settings, role: Role) -> Model:
    """Bind pricing metadata to one validated profile and its exact capability role."""
    try:
        explicit = {name: getattr(settings, name) for name in Settings.model_fields}
        checked = Settings(_env_file=None, **explicit)  # type: ignore[call-arg]
        if not checked.all_openai_enabled or not isinstance(role, Role) or role not in OPENAI_ROLES:
            raise OpenAIRuntimeConfigurationError()
        if role is Role.EMBEDDING:
            return Model.TEXT_EMBEDDING_3_SMALL
        if (
            role is Role.CONVERSATION
            and checked.openai_model_profile is OpenAIModelProfile.FOREGROUND_TERRA
        ):
            return Model.GPT_6_1_TERRA
        return Model.GPT_6_LUNA
    except (ValueError, TypeError, AttributeError):
        raise OpenAIRuntimeConfigurationError() from None


def build_openai_providers(
    settings: Settings,
    transports: Mapping[Role, OpenAIStructuredTransport],
) -> DesktopProviders:
    """Wire all nine capability ports to their distinct pre-admitted transport.

    Transports are supplied by the budget composition owner. This pure factory
    cannot prove their billing/disclosure authority; runtime admission still
    belongs to the existing source/budget owners at the actual dispatch point.
    """
    try:
        explicit = {name: getattr(settings, name) for name in Settings.model_fields}
        checked = Settings(_env_file=None, **explicit)  # type: ignore[call-arg]
        if not checked.all_openai_enabled or checked.openai_api_key is None:
            raise OpenAIRuntimeConfigurationError()
        supplied = dict(transports)
        if set(supplied) != OPENAI_ROLES or any(
            not callable(getattr(transport, "post_json", None)) for transport in supplied.values()
        ):
            raise OpenAIRuntimeConfigurationError()
        return DesktopProviders(
            conversation=OpenAIConversationAdapter(
                base_url=checked.openai_base_url,
                api_key=checked.openai_api_key.get_secret_value(),
                model=checked.conversation_model,
                timeout_seconds=checked.conversation_timeout_seconds,
                reasoning_effort="none",
                reasoning_token_allowance=0,
                http_client=supplied[Role.CONVERSATION],
            ),
            episode=OpenAIEpisodeFormationAdapter(
                client=OpenAIStructuredClient(supplied[Role.EPISODE]),
                model=checked.episode_formation_model,
                timeout_seconds=checked.conversation_timeout_seconds,
                max_output_tokens=checked.episode_formation_max_output_tokens,
            ),
            embedding=OpenAIEmbeddingAdapter(
                model=checked.embedding_model,
                dimensions=checked.embedding_dimensions,
                input_schema_version=1,
                timeout_seconds=checked.embedding_timeout_seconds,
                http_client=supplied[Role.EMBEDDING],
            ),
            semantic=OpenAISemanticFormationAdapter(
                client=OpenAIStructuredClient(supplied[Role.SEMANTIC]),
                model=checked.semantic_formation_model,
                timeout_seconds=checked.conversation_timeout_seconds,
                max_output_tokens=checked.semantic_formation_max_output_tokens,
            ),
            appraisal=OpenAIAffectiveAppraisalAdapter(
                client=OpenAIStructuredClient(supplied[Role.APPRAISAL]),
                model=checked.affective_appraisal_model,
                timeout_seconds=checked.affective_appraisal_timeout_seconds,
                max_output_tokens=checked.affective_appraisal_max_output_tokens,
            ),
            relationship=OpenAIRelationshipAppraisalAdapter(
                client=OpenAIStructuredClient(supplied[Role.RELATIONSHIP]),
                model=checked.relationship_appraisal_model,
                timeout_seconds=checked.relationship_appraisal_timeout_seconds,
                max_output_tokens=checked.relationship_appraisal_max_output_tokens,
            ),
            models=OpenAIModelFormationAdapter(
                client=OpenAIStructuredClient(supplied[Role.MODELS]),
                model=checked.model_formation_model,
                timeout_seconds=checked.conversation_timeout_seconds,
                max_output_tokens=checked.model_formation_max_output_tokens,
            ),
            positions=OpenAIPositionFormationAdapter(
                client=OpenAIStructuredClient(supplied[Role.POSITIONS]),
                model=checked.position_formation_model,
                timeout_seconds=checked.conversation_timeout_seconds,
                max_output_tokens=checked.position_formation_max_output_tokens,
            ),
            reflection=OpenAIReflectionAdapter(
                client=OpenAIStructuredClient(supplied[Role.REFLECTION]),
                model=checked.reflection_model,
                timeout_seconds=checked.reflection_timeout_seconds,
                max_output_tokens=checked.reflection_max_output_tokens,
            ),
        )
    except (ValueError, TypeError, AttributeError):
        raise OpenAIRuntimeConfigurationError() from None
