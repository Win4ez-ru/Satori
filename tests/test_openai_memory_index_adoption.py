"""Explicit new-space indexing with public Core data and an in-memory embeddings wire."""

import asyncio
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import event, text

from satori.application.conversation.contracts import TalkInput
from satori.application.retrieval.adoption import (
    MemoryIndexAdoptionError,
    MemoryIndexAdoptionRequest,
    adopt_openai_memory_index,
)
from satori.application.source_policy.dispatch import admit_provider_dispatch
from satori.core.embedding import EmbeddingSpace
from satori.domain.memory_scope import MemoryScope
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.providers.openai_embedding import OpenAIEmbeddingAdapter
from tests import calendar_planning_fixture as fixture
from tests.test_stage5_retrieval import episode_provider_with_summary


class PublicEmbeddingTransport:
    def __init__(self) -> None:
        self.calls: list[Mapping[str, Any]] = []
        self.before_return: Callable[[], None] | None = None
        self.fail = False

    def post_json(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        del timeout_seconds, max_response_bytes
        assert path == "/embeddings"
        admit_provider_dispatch()
        self.calls.append(payload)
        if self.fail:
            raise ValueError("PUBLIC fixture unavailable")
        if self.before_return is not None:
            self.before_return()
        return json.dumps(
            {
                "object": "list",
                "model": "text-embedding-3-small",
                "data": [
                    {"object": "embedding", "index": index, "embedding": [0.2, -0.4, 0.8]}
                    for index, _ in enumerate(payload["input"])
                ],
                "usage": {"prompt_tokens": 8, "total_tokens": 8},
            }
        ).encode()


def vectors(database: Database, provider: str) -> tuple[tuple[Any, ...], ...]:
    with database.engine.connect() as connection:
        return tuple(
            tuple(row)
            for row in connection.execute(
                text(
                    "SELECT embedding_id,memory_id,provider,model,dimensions,"
                    "input_schema_version,vector "
                    "FROM episodic_memory_embeddings WHERE provider=:provider ORDER BY embedding_id"
                ),
                {"provider": provider},
            )
        )


async def setup(database: Database, path: Path) -> tuple[Any, Any, Any, PublicEmbeddingTransport]:
    database, core, _, _clock, scope = await fixture.seeded_core(database, path)
    core.post_response.form_episode.provider = episode_provider_with_summary(fixture.SUMMARY)
    reply = await core.talk.execute(
        TalkInput(fixture.RECENT, "PUBLIC-extra", "PUBLIC-extra-request", str(scope.session_id))
    )
    assert (
        await core.post_response.execute(reply.interaction_id, trace_id="PUBLIC-extra-post")
    ).succeeded
    transport = PublicEmbeddingTransport()
    assert core.index_memories is not None
    core.index_memories.provider = OpenAIEmbeddingAdapter(
        model="text-embedding-3-small",
        dimensions=3,
        input_schema_version=1,
        timeout_seconds=3,
        http_client=transport,
    )
    return database, core, scope, transport


def request(scope: Any, limit: int = 1) -> MemoryIndexAdoptionRequest:
    return MemoryIndexAdoptionRequest(
        operation_id=uuid4(),
        identity_id=scope.identity_id,
        session_id=scope.session_id,
        confirmed=True,
        max_memories=limit,
    )


def test_bounded_adoption_preserves_old_space_and_canonical_state_then_continues_explicitly(
    migrated_database: Database,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database, core, scope, transport = await setup(migrated_database, tmp_path)
        old = vectors(database, "fake-embedding")
        memory_scope = MemoryScope(str(scope.identity_id), "local-default")
        canonical = (
            core.history.execute(),
            core.memories.execute(scope=memory_scope),
            core.talk.get_self.execute(),
        )
        selected_limits: list[tuple[Any, ...]] = []

        def capture(
            _conn: Any, _cursor: Any, statement: str, parameters: Any, _context: Any, _many: Any
        ) -> None:
            if "FROM episodic_memories" in statement and "LIMIT" in statement:
                selected_limits.append(tuple(parameters)[-2:])

        event.listen(database.engine, "before_cursor_execute", capture)
        attempted: set[Any] = set()
        first = await adopt_openai_memory_index(
            core, request(scope), attempted_operations=attempted, require_live=lambda: None
        )
        assert first.status == "deferred"
        assert first.considered == first.indexed == 1
        assert first.more_available
        assert (2, 0) in selected_limits
        assert len(transport.calls) == 1
        assert len(transport.calls[0]["input"]) == 1
        assert first.embedding_space == "openai/text-embedding-3-small/d3/input-v1"
        assert vectors(database, "fake-embedding") == old
        second = await adopt_openai_memory_index(
            core, request(scope), attempted_operations=attempted, require_live=lambda: None
        )
        assert second.status == "indexed"
        assert second.considered == second.indexed == 1
        third = await adopt_openai_memory_index(
            core, request(scope), attempted_operations=attempted, require_live=lambda: None
        )
        assert third.considered == 0
        assert len(transport.calls) == 2
        assert len(vectors(database, "openai")) == 2
        assert vectors(database, "fake-embedding") == old
        assert canonical == (
            core.history.execute(),
            core.memories.execute(scope=memory_scope),
            core.talk.get_self.execute(),
        )
        database.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["identity", "session", "closed", "provider", "revoked"])
def test_wrong_scope_or_provider_never_dispatches_embedding(
    migrated_database: Database,
    tmp_path: Path,
    change: str,
) -> None:
    async def scenario() -> None:
        database, core, scope, transport = await setup(migrated_database, tmp_path)
        value = request(scope)
        if change == "identity":
            value = value.model_copy(update={"identity_id": uuid4()})
        if change == "session":
            value = value.model_copy(update={"session_id": uuid4()})
        if change == "closed":
            core.close_session.execute(str(scope.session_id))
        if change == "provider":
            assert core.index_memories is not None
            # A different input-space provider is never implicitly adopted.
            from tests.fakes import FakeEmbeddingProvider

            core.index_memories.provider = FakeEmbeddingProvider(
                {}, space=EmbeddingSpace("ollama", "PUBLIC", 3, 1)
            )

        def guard() -> None:
            if change == "revoked":
                raise ValueError("PUBLIC revoked")

        with pytest.raises(ValueError, match=r"scope_invalid|provider_invalid|PUBLIC revoked"):
            await adopt_openai_memory_index(
                core, value, attempted_operations=set(), require_live=guard
            )
        assert transport.calls == []
        assert vectors(database, "openai") == ()
        database.dispose()

    asyncio.run(scenario())


def test_replay_is_rejected_and_transport_failure_has_no_auto_retry(
    migrated_database: Database,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database, core, scope, transport = await setup(migrated_database, tmp_path)
        transport.fail = True
        value = request(scope)
        attempted: set[UUID] = set()
        result = await adopt_openai_memory_index(
            core, value, attempted_operations=attempted, require_live=lambda: None
        )
        assert result.status == "failed"
        assert result.failed == 1
        with pytest.raises(MemoryIndexAdoptionError, match="replay_rejected"):
            await adopt_openai_memory_index(
                core, value, attempted_operations=attempted, require_live=lambda: None
            )
        assert len(transport.calls) == 1
        assert vectors(database, "openai") == ()
        database.dispose()

    asyncio.run(scenario())


def test_revocation_during_http_prevents_persisting_new_vectors(
    migrated_database: Database,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database, core, scope, transport = await setup(migrated_database, tmp_path)
        active = [True]

        def revoke() -> None:
            active[0] = False

        transport.before_return = revoke

        def guard() -> None:
            if not active[0]:
                raise ValueError("PUBLIC revoked")

        with pytest.raises(ValueError, match="PUBLIC revoked"):
            await adopt_openai_memory_index(
                core, request(scope, 2), attempted_operations=set(), require_live=guard
            )
        assert len(transport.calls) == 1
        assert vectors(database, "openai") == ()
        database.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "changes",
    [
        {"confirmed": False},
        {"confirmed": 1},
        {"max_memories": 0},
        {"max_memories": 101},
        {"max_memories": True},
        {"max_memories": 1.0},
    ],
)
def test_adoption_requires_actual_confirmation_and_a_strict_finite_cap(
    changes: dict[str, Any],
) -> None:
    fields = {
        "operation_id": uuid4(),
        "identity_id": uuid4(),
        "session_id": uuid4(),
        "confirmed": True,
        "max_memories": 32,
    } | changes
    with pytest.raises(ValidationError):
        MemoryIndexAdoptionRequest.model_validate(fields)
