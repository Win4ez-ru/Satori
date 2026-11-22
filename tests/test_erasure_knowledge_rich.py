"""All retained owner paths across canonical NULL, mixed live support and recovery."""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import event, update
from sqlalchemy.engine import Connection

from satori.application.erasure.body_contracts import BodyErasureRequest
from satori.application.erasure.dialogue_use_cases import DialogueBodyManager
from satori.application.erasure.knowledge_use_cases import KnowledgeBodyManager
from satori.domain.erasure_knowledge import KnowledgeBodyAction, KnowledgeWitnessOrigin
from satori.domain.memory_scope import scope_semantic_key
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.erasure_dialogue import SQLiteDialogueBodyStore
from satori.infrastructure.persistence.erasure_knowledge import SQLiteKnowledgeBodyStore
from satori.infrastructure.persistence.models import ConversationMessageRow, UserModelClaimRow
from satori.infrastructure.source_policy.installation import bind_installation
from tests.erasure_knowledge_support import KnowledgeFixture, add_knowledge
from tests.erasure_support import ErasureCase, create_case, provider_counts, stored_state
from tests.test_erasure_dialogue_integration import dialogue_command
from tests.test_erasure_intent_integration import _command as intent_command
from tests.test_erasure_intent_integration import _manager as intent_manager
from tests.test_erasure_intent_integration import _request as intent_request
from tests.test_erasure_knowledge_integration import knowledge_command, prepare_knowledge
from tests.test_recovery_whole_state import stored_rows
from tests.test_source_exclusion_production import SECRET, _exclude


@pytest.fixture
def rich(
    migrated_database: Database, tmp_path: Path
) -> tuple[ErasureCase, KnowledgeFixture, KnowledgeBodyManager, BodyErasureRequest]:
    case = create_case(migrated_database, tmp_path / "rich-floor", excluded=False)
    fixture = add_knowledge(case)
    _exclude(case.database, case.runtime, case.message_id)
    manager, request = prepare_knowledge(case)
    return case, fixture, manager, request


def test_all_five_owner_bodies_and_all_quotes_null_without_rewriting_history(
    rich: tuple[ErasureCase, KnowledgeFixture, KnowledgeBodyManager, BodyErasureRequest],
) -> None:
    case, fixture, manager, request = rich
    before = stored_rows(case.path)
    with case.runtime.exclusive():
        old_actions = case.runtime.body_actions()
    preview = manager.preview(request)
    assert (preview.semantic_claims, preview.user_model_claims, preview.world_model_claims) == (
        4,
        1,
        1,
    )
    assert (preview.positions, preview.inclinations, preview.position_quotes) == (1, 1, 2)
    assert preview.witness_transitions == 2
    result = manager.commit(knowledge_command(manager, request))
    assert result.main_projection_current
    assert result.selected_bodies_removed
    assert result.physical_erasure is False
    after = stored_rows(case.path)
    body_columns = {
        "semantic_claims": ("value", "normalized_value"),
        "user_model_claims": ("value", "normalized_value"),
        "world_model_claims": (
            "subject_label",
            "normalized_subject_label",
            "value",
            "normalized_value",
        ),
        "satori_positions": ("proposition", "normalized_proposition"),
        "satori_inclinations": (
            "topic",
            "normalized_topic",
            "alternative_topic",
            "normalized_alternative_topic",
        ),
        "satori_position_evidence": ("quote", "normalized_signature"),
    }
    with sqlite3.connect(case.path) as connection:
        for table, fields in body_columns.items():
            columns = tuple(row[1] for row in connection.execute(f'PRAGMA table_info("{table}")'))
            for prior, terminal in zip(before[table], after[table], strict=True):
                old, new = (
                    dict(zip(columns, prior, strict=True)),
                    dict(zip(columns, terminal, strict=True)),
                )
                assert all(new[field] is None for field in fields), table
                modified = {*fields, "body_state", "body_action_id"}
                assert {k: v for k, v in old.items() if k not in modified} == {
                    k: v for k, v in new.items() if k not in modified
                }, table
        semantic_values = dict(
            connection.execute(
                "SELECT claim_id, value FROM semantic_claims WHERE claim_id IN (?, ?)",
                fixture.scalar_claim_ids,
            )
        )
        assert semantic_values == dict.fromkeys(fixture.scalar_claim_ids)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    changed = {*body_columns, "erasure_body_applications", "erasure_body_bindings"}
    assert {name: rows for name, rows in before.items() if name not in changed} == {
        name: rows for name, rows in after.items() if name not in changed
    }
    with case.runtime.exclusive():
        actions = case.runtime.body_actions()
    assert actions[:2] == old_actions
    action = actions[-1]
    assert isinstance(action, KnowledgeBodyAction)
    origins = {
        transition.origin
        for target in action.proposal.targets
        for transition in target.witness_transitions
    }
    assert origins == {KnowledgeWitnessOrigin.DIALOGUE, KnowledgeWitnessOrigin.LIVE}
    assert provider_counts(case.services) == case.initial_provider_calls


def test_terminal_inventory_and_replay_are_read_only_without_disclosing_derived_text(
    rich: tuple[ErasureCase, KnowledgeFixture, KnowledgeBodyManager, BodyErasureRequest],
) -> None:
    case, fixture, manager, request = rich
    command = knowledge_command(manager, request)
    assert manager.commit(command).main_projection_current
    before = stored_state(case)
    status = manager.status(request, request_id=command.request_id)
    assert status.main_projection_current
    inventory = case.planner().preview(case.request)
    preview = manager.preview(request)
    assert preview.readiness == "blocked"
    assert preview.blockers == ("no_available_knowledge_bodies",)
    assert preview.already_erased == 8
    assert preview.semantic_claims == 0
    assert stored_state(case) == before
    public = inventory.model_dump_json() + status.model_dump_json() + preview.model_dump_json()
    for private in (SECRET, fixture.position_id, fixture.inclination_id, *fixture.scalar_claim_ids):
        assert private not in public
    assert manager.commit(command).main_projection_current
    assert stored_state(case) == before


def test_later_live_root_dialogue_uses_terminal_position_evidence_without_rewinding(
    rich: tuple[ErasureCase, KnowledgeFixture, KnowledgeBodyManager, BodyErasureRequest],
) -> None:
    case, _, knowledge, request = rich
    assert knowledge.commit(knowledge_command(knowledge, request)).main_projection_current
    with case.runtime.exclusive():
        prior = case.runtime.body_actions()
    _exclude(case.database, case.runtime, case.derived_message_id)
    intents = intent_manager(case)
    second = intent_request(case, message_id=case.derived_message_id)
    command = intent_command(intents, second, request_id="rich-second-intent")
    assert intents.commit(command).main_projection_current
    dialogue = DialogueBodyManager(case.runtime, SQLiteDialogueBodyStore(case.database))
    dialogue_request = BodyErasureRequest(
        message_id=case.derived_message_id,
        counterparty_id=case.scope.counterparty_id,
        intent_request_id=command.request_id,
    )
    assert dialogue.commit(
        dialogue_command(dialogue, dialogue_request, request_id="rich-second-dialogue")
    ).main_projection_current
    with case.runtime.exclusive():
        assert case.runtime.body_actions()[:3] == prior
    with case.database.session_factory() as session:
        source = session.get(ConversationMessageRow, case.derived_message_id)
        assert source is not None
        assert source.content is None
        assert source.body_state == "erased"
    assert knowledge.status(request, request_id="knowledge-action").main_projection_current
    bind_installation(case.database, case.directory)
    assert provider_counts(case.services) == case.initial_provider_calls


def test_mid_fifth_kind_fault_preserves_all_main_rows_and_resumes(
    rich: tuple[ErasureCase, KnowledgeFixture, KnowledgeBodyManager, BodyErasureRequest],
) -> None:
    case, _, manager, request = rich
    command = knowledge_command(manager, request)
    before = stored_rows(case.path)
    faults = 0

    def fail(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        many: bool,
    ) -> None:
        nonlocal faults
        if statement.startswith("UPDATE satori_inclinations SET"):
            assert (
                connection.exec_driver_sql(
                    "SELECT count(*) FROM semantic_claims WHERE value IS NULL"
                ).scalar_one()
                == 4
            )
            faults += 1
            raise RuntimeError("synthetic_fifth_kind_fault")

    event.listen(case.database.engine, "before_cursor_execute", fail)
    try:
        pending = manager.commit(command)
        assert not pending.main_projection_current
        assert faults == 1
        assert stored_rows(case.path) == before
    finally:
        event.remove(case.database.engine, "before_cursor_execute", fail)
    with pytest.raises(SourcePolicyError, match="body_reconciliation_required"):
        bind_installation(case.database, case.directory)
    assert manager.resume(request, request_id=command.request_id).main_projection_current
    bind_installation(case.database, case.directory)


def test_old_main_backup_with_terminal_dialogue_cannot_resurrect_any_knowledge_kind(
    rich: tuple[ErasureCase, KnowledgeFixture, KnowledgeBodyManager, BodyErasureRequest],
    tmp_path: Path,
) -> None:
    case, _, manager, request = rich
    backup = tmp_path / "old-rich.db"
    with sqlite3.connect(case.path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    command = knowledge_command(manager, request)
    assert manager.commit(command).main_projection_current
    restored = create_database(f"sqlite+pysqlite:///{backup}")
    try:
        with pytest.raises(SourcePolicyError, match="body_reconciliation_required"):
            bind_installation(restored, case.directory)
        fresh = KnowledgeBodyManager(case.runtime, SQLiteKnowledgeBodyStore(restored))
        assert not fresh.status(request, request_id=command.request_id).main_projection_current
        assert fresh.resume(request, request_id=command.request_id).main_projection_current
        bind_installation(restored, case.directory)
    finally:
        restored.dispose()


def test_changed_owner_body_stales_review_without_appending_floor(
    rich: tuple[ErasureCase, KnowledgeFixture, KnowledgeBodyManager, BodyErasureRequest],
) -> None:
    case, fixture, manager, request = rich
    command = knowledge_command(manager, request)
    with case.database.session_factory() as session:
        session.execute(
            update(UserModelClaimRow)
            .where(UserModelClaimRow.claim_id == fixture.user_claim_id)
            .values(value="synthetic-changed-body", normalized_value="synthetic-changed-body")
        )
        session.commit()
    before = stored_state(case)
    with pytest.raises(SourcePolicyError, match="body_erasure_review_stale"):
        manager.commit(command)
    assert stored_state(case) == before
    assert manager.commit(
        knowledge_command(manager, request, request_id="rich-fresh-review")
    ).main_projection_current


def test_pending_exact_position_does_not_block_fresh_review_of_changed_semantic_body(
    rich: tuple[ErasureCase, KnowledgeFixture, KnowledgeBodyManager, BodyErasureRequest],
) -> None:
    case, _, manager, request = rich
    command = knowledge_command(manager, request)

    def interrupted(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        many: bool,
    ) -> None:
        if statement.startswith("UPDATE satori_inclinations SET"):
            raise RuntimeError("synthetic_mid_owner_fault")

    event.listen(case.database.engine, "before_cursor_execute", interrupted)
    try:
        assert not manager.commit(command).main_projection_current
    finally:
        event.remove(case.database.engine, "before_cursor_execute", interrupted)
    with case.database.engine.begin() as connection:
        row = (
            connection.exec_driver_sql(
                "SELECT subject,predicate,value_kind,polarity,claim_kind,normalization_version "
                "FROM semantic_claims WHERE claim_id=?",
                (case.claim_ids[0],),
            )
            .mappings()
            .one()
        )
        normalized = "synthetic changed semantic body"
        assert row["value_kind"] == "text"
        material = dict(row)
        material["polarity"] = bool(row["polarity"])
        material["value"] = normalized
        base = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        connection.exec_driver_sql(
            "UPDATE semantic_claims SET value=?,normalized_value=?,claim_key=? WHERE claim_id=?",
            (
                json.dumps(normalized),
                normalized,
                scope_semantic_key(base, case.scope),
                case.claim_ids[0],
            ),
        )
    preview = manager.preview(request)
    assert preview.readiness == "ready", preview.blockers
    assert preview.position_quotes == 2
    assert manager.commit(
        knowledge_command(manager, request, request_id="rich-pending-fresh-review")
    ).main_projection_current
