"""Synthetic floor/enrollment/retry tests; no provider or real user database."""

import json
import os
import sqlite3
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from satori.application.source_policy.contracts import SourceExclusionCommand
from satori.application.source_policy.use_cases import SourcePolicyControls
from satori.domain.source_policy import (
    CanonicalRootRef,
    ExclusionPhase,
    FootprintStatus,
    ProtectedContentFootprint,
    SourceEligibilitySnapshot,
    SourceExclusionReceipt,
    SourcePolicyError,
)
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime


@pytest.fixture
def runtime(tmp_path: Path) -> SourcePolicyRuntime:
    result = SourcePolicyRuntime(tmp_path / "trusted", "satori")
    result.enroll(reviewed=True, new_installation=True, lineage_id="canonical-lineage")
    return result


def _root(message_id: str = "user-one", counterparty: str = "alice") -> CanonicalRootRef:
    return CanonicalRootRef(
        "canonical-lineage", "satori", counterparty, "session", "turn", message_id
    )


def _command(
    controls: SourcePolicyControls, root: CanonicalRootRef, request_id: str
) -> SourceExclusionCommand:
    preview = controls.preview(root)
    return SourceExclusionCommand(
        message_id=root.message_id,
        expected_generation=preview.generation,
        request_id=request_id,
        preview_digest=preview.preview_digest,
    )


def test_read_never_creates_missing_installation(tmp_path: Path) -> None:
    runtime = SourcePolicyRuntime(tmp_path / "absent", "satori")
    with (
        pytest.raises(SourcePolicyError, match="policy_installation_binding_unavailable"),
        runtime.shared(),
    ):
        pass
    assert not runtime.ledger.directory.exists()


def test_enrollment_requires_explicit_review_and_first_installation(tmp_path: Path) -> None:
    runtime = SourcePolicyRuntime(tmp_path / "trusted", "satori")
    with pytest.raises(SourcePolicyError, match="policy_enrollment_review_required"):
        runtime.enroll(reviewed=False, new_installation=True)
    with pytest.raises(SourcePolicyError, match="policy_installation_not_enrolled"):
        runtime.enroll(reviewed=True)
    assert not runtime.ledger.directory.exists()


def test_initial_enrollment_private_and_independent(runtime: SourcePolicyRuntime) -> None:
    with runtime.shared() as snapshot:
        assert snapshot.generation == 0
        assert snapshot.lineage_id == "canonical-lineage"
        assert snapshot.excluded_message_ids == frozenset()
        assert runtime.snapshot() == snapshot
    assert runtime.ledger.directory.stat().st_mode & 0o777 == 0o700
    for path in runtime.reserved_paths[:3]:
        assert path.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(runtime.ledger.database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "policy_0002",
        )
    before = runtime.ledger.binding_path.read_bytes()
    runtime.enroll(reviewed=True, lineage_id="canonical-lineage")
    assert runtime.ledger.binding_path.read_bytes() == before


def test_no_empty_replacement_when_enrolled_floor_missing(runtime: SourcePolicyRuntime) -> None:
    binding = runtime.ledger.binding_path.read_bytes()
    runtime.ledger.database_path.unlink()
    with pytest.raises(SourcePolicyError, match="policy_file_unavailable"):
        runtime.enroll(reviewed=True, new_installation=True)
    assert runtime.ledger.binding_path.read_bytes() == binding
    assert not runtime.ledger.database_path.exists()


def test_lost_binding_with_remaining_floor_requires_recovery(runtime: SourcePolicyRuntime) -> None:
    floor = runtime.ledger.database_path.read_bytes()
    runtime.ledger.binding_path.unlink()
    with pytest.raises(SourcePolicyError, match="policy_recovery_required"):
        runtime.enroll(reviewed=True, new_installation=True)
    assert runtime.ledger.database_path.read_bytes() == floor


@pytest.mark.parametrize("file_name", ["policy.sqlite", "policy.lock"])
def test_replaced_inode_is_not_same_trusted_installation(
    runtime: SourcePolicyRuntime,
    file_name: str,
) -> None:
    path = runtime.ledger.directory / file_name
    replacement = path.with_suffix(".replacement")
    replacement.write_bytes(path.read_bytes())
    replacement.chmod(0o600)
    os.replace(replacement, path)
    with pytest.raises(SourcePolicyError, match=r"policy_.*_binding_invalid"), runtime.shared():
        pass


@pytest.mark.parametrize("file_name", ["installation.json", "policy.sqlite", "policy.lock"])
def test_symlink_policy_target_is_rejected(runtime: SourcePolicyRuntime, file_name: str) -> None:
    path = runtime.ledger.directory / file_name
    alternate = path.with_suffix(".original")
    path.rename(alternate)
    path.symlink_to(alternate)
    with pytest.raises(SourcePolicyError), runtime.shared():
        pass


def test_missing_floor_cannot_be_selected_via_old_identity_metadata(
    runtime: SourcePolicyRuntime,
) -> None:
    with pytest.raises(SourcePolicyError, match="policy_lineage_binding_mismatch"):
        runtime.enroll(reviewed=True, lineage_id="old-backup-lineage")
    with runtime.shared() as snapshot:
        assert snapshot.lineage_id == "canonical-lineage"


def test_unknown_schema_and_revision_fail_closed(runtime: SourcePolicyRuntime) -> None:
    with runtime.shared():
        pass
    with sqlite3.connect(runtime.ledger.database_path) as connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
    with pytest.raises(SourcePolicyError, match="policy_schema_invalid"), runtime.shared():
        pass


def test_generation_corruption_is_rejected(runtime: SourcePolicyRuntime) -> None:
    with sqlite3.connect(runtime.ledger.database_path) as connection:
        connection.execute("UPDATE policy_lineages SET generation=5")
    with pytest.raises(SourcePolicyError, match="policy_integrity_invalid"), runtime.shared():
        pass


@pytest.mark.parametrize("corruption", ["remove_receipt", "receipt_generation_jump"])
def test_original_floor_decision_is_required_for_every_exclusion(
    runtime: SourcePolicyRuntime,
    corruption: str,
) -> None:
    controls, root = SourcePolicyControls(runtime), _root()
    controls.apply(_command(controls, root, "first"), root)
    if corruption == "receipt_generation_jump":
        other = _root("second-root")
        controls.apply(_command(controls, other, "second"), other)
    with sqlite3.connect(runtime.ledger.database_path) as connection:
        if corruption == "remove_receipt":
            connection.execute("DELETE FROM policy_receipts WHERE request_id='first'")
        else:
            connection.execute(
                "UPDATE policy_receipts SET result_generation=2 WHERE request_id='first'"
            )
    with pytest.raises(SourcePolicyError, match="policy_integrity_invalid"), runtime.shared():
        pass


def test_operator_effect_metadata_is_deeply_immutable(runtime: SourcePolicyRuntime) -> None:
    preview = SourcePolicyControls(runtime).preview(_root())
    with pytest.raises(ValidationError, match="frozen_instance"):
        preview.effects.source_excluded = False
    assert preview.model_dump()["effects"]["source_excluded"] is True


def test_binding_uses_strict_metadata_not_identity_string(runtime: SourcePolicyRuntime) -> None:
    value = json.loads(runtime.ledger.binding_path.read_text())
    value["floor_id"] = "invented-old-floor"
    runtime.ledger.binding_path.write_text(json.dumps(value))
    with pytest.raises(SourcePolicyError, match="policy_floor_binding_invalid"), runtime.shared():
        pass


def test_apply_commits_floor_before_failed_repair_and_replays_original(
    runtime: SourcePolicyRuntime,
) -> None:
    controls, root = SourcePolicyControls(runtime), _root()
    command = _command(controls, root, "operation")

    def fail(snapshot: SourceEligibilitySnapshot, receipt: SourceExclusionReceipt) -> None:
        assert root.message_id in snapshot.excluded_message_ids
        assert receipt.exclusion_committed
        raise RuntimeError("private user contents should never escape")

    pending = controls.apply(command, root, repair=fail)
    assert pending.phase is ExclusionPhase.REPAIR_PENDING
    assert pending.result_generation == 1
    with runtime.shared(), pytest.raises(SourcePolicyError, match="canonical_source_excluded"):
        runtime.check_root(root)
    observed: list[str] = []

    def repair(snapshot: SourceEligibilitySnapshot, receipt: SourceExclusionReceipt) -> None:
        with runtime.shared() as nested:
            assert nested == snapshot
            observed.append(receipt.decision_id)

    completed = controls.apply(command, root, repair=repair)
    assert completed.phase is ExclusionPhase.COMPLETE
    assert completed.decision_id == pending.decision_id
    assert observed == [pending.decision_id]
    assert completed.result_generation == 1


def test_completed_replay_repairs_selected_old_db_and_reports_failure(
    runtime: SourcePolicyRuntime,
) -> None:
    controls, root = SourcePolicyControls(runtime), _root()
    command = _command(controls, root, "operation")
    first = controls.apply(command, root, repair=lambda snapshot, receipt: None)
    assert first.phase is ExclusionPhase.COMPLETE

    def failed_old_database(
        snapshot: SourceEligibilitySnapshot, receipt: SourceExclusionReceipt
    ) -> None:
        raise OSError("private database filename")

    second = controls.apply(command, root, repair=failed_old_database)
    assert second.phase is ExclusionPhase.REPAIR_PENDING
    assert second.decision_id == first.decision_id


def test_repeated_target_noop_and_request_conflict(runtime: SourcePolicyRuntime) -> None:
    controls, root = SourcePolicyControls(runtime), _root()
    initial = _command(controls, root, "first")
    first = controls.apply(initial, root)
    second = controls.apply(_command(controls, root, "second"), root)
    assert second.result_generation == first.result_generation == 1
    assert second.decision_id != first.decision_id
    changed = initial.model_copy(update={"preview_digest": "a" * 64})
    with pytest.raises(SourcePolicyError, match="source_request_conflict"):
        controls.apply(changed, root)


def test_stale_preview_and_wrong_scope_have_no_receipt(runtime: SourcePolicyRuntime) -> None:
    controls = SourcePolicyControls(runtime)
    stale = _command(controls, _root("two"), "stale")
    controls.apply(_command(controls, _root(), "first"), _root())
    with pytest.raises(SourcePolicyError, match="source_generation_conflict"):
        controls.apply(stale, _root("two"))
    foreign = CanonicalRootRef("canonical-lineage", "foreign", "alice", "session", "turn", "two")
    with pytest.raises(SourcePolicyError, match="policy_lineage_binding_mismatch"):
        controls.apply(stale, foreign)
    with runtime.shared():
        assert runtime.receipt("stale") is None


def test_unknown_legacy_exception_is_lineage_wide_and_rechecked(
    runtime: SourcePolicyRuntime,
) -> None:
    legacy = ProtectedContentFootprint(status=FootprintStatus.UNKNOWN, legacy=True)
    with runtime.shared():
        runtime.check_footprint(legacy, "dispatch")
        with pytest.raises(SourcePolicyError, match="source_dependency_unknown"):
            runtime.check_footprint(ProtectedContentFootprint(status=FootprintStatus.UNKNOWN))
        with pytest.raises(SourcePolicyError, match="source_footprint_overflow"):
            runtime.check_footprint(ProtectedContentFootprint(status=FootprintStatus.OVERFLOW))
    controls, root = SourcePolicyControls(runtime), _root(counterparty="bob")
    controls.apply(_command(controls, root, "exclude-bob"), root)
    with runtime.shared():
        with pytest.raises(SourcePolicyError, match="source_dependency_unknown"):
            runtime.check_footprint(legacy, "commit")
        runtime.check_footprint(ProtectedContentFootprint(root_message_ids=("fresh-alice",)))


def test_late_actual_input_commit_cannot_cross_floor_advance(runtime: SourcePolicyRuntime) -> None:
    footprint = ProtectedContentFootprint(root_message_ids=("user-one",))
    with runtime.shared():
        runtime.check_footprint(footprint, "dispatch")
    controls, root = SourcePolicyControls(runtime), _root()
    controls.apply(_command(controls, root, "exclusion"), root)
    with runtime.shared(), pytest.raises(SourcePolicyError, match="canonical_source_excluded"):
        runtime.check_footprint(footprint, "commit")


def test_mirror_lag_blocks_shared_but_allows_exclusive_reconciliation(
    runtime: SourcePolicyRuntime,
) -> None:
    def mirror_lag(snapshot: SourceEligibilitySnapshot) -> None:
        raise SourcePolicyError("source_mirror_reconciliation_required")

    runtime.verify_mirror = mirror_lag
    with (
        pytest.raises(SourcePolicyError, match="source_mirror_reconciliation_required"),
        runtime.shared(),
    ):
        pass
    with runtime.exclusive() as snapshot:
        assert snapshot.generation == 0
    assert SourcePolicyControls(runtime).preview(_root()).generation == 0


def test_lease_required_and_upgrade_never_silently_downgrades(runtime: SourcePolicyRuntime) -> None:
    with pytest.raises(SourcePolicyError, match="source_policy_lease_required"):
        runtime.snapshot()
    with (
        runtime.shared(),
        pytest.raises(SourcePolicyError, match="source_policy_lock_upgrade_forbidden"),
        runtime.exclusive(),
    ):
        pass


@pytest.mark.parametrize("after_commit", [False, True])
def test_ambiguous_floor_commit_resolves_same_request(
    runtime: SourcePolicyRuntime,
    monkeypatch: pytest.MonkeyPatch,
    after_commit: bool,
) -> None:
    controls, root = SourcePolicyControls(runtime), _root()
    command = _command(controls, root, "same-request")
    original_connect = sqlite3.connect
    remaining = [True]

    class FaultyConnection(sqlite3.Connection):
        def commit(self) -> None:
            if remaining[0]:
                remaining[0] = False
                if after_commit:
                    super().commit()
                raise sqlite3.OperationalError("private sqlite failure")
            super().commit()

    def connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        kwargs["factory"] = FaultyConnection
        return cast(sqlite3.Connection, original_connect(*args, **kwargs))  # type: ignore[call-overload]

    monkeypatch.setattr(sqlite3, "connect", connect)
    if after_commit:
        receipt = controls.apply(command, root)
        assert receipt.result_generation == 1
    else:
        with pytest.raises(SourcePolicyError, match="policy_storage_unavailable"):
            controls.apply(command, root)
        with runtime.shared() as snapshot:
            assert snapshot.generation == 0
            assert runtime.receipt("same-request") is None
        receipt = controls.apply(command, root)
        assert receipt.result_generation == 1


@pytest.mark.parametrize("path_name", ["policy.sqlite", "policy.lock", "installation.json"])
def test_hardlinked_private_floor_files_are_rejected(
    runtime: SourcePolicyRuntime, path_name: str
) -> None:
    path = runtime.ledger.directory / path_name
    os.link(path, runtime.ledger.directory / "alias")
    with pytest.raises(SourcePolicyError), runtime.shared():
        pass
