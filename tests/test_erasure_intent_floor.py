"""Synthetic immutable intent/floor migration tests; no main payloads or providers."""

import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection

from satori.domain.erasure_intent import ErasureIntent, ErasureIntentProposal
from satori.domain.source_policy import CanonicalRootRef, SourcePolicyError
from satori.infrastructure.source_policy import ledger as ledger_module
from satori.infrastructure.source_policy.migrations import upgrade_policy
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime


@pytest.fixture
def runtime(tmp_path: Path) -> SourcePolicyRuntime:
    result = SourcePolicyRuntime(tmp_path / "policy", "satori")
    result.enroll(reviewed=True, new_installation=True, lineage_id="lineage")
    return result


def _root(message_id: str = "canonical-user-one") -> CanonicalRootRef:
    return CanonicalRootRef("lineage", "satori", "alice", "session", message_id, message_id)


def _exclude(runtime: SourcePolicyRuntime, root: CanonicalRootRef, request_id: str) -> None:
    with runtime.exclusive() as snapshot:
        runtime.commit_exclusion(
            root,
            expected_generation=snapshot.generation,
            request_id=request_id,
            preview_digest="e" * 64,
            trace_id="synthetic-exclusion",
        )


def _proposal(
    runtime: SourcePolicyRuntime,
    root: CanonicalRootRef,
    request_id: str = "erasure-request",
) -> ErasureIntentProposal:
    with runtime.shared() as snapshot:
        return ErasureIntentProposal(
            root,
            request_id,
            snapshot.generation,
            snapshot.erasure_revision,
            "a" * 64,
            "b" * 64,
            "0017_erasure_intent",
            "c" * 64,
            "d" * 64,
            "f" * 64,
            "blocked",
        )


def _commit(runtime: SourcePolicyRuntime, proposal: ErasureIntentProposal) -> ErasureIntent:
    with runtime.exclusive():
        return runtime.commit_erasure_intent(proposal)


def _old_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SourcePolicyRuntime:
    def old_initialization(connection: Connection, *, revision: str | None = None) -> None:
        upgrade_policy(connection, revision=revision or "policy_0001")

    with monkeypatch.context() as context:
        context.setattr(ledger_module, "upgrade_policy", old_initialization)
        result = SourcePolicyRuntime(tmp_path / "old-policy", "satori")
        result.enroll(reviewed=True, new_installation=True, lineage_id="lineage")
    return result


def _source_rows(runtime: SourcePolicyRuntime) -> tuple[tuple[tuple[object, ...], ...], ...]:
    with sqlite3.connect(runtime.ledger.database_path) as connection:
        return tuple(
            tuple(tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1"))
            for table in ("policy_installation", "policy_exclusions", "policy_receipts")
        )


def test_new_installation_uses_latest_floor_with_independent_erasure_head(
    runtime: SourcePolicyRuntime,
) -> None:
    with runtime.shared() as snapshot:
        assert (snapshot.generation, snapshot.erasure_revision) == (0, 0)
        assert runtime.erasure_intents() == ()
    with sqlite3.connect(runtime.ledger.database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "policy_0002",
        )


def test_intent_reads_and_upgrade_never_create_missing_floor(tmp_path: Path) -> None:
    runtime = SourcePolicyRuntime(tmp_path / "missing", "satori")
    with pytest.raises(SourcePolicyError, match="policy_installation_binding_unavailable"):
        runtime.upgrade_policy()
    with (
        pytest.raises(SourcePolicyError, match="policy_installation_binding_unavailable"),
        runtime.shared(),
    ):
        runtime.erasure_intents()
    assert not runtime.ledger.directory.exists()


def test_intent_operations_require_lease_and_exclusive_commit(runtime: SourcePolicyRuntime) -> None:
    root = _root()
    _exclude(runtime, root, "exclude-request")
    proposal = _proposal(runtime, root)
    with pytest.raises(SourcePolicyError, match="source_policy_lease_required"):
        runtime.erasure_intent(proposal.request_id)
    with pytest.raises(SourcePolicyError, match="source_policy_lease_required"):
        runtime.erasure_intents()
    with pytest.raises(SourcePolicyError, match="source_policy_lease_required"):
        runtime.commit_erasure_intent(proposal)
    with (
        runtime.shared(),
        pytest.raises(SourcePolicyError, match="source_policy_exclusive_lease_required"),
    ):
        runtime.commit_erasure_intent(proposal)


def test_intent_requires_exact_bound_excluded_root(runtime: SourcePolicyRuntime) -> None:
    root = _root()
    with pytest.raises(SourcePolicyError, match="erasure_requires_source_exclusion"):
        _commit(runtime, _proposal(runtime, root))
    _exclude(runtime, root, "exclude-request")
    proposal = _proposal(runtime, root)
    with pytest.raises(SourcePolicyError, match="source_root_binding_mismatch"):
        _commit(runtime, replace(proposal, root=replace(root, counterparty_id="bob")))
    with pytest.raises(SourcePolicyError, match="policy_lineage_binding_mismatch"):
        _commit(runtime, replace(proposal, root=replace(root, lineage_id="foreign-lineage")))
    with runtime.shared() as snapshot:
        assert snapshot.erasure_revision == 0


def test_intent_is_terminal_metadata_and_source_history_is_unchanged(
    runtime: SourcePolicyRuntime,
) -> None:
    root = _root()
    _exclude(runtime, root, "exclude-request")
    source_rows = _source_rows(runtime)
    proposal = _proposal(runtime, root)
    intent = _commit(runtime, proposal)
    assert intent.matches(proposal)
    assert intent.result_erasure_revision == 1
    assert intent.physical_erasure is False
    assert intent.execution_authorized is False
    assert _source_rows(runtime) == source_rows
    with runtime.shared() as snapshot:
        assert snapshot.generation == 1
        assert snapshot.erasure_revision == 1
        assert snapshot.excluded_message_ids == frozenset({root.message_id})
        assert runtime.erasure_intent(proposal.request_id) == intent
        assert runtime.erasure_intents() == (intent,)
    restarted = SourcePolicyRuntime(runtime.ledger.directory, "satori")
    with restarted.shared():
        assert restarted.erasure_intent(proposal.request_id) == intent
    assert _commit(restarted, proposal) == intent


def test_replay_resolves_original_receipt_after_both_heads_advance(
    runtime: SourcePolicyRuntime,
) -> None:
    first_root = _root()
    _exclude(runtime, first_root, "exclude-first")
    first_proposal = _proposal(runtime, first_root, "intent-first")
    first = _commit(runtime, first_proposal)
    second_root = _root("second-canonical-user")
    _exclude(runtime, second_root, "exclude-second")
    second = _commit(runtime, _proposal(runtime, second_root, "intent-second"))
    assert _commit(runtime, first_proposal) == first
    with runtime.shared() as snapshot:
        assert (snapshot.generation, snapshot.erasure_revision) == (2, 2)
        assert runtime.erasure_intents() == (first, second)


@pytest.mark.parametrize(
    "field",
    [
        "reviewed_preview_digest",
        "approval_digest",
        "schema_digest",
        "store_digest",
        "routing_digest",
        "expected_source_generation",
        "expected_erasure_revision",
        "retention_root",
        "assessment_status",
    ],
)
def test_request_reuse_must_match_original_approval(
    runtime: SourcePolicyRuntime,
    field: str,
) -> None:
    root = _root()
    _exclude(runtime, root, "exclude-request")
    proposal = _proposal(runtime, root)
    original = _commit(runtime, proposal)
    if field == "retention_root":
        changed = replace(proposal, root=replace(root, counterparty_id="bob"))
    elif field == "expected_source_generation":
        changed = replace(
            proposal, expected_source_generation=proposal.expected_source_generation + 1
        )
    elif field == "expected_erasure_revision":
        changed = replace(
            proposal, expected_erasure_revision=proposal.expected_erasure_revision + 1
        )
    elif field == "assessment_status":
        changed = replace(proposal, assessment_status="complete")
    elif field == "reviewed_preview_digest":
        changed = replace(proposal, reviewed_preview_digest="9" * 64)
    elif field == "approval_digest":
        changed = replace(proposal, approval_digest="9" * 64)
    elif field == "schema_digest":
        changed = replace(proposal, schema_digest="9" * 64)
    elif field == "store_digest":
        changed = replace(proposal, store_digest="9" * 64)
    elif field == "routing_digest":
        changed = replace(proposal, routing_digest="9" * 64)
    else:
        raise AssertionError("unknown synthetic mutation")
    with pytest.raises(SourcePolicyError, match="erasure_request_conflict"):
        _commit(runtime, changed)
    with runtime.shared():
        assert runtime.erasure_intent(proposal.request_id) == original


def test_request_ids_are_unambiguous_across_source_and_erasure_operations(
    runtime: SourcePolicyRuntime,
) -> None:
    root = _root()
    _exclude(runtime, root, "exclude-request")
    with pytest.raises(SourcePolicyError, match="erasure_request_conflict"):
        _commit(runtime, _proposal(runtime, root, "exclude-request"))
    intent = _commit(runtime, _proposal(runtime, root, "intent-request"))
    with pytest.raises(SourcePolicyError, match="source_request_conflict"):
        _exclude(runtime, _root("second"), intent.request_id)
    with runtime.shared() as snapshot:
        assert (snapshot.generation, snapshot.erasure_revision) == (1, 1)


def test_new_commit_requires_fresh_composite_head_and_never_reintends_root(
    runtime: SourcePolicyRuntime,
) -> None:
    first, second = _root(), _root("second")
    _exclude(runtime, first, "exclude-first")
    _exclude(runtime, second, "exclude-second")
    stale_erasure = _proposal(runtime, second, "intent-second")
    _commit(runtime, _proposal(runtime, first, "intent-first"))
    with pytest.raises(SourcePolicyError, match="erasure_revision_conflict"):
        _commit(runtime, stale_erasure)
    stale_source = _proposal(runtime, second, "intent-second")
    _exclude(runtime, _root("third"), "exclude-third")
    with pytest.raises(SourcePolicyError, match="source_generation_conflict"):
        _commit(runtime, stale_source)
    with pytest.raises(SourcePolicyError, match="erasure_root_already_intended"):
        _commit(runtime, _proposal(runtime, first, "new-request-same-root"))


@pytest.mark.parametrize("after_commit", [False, True])
def test_ambiguous_intent_commit_recovers_only_original_durable_request(
    runtime: SourcePolicyRuntime,
    monkeypatch: pytest.MonkeyPatch,
    after_commit: bool,
) -> None:
    root = _root()
    _exclude(runtime, root, "exclude-request")
    proposal = _proposal(runtime, root)
    original_connect = sqlite3.connect
    remaining = [True]

    class FaultyConnection(sqlite3.Connection):
        def commit(self) -> None:
            if remaining[0]:
                remaining[0] = False
                if after_commit:
                    super().commit()
                raise sqlite3.OperationalError("synthetic private exception text")
            super().commit()

    def connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        kwargs["factory"] = FaultyConnection
        return cast(sqlite3.Connection, original_connect(*args, **kwargs))  # type: ignore[call-overload]

    monkeypatch.setattr(sqlite3, "connect", connect)
    if after_commit:
        result = _commit(runtime, proposal)
        assert result.result_erasure_revision == 1
    else:
        with pytest.raises(SourcePolicyError, match="policy_storage_unavailable") as failure:
            _commit(runtime, proposal)
        assert "private" not in str(failure.value)
        with runtime.shared() as snapshot:
            assert (snapshot.generation, snapshot.erasure_revision) == (1, 0)
            assert runtime.erasure_intent(proposal.request_id) is None
        result = _commit(runtime, proposal)
    with runtime.shared() as snapshot:
        assert snapshot.erasure_revision == 1
        assert runtime.erasure_intents() == (result,)


def test_policy_0001_reads_and_source_operations_do_not_implicitly_upgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _old_runtime(tmp_path, monkeypatch)
    before = runtime.ledger.database_path.read_bytes()
    with runtime.shared() as snapshot:
        assert (snapshot.generation, snapshot.erasure_revision) == (0, 0)
        assert runtime.erasure_intents() == ()
        assert runtime.erasure_intent("unknown-request") is None
    assert runtime.ledger.database_path.read_bytes() == before
    root = _root()
    _exclude(runtime, root, "legacy-source-operation")
    with pytest.raises(SourcePolicyError, match="policy_schema_upgrade_required"):
        _commit(runtime, _proposal(runtime, root))
    with sqlite3.connect(runtime.ledger.database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "policy_0001",
        )


def test_explicit_upgrade_preserves_floor_binding_and_all_source_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _old_runtime(tmp_path, monkeypatch)
    root = _root()
    _exclude(runtime, root, "legacy-source-operation")
    source_rows = _source_rows(runtime)
    binding = runtime.ledger.binding_path.read_bytes()
    inodes = tuple((path.stat().st_dev, path.stat().st_ino) for path in runtime.reserved_paths[:3])
    with runtime.shared() as old_snapshot:
        pass
    snapshot = runtime.upgrade_policy()
    assert snapshot == old_snapshot
    assert snapshot.erasure_revision == 0
    assert runtime.ledger.binding_path.read_bytes() == binding
    assert (
        tuple((path.stat().st_dev, path.stat().st_ino) for path in runtime.reserved_paths[:3])
        == inodes
    )
    assert _source_rows(runtime) == source_rows
    assert runtime.upgrade_policy() == snapshot
    intent = _commit(runtime, _proposal(runtime, root))
    assert intent.result_erasure_revision == 1


def test_explicit_upgrade_requires_outer_fixed_lease(runtime: SourcePolicyRuntime) -> None:
    for lease in (runtime.shared, runtime.exclusive):
        with (
            lease(),
            pytest.raises(SourcePolicyError, match="policy_migration_requires_outer_lease"),
        ):
            runtime.upgrade_policy()


def test_unknown_identity_is_denied_before_explicit_upgrade_changes_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _old_runtime(tmp_path, monkeypatch)
    before = runtime.ledger.database_path.read_bytes()
    foreign = SourcePolicyRuntime(runtime.ledger.directory, "not-enrolled-identity")
    with pytest.raises(SourcePolicyError, match="policy_lineage_not_enrolled"):
        foreign.upgrade_policy()
    assert runtime.ledger.database_path.read_bytes() == before


def test_unknown_old_schema_is_denied_before_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _old_runtime(tmp_path, monkeypatch)
    with sqlite3.connect(runtime.ledger.database_path) as connection:
        connection.execute("CREATE TABLE unknown_carrier (contents TEXT)")
    before = runtime.ledger.database_path.read_bytes()
    with pytest.raises(SourcePolicyError, match="policy_schema_invalid"):
        runtime.upgrade_policy()
    assert runtime.ledger.database_path.read_bytes() == before


def test_upgrade_failure_rolls_back_without_replacing_floor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _old_runtime(tmp_path, monkeypatch)
    before = runtime.ledger.database_path.read_bytes()
    binding = runtime.ledger.binding_path.read_bytes()

    def fail_migration(connection: Connection, *, revision: str = "policy_0002") -> None:
        upgrade_policy(connection, revision=revision)
        raise ValueError("synthetic private exception text")

    monkeypatch.setattr(ledger_module, "upgrade_policy", fail_migration)
    with pytest.raises(SourcePolicyError, match="policy_migration_failed"):
        runtime.upgrade_policy()
    assert runtime.ledger.database_path.read_bytes() == before
    assert runtime.ledger.binding_path.read_bytes() == binding


def test_downgrade_cannot_discard_committed_intent(runtime: SourcePolicyRuntime) -> None:
    root = _root()
    _exclude(runtime, root, "exclude-request")
    intent = _commit(runtime, _proposal(runtime, root))
    configuration = Config()
    configuration.set_main_option(
        "script_location", str(Path(ledger_module.__file__).with_name("migrations"))
    )
    engine = create_engine(f"sqlite+pysqlite:///{runtime.ledger.database_path}")
    try:
        with runtime.exclusive(), engine.begin() as connection:
            configuration.attributes["connection"] = connection
            with pytest.raises(RuntimeError, match="policy_floor_downgrade_forbidden"):
                command.downgrade(configuration, "policy_0001")
    finally:
        engine.dispose()
    with runtime.shared() as snapshot:
        assert snapshot.erasure_revision == 1
        assert runtime.erasure_intent(intent.request_id) == intent


@pytest.mark.parametrize("corruption", ["head", "digest", "root"])
def test_corrupt_intent_metadata_invalidates_floor_snapshot(
    runtime: SourcePolicyRuntime,
    corruption: str,
) -> None:
    root = _root()
    _exclude(runtime, root, "exclude-request")
    _commit(runtime, _proposal(runtime, root))
    with sqlite3.connect(runtime.ledger.database_path) as connection:
        if corruption == "head":
            connection.execute("UPDATE policy_lineages SET erasure_revision=2")
        elif corruption == "digest":
            connection.execute("UPDATE policy_erasure_intents SET approval_digest=?", ("z" * 64,))
        else:
            connection.execute("UPDATE policy_erasure_intents SET counterparty_id='bob'")
    with pytest.raises(SourcePolicyError, match="policy_integrity_invalid"), runtime.shared():
        pass
