"""Current selected owner heads must survive inference; immutable parents may advance."""

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select

from satori.application.conversation.contracts import TalkInput
from satori.application.memory.lifecycle_contracts import MemoryLifecycleCommand
from satori.core.conversation import ConversationProviderRequest, ConversationProviderResponse
from satori.domain.memory import MemoryLifecycleStatus
from satori.domain.memory_scope import MemoryScope
from satori.domain.source_policy import PayloadRef, SourcePolicyError
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.models import (
    AffectiveStateRow,
    ConversationInteractionRow,
    ConversationMessageRow,
    EpisodicMemoryRow,
    SemanticClaimRow,
    SourceInputRow,
    SourcePayloadRow,
    UserModelClaimRow,
)
from satori.infrastructure.persistence.source_policy import (
    SessionSourceAdmission,
    materialize_mirror,
    record_payload,
    require_source_inputs,
)
from satori.infrastructure.source_policy.installation import enroll_installation
from tests.test_source_exclusion_production import (
    RECALL,
    ROOT_TEXT,
    KnowledgeProbe,
    _services,
    _turn,
)
from tests.test_source_policy_memory import _receipt, _snapshot
from tests.test_source_policy_owners import _form_models
from tests.test_stage4_conversation_memory import activate


@pytest.mark.parametrize("change", ["archive", "archive_reactivate", "semantic_merge"])
def test_current_payload_changed_while_foreground_awaits_rejects_commit(
    migrated_database: Database,
    tmp_path: Path,
    change: str,
) -> None:
    database = migrated_database
    identity = activate(database).identity.identity_id
    directory = tmp_path / "current-owner-installation"
    enroll_installation(database, directory, reviewed=True, new_installation=True)
    origin, _, _ = _services(database, directory, "current-origin", 1)
    saved = _turn(origin, ROOT_TEXT, "current-origin")
    with database.session_factory() as session:
        episode = session.scalar(
            select(EpisodicMemoryRow).where(
                EpisodicMemoryRow.source_interaction_id == saved.interaction_id
            )
        )
        claim = session.scalar(select(SemanticClaimRow))
        assert episode is not None
        assert claim is not None
        episode_id, claim_id = episode.memory_id, claim.claim_id
        expected = PayloadRef(
            "semantic_claim" if change == "semantic_merge" else "episode",
            claim_id if change == "semantic_merge" else episode_id,
            claim.aggregate_version if change == "semantic_merge" else episode.aggregate_version,
        )

    async def race() -> None:
        ready, release = asyncio.Event(), asyncio.Event()

        class Blocking(KnowledgeProbe):
            async def generate(
                self, request: ConversationProviderRequest, /
            ) -> ConversationProviderResponse:
                ready.set()
                await release.wait()
                return await super().generate(request)

        foreground = Blocking()
        services, _, _ = _services(
            database, directory, "current-await", 2, form=False, foreground=foreground
        )
        task = asyncio.create_task(
            services.talk.execute(TalkInput(RECALL, "current-await", "current-await"))
        )
        await asyncio.wait_for(ready.wait(), timeout=10)
        try:
            with database.session_factory() as session:
                pending = session.scalar(
                    select(ConversationInteractionRow).where(
                        ConversationInteractionRow.client_request_id == "current-await"
                    )
                )
                assert pending is not None
                pending_id = pending.interaction_id
                root = session.scalar(
                    select(ConversationMessageRow.message_id).where(
                        ConversationMessageRow.interaction_id == pending_id,
                        ConversationMessageRow.role == "user",
                    )
                )
                assert root is not None
                direct_ref = {
                    "kind": expected.kind,
                    "payload_id": expected.payload_id,
                    "version": expected.version,
                }
                assert any(
                    root in item.root_message_ids and direct_ref in item.payload_refs
                    for item in session.scalars(select(SourceInputRow))
                )

            if change == "semantic_merge":
                merger, _, _ = _services(database, directory, "current-merge", 3)
                independent = await merger.talk.execute(
                    TalkInput(ROOT_TEXT, "current-merge", "current-merge")
                )
                await merger.post_response.execute(
                    independent.interaction_id, trace_id="current-merge-post"
                )
                with database.session_factory() as session:
                    assert session.get_one(SemanticClaimRow, claim_id).aggregate_version > (
                        expected.version
                    )
            else:
                targets = (
                    (MemoryLifecycleStatus.ARCHIVED, MemoryLifecycleStatus.ACTIVE)
                    if change == "archive_reactivate"
                    else (MemoryLifecycleStatus.ARCHIVED,)
                )
                scope = MemoryScope(identity, "alice")
                for target in targets:
                    preview = origin.memory_lifecycle.preview(episode_id, target, scope=scope)
                    origin.memory_lifecycle.execute(
                        MemoryLifecycleCommand(
                            memory_id=episode_id,
                            target_status=target,
                            expected_version=preview.current_version,
                            request_id=f"current-{target.value}",
                            preview_digest=preview.preview_digest,
                        ),
                        scope=scope,
                        trace_id=f"current-{target.value}",
                    )
                with database.session_factory() as session:
                    current = session.get_one(EpisodicMemoryRow, episode_id)
                    assert current.aggregate_version == expected.version + len(targets)
                    assert current.lifecycle_status == targets[-1].value

            with database.session_factory() as session:
                affect_before = tuple(
                    (item.state_version, item.mood_version)
                    for item in session.scalars(select(AffectiveStateRow))
                )
            release.set()
            with pytest.raises(SourcePolicyError, match="source_dispatch_version_changed"):
                await task
            with database.session_factory() as session:
                assert session.get_one(ConversationInteractionRow, pending_id).status == "failed"
                assert (
                    session.scalar(
                        select(ConversationMessageRow.message_id).where(
                            ConversationMessageRow.interaction_id == pending_id,
                            ConversationMessageRow.role == "assistant",
                        )
                    )
                    is None
                )
                assert affect_before == tuple(
                    (item.state_version, item.mood_version)
                    for item in session.scalars(select(AffectiveStateRow))
                )
            assert len(foreground.requests) == 1
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(race())


def test_recording_new_owner_version_keeps_historical_parents_without_current_reproof(
    migrated_database: Database,
) -> None:
    identity, _, user_id, _ = _form_models(migrated_database)
    snapshot = _snapshot(identity)
    with migrated_database.session_factory() as session:
        session.info["source_snapshot"] = snapshot
        _receipt(session, snapshot, PayloadRef("user_model_claim", user_id))
        session.flush()
        materialize_mirror(session, snapshot)
        session.flush()
        admission = SessionSourceAdmission(session)
        original_input = admission.prepare(
            root_message_ids=(), payload_refs=(PayloadRef("user_model_claim", user_id),)
        )
        require_source_inputs(session, original_input)
        # This is the owner boundary after its admitted first write, before its new receipt.
        owner = session.get_one(UserModelClaimRow, user_id)
        owner.aggregate_version += 1
        session.flush()
        record_payload(session, "user_model_claim", user_id, 2, original_input)
        session.flush()
        recorded = session.get_one(SourcePayloadRow, ("user_model_claim", user_id, 2))
        assert recorded.payload_refs == [
            {"kind": "user_model_claim", "payload_id": user_id, "version": 1}
        ]
        current_input = admission.prepare(
            root_message_ids=(), payload_refs=(PayloadRef("user_model_claim", user_id, 2),)
        )
        require_source_inputs(session, current_input)
        admission.dispatch(current_input)
        with pytest.raises(SourcePolicyError, match="source_dispatch_version_changed"):
            require_source_inputs(session, original_input)
