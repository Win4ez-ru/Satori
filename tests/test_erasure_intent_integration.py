"""Real owner/floor/restart intent checks; controls invoke no providers or body writers."""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import null, select, update

from satori.application.erasure.intent_contracts import (
    ErasureIntentCommand,
    ErasureIntentRequest,
)
from satori.application.erasure.intent_use_cases import ErasureIntentManager
from satori.application.retrieval.contracts import RetrievalQuery, RetrievalStatus
from satori.domain.erasure import ErasureInventory
from satori.domain.erasure_bodies import BodyAction
from satori.domain.erasure_preparation import ErasureOwnerPreparation
from satori.domain.source_policy import (
    CanonicalRootRef,
    SourceEligibilitySnapshot,
    SourcePolicyError,
)
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.erasure import SQLiteErasureInventory
from satori.infrastructure.persistence.erasure_preparation import SQLiteErasurePreparationStore
from satori.infrastructure.persistence.models import (
    ConversationInteractionRow,
    ConversationMessageRow,
    ErasurePolicyBindingRow,
    ErasurePreparationRow,
    SemanticClaimRow,
)
from satori.infrastructure.source_policy.installation import bind_installation, repair_installation
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime
from tests.erasure_support import ErasureCase, create_case, provider_counts
from tests.test_recovery_whole_state import stored_rows
from tests.test_source_exclusion_production import SECRET, _exclude, _services

_PREPARATION_TABLES = {"erasure_policy_bindings", "erasure_preparations"}


@pytest.fixture
def case(migrated_database: Database, tmp_path: Path) -> ErasureCase:
    return create_case(migrated_database, tmp_path / "intent-floor")


def _manager(
    case: ErasureCase,
    *,
    database: Database | None = None,
    runtime: SourcePolicyRuntime | None = None,
) -> ErasureIntentManager:
    selected = database or case.database
    return ErasureIntentManager(
        runtime or case.runtime,
        SQLiteErasureInventory(selected),
        SQLiteErasurePreparationStore(selected),
    )


def _request(case: ErasureCase, *, message_id: str | None = None) -> ErasureIntentRequest:
    return ErasureIntentRequest(
        message_id=message_id or case.message_id, counterparty_id=case.scope.counterparty_id
    )


def _command(
    manager: ErasureIntentManager,
    request: ErasureIntentRequest,
    *,
    request_id: str = "terminal-intent",
) -> ErasureIntentCommand:
    preview = manager.preview(request)
    return ErasureIntentCommand(
        message_id=request.message_id,
        counterparty_id=request.counterparty_id,
        expected_generation=preview.inventory.generation,
        expected_erasure_revision=preview.erasure_revision,
        approval_digest=preview.approval_digest,
        request_id=request_id,
    )


def _owner_rows(path: Path) -> dict[str, list[tuple[object, ...]]]:
    return {
        name: rows for name, rows in stored_rows(path).items() if name not in _PREPARATION_TABLES
    }


def _preparation_rows(path: Path) -> list[tuple[object, ...]]:
    return stored_rows(path)["erasure_preparations"]


def _head(case: ErasureCase) -> int | None:
    with case.database.session_factory() as session:
        row = session.get(ErasurePolicyBindingRow, case.scope.identity_id)
        return None if row is None else row.erasure_revision


def test_review_commit_retry_and_status_preserve_all_bodies_and_provider_counts(
    case: ErasureCase,
) -> None:
    manager = _manager(case)
    request = _request(case)
    before = _owner_rows(case.path)
    command = _command(manager, request)
    assert _owner_rows(case.path) == before
    committed = manager.commit(command)
    assert committed.main_projection_current
    assert committed.preparation_current
    assert committed.phase == "intent_committed_preparation_blocked"
    assert committed.execution_authorized is False
    assert committed.physical_erasure is False
    assert committed.preparation is not None
    assert len(committed.preparation.assessments) == 15
    assert _owner_rows(case.path) == before
    rows = _preparation_rows(case.path)
    assert len(rows) == 15
    replayed = manager.commit(command)
    status = manager.status(request, request_id=command.request_id)
    assert replayed == committed
    assert status == committed
    assert _preparation_rows(case.path) == rows
    assert _owner_rows(case.path) == before
    assert provider_counts(case.services) == case.initial_provider_calls
    public = json.dumps(status.model_dump(mode="json"))
    assert SECRET not in public
    assert case.derived_message_id not in public
    assert all(memory_id not in public for memory_id in case.episode_ids)
    assert all(claim_id not in public for claim_id in case.claim_ids)


def test_floor_commit_survives_preparation_failure_and_restart_resumes(
    case: ErasureCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(case)
    command = _command(manager, _request(case))
    before = _owner_rows(case.path)

    def fail(
        preparations: tuple[ErasureOwnerPreparation, ...], *, snapshot: SourceEligibilitySnapshot
    ) -> None:
        assert preparations[0].intent.result_erasure_revision == 1
        assert snapshot.erasure_revision == 1
        raise RuntimeError("synthetic private storage exception")

    monkeypatch.setattr(manager.preparations, "persist", fail)
    pending = manager.commit(command)
    assert pending.phase == "intent_committed_preparation_pending"
    assert pending.failure_reason == "erasure_preparation_unavailable"
    assert pending.intent.result_erasure_revision == 1
    assert pending.execution_authorized is False
    assert pending.main_projection_current is False
    assert _head(case) is None
    assert _preparation_rows(case.path) == []
    assert _owner_rows(case.path) == before
    with pytest.raises(SourcePolicyError, match="erasure_reconciliation_required"):
        bind_installation(case.database, case.directory)
    restarted = SourcePolicyRuntime(case.directory, case.scope.identity_id)
    resumed = _manager(case, runtime=restarted).resume(
        _request(case), request_id=command.request_id
    )
    assert resumed.intent == pending.intent
    assert resumed.main_projection_current
    assert resumed.preparation_current
    assert _head(case) == 1
    assert _owner_rows(case.path) == before
    bind_installation(case.database, case.directory)
    assert provider_counts(case.services) == case.initial_provider_calls


@pytest.mark.parametrize("drift", ["owner_version", "opaque_content"])
def test_status_detects_same_generation_drift_and_resume_appends_metadata_only(
    case: ErasureCase,
    drift: str,
) -> None:
    manager = _manager(case)
    request = _request(case)
    command = _command(manager, request)
    first = manager.commit(command)
    assert first.preparation is not None
    original_rows = _preparation_rows(case.path)
    with case.database.session_factory() as session:
        if drift == "owner_version":
            session.execute(
                update(SemanticClaimRow)
                .where(SemanticClaimRow.claim_id == case.claim_ids[0])
                .values(aggregate_version=SemanticClaimRow.aggregate_version + 1)
            )
        else:
            session.execute(
                update(ConversationMessageRow)
                .where(ConversationMessageRow.message_id == case.message_id)
                .values(content="Synthetic owner content changed after the reviewed inventory.")
            )
        session.commit()
    owner_rows = _owner_rows(case.path)
    with case.runtime.shared() as snapshot:
        assert (snapshot.generation, snapshot.erasure_revision) == (1, 1)
    status = manager.status(request, request_id=command.request_id)
    assert status.main_projection_current
    assert status.preparation_current is False
    assert _preparation_rows(case.path) == original_rows
    resumed = manager.resume(request, request_id=command.request_id)
    assert resumed.intent == first.intent
    assert resumed.preparation_current
    assert resumed.preparation is not None
    assert resumed.preparation.preparation_digest != first.preparation.preparation_digest
    current_rows = _preparation_rows(case.path)
    assert len(current_rows) == 30
    assert set(original_rows).issubset(current_rows)
    assert _owner_rows(case.path) == owner_rows
    assert provider_counts(case.services) == case.initial_provider_calls


def test_source_generation_change_cannot_reuse_old_intent_preview(
    case: ErasureCase,
) -> None:
    manager = _manager(case)
    old = _command(manager, _request(case))
    _exclude(case.database, case.runtime, case.derived_message_id)
    with pytest.raises(SourcePolicyError, match="erasure_generation_conflict"):
        manager.commit(old)
    with case.runtime.shared() as snapshot:
        assert (snapshot.generation, snapshot.erasure_revision) == (2, 0)
        assert case.runtime.erasure_intents() == ()
    assert _head(case) is None
    assert provider_counts(case.services) == case.initial_provider_calls


@pytest.mark.parametrize("target", ["foreign_counterparty", "assistant", "pending"])
def test_scope_and_canonical_proof_precede_floor_request_lookup_and_content_scan(
    case: ErasureCase,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    manager = _manager(case)
    message_id, counterparty = case.message_id, "alice"
    if target == "foreign_counterparty":
        counterparty = "bob"
    elif target == "assistant":
        with case.database.session_factory() as session:
            assistant = session.scalar(
                select(ConversationMessageRow.message_id).where(
                    ConversationMessageRow.interaction_id == case.interaction_id,
                    ConversationMessageRow.role == "assistant",
                )
            )
        assert assistant is not None
        message_id = assistant
    else:
        with case.database.session_factory() as session:
            session.execute(
                update(ConversationInteractionRow)
                .where(ConversationInteractionRow.interaction_id == case.interaction_id)
                .values(
                    status="pending",
                    completed_at=None,
                    provider=None,
                    model=None,
                    finish_status=None,
                    input_tokens=None,
                    output_tokens=None,
                    context_schema_version=None,
                    context_manifest_schema_version=None,
                    policy_id=None,
                    policy_schema_version=None,
                    personality_aggregate_version=None,
                    personality_expression_schema_version=None,
                    personality_expression_cues=null(),
                )
            )
            session.commit()

    def forbidden_lookup(request_id: str) -> None:
        raise AssertionError("scope proof must run before request lookup")

    def forbidden_scan(
        message_id: str, *, counterparty_id: str, snapshot: SourceEligibilitySnapshot
    ) -> ErasureInventory:
        raise AssertionError("scope proof must run before content scan")

    monkeypatch.setattr(case.runtime, "erasure_intent", forbidden_lookup)
    monkeypatch.setattr(manager.inventory, "inspect", forbidden_scan)
    request = ErasureIntentRequest(message_id=message_id, counterparty_id=counterparty)
    with pytest.raises(SourcePolicyError, match="canonical_source_unavailable_in_scope"):
        manager.status(request, request_id="possibly-existing-private-request")
    assert provider_counts(case.services) == case.initial_provider_calls


def test_old_main_backup_keeps_intent_fence_until_explicit_resume(
    case: ErasureCase,
    tmp_path: Path,
) -> None:
    older_path = tmp_path / "after-exclusion-before-intent.db"
    with sqlite3.connect(case.path) as source, sqlite3.connect(older_path) as destination:
        source.backup(destination)
    original_rows = _owner_rows(older_path)
    manager = _manager(case)
    request = _request(case)
    committed = manager.commit(_command(manager, request))
    assert committed.main_projection_current
    older = create_database(f"sqlite+pysqlite:///{older_path}")
    try:
        with pytest.raises(SourcePolicyError, match="erasure_reconciliation_required"):
            bind_installation(older, case.directory)
        runtime = SourcePolicyRuntime(case.directory, case.scope.identity_id)
        with runtime.exclusive() as snapshot:
            exclusion = runtime.receipt(f"exclude-{case.message_id}")
            assert exclusion is not None
            repair_installation(older, runtime, snapshot, exclusion)
        with pytest.raises(SourcePolicyError, match="erasure_reconciliation_required"):
            bind_installation(older, case.directory)
        resumed = _manager(case, database=older, runtime=runtime).resume(
            request, request_id=committed.intent.request_id
        )
        assert resumed.intent == committed.intent
        assert resumed.main_projection_current
        assert resumed.preparation_current
        bound = bind_installation(older, case.directory)
        with bound.shared() as snapshot:
            assert (snapshot.generation, snapshot.erasure_revision) == (1, 1)
        assert _owner_rows(older_path) == original_rows
        unrelated, _, embedding = _services(
            older, case.directory, "resumed-unrelated", 4, form=False
        )
        retrieval = unrelated.retrieve_memories
        assert retrieval is not None
        context = asyncio.run(
            retrieval.execute(
                RetrievalQuery(
                    "unrelated safe query",
                    "resumed-retrieval",
                    datetime(2027, 1, 1, tzinfo=UTC),
                    None,
                    case.scope,
                )
            )
        )
        assert context.status is RetrievalStatus.NO_RELEVANT_MEMORY
        assert context.memories == ()
        assert embedding.requests == []
        assert provider_counts(case.services) == case.initial_provider_calls
    finally:
        older.dispose()


def test_full_head_ack_requires_every_intent_and_public_failure_is_selected_root_only(
    case: ErasureCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(case)
    first_request = _request(case)
    first = manager.commit(_command(manager, first_request, request_id="intent-first"))
    assert _head(case) == 1
    _exclude(case.database, case.runtime, case.derived_message_id)
    second_request = _request(case, message_id=case.derived_message_id)
    second_command = _command(manager, second_request, request_id="intent-second")
    original_inspect = manager.inventory.inspect_many

    def fail_prior_intent(
        roots: tuple[CanonicalRootRef, ...],
        *,
        snapshot: SourceEligibilitySnapshot,
        approved_actions: tuple[BodyAction, ...] | None = None,
    ) -> tuple[ErasureInventory, ...]:
        if any(root.message_id == case.message_id for root in roots):
            raise SourcePolicyError("erasure_prior_owner_unavailable")
        return original_inspect(roots, snapshot=snapshot, approved_actions=approved_actions)

    with monkeypatch.context() as context:
        context.setattr(manager.inventory, "inspect_many", fail_prior_intent)
        pending = manager.commit(second_command)
    assert pending.phase == "intent_committed_preparation_pending"
    assert pending.intent.root.message_id == case.derived_message_id
    assert pending.intent.result_erasure_revision == 2
    assert pending.main_projection_current is False
    assert _head(case) == 1
    with pytest.raises(SourcePolicyError, match="erasure_reconciliation_required"):
        bind_installation(case.database, case.directory)
    public = json.dumps(pending.model_dump(mode="json"))
    assert case.message_id not in public
    assert SECRET not in public
    resumed = manager.resume(second_request, request_id=second_command.request_id)
    assert resumed.intent == pending.intent
    assert resumed.main_projection_current
    assert _head(case) == 2
    with case.database.session_factory() as session:
        assert set(session.scalars(select(ErasurePreparationRow.request_id))) == {
            first.intent.request_id,
            pending.intent.request_id,
        }
    assert provider_counts(case.services) == case.initial_provider_calls


def test_missing_floor_is_not_recreated_by_intent_resume(case: ErasureCase) -> None:
    manager = _manager(case)
    committed = manager.commit(_command(manager, _request(case)))
    binding = case.runtime.ledger.binding_path.read_bytes()
    case.runtime.ledger.database_path.unlink()
    with pytest.raises(SourcePolicyError, match="policy_file_unavailable"):
        manager.resume(_request(case), request_id=committed.intent.request_id)
    assert not case.runtime.ledger.database_path.exists()
    assert case.runtime.ledger.binding_path.read_bytes() == binding
