"""Synthetic owner adoption only; never touches an installed app or real profile."""

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from pydantic import ValidationError

from satori.application.source_policy.binding_recovery import AdoptCurrentBindingFiles
from satori.binding_recovery_cli import main as recovery_cli
from satori.composition import build_initial_self_services
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.artifacts import PrivateArtifactStore
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.seeds.loader import JsonSeedLoader
from satori.infrastructure.source_policy.barrier import PolicyBarrier
from satori.infrastructure.source_policy.binding_recovery import (
    LedgerDeviceBindingRecovery,
    _Inspection,
)
from satori.infrastructure.source_policy.installation import enroll_installation
from satori.infrastructure.source_policy.ledger import InstallationBinding, InstallationPolicyLedger


@pytest.fixture
def recovery(migrated_database: Database, tmp_path: Path) -> LedgerDeviceBindingRecovery:
    initial = build_initial_self_services(migrated_database)
    initial.activate.execute(JsonSeedLoader().load_canonical(), trace_id="synthetic-activation")
    runtime = enroll_installation(
        migrated_database, tmp_path / "policy", reviewed=True, new_installation=True
    )
    ledger = runtime.ledger
    original = ledger.read_binding()
    digest = hashlib.sha256(ledger.binding_path.read_bytes()).hexdigest()
    # Model a previous mount's stored device component without replacing either
    # existing floor/lock inode. Only synthetic descriptor material is changed.
    ledger._publish_binding_descriptor(
        InstallationBinding(
            original.installation_id,
            original.floor_id,
            (original.database_inode[0] + 100, original.database_inode[1]),
            (original.lock_inode[0] + 100, original.lock_inode[1]),
        ),
        overwrite=True,
        expected_sha256=digest,
    )
    return LedgerDeviceBindingRecovery(ledger, Path(str(migrated_database.engine.url.database)))


def command(recovery: LedgerDeviceBindingRecovery) -> AdoptCurrentBindingFiles:
    plan = recovery.preview()
    return AdoptCurrentBindingFiles(
        plan=plan,
        expected_review_sha256=plan.review_sha256,
        request_id="synthetic-adoption-1",
        owner_adopts_current_files=True,
        runtime_confirmed_offline=True,
    )


def fingerprints(recovery: LedgerDeviceBindingRecovery) -> dict[str, tuple[object, ...]]:
    result: dict[str, tuple[object, ...]] = {}
    for path in (
        recovery.canonical,
        recovery.ledger.database_path,
        recovery.ledger.lock_path,
        recovery.ledger.binding_path,
    ):
        metadata = path.lstat()
        result[path.name] = (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mode,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    return result


def test_preview_is_readonly_and_explicitly_requires_new_owner_trust(
    recovery: LedgerDeviceBindingRecovery, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = fingerprints(recovery)
    names = set(recovery.ledger.directory.iterdir())

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("preview attempted a write/migration/runtime operation")

    monkeypatch.setattr(PrivateArtifactStore, "write", forbidden)
    monkeypatch.setattr("satori.infrastructure.source_policy.ledger.upgrade_policy", forbidden)
    monkeypatch.setattr("satori.infrastructure.persistence.migrations.upgrade_database", forbidden)
    plan = recovery.preview()
    assert plan.original_volume_proven is False
    assert plan.owner_adoption_required is True
    assert plan.offline_runtime_required is True
    assert plan.review_sha256 == plan.computed_digest()
    assert recovery.preview() == plan
    assert fingerprints(recovery) == before
    assert set(recovery.ledger.directory.iterdir()) == names
    with pytest.raises(SourcePolicyError, match="policy_floor_binding_invalid"):
        recovery.ledger.validate_binding()


def test_adoption_changes_only_two_device_scalars_and_preserves_owner_bytes(
    recovery: LedgerDeviceBindingRecovery,
) -> None:
    reviewed = command(recovery)
    before = fingerprints(recovery)
    old_descriptor = recovery.ledger.binding_path.read_bytes()
    expected = json.loads(old_descriptor)
    expected["database_inode"][0] = reviewed.plan.current_device
    expected["lock_inode"][0] = reviewed.plan.current_device
    result = recovery.adopt(reviewed)
    assert result.status == "adopted"
    assert result.original_volume_proven is False
    assert result.canonical_floor_lock_unchanged is True
    assert result.normal_binding_valid is True
    assert json.loads(recovery.ledger.binding_path.read_bytes()) == expected
    assert Path(result.backup_path).read_bytes() == old_descriptor
    assert json.loads(Path(result.receipt_path).read_bytes())["status"] == "adopted"
    after = fingerprints(recovery)
    for name in ("satori-test.db", "policy.sqlite", "policy.lock"):
        assert after[name] == before[name]
    recovery.ledger.validate_binding()
    with pytest.raises(SourcePolicyError, match="binding_recovery_review_consumed"):
        recovery.adopt(reviewed.model_copy(update={"request_id": "different-request"}))


@pytest.mark.parametrize(
    "mutation", ["unchanged", "one_device", "different_devices", "db_inode", "lock_inode"]
)
def test_only_exact_two_device_drift_is_reviewable(
    recovery: LedgerDeviceBindingRecovery,
    mutation: str,
) -> None:
    descriptor = json.loads(recovery.ledger.binding_path.read_bytes())
    current = recovery.ledger.database_path.stat().st_dev
    if mutation == "unchanged":
        descriptor["database_inode"][0] = descriptor["lock_inode"][0] = current
    elif mutation == "one_device":
        descriptor["database_inode"][0] = current
    elif mutation == "different_devices":
        descriptor["lock_inode"][0] += 1
    elif mutation == "db_inode":
        descriptor["database_inode"][1] += 1
    else:
        descriptor["lock_inode"][1] += 1
    recovery.ledger.binding_path.write_text(json.dumps(descriptor))
    with pytest.raises(SourcePolicyError, match="binding_recovery_not_device_only"):
        recovery.preview()


@pytest.mark.parametrize("target", ["policy.sqlite", "policy.lock"])
def test_byte_identical_replaced_inode_stays_rejected(
    recovery: LedgerDeviceBindingRecovery,
    target: str,
) -> None:
    path = recovery.ledger.directory / target
    replacement = path.with_suffix(".replacement")
    replacement.write_bytes(path.read_bytes())
    replacement.chmod(0o600)
    os.replace(replacement, path)
    with pytest.raises(SourcePolicyError, match="binding_recovery_not_device_only"):
        recovery.preview()
    with pytest.raises(SourcePolicyError, match="policy_floor_binding_invalid"):
        recovery.ledger.validate_binding()


@pytest.mark.parametrize(
    "mutation", ["mode", "directory", "hardlink", "symlink", "ancestor_link", "uid"]
)
def test_private_regular_single_link_same_uid_is_mandatory(
    recovery: LedgerDeviceBindingRecovery,
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = recovery.ledger.database_path
    if mutation == "mode":
        path.chmod(0o644)
    elif mutation == "directory":
        path.parent.chmod(0o755)
    elif mutation == "hardlink":
        os.link(path, path.with_suffix(".linked"))
    elif mutation == "symlink":
        moved = path.with_suffix(".saved")
        path.rename(moved)
        path.symlink_to(moved)
    elif mutation == "ancestor_link":
        moved = path.parent.with_name("saved-policy")
        path.parent.rename(moved)
        path.parent.symlink_to(moved, target_is_directory=True)
    else:
        current = os.getuid()
        monkeypatch.setattr(os, "getuid", lambda: current + 10000)
    with pytest.raises(SourcePolicyError, match="binding_recovery_"):
        recovery.preview()


@pytest.mark.parametrize("target", ["main", "floor"])
def test_dangling_sidecars_are_not_treated_as_absent(
    recovery: LedgerDeviceBindingRecovery,
    target: str,
) -> None:
    path = recovery.canonical if target == "main" else recovery.ledger.database_path
    Path(str(path) + "-wal").symlink_to(path.with_name("absent-target"))
    with pytest.raises(SourcePolicyError, match="binding_recovery_sidecar_present"):
        recovery.preview()


@pytest.mark.parametrize("target", ["main", "floor"])
def test_persistent_wal_mode_is_rejected_even_without_a_sidecar(
    recovery: LedgerDeviceBindingRecovery,
    target: str,
) -> None:
    path = recovery.canonical if target == "main" else recovery.ledger.database_path
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    connection.close()
    with pytest.raises(
        SourcePolicyError, match=r"binding_recovery_(journal_mode_unsupported|sidecar_present)"
    ):
        recovery.preview()


@pytest.mark.parametrize(
    ("target", "sql"),
    [
        ("main", "CREATE TABLE unreviewed(value TEXT)"),
        ("floor", "CREATE VIEW unreviewed AS SELECT 1"),
        ("floor", "UPDATE policy_lineages SET generation=1"),
        ("floor", "UPDATE policy_lineages SET erasure_revision=1"),
        ("floor", "UPDATE policy_installation SET floor_id='different-floor'"),
        ("main", "UPDATE source_policy_bindings SET lineage_id='different-lineage'"),
        ("main", "UPDATE source_policy_bindings SET policy_generation=1"),
        ("main", "UPDATE source_owner_repairs SET policy_generation=1"),
        ("main", "DELETE FROM source_owner_repairs WHERE owner='positions'"),
    ],
)
def test_unknown_schema_scope_or_nonzero_policy_state_denies_review(
    recovery: LedgerDeviceBindingRecovery,
    target: str,
    sql: str,
) -> None:
    path = recovery.canonical if target == "main" else recovery.ledger.database_path
    with sqlite3.connect(path) as connection:
        connection.execute(sql)
    with pytest.raises(SourcePolicyError, match="binding_recovery_"):
        recovery.preview()


def test_stale_bytes_and_false_adoption_never_publish(
    recovery: LedgerDeviceBindingRecovery,
) -> None:
    reviewed = command(recovery)
    with pytest.raises(ValidationError, match="owner_adopts_current_files"):
        AdoptCurrentBindingFiles.model_validate(
            {
                **reviewed.model_dump(),
                "owner_adopts_current_files": False,
            }
        )
    bypassed = reviewed.model_copy(update={"owner_adopts_current_files": False})
    with pytest.raises(SourcePolicyError, match="binding_recovery_explicit_owner_review_required"):
        recovery.adopt(bypassed)
    # Same bytes still have a fresh ctime/mtime after a rewrite: that review is stale.
    payload = recovery.canonical.read_bytes()
    recovery.canonical.write_bytes(payload)
    with pytest.raises(SourcePolicyError, match="binding_recovery_review_stale"):
        recovery.adopt(reviewed)
    assert not (recovery.ledger.directory / "binding-recovery").exists()


def test_guard_repeats_after_current_lock_lease(
    recovery: LedgerDeviceBindingRecovery,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reviewed = command(recovery)
    original = PolicyBarrier.lease

    @contextmanager
    def changed_after_lock(self: PolicyBarrier, *, exclusive: bool) -> Iterator[None]:
        with original(self, exclusive=exclusive):
            recovery.ledger.binding_path.write_bytes(recovery.ledger.binding_path.read_bytes())
            yield

    monkeypatch.setattr(PolicyBarrier, "lease", changed_after_lock)
    with pytest.raises(SourcePolicyError, match="binding_recovery_review_stale"):
        recovery.adopt(reviewed)
    assert not (recovery.ledger.directory / "binding-recovery").exists()


@pytest.mark.parametrize("phase", ["backup", "publish_before", "publish_after", "receipt"])
def test_partial_publication_has_truthful_consumed_review_outcomes(
    recovery: LedgerDeviceBindingRecovery,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    reviewed = command(recovery)
    original_write = PrivateArtifactStore.write
    original_publish = recovery.ledger._publish_binding_descriptor

    def faulty_write(
        self: PrivateArtifactStore, path: Path, payload: bytes, *, overwrite: bool = False
    ) -> None:
        if (phase == "backup" and path.name.endswith("descriptor-backup.json")) or (
            phase == "receipt" and path.name.endswith("receipt.json")
        ):
            raise OSError("synthetic artifact failure")
        original_write(self, path, payload, overwrite=overwrite)

    def faulty_publish(
        binding: InstallationBinding, *, overwrite: bool = False, expected_sha256: str | None = None
    ) -> None:
        if phase == "publish_before":
            raise OSError("synthetic before publication")
        original_publish(binding, overwrite=overwrite, expected_sha256=expected_sha256)
        if phase == "publish_after":
            raise OSError("synthetic post-replace/fsync uncertainty")

    monkeypatch.setattr(PrivateArtifactStore, "write", faulty_write)
    monkeypatch.setattr(recovery.ledger, "_publish_binding_descriptor", faulty_publish)
    result = recovery.adopt(reviewed)
    expected = {
        "backup": "not_applied_review_consumed",
        "publish_before": "not_applied_review_consumed",
        "publish_after": "outcome_unknown",
        "receipt": "adopted_audit_pending",
    }[phase]
    assert result.status == expected
    assert result.canonical_floor_lock_unchanged is True
    assert Path(result.claim_path).is_file()
    with pytest.raises(SourcePolicyError, match="binding_recovery_review_consumed"):
        recovery.adopt(reviewed)


def test_cli_preview_does_not_bootstrap_and_adopt_requires_explicit_decision(
    recovery: LedgerDeviceBindingRecovery,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = [
        "--policy-directory",
        str(recovery.ledger.directory),
        "--canonical-database",
        str(recovery.canonical),
    ]
    assert recovery_cli([*args, "--preview"]) == 0
    assert json.loads(capsys.readouterr().out)["original_volume_proven"] is False
    assert recovery_cli([*args, "--adopt"]) == 2
    assert "binding_recovery_explicit_owner_review_required" in capsys.readouterr().out


@pytest.mark.parametrize("after_write", [False, True])
def test_claim_publication_failure_never_mutates_descriptor_or_grants_retry(
    recovery: LedgerDeviceBindingRecovery,
    monkeypatch: pytest.MonkeyPatch,
    after_write: bool,
) -> None:
    reviewed = command(recovery)
    before = recovery.ledger.binding_path.read_bytes()
    original = PrivateArtifactStore.write

    def fail_claim(
        self: PrivateArtifactStore, path: Path, payload: bytes, *, overwrite: bool = False
    ) -> None:
        if path.name.endswith("claim.json"):
            if after_write:
                original(self, path, payload, overwrite=overwrite)
            raise OSError("synthetic claim publication failure")
        original(self, path, payload, overwrite=overwrite)

    monkeypatch.setattr(PrivateArtifactStore, "write", fail_claim)
    with pytest.raises(
        SourcePolicyError, match="binding_recovery_claim_failed_review_may_be_consumed"
    ):
        recovery.adopt(reviewed)
    assert recovery.ledger.binding_path.read_bytes() == before
    if after_write:
        with pytest.raises(SourcePolicyError, match="binding_recovery_review_consumed"):
            recovery.adopt(reviewed)


def test_changed_main_bytes_after_publication_remain_unknown_without_rollback(
    recovery: LedgerDeviceBindingRecovery,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reviewed = command(recovery)
    original = recovery.ledger._publish_binding_descriptor

    def noncooperating_writer(
        binding: InstallationBinding, *, overwrite: bool = False, expected_sha256: str | None = None
    ) -> None:
        original(binding, overwrite=overwrite, expected_sha256=expected_sha256)
        # A hostile/direct writer can bypass SQLite's shared read lock. The
        # metadata operation must report that loss rather than claim atomicity.
        with recovery.canonical.open("ab") as stream:
            stream.write(b"synthetic concurrent bytes")

    monkeypatch.setattr(recovery.ledger, "_publish_binding_descriptor", noncooperating_writer)
    result = recovery.adopt(reviewed)
    assert result.status == "outcome_unknown"
    assert result.canonical_floor_lock_unchanged is False
    assert result.normal_binding_valid is False
    assert (
        json.loads(recovery.ledger.binding_path.read_bytes())["database_inode"][0]
        == reviewed.plan.current_device
    )
    assert Path(result.backup_path).is_file()


def test_wrong_review_digest_and_unconfirmed_offline_are_rejected_before_claim(
    recovery: LedgerDeviceBindingRecovery,
) -> None:
    reviewed = command(recovery)
    for invalid, reason in (
        (
            reviewed.model_copy(update={"expected_review_sha256": "0" * 64}),
            "binding_recovery_review_digest_mismatch",
        ),
        (
            reviewed.model_copy(update={"runtime_confirmed_offline": False}),
            "binding_recovery_explicit_owner_review_required",
        ),
    ):
        with pytest.raises(SourcePolicyError, match=reason):
            recovery.adopt(invalid)
    assert not (recovery.ledger.directory / "binding-recovery").exists()


@pytest.mark.parametrize("phase", ["inspection", "lease", "lock_replaced"])
def test_context_teardown_failure_cannot_leave_a_successful_receipt(
    recovery: LedgerDeviceBindingRecovery,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    from satori.infrastructure.source_policy import binding_recovery as implementation

    reviewed = command(recovery)
    original_inspection = implementation._inspect
    original_lease = PolicyBarrier.lease

    @contextmanager
    def inspection_close_failure(
        ledger: InstallationPolicyLedger, canonical: Path
    ) -> Iterator[_Inspection]:
        with original_inspection(ledger, canonical) as observed:
            yield observed
        raise OSError("synthetic inspection finalizer failure")

    @contextmanager
    def lease_close_failure(self: PolicyBarrier, *, exclusive: bool) -> Iterator[None]:
        with original_lease(self, exclusive=exclusive):
            yield
            if phase == "lock_replaced":
                lock = recovery.ledger.lock_path
                replacement = lock.with_name("replacement-lock")
                replacement.write_bytes(b"")
                replacement.chmod(0o600)
                os.replace(replacement, lock)
            else:
                raise OSError("synthetic lease finalizer failure")

    if phase == "inspection":
        monkeypatch.setattr(implementation, "_inspect", inspection_close_failure)
    else:
        monkeypatch.setattr(PolicyBarrier, "lease", lease_close_failure)
    result = recovery.adopt(reviewed)
    assert result.status == "outcome_unknown"
    assert result.review_consumed is True
    assert result.normal_binding_valid is False
    assert result.canonical_floor_lock_unchanged is False
    assert result.descriptor_devices_updated is True
    assert json.loads(Path(result.receipt_path).read_bytes())["status"] == "outcome_unknown"
    assert Path(result.claim_path).is_file()
