"""Generation-zero owner adoption after device-only descriptor drift.

This is never called by ordinary admission. Same inode numbers and matching metadata
do not prove the previous volume: an operator adopts the reviewed current files.
"""

import hashlib
import json
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Never

from satori.application.source_policy.binding_recovery import (
    AdoptCurrentBindingFiles,
    BindingAdoptionStatus,
    DeviceBindingRecoveryPlan,
    DeviceBindingRecoveryReceipt,
    RecoveryFileReview,
)
from satori.domain.source_policy import SOURCE_POLICY_VERSION, SourcePolicyError
from satori.infrastructure.artifacts import PrivateArtifactStore, _bound_parent
from satori.infrastructure.persistence.erasure import (
    ERASURE_CHECKPOINT_REASON_SCHEMA_DIGEST,
    REQUIRED_REPAIR_OWNERS,
)
from satori.infrastructure.persistence.recovery import _schema_digest, _schema_rows
from satori.infrastructure.source_policy.barrier import PolicyBarrier
from satori.infrastructure.source_policy.ledger import InstallationBinding, InstallationPolicyLedger

MAX_CANONICAL_BYTES = 268_435_456
MAX_POLICY_BYTES = 67_108_864
# Exact declared policy_0002 schema, including every user table/index/constraint.
# Computed from the trusted migration template only during engineering, never by preview.
POLICY2_SCHEMA_SHA256 = "e34685efea8f24a8e70213e4918b9bc822ede50fb0e83aec2e614a1167adaec5"


def _deny(code: str) -> Never:
    raise SourcePolicyError(code)


def _path(path: Path) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        _deny("binding_recovery_absolute_path_required")
    return path


def _parent(path: Path) -> tuple[int, int]:
    with _bound_parent(path, create=False) as (parent, _name):
        metadata = os.fstat(parent)
        if stat.S_IMODE(metadata.st_mode) != 0o700 or metadata.st_uid != os.getuid():
            _deny("binding_recovery_directory_not_private")
        return metadata.st_dev, metadata.st_ino


def _file(
    path: Path, *, maximum: int, retain_bytes: bool = False
) -> tuple[RecoveryFileReview, bytes]:
    _parent(path)
    with _bound_parent(path, create=False) as (parent, name):
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or before.st_size > maximum
        ):
            _deny("binding_recovery_file_invalid")
        payload = PrivateArtifactStore().read(path, max_bytes=maximum)
        after = os.stat(name, dir_fd=parent, follow_symlinks=False)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_uid")
        if any(getattr(before, field) != getattr(after, field) for field in fields):
            _deny("binding_recovery_file_changed")
    return RecoveryFileReview(
        device=after.st_dev,
        inode=after.st_ino,
        size=after.st_size,
        modified_ns=after.st_mtime_ns,
        changed_ns=after.st_ctime_ns,
        sha256=hashlib.sha256(payload).hexdigest(),
    ), payload if retain_bytes else b""


def _no_sidecars(*databases: Path) -> None:
    for database in databases:
        # lexists includes dangling links; reject every SQLite suffix, not only WAL.
        if any(os.path.lexists(path) for path in database.parent.glob(database.name + "-*")):
            _deny("binding_recovery_sidecar_present")


@contextmanager
def _read_database(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1.0)
    try:
        connection.enable_load_extension(False)
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA journal_mode").fetchone() != ("delete",):
            _deny("binding_recovery_journal_mode_unsupported")
        connection.execute("BEGIN")
        # Metadata reads acquire a shared SQLite lock until the enclosing review ends.
        connection.execute("SELECT count(*) FROM sqlite_schema").fetchone()
        yield connection
    finally:
        connection.close()


def _zero(connection: sqlite3.Connection, *tables: str) -> None:
    for table in tables:  # Closed trusted identifiers after exact schema admission.
        if connection.execute(f'SELECT count(*) FROM "{table}"').fetchone() != (0,):
            _deny("binding_recovery_nonzero_policy_state")


def _metadata(
    main: sqlite3.Connection, floor: sqlite3.Connection, binding: InstallationBinding
) -> tuple[str, str]:
    main_digest = _schema_digest(_schema_rows(main))
    floor_rows = tuple(
        tuple(str(part) for part in row)
        for row in floor.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        )
    )
    floor_digest = hashlib.sha256(
        json.dumps(floor_rows, ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()
    if (
        main_digest != ERASURE_CHECKPOINT_REASON_SCHEMA_DIGEST
        or main.execute("SELECT version_num FROM alembic_version").fetchall()
        != [("0024_checkpoint_reasons",)]
        or floor_digest != POLICY2_SCHEMA_SHA256
        or floor.execute("SELECT version_num FROM alembic_version").fetchall() != [("policy_0002",)]
    ):
        _deny("binding_recovery_schema_unsupported")
    for connection in (main, floor):
        if (
            connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
            or connection.execute("PRAGMA foreign_key_check").fetchone() is not None
        ):
            _deny("binding_recovery_integrity_invalid")
    installations = floor.execute(
        "SELECT singleton,installation_id,floor_id,policy_version FROM policy_installation"
    ).fetchall()
    if installations != [(1, binding.installation_id, binding.floor_id, SOURCE_POLICY_VERSION)]:
        _deny("binding_recovery_installation_mismatch")
    identities = main.execute(
        "SELECT identity_id,installation_slot FROM satori_identities"
    ).fetchall()
    lineages = floor.execute(
        "SELECT identity_id,lineage_id,generation,erasure_revision FROM policy_lineages"
    ).fetchall()
    bindings = main.execute(
        "SELECT identity_id,installation_id,floor_id,lineage_id,policy_generation "
        "FROM source_policy_bindings"
    ).fetchall()
    if len(identities) != 1 or identities[0][1] != 1 or len(lineages) != 1:
        _deny("binding_recovery_identity_scope_invalid")
    identity_id = identities[0][0]
    lineage_id = lineages[0][1]
    if any(
        not isinstance(value, str) or not value or value.strip() != value or len(value) > 128
        for value in (identity_id, lineage_id, binding.installation_id, binding.floor_id)
    ):
        _deny("binding_recovery_identity_scope_invalid")
    if lineages != [(identity_id, lineage_id, 0, 0)] or bindings != [
        (identity_id, binding.installation_id, binding.floor_id, lineage_id, 0)
    ]:
        _deny("binding_recovery_lineage_mismatch")
    _zero(floor, "policy_exclusions", "policy_receipts", "policy_erasure_intents")
    _zero(
        main,
        "source_excluded_roots",
        "owner_source_withdrawals",
        "erasure_policy_bindings",
        "erasure_body_bindings",
        "erasure_body_applications",
        "erasure_preparations",
    )
    owners = main.execute(
        "SELECT owner,identity_id,policy_generation FROM source_owner_repairs ORDER BY owner"
    ).fetchall()
    if owners != [(owner, identity_id, 0) for owner in sorted(REQUIRED_REPAIR_OWNERS)]:
        _deny("binding_recovery_owner_generation_mismatch")
    for table in ("source_inputs", "source_payload_receipts"):
        if (
            main.execute(
                f'SELECT 1 FROM "{table}" WHERE identity_id!=? OR policy_generation!=0 LIMIT 1',
                (identity_id,),
            ).fetchone()
            is not None
        ):
            _deny("binding_recovery_source_generation_mismatch")
    return identity_id, lineage_id


@dataclass(frozen=True, slots=True)
class _Inspection:
    plan: DeviceBindingRecoveryPlan
    binding: InstallationBinding
    descriptor_bytes: bytes


@contextmanager
def _inspect(ledger: InstallationPolicyLedger, canonical: Path) -> Iterator[_Inspection]:
    """No normal-admission override, writes, migrations, providers or body queries."""
    canonical = _path(canonical)
    _path(ledger.directory)
    floor_parent = _parent(ledger.binding_path)
    main_parent = _parent(canonical)
    _no_sidecars(canonical, ledger.database_path)
    descriptor, descriptor_bytes = _file(ledger.binding_path, maximum=4096, retain_bytes=True)
    floor, _opaque_floor = _file(ledger.database_path, maximum=MAX_POLICY_BYTES)
    lock, _opaque_lock = _file(ledger.lock_path, maximum=4096)
    main, _opaque_main = _file(canonical, maximum=MAX_CANONICAL_BYTES)
    binding = ledger.read_binding()
    old_device = binding.database_inode[0]
    if (
        binding.lock_inode[0] != old_device
        or old_device == floor.device
        or floor.device != lock.device
        or floor.device != descriptor.device
        or floor.device != main.device
        or binding.database_inode[1] != floor.inode
        or binding.lock_inode[1] != lock.inode
        or lock.size != 0
    ):
        _deny("binding_recovery_not_device_only")
    with ExitStack() as stack:
        main_connection = stack.enter_context(_read_database(canonical))
        floor_connection = stack.enter_context(_read_database(ledger.database_path))
        identity_id, lineage_id = _metadata(main_connection, floor_connection, binding)
        adopted_binding = InstallationBinding(
            binding.installation_id,
            binding.floor_id,
            (floor.device, floor.inode),
            (lock.device, lock.inode),
        )
        plan = DeviceBindingRecoveryPlan(
            policy_directory=str(ledger.directory),
            canonical_database=str(canonical),
            policy_directory_inode=floor_parent[1],
            canonical_directory_inode=main_parent[1],
            previous_device=old_device,
            current_device=floor.device,
            owner_uid=os.getuid(),
            descriptor=descriptor,
            floor=floor,
            lock=lock,
            canonical=main,
            identity_id=identity_id,
            installation_id=binding.installation_id,
            floor_id=binding.floor_id,
            lineage_id=lineage_id,
            canonical_schema_sha256=ERASURE_CHECKPOINT_REASON_SCHEMA_DIGEST,
            policy_schema_sha256=POLICY2_SCHEMA_SHA256,
            adopted_descriptor_sha256=hashlib.sha256(
                ledger._binding_descriptor_bytes(adopted_binding)
            ).hexdigest(),
            review_sha256="0" * 64,
        )
        plan = plan.model_copy(update={"review_sha256": plan.computed_digest()})
        _same_material(ledger, canonical, plan)
        yield _Inspection(plan, binding, descriptor_bytes)


def _same_material(
    ledger: InstallationPolicyLedger,
    canonical: Path,
    plan: DeviceBindingRecoveryPlan,
    *,
    descriptor: RecoveryFileReview | None = None,
) -> None:
    if _parent(ledger.binding_path) != (
        plan.current_device,
        plan.policy_directory_inode,
    ) or _parent(canonical) != (plan.current_device, plan.canonical_directory_inode):
        _deny("binding_recovery_directory_changed")
    _no_sidecars(canonical, ledger.database_path)
    for path, expected, maximum in (
        (ledger.binding_path, descriptor or plan.descriptor, 4096),
        (ledger.database_path, plan.floor, MAX_POLICY_BYTES),
        (ledger.lock_path, plan.lock, 4096),
        (canonical, plan.canonical, MAX_CANONICAL_BYTES),
    ):
        observed, _opaque = _file(path, maximum=maximum)
        if observed != expected:
            _deny("binding_recovery_review_stale")


class LedgerDeviceBindingRecovery:
    """Typed recovery port; the existing ledger publishes its descriptor itself."""

    def __init__(self, ledger: InstallationPolicyLedger, canonical_database: Path) -> None:
        self.ledger = ledger
        self.canonical = _path(canonical_database)

    def preview(self) -> DeviceBindingRecoveryPlan:
        try:
            with _inspect(self.ledger, self.canonical) as inspection:
                return inspection.plan
        except SourcePolicyError:
            raise
        except (OSError, ValueError, sqlite3.Error, KeyError, TypeError):
            raise SourcePolicyError("binding_recovery_inspection_failed") from None

    def adopt(self, command: AdoptCurrentBindingFiles) -> DeviceBindingRecoveryReceipt:
        # No inference from matching IDs constitutes consent. Literal declarations
        # and a fresh digest-bound plan are both mandatory operator inputs.
        if (
            command.plan.computed_digest() != command.expected_review_sha256
            or command.plan.review_sha256 != command.expected_review_sha256
        ):
            _deny("binding_recovery_review_digest_mismatch")
        return self.ledger.adopt_reviewed_device_binding(self.canonical, command)


def adopt_under_ledger_owner(
    ledger: InstallationPolicyLedger, canonical: Path, command: AdoptCurrentBindingFiles
) -> DeviceBindingRecoveryReceipt:
    """Called only through the ledger's explicit owner command, never normal reads."""
    expected = command.plan
    if (
        command.owner_adopts_current_files is not True
        or command.runtime_confirmed_offline is not True
    ):
        _deny("binding_recovery_explicit_owner_review_required")
    if (
        expected.owner_uid != os.getuid()
        or expected.computed_digest() != command.expected_review_sha256
        or expected.review_sha256 != command.expected_review_sha256
        or expected.policy_directory != str(ledger.directory)
        or expected.canonical_database != str(canonical)
    ):
        _deny("binding_recovery_review_digest_mismatch")
    artifacts = PrivateArtifactStore()
    audit_directory = ledger.directory / "binding-recovery"
    prefix = expected.review_sha256
    claim = audit_directory / (prefix + ".claim.json")
    backup = audit_directory / (prefix + ".descriptor-backup.json")
    receipt_path = audit_directory / (prefix + ".receipt.json")
    if any(os.path.lexists(path) for path in (claim, backup, receipt_path)):
        _deny("binding_recovery_review_consumed")
    barrier = PolicyBarrier(
        ledger.lock_path, expected_inode=(expected.lock.device, expected.lock.inode)
    )
    claim_confirmed = False
    observed_hash: str | None = None
    status: BindingAdoptionStatus = "outcome_unknown"
    normal_valid = unchanged = updated = False
    new_descriptor_sha256 = expected.adopted_descriptor_sha256
    try:
        with barrier.lease(exclusive=True), _inspect(ledger, canonical) as inspection:
            if inspection.plan != expected:
                _deny("binding_recovery_review_stale")
            if any(os.path.lexists(path) for path in (claim, backup, receipt_path)):
                _deny("binding_recovery_review_consumed")
            with _bound_parent(claim, create=True) as (parent, _name):
                metadata = os.fstat(parent)
                if stat.S_IMODE(metadata.st_mode) != 0o700 or metadata.st_uid != os.getuid():
                    _deny("binding_recovery_directory_not_private")
            adopted = InstallationBinding(
                inspection.binding.installation_id,
                inspection.binding.floor_id,
                (expected.floor.device, expected.floor.inode),
                (expected.lock.device, expected.lock.inode),
            )
            new_descriptor_sha256 = hashlib.sha256(
                ledger._binding_descriptor_bytes(adopted)
            ).hexdigest()
            if new_descriptor_sha256 != expected.adopted_descriptor_sha256:
                _deny("binding_recovery_review_stale")
            # Claim is durable before descriptor mutation. Any later failure consumes
            # the review and leaves evidence; a fresh ID is not a blind retry permit.
            try:
                artifacts.write(
                    claim,
                    json.dumps(
                        {
                            "format": "binding-adoption-claim-v1",
                            "request_id": command.request_id,
                            "review_sha256": prefix,
                            "owner_adoption": True,
                            "original_volume_proven": False,
                            "plan": expected.model_dump(mode="json"),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode(),
                )
            except Exception:
                _deny("binding_recovery_claim_failed_review_may_be_consumed")
            claim_confirmed = True
            publication_completed = False
            try:
                artifacts.write(backup, inspection.descriptor_bytes)
                stored_backup, _opaque = _file(backup, maximum=4096)
                if stored_backup.sha256 != expected.descriptor.sha256:
                    _deny("binding_recovery_backup_invalid")
                _same_material(ledger, canonical, expected)
                ledger._publish_binding_descriptor(
                    adopted, overwrite=True, expected_sha256=expected.descriptor.sha256
                )
                publication_completed = True
                descriptor, _payload = _file(ledger.binding_path, maximum=4096)
                observed_hash = descriptor.sha256
                _same_material(ledger, canonical, expected, descriptor=descriptor)
                if (
                    descriptor.sha256 != new_descriptor_sha256
                    or ledger.validate_binding() != adopted
                ):
                    _deny("binding_recovery_postvalidation_failed")
                status = "adopted"
                normal_valid = unchanged = updated = True
            except Exception:
                # A failed rename/fsync/receipt can already have published. Observe
                # exact current bytes; never claim rollback or automatically undo.
                status = "outcome_unknown"
                normal_valid = unchanged = updated = False
                try:
                    descriptor, _payload = _file(ledger.binding_path, maximum=4096)
                    observed_hash = descriptor.sha256
                    _same_material(ledger, canonical, expected, descriptor=descriptor)
                    unchanged = True
                    if descriptor.sha256 == expected.descriptor.sha256:
                        status = "not_applied_review_consumed"
                    else:
                        updated = (
                            descriptor.sha256 == new_descriptor_sha256
                            and ledger.validate_binding() == adopted
                        )
                        if updated:
                            normal_valid = True
                            # Matching readback does not prove a failed directory
                            # fsync durable. Only a returned publisher establishes
                            # that boundary; otherwise outcome stays unknown.
                            if publication_completed:
                                status = "adopted_audit_pending"
                except Exception:
                    pass
        # Success includes SQLite close and the barrier's final exact-lock guard.
        # No successful receipt is published until all finalizers have returned.
    except Exception as error:
        if not claim_confirmed:
            if isinstance(error, SourcePolicyError):
                raise
            raise SourcePolicyError("binding_recovery_unavailable") from None
        status = "outcome_unknown"
        normal_valid = unchanged = False
    result = DeviceBindingRecoveryReceipt(
        request_id=command.request_id,
        review_sha256=prefix,
        owner_uid=os.getuid(),
        status=status,
        descriptor_devices_updated=updated,
        canonical_floor_lock_unchanged=unchanged,
        normal_binding_valid=normal_valid,
        backup_path=str(backup),
        claim_path=str(claim),
        receipt_path=str(receipt_path),
        expected_adopted_descriptor_sha256=new_descriptor_sha256,
        observed_descriptor_sha256=observed_hash,
    )
    try:
        _parent(receipt_path)
        artifacts.write(receipt_path, result.model_dump_json(indent=2).encode())
    except Exception:
        if result.status == "adopted":
            result = result.model_copy(update={"status": "adopted_audit_pending"})
    return result
