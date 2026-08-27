"""Vendor-neutral capability ports bound to versioned contracts by owning stages."""

from typing import Protocol, TypeVar

RequestT = TypeVar("RequestT", contravariant=True)
ResponseT = TypeVar("ResponseT", covariant=True)


class ConversationGenerationPort(Protocol[RequestT, ResponseT]):
    """Generate a conversational response artifact."""

    async def generate(self, request: RequestT, /) -> ResponseT:
        """Generate one response for a typed request."""


class StructuredGenerationPort(Protocol[RequestT, ResponseT]):
    """Generate a schema-bound semantic artifact."""

    async def generate_structured(self, request: RequestT, /) -> ResponseT:
        """Generate one typed structured result."""


class EmbeddingPort(Protocol[RequestT, ResponseT]):
    """Create versioned vector representations through a provider adapter."""

    async def embed(self, request: RequestT, /) -> ResponseT:
        """Embed one typed request."""
