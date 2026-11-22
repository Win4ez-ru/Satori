"""Durable bounded body-action floor; no main bodies, providers, or physical claims."""

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection

from satori.domain.erasure_bodies import (
    BodyErasureAction,
    BodyErasureProposal,
    BodyErasureTarget,
    body_actions_digest,
)
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.source_policy import ledger as ledger_module
from satori.infrastructure.source_policy.migrations import upgrade_policy
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime
from tests.test_erasure_intent_floor import _commit, _exclude, _proposal, _root, _source_rows


@pytest.fixture
def runtime(tmp_path: Path) -> SourcePolicyRuntime:
    result = SourcePolicyRuntime(tmp_path / "body-policy", "satori")
    result.enroll(reviewed=True, new_installation=True, lineage_id="lineage")
    _exclude(result, _root(), "source-request")
    _commit(result, _proposal(result, _root(), "intent-request"))
    return result


def _body_proposal(
    runtime: SourcePolicyRuntime,
    request_id: str = "body-request",
    *,
    memory_id: str = "episode-one",
) -> BodyErasureProposal:
    with runtime.exclusive() as snapshot:
        return BodyErasureProposal(
            _root(),
            "intent-request",
            request_id,
            snapshot.generation,
            snapshot.erasure_revision,
            snapshot.body_revision,
            "a" * 64,
            "b" * 64,
            "c" * 64,
            (BodyErasureTarget(memory_id, 1, "d" * 64, "e" * 64),),
        )


def _body_commit(runtime: SourcePolicyRuntime, proposal: BodyErasureProposal) -> BodyErasureAction:
    with runtime.exclusive():
        return runtime.commit_body_action(proposal)


def _rows(runtime: SourcePolicyRuntime) -> dict[str, tuple[tuple[object, ...], ...]]:
    with sqlite3.connect(runtime.ledger.database_path) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name"
        ).fetchall()
        return {
            table: tuple(
                tuple(row) for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY 1')
            )
            for (table,) in tables
        }


def test_policy_v2_body_reads_are_empty_and_commit_never_upgrades(
    runtime: SourcePolicyRuntime,
) -> None:
    before = runtime.ledger.database_path.read_bytes()
    with runtime.shared() as snapshot:
        assert snapshot.body_revision == 0
        assert snapshot.body_head_digest == ""
        assert runtime.body_actions() == ()
        assert runtime.body_action("missing-body-request") is None
    with pytest.raises(SourcePolicyError, match="policy_body_schema_upgrade_required"):
        _body_commit(runtime, _body_proposal(runtime))
    assert runtime.ledger.database_path.read_bytes() == before


def test_explicit_v3_upgrade_preserves_binding_history_and_default_upgrade_noop(
    runtime: SourcePolicyRuntime,
) -> None:
    before = _rows(runtime)
    binding = runtime.ledger.binding_path.read_bytes()
    inodes = tuple((path.stat().st_dev, path.stat().st_ino) for path in runtime.reserved_paths[:3])
    with runtime.shared() as old_snapshot:
        pass
    assert runtime.upgrade_body_policy() == old_snapshot
    after = _rows(runtime)
    for table, rows in before.items():
        if table == "policy_lineages":
            assert after[table] == tuple((*row, 0) for row in rows)
        elif table == "alembic_version":
            assert after[table] == (("policy_0003",),)
        else:
            assert after[table] == rows
    assert after["policy_body_actions"] == ()
    assert runtime.ledger.binding_path.read_bytes() == binding
    assert (
        tuple((path.stat().st_dev, path.stat().st_ino) for path in runtime.reserved_paths[:3])
        == inodes
    )
    after_bytes = runtime.ledger.database_path.read_bytes()
    assert runtime.upgrade_body_policy() == old_snapshot
    assert runtime.upgrade_policy() == old_snapshot
    assert runtime.ledger.database_path.read_bytes() == after_bytes


def test_body_action_is_exact_private_durable_replay_with_independent_head(
    runtime: SourcePolicyRuntime,
) -> None:
    runtime.upgrade_body_policy()
    before = _source_rows(runtime)
    proposal = _body_proposal(runtime)
    action = _body_commit(runtime, proposal)
    assert action.matches(proposal)
    assert action.result_body_revision == 1
    committed = runtime.ledger.database_path.read_bytes()
    assert _body_commit(runtime, proposal) == action
    assert runtime.ledger.database_path.read_bytes() == committed
    assert _source_rows(runtime) == before
    with runtime.shared() as snapshot:
        assert (snapshot.generation, snapshot.erasure_revision, snapshot.body_revision) == (1, 1, 1)
        assert snapshot.body_head_digest == body_actions_digest((action,))
        assert runtime.body_actions() == (action,)
        assert runtime.body_action(proposal.request_id) == action
    restarted = SourcePolicyRuntime(runtime.ledger.directory, "satori")
    with restarted.shared() as snapshot:
        assert snapshot.body_head_digest == body_actions_digest((action,))
        assert restarted.body_actions() == (action,)


@pytest.mark.parametrize(
    "field",
    ["approval_digest", "store_digest", "schema_digest", "intent_request_id", "root", "targets"],
)
def test_same_request_changed_review_or_private_targets_rejects(
    runtime: SourcePolicyRuntime, field: str
) -> None:
    runtime.upgrade_body_policy()
    proposal = _body_proposal(runtime)
    _body_commit(runtime, proposal)
    if field == "approval_digest":
        conflicting = replace(proposal, approval_digest="f" * 64)
    elif field == "store_digest":
        conflicting = replace(proposal, store_digest="f" * 64)
    elif field == "schema_digest":
        conflicting = replace(proposal, schema_digest="f" * 64)
    elif field == "intent_request_id":
        conflicting = replace(proposal, intent_request_id="foreign-intent")
    elif field == "root":
        conflicting = replace(proposal, root=replace(proposal.root, counterparty_id="bob"))
    else:
        conflicting = replace(
            proposal, targets=(replace(proposal.targets[0], terminal_digest="f" * 64),)
        )
    before = _rows(runtime)
    with pytest.raises(SourcePolicyError, match="body_erasure_request_conflict"):
        _body_commit(runtime, conflicting)
    assert _rows(runtime) == before


def test_body_operations_require_lease_and_exclusive_commit(runtime: SourcePolicyRuntime) -> None:
    runtime.upgrade_body_policy()
    proposal = _body_proposal(runtime)
    for operation in (
        lambda: runtime.body_action("request"),
        runtime.body_actions,
        lambda: runtime.commit_body_action(proposal),
    ):
        with pytest.raises(SourcePolicyError, match="source_policy_lease_required"):
            operation()
    with (
        runtime.shared(),
        pytest.raises(SourcePolicyError, match="source_policy_exclusive_lease_required"),
    ):
        runtime.commit_body_action(proposal)
    for lease in (runtime.shared, runtime.exclusive):
        with (
            lease(),
            pytest.raises(SourcePolicyError, match="policy_migration_requires_outer_lease"),
        ):
            runtime.upgrade_body_policy()


@pytest.mark.parametrize(
    ("head", "reason"),
    [
        ("expected_source_generation", "source_generation_conflict"),
        ("expected_erasure_revision", "erasure_revision_conflict"),
        ("expected_body_revision", "body_revision_conflict"),
    ],
)
def test_all_three_heads_revalidated_before_floor_append(
    runtime: SourcePolicyRuntime, head: str, reason: str
) -> None:
    runtime.upgrade_body_policy()
    _body_commit(runtime, _body_proposal(runtime))
    fresh = _body_proposal(runtime, "second-request", memory_id="episode-two")
    if head == "expected_source_generation":
        stale = replace(fresh, expected_source_generation=0)
    elif head == "expected_erasure_revision":
        stale = replace(fresh, expected_erasure_revision=0)
    else:
        stale = replace(fresh, expected_body_revision=0)
    before = _rows(runtime)
    with pytest.raises(SourcePolicyError, match=reason):
        _body_commit(runtime, stale)
    assert _rows(runtime) == before


@pytest.mark.parametrize("request_id", ["source-request", "intent-request"])
def test_body_request_namespace_cannot_reuse_other_stream_receipts(
    runtime: SourcePolicyRuntime, request_id: str
) -> None:
    runtime.upgrade_body_policy()
    before = _rows(runtime)
    with pytest.raises(SourcePolicyError, match="body_erasure_request_conflict"):
        _body_commit(runtime, _body_proposal(runtime, request_id))
    assert _rows(runtime) == before


def test_source_and_intent_streams_cannot_reuse_body_request(runtime: SourcePolicyRuntime) -> None:
    runtime.upgrade_body_policy()
    proposal = _body_proposal(runtime)
    _body_commit(runtime, proposal)
    before = _rows(runtime)
    with runtime.exclusive() as snapshot:
        with pytest.raises(SourcePolicyError, match="source_request_conflict"):
            runtime.commit_exclusion(
                _root("second-root"),
                expected_generation=snapshot.generation,
                request_id=proposal.request_id,
                preview_digest="f" * 64,
                trace_id="source-collision",
            )
        with pytest.raises(SourcePolicyError, match="erasure_request_conflict"):
            runtime.commit_erasure_intent(
                replace(_proposal(runtime, _root()), request_id=proposal.request_id)
            )
    assert _rows(runtime) == before


@pytest.mark.parametrize("invalid", ["missing_intent", "foreign_intent_scope"])
def test_body_requires_existing_exact_root_intent(
    runtime: SourcePolicyRuntime, invalid: str
) -> None:
    runtime.upgrade_body_policy()
    proposal = _body_proposal(runtime)
    if invalid == "missing_intent":
        proposal = replace(proposal, intent_request_id="missing-intent")
        reason = "body_erasure_intent_required"
    else:
        proposal = replace(proposal, root=replace(proposal.root, counterparty_id="bob"))
        reason = "body_erasure_intent_scope_mismatch"
    before = _rows(runtime)
    with pytest.raises(SourcePolicyError, match=reason):
        _body_commit(runtime, proposal)
    assert _rows(runtime) == before


@pytest.mark.parametrize("capacity", ["actions", "selectors"])
def test_action_and_total_selector_cap_deny_without_revision_increment(
    runtime: SourcePolicyRuntime, monkeypatch: pytest.MonkeyPatch, capacity: str
) -> None:
    runtime.upgrade_body_policy()
    _body_commit(runtime, _body_proposal(runtime))
    monkeypatch.setattr(
        ledger_module, "MAX_BODY_ACTIONS" if capacity == "actions" else "MAX_BODY_REFERENCES", 1
    )
    before = _rows(runtime)
    with pytest.raises(SourcePolicyError, match="policy_body_capacity_exceeded"):
        _body_commit(runtime, _body_proposal(runtime, "second-request", memory_id="episode-two"))
    assert _rows(runtime) == before


@pytest.mark.parametrize("after_commit", [False, True])
def test_unknown_commit_outcome_resolves_only_exact_original_request(
    runtime: SourcePolicyRuntime, monkeypatch: pytest.MonkeyPatch, after_commit: bool
) -> None:
    runtime.upgrade_body_policy()
    proposal = _body_proposal(runtime)
    original_connect = sqlite3.connect
    remaining = [True]

    class FaultyConnection(sqlite3.Connection):
        def commit(self) -> None:
            if remaining[0]:
                remaining[0] = False
                if after_commit:
                    super().commit()
                raise sqlite3.OperationalError("synthetic private data")
            super().commit()

    def connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        kwargs["factory"] = FaultyConnection
        return cast(sqlite3.Connection, original_connect(*args, **kwargs))  # type: ignore[call-overload]

    monkeypatch.setattr(sqlite3, "connect", connect)
    if after_commit:
        action = _body_commit(runtime, proposal)
    else:
        with pytest.raises(SourcePolicyError, match="policy_storage_unavailable") as failure:
            _body_commit(runtime, proposal)
        assert "private data" not in str(failure.value)
        with runtime.shared() as snapshot:
            assert snapshot.body_revision == 0
            assert runtime.body_action(proposal.request_id) is None
        action = _body_commit(runtime, proposal)
    with runtime.shared() as snapshot:
        assert snapshot.body_revision == 1
        assert snapshot.body_head_digest == body_actions_digest((action,))


@pytest.mark.parametrize(
    "corruption",
    [
        "head",
        "target_digest",
        "terminal_digest",
        "target_shape",
        "target_capacity",
        "root",
        "intent",
    ],
)
def test_corrupt_body_receipt_denies_all_floor_snapshots(
    runtime: SourcePolicyRuntime, corruption: str
) -> None:
    runtime.upgrade_body_policy()
    _body_commit(runtime, _body_proposal(runtime))
    with sqlite3.connect(runtime.ledger.database_path) as connection:
        if corruption == "head":
            connection.execute("UPDATE policy_lineages SET body_revision=2")
        elif corruption == "root":
            connection.execute("UPDATE policy_body_actions SET counterparty_id='bob'")
        elif corruption == "intent":
            connection.execute("UPDATE policy_body_actions SET intent_request_id='missing-intent'")
        else:
            targets = [
                {
                    "memory_id": "episode-one",
                    "aggregate_version": 1,
                    "body_digest": "d" * 64,
                    "terminal_digest": "e" * 64,
                }
            ]
            if corruption == "target_digest":
                targets[0]["body_digest"] = "z" * 64
            elif corruption == "terminal_digest":
                targets[0]["terminal_digest"] = "z" * 64
            elif corruption == "target_shape":
                targets[0]["raw_text"] = "unexpected source content"
            else:
                targets = [
                    {
                        "memory_id": f"episode-{i:03}",
                        "aggregate_version": 1,
                        "body_digest": "d" * 64,
                        "terminal_digest": "e" * 64,
                    }
                    for i in range(257)
                ]
            connection.execute("UPDATE policy_body_actions SET targets=?", (json.dumps(targets),))
    with pytest.raises(SourcePolicyError, match="policy_integrity_invalid"), runtime.shared():
        pytest.fail("corrupt body action must not publish a permissive snapshot")


def test_missing_floor_and_foreign_identity_upgrade_never_create_or_migrate(
    tmp_path: Path, runtime: SourcePolicyRuntime
) -> None:
    missing = SourcePolicyRuntime(tmp_path / "absent", "satori")
    with pytest.raises(SourcePolicyError, match="policy_installation_binding_unavailable"):
        missing.upgrade_body_policy()
    assert not missing.ledger.directory.exists()
    before = runtime.ledger.database_path.read_bytes()
    foreign = SourcePolicyRuntime(runtime.ledger.directory, "not-enrolled")
    with pytest.raises(SourcePolicyError, match="policy_lineage_not_enrolled"):
        foreign.upgrade_body_policy()
    assert runtime.ledger.database_path.read_bytes() == before


def test_unknown_old_schema_denies_body_upgrade_before_ddl(runtime: SourcePolicyRuntime) -> None:
    with sqlite3.connect(runtime.ledger.database_path) as connection:
        connection.execute("CREATE TABLE unknown_payload(value TEXT)")
    before = runtime.ledger.database_path.read_bytes()
    with pytest.raises(SourcePolicyError, match="policy_schema_invalid"):
        runtime.upgrade_body_policy()
    assert runtime.ledger.database_path.read_bytes() == before


def test_body_upgrade_failure_rolls_back_and_preserves_fixed_floor(
    runtime: SourcePolicyRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = runtime.ledger.database_path.read_bytes()
    binding = runtime.ledger.binding_path.read_bytes()

    def fail_upgrade(connection: Connection, *, revision: str = "policy_0002") -> None:
        upgrade_policy(connection, revision=revision)
        raise ValueError("synthetic private data")

    monkeypatch.setattr(ledger_module, "upgrade_policy", fail_upgrade)
    with pytest.raises(SourcePolicyError, match="policy_migration_failed"):
        runtime.upgrade_body_policy()
    assert runtime.ledger.database_path.read_bytes() == before
    assert runtime.ledger.binding_path.read_bytes() == binding


def test_v3_downgrade_never_discards_body_action(runtime: SourcePolicyRuntime) -> None:
    runtime.upgrade_body_policy()
    action = _body_commit(runtime, _body_proposal(runtime))
    configuration = Config()
    configuration.set_main_option(
        "script_location", str(Path(ledger_module.__file__).with_name("migrations"))
    )
    engine = create_engine(f"sqlite+pysqlite:///{runtime.ledger.database_path}")
    try:
        with runtime.exclusive(), engine.begin() as connection:
            configuration.attributes["connection"] = connection
            with pytest.raises(RuntimeError, match="policy_floor_downgrade_forbidden"):
                command.downgrade(configuration, "policy_0002")
    finally:
        engine.dispose()
    with runtime.shared() as snapshot:
        assert snapshot.body_revision == 1
        assert runtime.body_action(action.proposal.request_id) == action
