"""Bounded whole-state export, staged preview and explicit new-target recovery."""

import hashlib
import io
import json
import tempfile
import zipfile
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from satori.application.recovery.contracts import (
    RecoveryError,
    RecoveryManifest,
    RecoveryPreview,
    SnapshotInventory,
)
from satori.application.recovery.ports import PrivateArtifactPort, StoredSnapshotPort
from satori.core.clock import Clock, SystemClock

MAX_SNAPSHOT_BYTES = 268_435_456
MAX_MANIFEST_BYTES = 65_536
MAX_ARCHIVE_BYTES = MAX_SNAPSHOT_BYTES + MAX_MANIFEST_BYTES + 4096


class WholeStateRecovery:
    """Coordinates storage transport; never owns or derives persistent self."""

    def __init__(
        self,
        snapshots: StoredSnapshotPort,
        artifacts: PrivateArtifactPort,
        *,
        clock: Clock | None = None,
        reserved_paths: tuple[Path, ...] = (),
    ) -> None:
        self._snapshots = snapshots
        self._artifacts = artifacts
        self._clock = clock or SystemClock()
        self._reserved_paths = reserved_paths

    def check_artifact_target(self, path: Path) -> None:
        """Prevent export/receipt publication over canonical storage or sidecars."""

        target = path.expanduser().absolute()
        target = target.parent.resolve() / target.name
        source = self._snapshots.source_path
        reserved = {
            source,
            *(path.expanduser().resolve() for path in self._reserved_paths),
            *(Path(str(source) + suffix) for suffix in ("-wal", "-shm", "-journal")),
        }
        if target in reserved or any(
            target.exists() and item.exists() and target.samefile(item) for item in reserved
        ):
            raise RecoveryError("canonical_database_target")

    def export(self, output: Path, *, overwrite: bool = False) -> RecoveryPreview:
        """Capture committed stored state without opening application owners/jobs."""

        self.check_artifact_target(output)
        self.check_artifact_target(self._receipt_path(output))
        with tempfile.TemporaryDirectory(prefix="satori-recovery-") as temporary:
            snapshot = Path(temporary) / "snapshot.sqlite"
            inventory = self._snapshots.capture(snapshot)
            payload = self._artifacts.read(snapshot, max_bytes=MAX_SNAPSHOT_BYTES)
        manifest = RecoveryManifest(
            **inventory.model_dump(),
            format="satori-state-v1",
            app_version="0.1.0",
            created_at=self._clock.now(),
            snapshot_bytes=len(payload),
            snapshot_sha256=hashlib.sha256(payload).hexdigest(),
        )
        serialized_manifest = manifest.model_dump_json().encode("utf-8")
        if len(serialized_manifest) > MAX_MANIFEST_BYTES:
            raise RecoveryError("manifest_too_large")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("manifest.json", serialized_manifest)
            archive.writestr("snapshot.sqlite", payload)
        archive_bytes = buffer.getvalue()
        result = RecoveryPreview(
            archive_sha256=hashlib.sha256(archive_bytes).hexdigest(), manifest=manifest
        )
        # A receipt starts as intent, so a crash cannot falsely attest completion.
        operation_id = str(uuid4())
        self._write_receipt(output, result, operation_id, "export", "prepared", overwrite)
        try:
            self._artifacts.write(output, archive_bytes, overwrite=overwrite)
        except OSError:
            self._write_receipt(output, result, operation_id, "export", "failed", True)
            raise
        try:
            self._write_receipt(output, result, operation_id, "export", "published", True)
        except OSError:
            raise RecoveryError("archive_published_receipt_incomplete") from None
        return result

    def preview(self, archive: Path) -> RecoveryPreview:
        """Validate bounded bytes in staging and reveal metadata only."""

        payload = self._artifacts.read(archive, max_bytes=MAX_ARCHIVE_BYTES)
        result, _snapshot = self._validate(payload)
        return result

    def restore(self, archive: Path, target: Path, *, expected_sha256: str) -> RecoveryPreview:
        """Revalidate exactly reviewed bytes; atomically create a new offline database."""

        self.check_artifact_target(target)
        self.check_artifact_target(self._receipt_path(target))
        self._require_absent_database(target)
        payload = self._artifacts.read(archive, max_bytes=MAX_ARCHIVE_BYTES)
        if hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise RecoveryError("reviewed_archive_changed")
        result, snapshot = self._validate(payload)
        operation_id = str(uuid4())
        self._write_receipt(target, result, operation_id, "restore", "prepared", False)
        try:
            # Validation can be slow; the target family must still be unused at publication.
            self._require_absent_database(target)
            self._artifacts.write(target, snapshot)
        except (OSError, RecoveryError):
            self._write_receipt(target, result, operation_id, "restore", "failed", True)
            raise
        try:
            self._write_receipt(target, result, operation_id, "restore", "published", True)
        except OSError:
            raise RecoveryError("database_restored_receipt_incomplete") from None
        return result

    @staticmethod
    def _require_absent_database(target: Path) -> None:
        for suffix in ("", "-wal", "-shm", "-journal"):
            candidate = Path(str(target.expanduser()) + suffix)
            if candidate.exists() or candidate.is_symlink():
                raise RecoveryError("restore_target_exists")

    def _validate(self, payload: bytes) -> tuple[RecoveryPreview, bytes]:
        try:
            with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
                members = archive.infolist()
                if len(members) != 2 or {member.filename for member in members} != {
                    "manifest.json",
                    "snapshot.sqlite",
                }:
                    raise RecoveryError("invalid_archive_members")
                if any(
                    member.flag_bits & 1 or member.compress_type != zipfile.ZIP_STORED
                    for member in members
                ):
                    raise RecoveryError("unsupported_archive_encoding")
                for member in members:
                    limit = (
                        MAX_MANIFEST_BYTES
                        if member.filename == "manifest.json"
                        else MAX_SNAPSHOT_BYTES
                    )
                    if member.file_size <= 0 or member.file_size > limit:
                        raise RecoveryError("archive_member_size")
                manifest = RecoveryManifest.model_validate_json(archive.read("manifest.json"))
                snapshot_bytes = archive.read("snapshot.sqlite")
        except (zipfile.BadZipFile, ValidationError, UnicodeError, RuntimeError, EOFError):
            raise RecoveryError("invalid_archive") from None
        if len(snapshot_bytes) != manifest.snapshot_bytes or (
            hashlib.sha256(snapshot_bytes).hexdigest() != manifest.snapshot_sha256
        ):
            raise RecoveryError("snapshot_checksum_mismatch")
        with tempfile.TemporaryDirectory(prefix="satori-recovery-") as temporary:
            staged = Path(temporary) / "snapshot.sqlite"
            self._artifacts.write(staged, snapshot_bytes)
            inventory = self._snapshots.inspect(staged)
        if inventory != SnapshotInventory.model_validate(
            manifest.model_dump(include=set(SnapshotInventory.model_fields))
        ):
            raise RecoveryError("manifest_inventory_mismatch")
        return (
            RecoveryPreview(archive_sha256=hashlib.sha256(payload).hexdigest(), manifest=manifest),
            snapshot_bytes,
        )

    @staticmethod
    def _receipt_path(target: Path) -> Path:
        return target.with_name(target.name + ".receipt.json")

    def _write_receipt(
        self,
        target: Path,
        preview: RecoveryPreview,
        operation_id: str,
        operation: str,
        status: str,
        overwrite: bool,
    ) -> None:
        receipt = {
            "schema_version": 1,
            "operation_id": operation_id,
            "operation": operation,
            "status": status,
            "recorded_at": self._clock.now().isoformat(),
            "archive_sha256": preview.archive_sha256,
            "identity_id": preview.manifest.identity_id,
            "alembic_revision": preview.manifest.alembic_revision,
            "schema_sha256": preview.manifest.schema_sha256,
            "table_counts": preview.manifest.table_counts,
        }
        self._artifacts.write(
            self._receipt_path(target),
            (json.dumps(receipt, sort_keys=True) + "\n").encode("utf-8"),
            overwrite=overwrite,
        )
