"""Independent Appraisal actions preserve old envelopes and the single monotonic head."""

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy.engine import Connection

from satori.domain.erasure_appraisal import (
    AppraisalBodyAction,
    AppraisalBodyProposal,
    AppraisalBodyTarget,
)
from satori.domain.erasure_bodies import body_actions_digest, body_reference_count
from satori.domain.erasure_knowledge import KnowledgeBodyKind, KnowledgeBodyTarget
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.source_policy import ledger as ledger_module
from satori.infrastructure.source_policy.migrations import upgrade_policy
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime
from tests.test_erasure_body_floor import _body_commit, _body_proposal, _rows
from tests.test_erasure_dialogue_floor import _dialogue_commit, _dialogue_proposal
from tests.test_erasure_intent_floor import _commit, _exclude, _proposal, _root
from tests.test_erasure_knowledge_floor import _knowledge_commit, _knowledge_proposal
from tests.test_erasure_reflection_floor import _reflection_commit, _reflection_proposal


@pytest.fixture
def runtime(tmp_path: Path) -> SourcePolicyRuntime:
    result = SourcePolicyRuntime(tmp_path / "appraisal-floor", "satori")
    result.enroll(reviewed=True, new_installation=True, lineage_id="lineage")
    _exclude(result, _root(), "source-request")
    _commit(result, _proposal(result, _root(), "intent-request"))
    result.upgrade_body_policy()
    _body_commit(result, _body_proposal(result))
    result.upgrade_dialogue_policy()
    _dialogue_commit(result, _dialogue_proposal(result))
    result.upgrade_knowledge_policy()
    _knowledge_commit(result, _knowledge_proposal(result))
    result.upgrade_reflection_policy()
    _reflection_commit(result, _reflection_proposal(result))
    return result


def _appraisal_proposal(
    runtime: SourcePolicyRuntime, request_id: str = "appraisal-request"
) -> AppraisalBodyProposal:
    with runtime.exclusive() as snapshot:
        return AppraisalBodyProposal(
            _root(),
            "intent-request",
            request_id,
            snapshot.generation,
            snapshot.erasure_revision,
            snapshot.body_revision,
            "a" * 64,
            "b" * 64,
            "c" * 64,
            (
                AppraisalBodyTarget(
                    "transition", "input", "audit", 1, "d" * 64, "e" * 64, 3, "f" * 64
                ),
            ),
        )


def _appraisal_commit(
    runtime: SourcePolicyRuntime, proposal: AppraisalBodyProposal
) -> AppraisalBodyAction:
    with runtime.exclusive():
        return runtime.commit_appraisal_action(proposal)


def test_appraisal_operation_requires_explicit_v7_upgrade(runtime: SourcePolicyRuntime) -> None:
    proposal = _appraisal_proposal(runtime)
    before = runtime.ledger.database_path.read_bytes()
    with pytest.raises(SourcePolicyError, match="policy_appraisal_schema_upgrade_required"):
        _appraisal_commit(runtime, proposal)
    assert runtime.ledger.database_path.read_bytes() == before


def test_v7_preserves_all_old_values_head_inodes_and_capabilities(
    runtime: SourcePolicyRuntime,
) -> None:
    before = _rows(runtime)
    binding = runtime.ledger.binding_path.read_bytes()
    inode = runtime.ledger.database_path.stat().st_ino
    with runtime.shared() as original:
        old_actions = runtime.body_actions()
    assert runtime.upgrade_appraisal_policy() == original
    after = _rows(runtime)
    assert after["alembic_version"] == (("policy_0007",),)
    for name in before:
        if name != "alembic_version":
            assert after[name] == before[name]
    assert runtime.ledger.binding_path.read_bytes() == binding
    assert runtime.ledger.database_path.stat().st_ino == inode
    with runtime.shared() as snapshot:
        assert runtime.body_actions() == old_actions
        assert snapshot.body_head_digest == original.body_head_digest
        assert runtime.body_policy_ready()
        assert runtime.dialogue_policy_ready()
        assert runtime.knowledge_policy_ready()
        assert runtime.reflection_policy_ready()
        assert runtime.appraisal_policy_ready()
    committed = runtime.ledger.database_path.read_bytes()
    runtime.upgrade_policy()
    runtime.upgrade_body_policy()
    runtime.upgrade_dialogue_policy()
    runtime.upgrade_knowledge_policy()
    runtime.upgrade_reflection_policy()
    runtime.upgrade_appraisal_policy()
    assert runtime.ledger.database_path.read_bytes() == committed


def test_fifth_action_replay_restart_and_old_owners_keep_same_head(
    runtime: SourcePolicyRuntime,
) -> None:
    runtime.upgrade_appraisal_policy()
    proposal = _appraisal_proposal(runtime)
    action = _appraisal_commit(runtime, proposal)
    before = runtime.ledger.database_path.read_bytes()
    assert _appraisal_commit(runtime, proposal) == action
    restarted = SourcePolicyRuntime(runtime.ledger.directory, "satori")
    with restarted.shared() as snapshot:
        actions = restarted.body_actions()
        assert (snapshot.generation, snapshot.erasure_revision, snapshot.body_revision) == (1, 1, 5)
        assert snapshot.body_head_digest == body_actions_digest(actions)
        assert body_reference_count(actions) == 16
        assert restarted.body_action(proposal.request_id) == action
    assert runtime.ledger.database_path.read_bytes() == before
    old = runtime.ledger.body_actions("satori")
    episode = _body_commit(runtime, _body_proposal(runtime, "later-episode", memory_id="later"))
    assert episode.result_body_revision == 6
    dialogue = _dialogue_commit(runtime, _dialogue_proposal(runtime, "later-dialogue"))
    assert dialogue.result_body_revision == 7
    knowledge = replace(
        _knowledge_proposal(runtime, "later-knowledge"),
        targets=(
            KnowledgeBodyTarget(KnowledgeBodyKind.SEMANTIC_CLAIM, "later", 1, "d" * 64, "e" * 64),
        ),
    )
    assert _knowledge_commit(runtime, knowledge).result_body_revision == 8
    reflection = _reflection_commit(runtime, _reflection_proposal(runtime, "later-reflection"))
    assert reflection.result_body_revision == 9
    for prior in old:
        assert runtime.ledger.body_action(prior.proposal.request_id) == prior


@pytest.mark.parametrize(
    "request_id",
    [
        "source-request",
        "intent-request",
        "body-request",
        "dialogue-request",
        "knowledge-request",
        "reflection-request",
    ],
)
def test_request_id_cannot_cross_operations(runtime: SourcePolicyRuntime, request_id: str) -> None:
    runtime.upgrade_appraisal_policy()
    before = _rows(runtime)
    with pytest.raises(SourcePolicyError, match="body_erasure_request_conflict"):
        _appraisal_commit(runtime, _appraisal_proposal(runtime, request_id))
    assert _rows(runtime) == before


def test_reviewed_metadata_counts_consume_whole_head_quota_before_write(
    runtime: SourcePolicyRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime.upgrade_appraisal_policy()
    proposal = _appraisal_proposal(runtime)
    monkeypatch.setattr(ledger_module, "MAX_BODY_REFERENCES", 15)
    before = _rows(runtime)
    with pytest.raises(SourcePolicyError, match="policy_body_capacity_exceeded"):
        _appraisal_commit(runtime, proposal)
    assert _rows(runtime) == before


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("source", "source_generation_conflict"),
        ("intent", "erasure_revision_conflict"),
        ("body", "body_revision_conflict"),
    ],
)
def test_all_expected_counters_are_required_before_fifth_action_append(
    runtime: SourcePolicyRuntime, field: str, reason: str
) -> None:
    runtime.upgrade_appraisal_policy()
    proposal = _appraisal_proposal(runtime)
    if field == "source":
        proposal = replace(proposal, expected_source_generation=0)
    elif field == "intent":
        proposal = replace(proposal, expected_erasure_revision=0)
    else:
        proposal = replace(proposal, expected_body_revision=0)
    before = _rows(runtime)
    with pytest.raises(SourcePolicyError, match=reason):
        _appraisal_commit(runtime, proposal)
    assert _rows(runtime) == before


def test_replay_cannot_change_reviewed_input_or_audit_pair(
    runtime: SourcePolicyRuntime,
) -> None:
    runtime.upgrade_appraisal_policy()
    proposal = _appraisal_proposal(runtime)
    _appraisal_commit(runtime, proposal)
    before = _rows(runtime)
    for target in (
        replace(proposal.targets[0], source_input_id="different-input"),
        replace(proposal.targets[0], audit_event_id="different-audit"),
        replace(proposal.targets[0], input_closure_digest="9" * 64),
    ):
        with pytest.raises(SourcePolicyError, match="body_erasure_request_conflict"):
            _appraisal_commit(runtime, replace(proposal, targets=(target,)))
    assert _rows(runtime) == before


def test_appraisal_commit_requires_exclusive_lease(runtime: SourcePolicyRuntime) -> None:
    runtime.upgrade_appraisal_policy()
    proposal = _appraisal_proposal(runtime)
    with pytest.raises(SourcePolicyError, match="source_policy_lease_required"):
        runtime.commit_appraisal_action(proposal)
    with (
        runtime.shared(),
        pytest.raises(SourcePolicyError, match="source_policy_exclusive_lease_required"),
    ):
        runtime.commit_appraisal_action(proposal)


def test_failed_v7_upgrade_preserves_v6_bytes_and_binding(
    runtime: SourcePolicyRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = runtime.ledger.database_path.read_bytes()
    binding = runtime.ledger.binding_path.read_bytes()

    def fail(connection: Connection, *, revision: str = "policy_0002") -> None:
        upgrade_policy(connection, revision=revision)
        raise ValueError("synthetic private failure")

    monkeypatch.setattr(ledger_module, "upgrade_policy", fail)
    with pytest.raises(SourcePolicyError, match="policy_migration_failed"):
        runtime.upgrade_appraisal_policy()
    assert runtime.ledger.database_path.read_bytes() == before
    assert runtime.ledger.binding_path.read_bytes() == binding


def test_corrupt_reviewed_count_fails_closed_on_restart(runtime: SourcePolicyRuntime) -> None:
    runtime.upgrade_appraisal_policy()
    _appraisal_commit(runtime, _appraisal_proposal(runtime))
    with sqlite3.connect(runtime.ledger.database_path) as connection:
        raw = json.loads(
            connection.execute(
                "SELECT targets FROM policy_body_actions WHERE request_id='appraisal-request'"
            ).fetchone()[0]
        )
        raw[0]["metadata_reference_count"] = True
        connection.execute(
            "UPDATE policy_body_actions SET targets=? WHERE request_id='appraisal-request'",
            (json.dumps(raw),),
        )
    restarted = SourcePolicyRuntime(runtime.ledger.directory, "satori")
    with pytest.raises(SourcePolicyError, match="policy_integrity_invalid"), restarted.shared():
        pass
