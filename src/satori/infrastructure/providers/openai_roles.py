"""Typed OpenAI structured-role ports sharing the existing policies and validators.

Transport has no persistence authority. Parsed proposals still pass the original
application owner, evidence eligibility and deterministic domain bounds.
"""

import asyncio
import math
from dataclasses import dataclass

from satori.application.source_policy.dispatch import admit_provider_dispatch
from satori.core.affect import (
    AffectiveAppraisalProviderError,
    AffectiveAppraisalProviderResponse,
    AffectiveAppraisalRequest,
)
from satori.core.episode import (
    EpisodeFormationProviderError,
    EpisodeFormationProviderResponse,
    EpisodeFormationRequest,
)
from satori.core.models import (
    ModelFormationProviderError,
    ModelFormationProviderResponse,
    ModelFormationRequest,
)
from satori.core.positions import (
    PositionFormationProviderError,
    PositionFormationProviderResponse,
    PositionFormationRequest,
)
from satori.core.reflection import (
    ReflectionGenerationRequest,
    ReflectionProviderError,
    ReflectionProviderResponse,
)
from satori.core.relationship import (
    RelationshipAppraisalProviderError,
    RelationshipAppraisalRequest,
    RelationshipAppraisalResponse,
)
from satori.core.semantic import (
    SemanticFormationProviderError,
    SemanticFormationProviderResponse,
    SemanticFormationRequest,
)
from satori.infrastructure.providers.inference_scheduler import (
    InferencePriority,
    OllamaInferenceScheduler,
)
from satori.infrastructure.providers.ollama_affect import (
    APPRAISAL_METHOD,
    APPRAISAL_PREVIOUS_EXCHANGE_METHOD,
    affect_input,
    parse_affect_document,
)
from satori.infrastructure.providers.ollama_episode import (
    FORMATION_METHOD as EPISODE_METHOD,
)
from satori.infrastructure.providers.ollama_episode import (
    episode_messages,
    episode_schema,
    parse_episode_document,
)
from satori.infrastructure.providers.ollama_models import (
    FORMATION_METHOD as MODELS_METHOD,
)
from satori.infrastructure.providers.ollama_models import (
    models_messages,
    models_schema,
    parse_models_document,
)
from satori.infrastructure.providers.ollama_positions import (
    FORMATION_METHOD as POSITIONS_METHOD,
)
from satori.infrastructure.providers.ollama_positions import (
    parse_positions_document,
    positions_messages,
    positions_schema,
)
from satori.infrastructure.providers.ollama_reflection import (
    parse_reflection_document,
    reflection_document_model,
    reflection_messages,
    reflection_schema,
)
from satori.infrastructure.providers.ollama_relationship import (
    RELATIONSHIP_APPRAISAL_METHOD,
    parse_relationship_document,
    relationship_input,
)
from satori.infrastructure.providers.ollama_semantic import (
    FORMATION_METHOD as SEMANTIC_METHOD,
)
from satori.infrastructure.providers.ollama_semantic import (
    parse_semantic_document,
    semantic_messages,
    semantic_schema,
)
from satori.infrastructure.providers.openai_structured import (
    OpenAIStructuredClient,
    OpenAIStructuredError,
    StructuredResult,
)

OPENAI_PROVIDER_NAME = "openai"


def _method(legacy: str) -> str:
    if not legacy.startswith("ollama."):
        raise ValueError("unknown role provenance version")
    return "openai." + legacy.removeprefix("ollama.")


@dataclass(frozen=True, slots=True)
class _OpenAIRole:
    client: OpenAIStructuredClient
    model: str
    timeout_seconds: float
    max_output_tokens: int
    scheduler: OllamaInferenceScheduler | None = None

    def __post_init__(self) -> None:
        if not self.model.strip() or self.model != self.model.strip():
            raise ValueError("OpenAI role model must be nonblank and normalized")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("OpenAI role timeout must be finite and positive")
        if type(self.max_output_tokens) is not int or self.max_output_tokens < 1:
            raise ValueError("OpenAI role output limit must be positive")

    def _request(
        self, messages: list[dict[str, str]], schema: dict[str, object], schema_name: str
    ) -> StructuredResult:
        admit_provider_dispatch()
        return self.client.generate(
            model=self.model,
            messages=messages,
            schema=schema,
            schema_name=schema_name,
            max_output_tokens=self.max_output_tokens,
            timeout_seconds=self.timeout_seconds,
        )


class OpenAIEpisodeFormationAdapter(_OpenAIRole):
    async def generate_structured(
        self, request: EpisodeFormationRequest, /
    ) -> EpisodeFormationProviderResponse:
        if self.scheduler is None:
            return await asyncio.to_thread(self._generate_sync, request)
        async with self.scheduler.reserve(InferencePriority.EPISODE):
            return await asyncio.to_thread(self._generate_sync, request)

    def _generate_sync(self, request: EpisodeFormationRequest) -> EpisodeFormationProviderResponse:
        try:
            result = self._request(episode_messages(request), episode_schema(), "satori_episode_v1")
            return EpisodeFormationProviderResponse(
                proposal=parse_episode_document(result.content),
                provider=OPENAI_PROVIDER_NAME,
                model=result.model,
                formation_method=_method(EPISODE_METHOD),
                metrics=result.metrics,
            )
        except OpenAIStructuredError as error:
            raise EpisodeFormationProviderError(
                OPENAI_PROVIDER_NAME, self.model, error.code
            ) from None
        except (ValueError, KeyError, UnicodeError, TypeError):
            raise EpisodeFormationProviderError(
                OPENAI_PROVIDER_NAME, self.model, "invalid_episode_document"
            ) from None


class OpenAISemanticFormationAdapter(_OpenAIRole):
    async def generate_structured(
        self, request: SemanticFormationRequest, /
    ) -> SemanticFormationProviderResponse:
        if self.scheduler is None:
            return await asyncio.to_thread(self._generate_sync, request)
        async with self.scheduler.reserve(InferencePriority.SEMANTIC):
            return await asyncio.to_thread(self._generate_sync, request)

    def _generate_sync(
        self, request: SemanticFormationRequest
    ) -> SemanticFormationProviderResponse:
        try:
            result = self._request(
                semantic_messages(request), semantic_schema(), "satori_semantic_v1"
            )
            return SemanticFormationProviderResponse(
                proposal=parse_semantic_document(result.content),
                provider=OPENAI_PROVIDER_NAME,
                model=result.model,
                formation_method=_method(SEMANTIC_METHOD),
                metrics=result.metrics,
            )
        except OpenAIStructuredError as error:
            raise SemanticFormationProviderError(
                OPENAI_PROVIDER_NAME, self.model, error.code
            ) from None
        except (ValueError, KeyError, UnicodeError, TypeError):
            raise SemanticFormationProviderError(
                OPENAI_PROVIDER_NAME, self.model, "invalid_semantic_document"
            ) from None


class OpenAIModelFormationAdapter(_OpenAIRole):
    async def generate_structured(
        self, request: ModelFormationRequest, /
    ) -> ModelFormationProviderResponse:
        if self.scheduler is None:
            return await asyncio.to_thread(self._generate_sync, request)
        async with self.scheduler.reserve(InferencePriority.SEMANTIC):
            return await asyncio.to_thread(self._generate_sync, request)

    def _generate_sync(self, request: ModelFormationRequest) -> ModelFormationProviderResponse:
        try:
            result = self._request(models_messages(request), models_schema(), "satori_models_v1")
            return ModelFormationProviderResponse(
                proposal=parse_models_document(result.content),
                provider=OPENAI_PROVIDER_NAME,
                model=result.model,
                formation_method=_method(MODELS_METHOD),
                metrics=result.metrics,
            )
        except OpenAIStructuredError as error:
            raise ModelFormationProviderError(
                OPENAI_PROVIDER_NAME, self.model, error.code
            ) from None
        except (ValueError, KeyError, UnicodeError, TypeError):
            raise ModelFormationProviderError(
                OPENAI_PROVIDER_NAME, self.model, "invalid_models_document"
            ) from None


class OpenAIPositionFormationAdapter(_OpenAIRole):
    async def generate_structured(
        self, request: PositionFormationRequest, /
    ) -> PositionFormationProviderResponse:
        if self.scheduler is None:
            return await asyncio.to_thread(self._generate_sync, request)
        async with self.scheduler.reserve(InferencePriority.SEMANTIC):
            return await asyncio.to_thread(self._generate_sync, request)

    def _generate_sync(
        self, request: PositionFormationRequest
    ) -> PositionFormationProviderResponse:
        try:
            result = self._request(
                positions_messages(request), positions_schema(), "satori_positions_v1"
            )
            return PositionFormationProviderResponse(
                proposal=parse_positions_document(result.content),
                provider=OPENAI_PROVIDER_NAME,
                model=result.model,
                formation_method=_method(POSITIONS_METHOD),
                metrics=result.metrics,
            )
        except OpenAIStructuredError as error:
            raise PositionFormationProviderError(
                OPENAI_PROVIDER_NAME, self.model, error.code
            ) from None
        except (ValueError, KeyError, UnicodeError, TypeError):
            raise PositionFormationProviderError(
                OPENAI_PROVIDER_NAME, self.model, "invalid_positions_document"
            ) from None


class OpenAIAffectiveAppraisalAdapter(_OpenAIRole):
    async def generate_structured(
        self, request: AffectiveAppraisalRequest, /
    ) -> AffectiveAppraisalProviderResponse:
        if self.scheduler is None:
            return await asyncio.to_thread(self._generate_sync, request)
        async with self.scheduler.reserve(InferencePriority.APPRAISAL):
            return await asyncio.to_thread(self._generate_sync, request)

    def _generate_sync(
        self, request: AffectiveAppraisalRequest
    ) -> AffectiveAppraisalProviderResponse:
        try:
            messages, schema, ref_map = affect_input(request)
            result = self._request(messages, schema, "satori_affect_v2")
            return AffectiveAppraisalProviderResponse(
                proposal=parse_affect_document(result.content, ref_map),
                provider=OPENAI_PROVIDER_NAME,
                model=result.model,
                appraisal_method=_method(
                    APPRAISAL_PREVIOUS_EXCHANGE_METHOD
                    if request.previous_exchange is not None
                    else APPRAISAL_METHOD
                ),
                metrics=result.metrics,
            )
        except OpenAIStructuredError as error:
            raise AffectiveAppraisalProviderError(
                OPENAI_PROVIDER_NAME, self.model, error.code
            ) from None
        except (ValueError, KeyError, UnicodeError, TypeError):
            raise AffectiveAppraisalProviderError(
                OPENAI_PROVIDER_NAME, self.model, "invalid_affect_document"
            ) from None


class OpenAIRelationshipAppraisalAdapter(_OpenAIRole):
    async def generate_structured(
        self, request: RelationshipAppraisalRequest, /
    ) -> RelationshipAppraisalResponse:
        if self.scheduler is None:
            return await asyncio.to_thread(self._generate_sync, request)
        async with self.scheduler.reserve(InferencePriority.RELATIONSHIP):
            return await asyncio.to_thread(self._generate_sync, request)

    def _generate_sync(
        self, request: RelationshipAppraisalRequest
    ) -> RelationshipAppraisalResponse:
        try:
            messages, schema = relationship_input(request)
            result = self._request(messages, schema, "satori_relationship_v1")
            return RelationshipAppraisalResponse(
                proposal=parse_relationship_document(result.content, request),
                provider=OPENAI_PROVIDER_NAME,
                model=result.model,
                appraisal_method=_method(RELATIONSHIP_APPRAISAL_METHOD),
                metrics=result.metrics,
            )
        except OpenAIStructuredError as error:
            raise RelationshipAppraisalProviderError(
                OPENAI_PROVIDER_NAME, self.model, error.code
            ) from None
        except (ValueError, KeyError, UnicodeError, TypeError):
            raise RelationshipAppraisalProviderError(
                OPENAI_PROVIDER_NAME, self.model, "invalid_relationship_document"
            ) from None


class OpenAIReflectionAdapter(_OpenAIRole):
    def __post_init__(self) -> None:
        super().__post_init__()
        if self.max_output_tokens > 768:
            raise ValueError("Reflection output limit is outside the existing policy")

    async def generate_structured(
        self, request: ReflectionGenerationRequest, /
    ) -> ReflectionProviderResponse:
        if self.scheduler is None:
            return await asyncio.to_thread(self._generate_sync, request)
        async with self.scheduler.reserve(InferencePriority.SEMANTIC):
            return await asyncio.to_thread(self._generate_sync, request)

    def _generate_sync(self, request: ReflectionGenerationRequest) -> ReflectionProviderResponse:
        try:
            result = self._request(
                reflection_messages(request),
                reflection_schema(request),
                f"satori_reflection_v{request.schema_version}",
            )
            _, method = reflection_document_model(request)
            return ReflectionProviderResponse(
                document=parse_reflection_document(result.content, request),
                provider=OPENAI_PROVIDER_NAME,
                model=result.model,
                formation_method=_method(method),
                metrics=result.metrics,
            )
        except OpenAIStructuredError as error:
            raise ReflectionProviderError(OPENAI_PROVIDER_NAME, self.model, error.code) from None
        except (ValueError, KeyError, UnicodeError, TypeError):
            raise ReflectionProviderError(
                OPENAI_PROVIDER_NAME, self.model, "invalid_reflection_document"
            ) from None
