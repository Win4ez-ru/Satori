"""Whole-head retention authority is fresh material proof, never copied markers."""

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy.engine import Connection

import satori.infrastructure.persistence.erasure as erasure_adapter
from satori.application.erasure.intent_contracts import ErasureIntentRequest
from satori.application.erasure.intent_use_cases import ErasureIntentManager
from satori.application.erasure.personality_reason_contracts import (
    CheckpointReasonCommand,
    CheckpointReasonRequest,
)
from satori.application.erasure.personality_reason_use_cases import CheckpointReasonManager
from satori.application.source_policy.admission import prepare_source_input
from satori.core.embedding import EmbeddingResponse, EmbeddingSpace
from satori.domain.erasure_bodies import BodyAction
from satori.domain.retention import RetentionClass, RetentionFacet
from satori.domain.source_policy import PayloadRef, SourceEligibilitySnapshot, SourcePolicyError
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure import (
    SQLiteErasureInventory,
    _admit_schema,
    _load_store,
    _read_transaction,
)
from satori.infrastructure.persistence.erasure_inventory import Store
from satori.infrastructure.persistence.erasure_personality_reasons import (
    SQLiteCheckpointReasonBodyStore,
)
from satori.infrastructure.persistence.erasure_preparation import SQLiteErasurePreparationStore
from satori.infrastructure.persistence.retention_context import RetentionContext
from satori.infrastructure.persistence.retention_contracts import RetentionWorkBudget
from satori.infrastructure.persistence.retrieval_uow import SQLAlchemyEpisodicMemoryIndexUnitOfWork
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime
from tests.checkpoint_reason_support import (
    CheckpointReasonFixture,
    create_checkpoint_reason_fixture,
)
from tests.erasure_support import create_case, provider_counts, stored_state
from tests.test_checkpoint_reason_selection import create_reason_case
from tests.test_source_exclusion_production import _exclude
from tests.test_stage4_conversation_memory import INTERACTION_TIME


def census(database: Database) -> tuple[Store, dict[str, tuple[str, ...]], str, str]:
    with _read_transaction(database) as connection:
        _revision, schema = _admit_schema(connection)
        store, _rows, digest, keys, _retention = _load_store(connection, schema)
    return store, keys, schema, digest


def context_for(database: Database, runtime: SourcePolicyRuntime) -> RetentionContext:
    with runtime.exclusive():
        snapshot = runtime.snapshot(verify_mirror=False)
        store, keys, schema, digest = census(database)
        return RetentionContext.build(
            store, snapshot=snapshot, primary_keys=keys, schema_digest=schema, store_digest=digest
        )


def finish_reasons(fixture: CheckpointReasonFixture) -> None:
    case = fixture.case
    case.runtime.upgrade_checkpoint_reason_policy()
    manager = CheckpointReasonManager(case.runtime, SQLiteCheckpointReasonBodyStore(case.database))
    request = CheckpointReasonRequest(
        identity_id=case.scope.identity_id, selectors=fixture.selectors
    )
    preview = manager.preview(request)
    assert preview.readiness == "ready", preview.blockers
    result = manager.commit(
        CheckpointReasonCommand(
            identity_id=request.identity_id,
            selectors=request.selectors,
            request_id="retention-complete-reasons",
            expected_generation=preview.generation,
            expected_erasure_revision=preview.erasure_revision,
            expected_body_revision=preview.body_revision,
            approval_digest=preview.approval_digest,
        )
    )
    assert result.main_projection_current


def test_available_initial_bodies_are_owned_and_context_cannot_cross_stores(
    migrated_database: Database, tmp_path: Path
) -> None:
    case = create_case(migrated_database, tmp_path / "floor")
    context = context_for(migrated_database, case.runtime)
    for table in ("satori_identities", "satori_values", "episodic_memories"):
        row = context._store[table][0]
        assert context.classify(table, row).category is RetentionClass.OWNED_CONTENT
    before = provider_counts(case.services)
    with pytest.raises(SourcePolicyError, match="retention_context_binding_mismatch"):
        context.classify("satori_identities", dict(context._store["satori_identities"][0]))
    with pytest.raises(SourcePolicyError, match="retention_context_binding_mismatch"):
        context.assert_bound(
            deepcopy(context._store),
            context._snapshot,
            context._schema_digest,
            context._store_digest,
        )
    row = context._store["satori_identities"][0]
    row["name"] = "different synthetic name"
    with pytest.raises(SourcePolicyError, match="retention_context_binding_mismatch"):
        context.classify("satori_identities", row)
    assert provider_counts(case.services) == before


def test_fresh_whole_head_proves_all_six_families_and_retained_material(
    migrated_database: Database, tmp_path: Path
) -> None:
    fixture = create_checkpoint_reason_fixture(migrated_database, tmp_path / "floor")
    finish_reasons(fixture)
    context = context_for(migrated_database, fixture.case.runtime)
    assert len({type(action) for action in context.actions}) == 6
    for table in (
        "episodic_memories",
        "conversation_messages",
        "semantic_claims",
        "reflection_proposals",
        "affective_transitions",
        "personality_checkpoint_approvals",
        "personality_restore_events",
    ):
        terminal = [
            row
            for row in context._store[table]
            if RetentionFacet.TERMINAL_BODY in context.classify(table, row).facets
        ]
        assert terminal, table
        assert all(
            context.classify(table, row).category is RetentionClass.SUPPORTED_RETAINED
            for row in terminal
        )
    changed = deepcopy(context._store)
    changed["reflection_runs"][0]["trace_id"] = "different-retained-header"
    with pytest.raises(SourcePolicyError, match="retention_body_head_unproven"):
        RetentionContext.build(
            changed,
            snapshot=context._snapshot,
            primary_keys=context._primary_keys,
            schema_digest=context._schema_digest,
            store_digest=context._store_digest,
        )


def test_copied_genuine_ack_on_available_reason_rows_is_not_terminal_authority(
    migrated_database: Database, tmp_path: Path
) -> None:
    case = create_reason_case(migrated_database, tmp_path / "floor")
    original, _keys, _schema, _digest = census(migrated_database)
    assert case.manager.commit(case.command()).main_projection_current
    current = context_for(migrated_database, case.runtime)
    forged = deepcopy(current._store)
    for table in ("personality_checkpoint_approvals", "personality_restore_events"):
        forged[table] = original[table]
    with pytest.raises(SourcePolicyError, match="retention_body_head_unproven"):
        RetentionContext.build(
            forged,
            snapshot=current._snapshot,
            primary_keys=current._primary_keys,
            schema_digest=current._schema_digest,
            store_digest=current._store_digest,
        )
    operator = RetentionContext.for_owner_assessment(
        forged,
        snapshot=current._snapshot,
        primary_keys=current._primary_keys,
        schema_digest=current._schema_digest,
        store_digest=current._store_digest,
        approved_actions=current.actions,
    )
    assert not any(
        RetentionFacet.TERMINAL_BODY in operator.classify(table, row).facets
        for table, rows in forged.items()
        for row in rows
    )


def test_pending_operator_proof_does_not_issue_terminal_badges(
    migrated_database: Database, tmp_path: Path
) -> None:
    case = create_reason_case(migrated_database, tmp_path / "floor")
    before, keys, schema, digest = census(migrated_database)
    assert case.manager.commit(case.command()).main_projection_current
    with case.runtime.shared() as snapshot:
        actions = case.runtime.body_actions()
    with pytest.raises(SourcePolicyError, match="retention_body_head_unproven"):
        RetentionContext.build(
            before, snapshot=snapshot, primary_keys=keys, schema_digest=schema, store_digest=digest
        )
    operator = RetentionContext.for_owner_assessment(
        before,
        snapshot=snapshot,
        primary_keys=keys,
        schema_digest=schema,
        store_digest=digest,
        approved_actions=actions,
    )
    for table in ("personality_checkpoint_approvals", "personality_restore_events"):
        assert all(
            operator.classify(table, row).category is RetentionClass.OWNED_CONTENT
            for row in before[table]
        )
    with pytest.raises(SourcePolicyError):
        RetentionContext.for_owner_assessment(
            before,
            snapshot=replace(snapshot, body_head_digest="f" * 64),
            primary_keys=keys,
            schema_digest=schema,
            store_digest=digest,
            approved_actions=actions,
        )


def test_classifier_budget_is_shared_and_bounded(
    migrated_database: Database, tmp_path: Path
) -> None:
    case = create_case(migrated_database, tmp_path / "floor")
    store, keys, schema, digest = census(migrated_database)
    with (
        case.runtime.shared() as snapshot,
        pytest.raises(SourcePolicyError, match="retention_assessment_capacity_exceeded"),
    ):
        RetentionContext.build(
            store,
            snapshot=snapshot,
            primary_keys=keys,
            schema_digest=schema,
            store_digest=digest,
            budget=RetentionWorkBudget(limit=1),
        )


def test_pending_sixth_action_and_old_intent_ack_keep_historical_root_resumable(
    migrated_database: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = create_checkpoint_reason_fixture(migrated_database, tmp_path / "floor")
    case = fixture.case
    case.runtime.upgrade_checkpoint_reason_policy()
    store = SQLiteCheckpointReasonBodyStore(migrated_database)
    manager = CheckpointReasonManager(case.runtime, store)
    request = CheckpointReasonRequest(
        identity_id=case.scope.identity_id, selectors=fixture.selectors
    )
    preview = manager.preview(request)
    command = CheckpointReasonCommand(
        identity_id=request.identity_id,
        selectors=request.selectors,
        request_id="pending-sixth-action",
        expected_generation=preview.generation,
        expected_erasure_revision=preview.erasure_revision,
        expected_body_revision=preview.body_revision,
        approval_digest=preview.approval_digest,
    )

    def fail_owner(*_args: object, **_kwargs: object) -> None:
        raise SourcePolicyError("synthetic_owner_failure")

    with monkeypatch.context() as fault:
        fault.setattr(store, "apply", fail_owner)
        assert not manager.commit(command).main_projection_current
    # An operator-only disposable old-main projection models independent intent
    # lag while preserving the genuine first-five terminal action/header rows.
    with migrated_database.engine.begin() as connection:
        connection.exec_driver_sql("DELETE FROM erasure_preparations")
        connection.exec_driver_sql("DELETE FROM erasure_policy_bindings")
    with case.runtime.exclusive() as snapshot:
        actions = case.runtime.body_actions()
        intent = case.runtime.erasure_intents()[0]
        physical, keys, schema, digest = census(migrated_database)
        assert (
            next(
                row
                for row in physical["conversation_messages"]
                if row["message_id"] == case.message_id
            )["content"]
            is None
        )
        operator = RetentionContext.for_owner_assessment(
            physical,
            snapshot=snapshot,
            primary_keys=keys,
            schema_digest=schema,
            store_digest=digest,
            approved_actions=actions,
        )
        assert operator.historical.admits_root(case.message_id)
        assert all(
            RetentionFacet.TERMINAL_BODY not in operator.classify(table, row).facets
            for table, rows in physical.items()
            for row in rows
        )
    intent_manager = ErasureIntentManager(
        case.runtime,
        SQLiteErasureInventory(migrated_database),
        SQLiteErasurePreparationStore(migrated_database),
    )
    result = intent_manager.resume(
        ErasureIntentRequest(
            message_id=case.message_id, counterparty_id=case.scope.counterparty_id
        ),
        request_id=intent.request_id,
    )
    assert not result.main_projection_current  # Whole body head still legitimately pending.
    with migrated_database.engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT erasure_revision FROM erasure_policy_bindings"
            ).scalar_one()
            == snapshot.erasure_revision
        )
    assert manager.resume(request, request_id=command.request_id).main_projection_current
    current = context_for(migrated_database, case.runtime)
    assert (
        RetentionFacet.TERMINAL_BODY
        in current.classify(
            "personality_checkpoint_approvals",
            current._store["personality_checkpoint_approvals"][0],
        ).facets
    )


def test_multiple_roots_share_one_census_and_context_after_all_scope_proofs(
    migrated_database: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = create_case(migrated_database, tmp_path / "floor")
    _exclude(migrated_database, case.runtime, case.derived_message_id)
    inventory = SQLiteErasureInventory(migrated_database)
    calls = {"census": 0, "context": 0}
    load = erasure_adapter._load_store
    build = RetentionContext.for_owner_assessment

    def observe_load(
        connection: Connection, schema_digest: str
    ) -> tuple[Store, int, str, dict[str, tuple[str, ...]], str]:
        calls["census"] += 1
        return load(connection, schema_digest)

    def observe_context(
        store: Store,
        *,
        snapshot: SourceEligibilitySnapshot,
        primary_keys: dict[str, tuple[str, ...]],
        schema_digest: str,
        store_digest: str,
        budget: RetentionWorkBudget | None = None,
        approved_actions: tuple[BodyAction, ...] | None = None,
    ) -> RetentionContext:
        calls["context"] += 1
        return build(
            store,
            snapshot=snapshot,
            primary_keys=primary_keys,
            schema_digest=schema_digest,
            store_digest=store_digest,
            budget=budget,
            approved_actions=approved_actions,
        )

    monkeypatch.setattr(erasure_adapter, "_load_store", observe_load)
    monkeypatch.setattr(RetentionContext, "for_owner_assessment", observe_context)
    with case.runtime.exclusive() as snapshot:
        roots = tuple(
            inventory.resolve_root(message, counterparty_id="alice", snapshot=snapshot)
            for message in (case.message_id, case.derived_message_id)
        )
        results = inventory.inspect_many(roots, snapshot=snapshot, approved_actions=())
        assert calls == {"census": 1, "context": 1}
        assert results[0].store_digest == results[1].store_digest
        assert results[0].scanned_rows == results[1].scanned_rows
        calls.update(census=0, context=0)
        with pytest.raises(SourcePolicyError, match="canonical_source_unavailable_in_scope"):
            inventory.inspect_many(
                (roots[0], replace(roots[1], counterparty_id="bob")),
                snapshot=snapshot,
                approved_actions=(),
            )
        assert calls == {"census": 0, "context": 0}


def test_assistant_owner_proof_uses_actual_message_session_not_only_canonical_user(
    migrated_database: Database, tmp_path: Path
) -> None:
    case = create_case(migrated_database, tmp_path / "floor")
    original = context_for(migrated_database, case.runtime)
    store = deepcopy(original._store)
    assistant = next(row for row in store["conversation_messages"] if row["role"] == "assistant")
    other = next(
        row
        for row in store["conversation_sessions"]
        if row["session_id"] != assistant["session_id"]
    )
    assistant["session_id"] = other["session_id"]
    context = RetentionContext.build(
        store,
        snapshot=original._snapshot,
        primary_keys=original._primary_keys,
        schema_digest=original._schema_digest,
        store_digest="c" * 64,
    )
    assert context.classify("conversation_messages", assistant).category is RetentionClass.UNKNOWN


@pytest.mark.parametrize(
    ("dimensions", "magnitude"), [(768, 1.0), (1536, 1.0), (8192, 1.0), (768, 1e100)]
)
def test_actual_owner_stored_vector_uses_embedding_role_not_generic_json_limits(
    migrated_database: Database, tmp_path: Path, dimensions: int, magnitude: float
) -> None:
    case = create_case(migrated_database, tmp_path / "floor", excluded=False)
    calls = provider_counts(case.services)
    vector = (magnitude, *(0.0 for _ in range(dimensions - 1)))
    space = EmbeddingSpace("synthetic-retention-vector", "controlled-native-vector", dimensions, 1)
    assert EmbeddingResponse(space, (vector,)).vectors == (vector,)
    with SQLAlchemyEpisodicMemoryIndexUnitOfWork(migrated_database.session_factory) as unit:
        eligible = unit.memory_index.get_eligible(case.episode_ids[0])
        assert eligible is not None
        scope, memory = eligible
        input_id = prepare_source_input(
            unit, payload_refs=(PayloadRef("episode", memory.memory_id, memory.aggregate_version),)
        )
        unit.memory_index.upsert(
            memory.memory_id,
            space,
            vector,
            scope=scope,
            expected_version=memory.aggregate_version,
            indexed_at=INTERACTION_TIME,
            input_id=input_id,
        )
        unit.commit()
    before_read = stored_state(case)
    main_before = case.path.read_bytes()
    floor_before = case.runtime.ledger.database_path.read_bytes()
    context = context_for(migrated_database, case.runtime)
    row = next(
        row
        for row in context._store["episodic_memory_embeddings"]
        if row["provider"] == space.provider and row["dimensions"] == dimensions
    )
    assert (
        context.classify("episodic_memory_embeddings", row).category is RetentionClass.OWNED_CONTENT
    )
    assert provider_counts(case.services) == calls

    # Corrupted private census data has no learning/write authority; its malformed
    # index body must remain UNKNOWN rather than acquire a retained/content badge.
    embedding_id = row["embedding_id"]
    for invalid in (
        [0.0] * dimensions,
        [float("nan"), *([0.0] * (dimensions - 1))],
        [True, *([0.0] * (dimensions - 1))],
        ["1", *([0.0] * (dimensions - 1))],
        [[1.0], *([0.0] * (dimensions - 1))],
        [1.0] * (dimensions - 1),
    ):
        corrupted = deepcopy(context._store)
        broken = next(
            item
            for item in corrupted["episodic_memory_embeddings"]
            if item["embedding_id"] == embedding_id
        )
        broken["vector"] = json.dumps(invalid)
        reviewed = RetentionContext.build(
            corrupted,
            snapshot=context._snapshot,
            primary_keys=context._primary_keys,
            schema_digest=context._schema_digest,
            store_digest="b" * 64,
        )
        assert (
            reviewed.classify("episodic_memory_embeddings", broken).category
            is RetentionClass.UNKNOWN
        )
    assert stored_state(case) == before_read
    assert case.path.read_bytes() == main_before
    assert case.runtime.ledger.database_path.read_bytes() == floor_before
