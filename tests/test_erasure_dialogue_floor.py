"""Dialogue approval extends the same private floor without rewriting episode history."""

import json
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection

from satori.domain.erasure_bodies import BodyErasureAction, body_actions_digest
from satori.domain.erasure_dialogue import (
    DialogueBodyAction,
    DialogueBodyProposal,
    DialogueBodyTarget,
    HistoricalEvidenceTable,
    HistoricalEvidenceWitness,
)
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.source_policy import body_policy
from satori.infrastructure.source_policy import ledger as ledger_module
from satori.infrastructure.source_policy.migrations import upgrade_policy
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime
from tests.test_erasure_body_floor import _body_commit, _body_proposal, _rows
from tests.test_erasure_intent_floor import _commit, _exclude, _proposal, _root


@pytest.fixture
def runtime(tmp_path: Path) -> SourcePolicyRuntime:
    result = SourcePolicyRuntime(tmp_path / "dialogue-policy", "satori")
    result.enroll(reviewed=True, new_installation=True, lineage_id="lineage")
    _exclude(result, _root(), "source-request")
    _commit(result, _proposal(result, _root(), "intent-request"))
    result.upgrade_body_policy()
    return result


def _dialogue_proposal(
    runtime: SourcePolicyRuntime, request_id: str = "dialogue-request"
) -> DialogueBodyProposal:
    with runtime.exclusive() as snapshot:
        root = _root()
        return DialogueBodyProposal(
            root,
            "intent-request",
            request_id,
            snapshot.generation,
            snapshot.erasure_revision,
            snapshot.body_revision,
            "a" * 64,
            "b" * 64,
            "c" * 64,
            (
                DialogueBodyTarget(
                    root.message_id,
                    1,
                    "d" * 64,
                    "e" * 64,
                    (
                        HistoricalEvidenceWitness(
                            HistoricalEvidenceTable.POSITION, "position-proof", "f" * 64
                        ),
                    ),
                ),
            ),
        )


def _dialogue_commit(
    runtime: SourcePolicyRuntime, proposal: DialogueBodyProposal
) -> DialogueBodyAction:
    with runtime.exclusive():
        return runtime.commit_dialogue_action(proposal)


def _ready(runtime: SourcePolicyRuntime) -> BodyErasureAction:
    action = _body_commit(runtime, _body_proposal(runtime))
    runtime.upgrade_dialogue_policy()
    return action


def test_dialogue_cannot_implicitly_upgrade_v3(runtime: SourcePolicyRuntime) -> None:
    _body_commit(runtime, _body_proposal(runtime))
    proposal = _dialogue_proposal(runtime)
    before = runtime.ledger.database_path.read_bytes()
    with runtime.shared():
        assert runtime.body_policy_ready()
        assert not runtime.dialogue_policy_ready()
    with pytest.raises(SourcePolicyError, match="policy_dialogue_schema_upgrade_required"):
        _dialogue_commit(runtime, proposal)
    assert runtime.ledger.database_path.read_bytes() == before


def test_upgrade_preserves_every_existing_episode_value_binding_inode_and_hash(
    runtime: SourcePolicyRuntime,
) -> None:
    episode = _body_commit(runtime, _body_proposal(runtime))
    before = _rows(runtime)
    binding = runtime.ledger.binding_path.read_bytes()
    inode = runtime.ledger.database_path.stat().st_ino
    with runtime.shared() as snapshot:
        original_digest = snapshot.body_head_digest
    assert runtime.upgrade_dialogue_policy() == snapshot
    after = _rows(runtime)
    assert after["policy_body_actions"] == before["policy_body_actions"]
    for table in before:
        if table != "alembic_version":
            assert after[table] == before[table]
    assert after["alembic_version"] == (("policy_0004",),)
    assert runtime.ledger.binding_path.read_bytes() == binding
    assert runtime.ledger.database_path.stat().st_ino == inode
    with runtime.shared() as upgraded:
        assert upgraded.body_head_digest == original_digest
        assert runtime.body_action(episode.proposal.request_id) == episode
        assert runtime.body_actions() == (episode,)
        assert runtime.dialogue_policy_ready()
    committed = runtime.ledger.database_path.read_bytes()
    runtime.upgrade_body_policy()
    runtime.upgrade_policy()
    runtime.upgrade_dialogue_policy()
    assert runtime.ledger.database_path.read_bytes() == committed


def test_dialogue_only_root_can_advance_shared_head_without_fabricated_episode(
    runtime: SourcePolicyRuntime,
) -> None:
    # Main selection proves the empty affected-episode set. Floor must not invent an episode.
    runtime.upgrade_dialogue_policy()
    proposal = _dialogue_proposal(runtime)
    action = _dialogue_commit(runtime, proposal)
    assert action.result_body_revision == 1
    with runtime.shared() as snapshot:
        assert snapshot.body_head_digest == body_actions_digest((action,))
        assert runtime.body_actions() == (action,)


def test_mixed_actions_keep_one_head_and_original_replays_across_restart(
    runtime: SourcePolicyRuntime,
) -> None:
    episode = _ready(runtime)
    proposal = _dialogue_proposal(runtime)
    dialogue = _dialogue_commit(runtime, proposal)
    assert dialogue.result_body_revision == 2
    before = runtime.ledger.database_path.read_bytes()
    assert _dialogue_commit(runtime, proposal) == dialogue
    assert _body_commit(runtime, episode.proposal) == episode
    assert runtime.ledger.database_path.read_bytes() == before
    restarted = SourcePolicyRuntime(runtime.ledger.directory, "satori")
    with restarted.shared() as snapshot:
        assert (snapshot.generation, snapshot.erasure_revision, snapshot.body_revision) == (1, 1, 2)
        assert snapshot.body_head_digest == body_actions_digest((episode, dialogue))
        assert restarted.body_actions() == (episode, dialogue)
        assert restarted.body_action(proposal.request_id) == dialogue
    next_episode = _body_commit(runtime, _body_proposal(runtime, "later-episode", memory_id="next"))
    assert next_episode.result_body_revision == 3
    assert _dialogue_commit(runtime, proposal) == dialogue
    with runtime.shared() as current:
        assert current.body_head_digest == body_actions_digest((episode, dialogue, next_episode))


@pytest.mark.parametrize("field", ["approval_digest", "root", "targets", "intent_request_id"])
def test_changed_dialogue_replay_denies_without_any_floor_mutation(
    runtime: SourcePolicyRuntime, field: str
) -> None:
    _ready(runtime)
    proposal = _dialogue_proposal(runtime)
    _dialogue_commit(runtime, proposal)
    if field == "targets":
        changed = replace(
            proposal, targets=(replace(proposal.targets[0], terminal_digest="9" * 64),)
        )
    elif field == "root":
        changed = replace(proposal, root=replace(proposal.root, counterparty_id="bob"))
    elif field == "intent_request_id":
        changed = replace(proposal, intent_request_id="other-intent")
    else:
        changed = replace(proposal, approval_digest="9" * 64)
    before = _rows(runtime)
    with pytest.raises(SourcePolicyError, match="body_erasure_request_conflict"):
        _dialogue_commit(runtime, changed)
    assert _rows(runtime) == before


@pytest.mark.parametrize("request_id", ["source-request", "intent-request", "body-request"])
def test_request_identity_cannot_cross_any_stream(
    runtime: SourcePolicyRuntime, request_id: str
) -> None:
    _ready(runtime)
    before = _rows(runtime)
    with pytest.raises(SourcePolicyError, match="body_erasure_request_conflict"):
        _dialogue_commit(runtime, _dialogue_proposal(runtime, request_id))
    assert _rows(runtime) == before


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("expected_source_generation", "source_generation_conflict"),
        ("expected_erasure_revision", "erasure_revision_conflict"),
        ("expected_body_revision", "body_revision_conflict"),
    ],
)
def test_all_three_expected_heads_remain_required(
    runtime: SourcePolicyRuntime, field: str, reason: str
) -> None:
    _ready(runtime)
    proposal = _dialogue_proposal(runtime)
    if field == "expected_source_generation":
        changed = replace(proposal, expected_source_generation=0)
    elif field == "expected_erasure_revision":
        changed = replace(proposal, expected_erasure_revision=0)
    else:
        changed = replace(proposal, expected_body_revision=0)
    before = _rows(runtime)
    with pytest.raises(SourcePolicyError, match=reason):
        _dialogue_commit(runtime, changed)
    assert _rows(runtime) == before


def test_witnesses_consume_reference_quota_before_append(
    runtime: SourcePolicyRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ready(runtime)
    # One episode target + one dialogue target + one witness is three references.
    monkeypatch.setattr(ledger_module, "MAX_BODY_REFERENCES", 2)
    before = _rows(runtime)
    with pytest.raises(SourcePolicyError, match="policy_body_capacity_exceeded"):
        _dialogue_commit(runtime, _dialogue_proposal(runtime))
    assert _rows(runtime) == before


def test_target_json_capacity_denies_before_head_write(
    runtime: SourcePolicyRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ready(runtime)
    proposal = _dialogue_proposal(runtime)
    monkeypatch.setattr(body_policy, "MAX_TARGET_JSON_BYTES", 300)
    before = _rows(runtime)
    with pytest.raises(SourcePolicyError, match="policy_body_capacity_exceeded"):
        _dialogue_commit(runtime, proposal)
    assert _rows(runtime) == before


def test_dialogue_commit_requires_exclusive_lease(runtime: SourcePolicyRuntime) -> None:
    _ready(runtime)
    proposal = _dialogue_proposal(runtime)
    with pytest.raises(SourcePolicyError, match="source_policy_lease_required"):
        runtime.commit_dialogue_action(proposal)
    with (
        runtime.shared(),
        pytest.raises(SourcePolicyError, match="source_policy_exclusive_lease_required"),
    ):
        runtime.commit_dialogue_action(proposal)


def test_v4_upgrade_failure_rolls_back_without_rehashing_old_evidence(
    runtime: SourcePolicyRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _body_commit(runtime, _body_proposal(runtime))
    before = runtime.ledger.database_path.read_bytes()
    binding = runtime.ledger.binding_path.read_bytes()

    def fail(connection: Connection, *, revision: str = "policy_0002") -> None:
        upgrade_policy(connection, revision=revision)
        raise ValueError("synthetic failure")

    monkeypatch.setattr(ledger_module, "upgrade_policy", fail)
    with pytest.raises(SourcePolicyError, match="policy_migration_failed"):
        runtime.upgrade_dialogue_policy()
    assert runtime.ledger.database_path.read_bytes() == before
    assert runtime.ledger.binding_path.read_bytes() == binding


@pytest.mark.parametrize("corruption", ["missing_prior_action", "unknown_table", "changed_scope"])
def test_corrupt_mixed_receipts_fail_before_snapshot_publication(
    runtime: SourcePolicyRuntime, corruption: str
) -> None:
    _ready(runtime)
    _dialogue_commit(runtime, _dialogue_proposal(runtime))
    with sqlite3.connect(runtime.ledger.database_path) as connection:
        if corruption == "missing_prior_action":
            connection.execute("DELETE FROM policy_body_actions WHERE request_id='body-request'")
            # The head stays 2: removing an earlier approval must not silently rewrite history.
            connection.execute("UPDATE policy_lineages SET body_revision=2")
        elif corruption == "changed_scope":
            connection.execute(
                "UPDATE policy_body_actions SET counterparty_id='bob' "
                "WHERE request_id='dialogue-request'"
            )
        else:
            raw = json.loads(
                connection.execute(
                    "SELECT targets FROM policy_body_actions WHERE request_id='dialogue-request'"
                ).fetchone()[0]
            )
            raw[0]["witnesses"][0]["table"] = "arbitrary_table"
            connection.execute(
                "UPDATE policy_body_actions SET targets=? WHERE request_id='dialogue-request'",
                (json.dumps(raw),),
            )
    restarted = SourcePolicyRuntime(runtime.ledger.directory, "satori")
    with pytest.raises(SourcePolicyError, match="policy_integrity_invalid"), restarted.shared():
        pass


def test_v4_downgrade_never_discards_mixed_approvals(runtime: SourcePolicyRuntime) -> None:
    episode = _ready(runtime)
    dialogue = _dialogue_commit(runtime, _dialogue_proposal(runtime))
    configuration = Config()
    configuration.set_main_option(
        "script_location", str(Path(ledger_module.__file__).with_name("migrations"))
    )
    engine = create_engine(f"sqlite+pysqlite:///{runtime.ledger.database_path}")
    try:
        with runtime.exclusive(), engine.begin() as connection:
            configuration.attributes["connection"] = connection
            with pytest.raises(RuntimeError, match="policy_floor_downgrade_forbidden"):
                command.downgrade(configuration, "policy_0003")
    finally:
        engine.dispose()
    with runtime.shared():
        assert runtime.body_actions() == (episode, dialogue)


@pytest.mark.parametrize("after_commit", [False, True])
def test_uncertain_dialogue_commit_resolves_only_original_approved_envelope(
    runtime: SourcePolicyRuntime, monkeypatch: pytest.MonkeyPatch, after_commit: bool
) -> None:
    episode = _ready(runtime)
    proposal = _dialogue_proposal(runtime)
    original_connect = sqlite3.connect
    remaining = [True]

    class FaultyConnection(sqlite3.Connection):
        def commit(self) -> None:
            if remaining[0]:
                remaining[0] = False
                if after_commit:
                    super().commit()
                raise sqlite3.OperationalError("synthetic private content")
            super().commit()

    def connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        kwargs["factory"] = FaultyConnection
        return cast(sqlite3.Connection, original_connect(*args, **kwargs))  # type: ignore[call-overload]

    monkeypatch.setattr(sqlite3, "connect", connect)
    if after_commit:
        action = _dialogue_commit(runtime, proposal)
    else:
        with pytest.raises(SourcePolicyError, match="policy_storage_unavailable") as failure:
            _dialogue_commit(runtime, proposal)
        assert "private content" not in str(failure.value)
        with runtime.shared() as snapshot:
            assert snapshot.body_revision == 1
            assert runtime.body_actions() == (episode,)
            assert runtime.body_action(proposal.request_id) is None
        action = _dialogue_commit(runtime, proposal)
    with runtime.shared() as snapshot:
        assert snapshot.body_revision == 2
        assert snapshot.body_head_digest == body_actions_digest((episode, action))


def test_wrong_typed_commit_port_denies_before_any_durable_action(
    runtime: SourcePolicyRuntime,
) -> None:
    _ready(runtime)
    before = _rows(runtime)
    dialogue = _dialogue_proposal(runtime)
    episode = _body_proposal(runtime, "new-episode")
    with runtime.exclusive():
        with pytest.raises(SourcePolicyError, match="invalid_body_proposal"):
            runtime.ledger.commit_body_action(dialogue, trace_id="test")  # type: ignore[arg-type]
        with pytest.raises(SourcePolicyError, match="invalid_dialogue_proposal"):
            runtime.ledger.commit_dialogue_action(episode, trace_id="test")  # type: ignore[arg-type]
    assert _rows(runtime) == before


def test_deeply_nested_floor_targets_never_escape_typed_integrity_rejection(
    runtime: SourcePolicyRuntime,
) -> None:
    _ready(runtime)
    _dialogue_commit(runtime, _dialogue_proposal(runtime))
    depth = sys.getrecursionlimit() + 100
    raw = "[" * depth + '"synthetic private content"' + "]" * depth
    with sqlite3.connect(runtime.ledger.database_path) as connection:
        connection.execute(
            "UPDATE policy_body_actions SET targets=? WHERE request_id='dialogue-request'",
            (raw,),
        )
    restarted = SourcePolicyRuntime(runtime.ledger.directory, "satori")
    with pytest.raises(SourcePolicyError, match=r"^policy_integrity_invalid$"), restarted.shared():
        pass
