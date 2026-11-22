"""Synthetic actual-composition setup shared by read-only erasure tests and the CLI drill."""

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from satori.application.erasure.contracts import ErasurePreviewRequest
from satori.application.erasure.use_cases import ErasurePlanner
from satori.composition import ConversationServices
from satori.domain.memory_scope import MemoryScope
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure import SQLiteErasureInventory
from satori.infrastructure.persistence.models import (
    ConversationMessageRow,
    EpisodicMemoryRow,
    SemanticClaimRow,
)
from satori.infrastructure.source_policy.installation import enroll_installation
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime
from tests.test_recovery_whole_state import stored_rows
from tests.test_source_exclusion_production import (
    DERIVE_TEXT,
    ROOT_TEXT,
    _exclude,
    _services,
    _turn,
)
from tests.test_stage4_conversation_memory import activate


@dataclass(frozen=True, slots=True)
class ErasureCase:
    database: Database
    directory: Path
    runtime: SourcePolicyRuntime
    scope: MemoryScope
    message_id: str
    interaction_id: str
    derived_message_id: str
    derived_interaction_id: str
    episode_ids: tuple[str, ...]
    claim_ids: tuple[str, ...]
    services: tuple[ConversationServices, ...]
    initial_provider_calls: tuple[int, ...]

    @property
    def request(self) -> ErasurePreviewRequest:
        return ErasurePreviewRequest(
            message_id=self.message_id, counterparty_id=self.scope.counterparty_id
        )

    @property
    def path(self) -> Path:
        return Path(str(self.database.engine.url.database)).absolute()

    def planner(self) -> ErasurePlanner:
        return ErasurePlanner(self.runtime, SQLiteErasureInventory(self.database))


def provider_counts(services: tuple[ConversationServices, ...]) -> tuple[int, ...]:
    counts: list[int] = []
    for service in services:
        ports = (
            service.talk.provider,
            service.post_response.form_episode.provider,
            service.post_response.index_memory.provider
            if service.post_response.index_memory is not None
            else None,
            service.process_semantic.provider if service.process_semantic is not None else None,
        )
        for port in ports:
            requests = getattr(port, "requests", ())
            counts.append(len(requests))
    return tuple(counts)


def create_case(database: Database, directory: Path, *, excluded: bool = True) -> ErasureCase:
    identity = activate(database).identity.identity_id
    runtime = enroll_installation(database, directory, reviewed=True, new_installation=True)
    first, _, _ = _services(database, directory, "erasure-origin", 1)
    source = _turn(first, ROOT_TEXT, "erasure-origin")
    second, _, _ = _services(database, directory, "erasure-derived", 2)
    derived = _turn(second, DERIVE_TEXT, "erasure-derived")
    with database.session_factory() as session:
        message_id = session.scalar(
            select(ConversationMessageRow.message_id).where(
                ConversationMessageRow.interaction_id == source.interaction_id,
                ConversationMessageRow.role == "user",
            )
        )
        derived_message_id = session.scalar(
            select(ConversationMessageRow.message_id).where(
                ConversationMessageRow.interaction_id == derived.interaction_id,
                ConversationMessageRow.role == "user",
            )
        )
        episode_ids = tuple(session.scalars(select(EpisodicMemoryRow.memory_id)))
        claim_ids = tuple(session.scalars(select(SemanticClaimRow.claim_id)))
    assert message_id is not None
    assert derived_message_id is not None
    assert len(episode_ids) == 2
    assert len(claim_ids) == 2
    if excluded:
        _exclude(database, runtime, message_id)
    active = (first, second)
    return ErasureCase(
        database,
        directory,
        runtime,
        MemoryScope(identity, "alice"),
        message_id,
        source.interaction_id,
        derived_message_id,
        derived.interaction_id,
        episode_ids,
        claim_ids,
        active,
        provider_counts(active),
    )


def stored_state(case: ErasureCase) -> tuple[object, ...]:
    """Private in-memory assertion data; never serialize it into a scenario report."""
    return (
        stored_rows(case.path),
        stored_rows(case.runtime.ledger.database_path),
        case.runtime.ledger.binding_path.read_bytes(),
        case.runtime.ledger.lock_path.read_bytes(),
    )
