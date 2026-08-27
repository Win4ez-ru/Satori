"""Deterministic standard-library fakes for injected core ports."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from satori.core.affect import (
    AffectiveAppraisalProviderResponse,
    AffectiveAppraisalRequest,
)
from satori.core.conversation import (
    ConversationProviderRequest,
    ConversationProviderResponse,
)
from satori.core.embedding import EmbeddingRequest, EmbeddingResponse, EmbeddingSpace
from satori.core.episode import (
    EpisodeFormationProviderResponse,
    EpisodeFormationRequest,
)
from satori.core.models import ModelFormationProviderResponse, ModelFormationRequest
from satori.core.relationship import (
    RelationshipAppraisalRequest,
    RelationshipAppraisalResponse,
)
from satori.core.semantic import (
    SemanticFormationProviderResponse,
    SemanticFormationRequest,
)


@dataclass(frozen=True, slots=True)
class FrozenClock:
    """Always return one test-controlled aware instant."""

    instant: datetime

    def now(self) -> datetime:
        """Return the configured instant."""

        return self.instant


class SequenceIdGenerator:
    """Return a finite sequence of deterministic identifiers."""

    def __init__(self, *identifiers: str) -> None:
        self._identifiers = iter(identifiers)

    def new(self) -> str:
        """Return the next configured identifier."""

        return next(self._identifiers)


class FakeConversationProvider:
    """Capture typed requests and return or raise one controlled outcome."""

    def __init__(
        self,
        *,
        response: ConversationProviderResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        if response is None and error is None:
            raise ValueError("fake provider requires a response or error")
        if response is not None and error is not None:
            raise ValueError("fake provider accepts either a response or error")
        self.response = response
        self.error = error
        self.requests: list[ConversationProviderRequest] = []

    async def generate(
        self,
        request: ConversationProviderRequest,
        /,
    ) -> ConversationProviderResponse:
        """Capture one request before returning the configured result."""

        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if self.response is None:
            raise AssertionError("fake provider has no response")
        return self.response


class FakeAffectiveAppraisalProvider:
    """Capture affect requests and return or raise one controlled outcome."""

    def __init__(
        self,
        *,
        response: AffectiveAppraisalProviderResponse | None = None,
        error: Exception | None = None,
        response_factory: (
            Callable[[AffectiveAppraisalRequest], AffectiveAppraisalProviderResponse] | None
        ) = None,
    ) -> None:
        configured = sum(value is not None for value in (response, error, response_factory))
        if configured != 1:
            raise ValueError("fake appraisal provider requires exactly one outcome")
        self.response = response
        self.error = error
        self.response_factory = response_factory
        self.requests: list[AffectiveAppraisalRequest] = []

    async def generate_structured(
        self,
        request: AffectiveAppraisalRequest,
        /,
    ) -> AffectiveAppraisalProviderResponse:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if self.response_factory is not None:
            return self.response_factory(request)
        if self.response is None:
            raise AssertionError("fake appraisal provider has no response")
        return self.response


class FakeRelationshipAppraisalProvider:
    """Capture post-commit relationship requests and return one controlled outcome."""

    def __init__(
        self,
        *,
        response: RelationshipAppraisalResponse | None = None,
        error: Exception | None = None,
        response_factory: (
            Callable[[RelationshipAppraisalRequest], RelationshipAppraisalResponse] | None
        ) = None,
    ) -> None:
        configured = sum(value is not None for value in (response, error, response_factory))
        if configured != 1:
            raise ValueError("fake relationship provider requires exactly one outcome")
        self.response = response
        self.error = error
        self.response_factory = response_factory
        self.requests: list[RelationshipAppraisalRequest] = []

    async def generate_structured(
        self,
        request: RelationshipAppraisalRequest,
        /,
    ) -> RelationshipAppraisalResponse:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if self.response_factory is not None:
            return self.response_factory(request)
        if self.response is None:
            raise AssertionError("fake relationship provider has no response")
        return self.response


class FakeEpisodeFormationProvider:
    """Capture structured formation requests and return one controlled outcome."""

    def __init__(
        self,
        *,
        response: EpisodeFormationProviderResponse | None = None,
        error: Exception | None = None,
        response_factory: (
            Callable[[EpisodeFormationRequest], EpisodeFormationProviderResponse] | None
        ) = None,
    ) -> None:
        configured = sum(value is not None for value in (response, error, response_factory))
        if configured == 0:
            raise ValueError("fake episode provider requires a response or error")
        if configured > 1:
            raise ValueError("fake episode provider accepts exactly one outcome")
        self.response = response
        self.error = error
        self.response_factory = response_factory
        self.requests: list[EpisodeFormationRequest] = []

    async def generate_structured(
        self,
        request: EpisodeFormationRequest,
        /,
    ) -> EpisodeFormationProviderResponse:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if self.response_factory is not None:
            return self.response_factory(request)
        if self.response is None:
            raise AssertionError("fake episode provider has no response")
        return self.response


class FakeEmbeddingProvider:
    """Deterministic text-to-vector fixture with an explicit compatibility space."""

    def __init__(
        self,
        vectors: dict[str, tuple[float, ...]],
        *,
        space: EmbeddingSpace | None = None,
        error: Exception | None = None,
    ) -> None:
        self._space = space or EmbeddingSpace("fake-embedding", "fixture-v1", 3, 1)
        self.vectors = dict(vectors)
        self.error = error
        self.requests: list[EmbeddingRequest] = []

    @property
    def space(self) -> EmbeddingSpace:
        return self._space

    async def embed(self, request: EmbeddingRequest, /) -> EmbeddingResponse:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return EmbeddingResponse(
            space=self.space,
            vectors=tuple(self.vectors[text] for text in request.texts),
        )


class FakeSemanticFormationProvider:
    """Capture semantic requests and return one deterministic proposal outcome."""

    def __init__(
        self,
        *,
        response: SemanticFormationProviderResponse | None = None,
        error: Exception | None = None,
        response_factory: (
            Callable[[SemanticFormationRequest], SemanticFormationProviderResponse] | None
        ) = None,
    ) -> None:
        configured = sum(value is not None for value in (response, error, response_factory))
        if configured != 1:
            raise ValueError("fake semantic provider requires exactly one outcome")
        self.response = response
        self.error = error
        self.response_factory = response_factory
        self.requests: list[SemanticFormationRequest] = []

    async def generate_structured(
        self, request: SemanticFormationRequest, /
    ) -> SemanticFormationProviderResponse:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if self.response_factory is not None:
            return self.response_factory(request)
        if self.response is None:
            raise AssertionError("fake semantic provider has no response")
        return self.response


class FakeModelFormationProvider:
    """Capture Stage 9 requests and return one deterministic proposal outcome."""

    def __init__(
        self,
        *,
        response: ModelFormationProviderResponse | None = None,
        error: Exception | None = None,
        response_factory: (
            Callable[[ModelFormationRequest], ModelFormationProviderResponse] | None
        ) = None,
    ) -> None:
        configured = sum(value is not None for value in (response, error, response_factory))
        if configured != 1:
            raise ValueError("fake model-formation provider requires exactly one outcome")
        self.response = response
        self.error = error
        self.response_factory = response_factory
        self.requests: list[ModelFormationRequest] = []

    async def generate_structured(
        self, request: ModelFormationRequest, /
    ) -> ModelFormationProviderResponse:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if self.response_factory is not None:
            return self.response_factory(request)
        if self.response is None:
            raise AssertionError("fake model-formation provider has no response")
        return self.response
