"""Real application composition + durable floor + adversarial derived text + restart.

All text is synthetic; fake cognitive ports make causal influence observable. The privacy
assertion is the actual provider input/owner commit barrier, independent of Character tuning.
"""

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from satori.application.conversation.contracts import SatoriReply, TalkInput
from satori.application.memory.lifecycle_contracts import (
    MemoryLifecycleCommand,
    MemoryLifecycleError,
)
from satori.application.source_policy.contracts import SourceExclusionCommand
from satori.application.source_policy.use_cases import SourcePolicyControls
from satori.composition import (
    ConversationServices,
    build_conversation_services,
    build_initial_self_services,
)
from satori.config import Environment, Settings
from satori.core.conversation import ConversationProviderRequest, ConversationProviderResponse
from satori.core.episode import (
    EpisodeEvidenceProposal,
    EpisodeFormationProposal,
    EpisodeFormationProviderResponse,
    EpisodeFormationRequest,
)
from satori.core.semantic import SemanticFormationProposal, SemanticFormationRequest
from satori.domain.memory import MemoryLifecycleStatus
from satori.domain.memory_scope import MemoryScope
from satori.domain.semantic_memory import SemanticClaimStatus
from satori.domain.source_policy import SourceExclusionReceipt, SourcePolicyError
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.models import (
    AffectiveStateRow,
    ConversationInteractionRow,
    ConversationMessageRow,
    EpisodicMemoryRow,
    SemanticClaimEvidenceRow,
    SemanticClaimRow,
    SourcePayloadRow,
)
from satori.infrastructure.source_policy.installation import (
    bind_installation,
    canonical_root,
    enroll_installation,
    repair_installation,
)
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime
from tests.fakes import (
    FakeAffectiveAppraisalProvider,
    FakeConversationProvider,
    FakeEmbeddingProvider,
    FakeEpisodeFormationProvider,
    FrozenClock,
)
from tests.test_memory_scope_integration import _neutral_appraisal
from tests.test_stage4_conversation_memory import (
    INTERACTION_TIME,
    activate,
    id_sequence,
    skip_episode_provider,
)
from tests.test_stage6_semantic_memory import one_claim, semantic_provider

SECRET = "SYNTHETIC-SEVER-731"
ROOT_TEXT = f"Мой закрытый код проекта — {SECRET}."
DERIVE_TEXT = "Помнишь код моего проекта? Сохрани общее описание."
RECALL = "Что ты помнишь о моём проекте?"  # noqa: RUF001


class KnowledgeProbe(FakeConversationProvider):
    """Echo synthetic fact iff it actually reaches cognition, not based on test-side state."""

    def __init__(self) -> None:
        super().__init__(response=ConversationProviderResponse("unused", "fake", "fixture", "stop"))

    async def generate(
        self, request: ConversationProviderRequest, /
    ) -> ConversationProviderResponse:
        self.requests.append(request)
        text = SECRET if SECRET in repr(request.messages) else "Нет доступных сведений о коде."  # noqa: RUF001
        return ConversationProviderResponse(text, "fake", "source-policy-scenario", "stop")


def _episode() -> FakeEpisodeFormationProvider:
    def response(request: EpisodeFormationRequest) -> EpisodeFormationProviderResponse:
        user = next(message for message in request.messages if message.role.value == "user")
        summary = user.content
        if user.content == DERIVE_TEXT:
            # Evidence cites only B. Actual paired assistant input inherited A.
            assert SECRET in repr(request.messages)
            summary = f"Описание проекта содержит код {SECRET}."
        return EpisodeFormationProviderResponse(
            EpisodeFormationProposal(
                1,
                True,
                summary,
                0.8,
                0.95,
                (EpisodeEvidenceProposal(user.message_id, user.content),),
            ),
            "fake",
            "source-policy-scenario",
            "synthetic.episode.v1",
        )

    return FakeEpisodeFormationProvider(response_factory=response)


def _services(
    database: Database,
    directory: Path,
    prefix: str,
    day: int,
    *,
    form: bool = True,
    foreground: FakeConversationProvider | None = None,
    counterparty: str = "alice",
) -> tuple[ConversationServices, FakeConversationProvider, FakeEmbeddingProvider]:
    provider = foreground or KnowledgeProbe()
    embedding = FakeEmbeddingProvider(
        {
            ROOT_TEXT: (1.0, 0.0, 0.0),
            DERIVE_TEXT: (1.0, 0.0, 0.0),
            RECALL: (1.0, 0.0, 0.0),
            f"Описание проекта содержит код {SECRET}.": (1.0, 0.0, 0.0),
        }
    )

    def claim(request: SemanticFormationRequest) -> SemanticFormationProposal:
        source = next(
            item for item in request.memories if item.memory_id == request.source_memory_id
        )
        return one_claim(
            request,
            predicate="works_on_project" if source.summary == ROOT_TEXT else "studies_topic",
            # B's accepted quote supports this value, but its actual summary input
            # contains A's secret. Accepted evidence alone cannot certify independence.
            value=SECRET if source.summary == ROOT_TEXT else "код",
            evidence_memory_ids=(source.memory_id,),
        )

    services = build_conversation_services(
        database,
        build_initial_self_services(database),
        provider,
        _episode() if form else skip_episode_provider(),
        Settings(
            environment=Environment.TEST,
            database_url=str(database.engine.url),
            source_policy_directory=directory,
            default_counterparty_id=counterparty,
        ),
        clock=FrozenClock(INTERACTION_TIME + timedelta(days=day)),
        id_generator=id_sequence(prefix),
        embedding_provider=embedding,
        semantic_provider=semantic_provider(claim) if form else None,
        appraisal_provider=FakeAffectiveAppraisalProvider(response_factory=_neutral_appraisal),
    )
    return services, provider, embedding


def _turn(
    services: ConversationServices, text: str, request_id: str, *, session_id: str | None = None
) -> SatoriReply:
    async def run() -> SatoriReply:
        reply = await services.talk.execute(
            TalkInput(text, f"trace-{request_id}", request_id, session_id=session_id)
        )
        if not reply.replayed:
            await services.post_response.execute(
                reply.interaction_id, trace_id=f"post-{request_id}"
            )
        return reply

    return asyncio.run(run())


def _exclude(
    database: Database, runtime: SourcePolicyRuntime, message_id: str
) -> SourceExclusionReceipt:
    root = canonical_root(database, runtime, message_id, counterparty_id="alice")
    controls = SourcePolicyControls(runtime)
    preview = controls.preview(root)
    command = SourceExclusionCommand(
        message_id=message_id,
        expected_generation=preview.generation,
        request_id=f"exclude-{message_id}",
        preview_digest=preview.preview_digest,
    )
    receipt = controls.apply(
        command,
        root,
        repair=lambda snapshot, item: repair_installation(database, runtime, snapshot, item),
    )
    assert receipt.phase.value == "complete"
    return receipt


def test_derived_knowledge_cannot_survive_exclusion_restart_archive_or_replay(
    migrated_database: Database,
    tmp_path: Path,
) -> None:
    database = migrated_database
    identity = activate(database).identity.identity_id
    scope = MemoryScope(identity, "alice")
    directory = tmp_path / "trusted-installation"
    runtime = enroll_installation(database, directory, reviewed=True, new_installation=True)
    first, _, _ = _services(database, directory, "origin", 1)
    source = _turn(first, ROOT_TEXT, "origin")
    second, _, _ = _services(database, directory, "derive", 2)
    derived = _turn(second, DERIVE_TEXT, "derive")
    with database.session_factory() as session:
        root = session.scalar(
            select(ConversationMessageRow.message_id).where(
                ConversationMessageRow.interaction_id == source.interaction_id,
                ConversationMessageRow.role == "user",
            )
        )
        episode = session.scalar(
            select(EpisodicMemoryRow.memory_id).where(
                EpisodicMemoryRow.source_interaction_id == source.interaction_id
            )
        )
        derived_episode = session.scalar(
            select(EpisodicMemoryRow.memory_id).where(
                EpisodicMemoryRow.source_interaction_id == derived.interaction_id
            )
        )
        assert root
        assert episode
        assert derived_episode
        claim = session.scalar(
            select(SemanticClaimRow).where(SemanticClaimRow.predicate == "studies_topic")
        )
        assert claim is not None
        assert claim.value == "код"
        # Its accepted semantic evidence does NOT directly cite the excluded root.
        assert root not in tuple(
            session.scalars(
                select(SemanticClaimEvidenceRow.root_message_id).where(
                    SemanticClaimEvidenceRow.claim_id == claim.claim_id
                )
            )
        )
        claim_id = claim.claim_id
    archived = first.memory_lifecycle.preview(episode, MemoryLifecycleStatus.ARCHIVED, scope=scope)
    first.memory_lifecycle.execute(
        MemoryLifecycleCommand(
            memory_id=episode,
            target_status=MemoryLifecycleStatus.ARCHIVED,
            expected_version=archived.current_version,
            request_id="archive-origin",
            preview_digest=archived.preview_digest,
        ),
        scope=scope,
        trace_id="archive-origin",
    )
    before, _, _ = _services(database, directory, "before", 3, form=False)
    assert _turn(before, RECALL, "before").text == SECRET
    with database.session_factory() as session:
        numerical = tuple(
            (row.identity_id, row.state_version, row.mood_version)
            for row in session.scalars(select(AffectiveStateRow))
        )
    _exclude(database, runtime, root)
    with database.session_factory() as session:
        retired = session.get(SemanticClaimRow, claim_id)
        held = session.get(SourcePayloadRow, ("episode", derived_episode, 1))
        assert retired is not None
        assert retired.status == SemanticClaimStatus.RETRACTED.value
        assert held is not None
        assert held.eligible is False
        assert numerical == tuple(
            (row.identity_id, row.state_version, row.mood_version)
            for row in session.scalars(select(AffectiveStateRow))
        )
        # Logical exclusion retains text physically; later erasure owns that operation.
        retained = session.get(ConversationMessageRow, root)
        assert retained is not None
        assert retained.content == ROOT_TEXT
    url = str(database.engine.url)
    database.dispose()
    restarted = create_database(url)
    try:
        bind_installation(restarted, directory)
        after, provider, embedding = _services(restarted, directory, "after", 4, form=False)
        answer = _turn(after, RECALL, "after")
        assert SECRET not in answer.text
        assert SECRET not in repr(provider.requests)
        assert SECRET not in repr(embedding.requests)
        assert not after.memories.execute()
        assert not after.semantic_claims.list(scope=scope)
        with pytest.raises(SourcePolicyError):
            _turn(after, ROOT_TEXT, "origin")
        with pytest.raises(MemoryLifecycleError):
            after.memory_lifecycle.preview(episode, MemoryLifecycleStatus.ACTIVE, scope=scope)
        # Fresh, independently grounded reintroduction can establish new knowledge.
        fresh, _, _ = _services(restarted, directory, "fresh", 5)
        _turn(fresh, ROOT_TEXT, "fresh")
        with restarted.session_factory() as session:
            new = session.scalar(
                select(SemanticClaimRow).where(
                    SemanticClaimRow.predicate == "works_on_project",
                    SemanticClaimRow.status == "active",
                )
            )
            assert new is not None
            assert new.claim_id != claim_id
            roots = tuple(
                session.scalars(
                    select(SemanticClaimEvidenceRow.root_message_id).where(
                        SemanticClaimEvidenceRow.claim_id == new.claim_id
                    )
                )
            )
            assert root not in roots
    finally:
        restarted.dispose()


def test_exclusion_during_generation_rejects_reply_and_tentative_affect(
    migrated_database: Database,
    tmp_path: Path,
) -> None:
    database = migrated_database
    activate(database)
    directory = tmp_path / "installation"
    runtime = enroll_installation(database, directory, reviewed=True, new_installation=True)
    ready = asyncio.Event()
    release = asyncio.Event()

    class Blocking(KnowledgeProbe):
        async def generate(
            self, request: ConversationProviderRequest, /
        ) -> ConversationProviderResponse:
            ready.set()
            await release.wait()
            return await super().generate(request)

    foreground = Blocking()
    services, _, _ = _services(
        database, directory, "inflight", 1, form=False, foreground=foreground
    )

    async def race() -> None:
        session = services.start_session.execute()
        task = asyncio.create_task(
            services.talk.execute(
                TalkInput(ROOT_TEXT, "race", "race", session_id=session.session_id)
            )
        )
        await ready.wait()
        with database.session_factory() as storage:
            root = storage.scalar(
                select(ConversationMessageRow.message_id).where(
                    ConversationMessageRow.role == "user"
                )
            )
            assert root
            before = tuple(storage.scalars(select(AffectiveStateRow.state_version)))
        _exclude(database, runtime, root)
        release.set()
        with pytest.raises(SourcePolicyError):
            await task
        with database.session_factory() as storage:
            assert not tuple(
                storage.scalars(
                    select(ConversationMessageRow.message_id).where(
                        ConversationMessageRow.role == "assistant"
                    )
                )
            )
            assert before == tuple(storage.scalars(select(AffectiveStateRow.state_version)))
            assert storage.scalar(select(ConversationInteractionRow.status)) == "failed"

    asyncio.run(race())
