"""Explicit bounded adoption of a new OpenAI index through the existing retrieval owner."""

from collections.abc import Callable
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from satori.application.source_policy.dispatch import operation_dispatch_admission
from satori.domain.memory_scope import MemoryScope

if TYPE_CHECKING:
    from satori.composition import ConversationServices


class MemoryIndexAdoptionError(ValueError):
    def __init__(self, code: str = "memory_index_adoption_unavailable") -> None:
        self.code = code
        super().__init__(code)


class AdoptionDTO(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
    )


class MemoryIndexAdoptionRequest(AdoptionDTO):
    operation_id: UUID
    identity_id: UUID
    session_id: UUID
    confirmed: Literal[True]
    max_memories: int = Field(default=32, ge=1, le=100)

    @field_validator("operation_id", "identity_id", "session_id")
    @classmethod
    def nonzero(cls, value: UUID) -> UUID:
        if not value.int:
            raise ValueError("memory_index_adoption_scope_invalid")
        return value

    @field_validator("confirmed", mode="before")
    @classmethod
    def actual_confirmation(cls, value: object) -> Literal[True]:
        if value is not True:
            raise ValueError("memory_index_adoption_confirmation_required")
        return True


class MemoryIndexAdoptionReport(AdoptionDTO):
    operation_id: UUID
    status: Literal["indexed", "deferred", "failed"]
    embedding_space: str = Field(min_length=1, max_length=256)
    considered: int = Field(ge=0, le=100)
    indexed: int = Field(ge=0, le=100)
    failed: int = Field(ge=0, le=100)
    more_available: bool

    @model_validator(mode="after")
    def consistent(self) -> "MemoryIndexAdoptionReport":
        if self.indexed + self.failed > self.considered:
            raise ValueError("memory_index_adoption_counts_invalid")
        expected = "failed" if self.failed else "deferred" if self.more_available else "indexed"
        if self.status != expected:
            raise ValueError("memory_index_adoption_status_invalid")
        return self


async def adopt_openai_memory_index(
    core: "ConversationServices",
    request: MemoryIndexAdoptionRequest,
    *,
    attempted_operations: set[UUID],
    require_live: Callable[[], None],
) -> MemoryIndexAdoptionReport:
    """Consume one current owner-confirmed operation, never relabel existing vectors.

    The existing backend holds the small RAM replay ledger for its lifetime. A new
    explicit operation can continue deferred work through the same idempotent owner;
    no missing index is silently treated as recalled long-term memory.
    """
    request = MemoryIndexAdoptionRequest.model_validate(request)
    if request.operation_id in attempted_operations or len(attempted_operations) >= 128:
        raise MemoryIndexAdoptionError("memory_index_adoption_replay_rejected")
    attempted_operations.add(request.operation_id)

    def guard() -> MemoryScope:
        require_live()
        identity = core.talk.get_self.execute().identity.identity_id
        session = core.talk.interaction_log.get_session(str(request.session_id))
        if (
            identity != str(request.identity_id)
            or session is None
            or session.identity_id != identity
            or session.counterparty_id != core.talk.interaction_log.default_counterparty_id
            or session.status.value != "open"
            or not session.scope_verified
        ):
            raise MemoryIndexAdoptionError("memory_index_adoption_scope_invalid")
        return MemoryScope(identity, session.counterparty_id)

    scope = guard()
    owner = core.index_memories
    if owner is None or owner.provider.space.provider != "openai":
        raise MemoryIndexAdoptionError("memory_index_adoption_provider_invalid")

    def require_scope() -> None:
        if guard() != scope:
            raise MemoryIndexAdoptionError("memory_index_adoption_scope_invalid")

    with operation_dispatch_admission(require_scope):
        result = await owner.execute(
            scope=scope,
            trace_id=str(request.operation_id),
            rebuild=False,
            max_memories=request.max_memories,
            require_live=require_scope,
        )
    require_scope()
    if result.space != owner.provider.space:
        raise MemoryIndexAdoptionError("memory_index_adoption_space_invalid")
    return MemoryIndexAdoptionReport(
        operation_id=request.operation_id,
        status="failed" if result.failed else "deferred" if result.more_available else "indexed",
        embedding_space=owner.provider.space.key,
        considered=result.considered,
        indexed=result.indexed,
        failed=result.failed,
        more_available=result.more_available,
    )
