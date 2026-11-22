"""Identity-only approvals share the existing floor head, lease and replay contract."""

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy.engine import Connection

from satori.domain.erasure_bodies import body_actions_digest
from satori.domain.erasure_personality_reasons import (
    CheckpointReasonAction,
    CheckpointReasonKind,
    CheckpointReasonProposal,
    CheckpointReasonScope,
    CheckpointReasonSelector,
    CheckpointReasonTarget,
)
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.source_policy import ledger as ledger_module
from satori.infrastructure.source_policy.migrations import upgrade_policy
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime
from tests.test_erasure_appraisal_floor import _appraisal_commit, _appraisal_proposal
from tests.test_erasure_body_floor import _body_commit, _body_proposal, _rows
from tests.test_erasure_dialogue_floor import _dialogue_commit, _dialogue_proposal
from tests.test_erasure_intent_floor import _commit as _intent_commit
from tests.test_erasure_intent_floor import _exclude, _root
from tests.test_erasure_intent_floor import _proposal as _intent_proposal
from tests.test_erasure_knowledge_floor import _knowledge_commit, _knowledge_proposal
from tests.test_erasure_reflection_floor import _reflection_commit, _reflection_proposal


@pytest.fixture
def runtime(tmp_path: Path) -> SourcePolicyRuntime:
    result = SourcePolicyRuntime(tmp_path / "checkpoint-reasons-floor", "satori")
    result.enroll(reviewed=True, new_installation=True, lineage_id="lineage")
    result.upgrade_appraisal_policy()
    return result


def _proposal(
    runtime: SourcePolicyRuntime, request_id: str = "reason-request"
) -> CheckpointReasonProposal:
    with runtime.exclusive() as snapshot:
        return CheckpointReasonProposal(
            CheckpointReasonScope(snapshot.identity_id, snapshot.lineage_id),
            request_id,
            snapshot.generation,
            snapshot.erasure_revision,
            snapshot.body_revision,
            "a" * 64,
            "b" * 64,
            "c" * 64,
            (
                CheckpointReasonTarget(
                    CheckpointReasonSelector(CheckpointReasonKind.CHECKPOINT_APPROVAL, "approval"),
                    1,
                    "d" * 64,
                    "e" * 64,
                    2,
                ),
            ),
        )


def _reason_commit(
    runtime: SourcePolicyRuntime, proposal: CheckpointReasonProposal
) -> CheckpointReasonAction:
    with runtime.exclusive():
        return runtime.commit_checkpoint_reason_action(proposal)


def test_identity_reason_requires_explicit_v8_before_any_floor_mutation(
    runtime: SourcePolicyRuntime,
) -> None:
    proposal = _proposal(runtime)
    before = runtime.ledger.database_path.read_bytes()
    with pytest.raises(SourcePolicyError, match="policy_checkpoint_reason_schema_upgrade_required"):
        _reason_commit(runtime, proposal)
    assert runtime.ledger.database_path.read_bytes() == before


def test_reason_without_any_exclusion_or_intent_replays_and_restarts(
    runtime: SourcePolicyRuntime,
) -> None:
    runtime.upgrade_checkpoint_reason_policy()
    proposal = _proposal(runtime)
    action = _reason_commit(runtime, proposal)
    before = runtime.ledger.database_path.read_bytes()
    assert _reason_commit(runtime, proposal) == action
    restarted = SourcePolicyRuntime(runtime.ledger.directory, "satori")
    with restarted.shared() as snapshot:
        assert (snapshot.generation, snapshot.erasure_revision, snapshot.body_revision) == (0, 0, 1)
        assert snapshot.excluded_message_ids == frozenset()
        assert restarted.erasure_intents() == ()
        assert restarted.body_actions() == (action,)
        assert restarted.body_action(proposal.request_id) == action
        assert snapshot.body_head_digest == body_actions_digest((action,))
    rows = _rows(runtime)
    assert rows["policy_body_actions"][0][4:9] == (None,) * 5
    encoded_targets = rows["policy_body_actions"][0][16]
    assert isinstance(encoded_targets, str)
    assert json.loads(encoded_targets)[0]["selector"] == {
        "kind": "checkpoint_approval",
        "record_id": "approval",
    }
    assert runtime.ledger.database_path.read_bytes() == before


def test_v8_preserves_all_five_old_rows_hashes_binding_inode_and_readiness(
    runtime: SourcePolicyRuntime,
) -> None:
    _exclude(runtime, _root(), "source-request")
    _intent_commit(runtime, _intent_proposal(runtime, _root(), "intent-request"))
    _body_commit(runtime, _body_proposal(runtime))
    _dialogue_commit(runtime, _dialogue_proposal(runtime))
    _knowledge_commit(runtime, _knowledge_proposal(runtime))
    _reflection_commit(runtime, _reflection_proposal(runtime))
    _appraisal_commit(runtime, _appraisal_proposal(runtime))
    before = _rows(runtime)
    binding = runtime.ledger.binding_path.read_bytes()
    inode = runtime.ledger.database_path.stat().st_ino
    with runtime.shared() as snapshot:
        old = runtime.body_actions()
    assert runtime.upgrade_checkpoint_reason_policy() == snapshot
    after = _rows(runtime)
    for table in before:
        if table != "alembic_version":
            assert after[table] == before[table]
    assert after["alembic_version"] == (("policy_0008",),)
    assert runtime.ledger.binding_path.read_bytes() == binding
    assert runtime.ledger.database_path.stat().st_ino == inode
    with runtime.shared():
        assert runtime.body_actions() == old
        assert all(
            (
                runtime.body_policy_ready(),
                runtime.dialogue_policy_ready(),
                runtime.knowledge_policy_ready(),
                runtime.reflection_policy_ready(),
                runtime.appraisal_policy_ready(),
                runtime.checkpoint_reason_policy_ready(),
            )
        )
    sixth = _reason_commit(runtime, _proposal(runtime))
    assert sixth.result_body_revision == 6
    assert runtime.ledger.body_actions("satori")[:5] == old


def test_reason_append_requires_exclusive_lease(runtime: SourcePolicyRuntime) -> None:
    runtime.upgrade_checkpoint_reason_policy()
    proposal = _proposal(runtime)
    with pytest.raises(SourcePolicyError, match="source_policy_lease_required"):
        runtime.commit_checkpoint_reason_action(proposal)
    with (
        runtime.shared(),
        pytest.raises(SourcePolicyError, match="source_policy_exclusive_lease_required"),
    ):
        runtime.commit_checkpoint_reason_action(proposal)


@pytest.mark.parametrize("field", ["identity", "lineage"])
def test_foreign_scope_denies_before_append(runtime: SourcePolicyRuntime, field: str) -> None:
    runtime.upgrade_checkpoint_reason_policy()
    proposal = _proposal(runtime)
    scope = CheckpointReasonScope(
        "foreign" if field == "identity" else "satori",
        "foreign" if field == "lineage" else "lineage",
    )
    before = _rows(runtime)
    with pytest.raises(SourcePolicyError):
        _reason_commit(runtime, replace(proposal, scope=scope))
    assert _rows(runtime) == before


def test_replay_and_counters_cannot_silently_change_approval(runtime: SourcePolicyRuntime) -> None:
    runtime.upgrade_checkpoint_reason_policy()
    proposal = _proposal(runtime)
    _reason_commit(runtime, proposal)
    before = _rows(runtime)
    with pytest.raises(SourcePolicyError, match="body_erasure_request_conflict"):
        _reason_commit(runtime, replace(proposal, approval_digest="9" * 64))
    stale = replace(proposal, request_id="different-request")
    with pytest.raises(SourcePolicyError, match="body_revision_conflict"):
        _reason_commit(runtime, stale)
    assert _rows(runtime) == before


def test_global_request_namespace_and_metadata_budget_stay_shared(
    runtime: SourcePolicyRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime.upgrade_checkpoint_reason_policy()
    _exclude(runtime, _root(), "source-request")
    before = _rows(runtime)
    with pytest.raises(SourcePolicyError, match="body_erasure_request_conflict"):
        _reason_commit(runtime, _proposal(runtime, "source-request"))
    monkeypatch.setattr(ledger_module, "MAX_BODY_REFERENCES", 2)
    with pytest.raises(SourcePolicyError, match="policy_body_capacity_exceeded"):
        _reason_commit(runtime, _proposal(runtime))
    assert _rows(runtime) == before


def test_failed_v8_upgrade_preserves_v7_bytes(
    runtime: SourcePolicyRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = runtime.ledger.database_path.read_bytes()
    binding = runtime.ledger.binding_path.read_bytes()

    def fail(connection: Connection, *, revision: str = "policy_0002") -> None:
        upgrade_policy(connection, revision=revision)
        raise ValueError("synthetic failure")

    monkeypatch.setattr(ledger_module, "upgrade_policy", fail)
    with pytest.raises(SourcePolicyError, match="policy_migration_failed"):
        runtime.upgrade_checkpoint_reason_policy()
    assert runtime.ledger.database_path.read_bytes() == before
    assert runtime.ledger.binding_path.read_bytes() == binding


def test_identity_only_row_does_not_escape_floor_integrity_validation(
    runtime: SourcePolicyRuntime,
) -> None:
    runtime.upgrade_checkpoint_reason_policy()
    _reason_commit(runtime, _proposal(runtime))
    with sqlite3.connect(runtime.ledger.database_path) as connection:
        connection.execute("UPDATE policy_body_actions SET lineage_id='foreign'")
    restarted = SourcePolicyRuntime(runtime.ledger.directory, "satori")
    with pytest.raises(SourcePolicyError, match="policy_integrity_invalid"), restarted.shared():
        pass
