"""Recovery preserves canonical state; imported content has no execution authority."""

import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from satori.application.recovery.contracts import RecoveryError
from satori.application.recovery.use_cases import WholeStateRecovery
from satori.infrastructure.artifacts import PrivateArtifactStore
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.recovery import SQLiteStoredSnapshot
from tests.test_stage4_conversation_memory import (
    activate,
    build_services,
    conversation_provider,
    meaningful_episode_provider,
    run_talk,
)
from tests.test_stage14_personality_persistence import _seed_personality_run


def recovery(database: Database, project_root: Path) -> WholeStateRecovery:
    return WholeStateRecovery(
        SQLiteStoredSnapshot(str(database.engine.url), alembic_config=project_root / "alembic.ini"),
        PrivateArtifactStore(),
    )


def stored_rows(path: Path) -> dict[str, list[tuple[object, ...]]]:
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return {
            name: sorted(connection.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr)
            for (name,) in tables
        }


def altered_archive(path: Path, *, manifest_changes: dict[str, object]) -> bytes:
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        snapshot = archive.read("snapshot.sqlite")
    manifest.update(manifest_changes)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("snapshot.sqlite", snapshot)
    return buffer.getvalue()


def test_full_archive_restores_history_evidence_owners_and_audit_after_restart(
    migrated_database: Database, project_root: Path, tmp_path: Path
) -> None:
    before = activate(migrated_database)
    services = build_services(
        migrated_database, conversation_provider(), meaningful_episode_provider()
    )
    run_talk(services)
    _seed_personality_run(migrated_database, identity_id=before.identity.identity_id)
    backend = SQLiteStoredSnapshot(
        str(migrated_database.engine.url), alembic_config=project_root / "alembic.ini"
    )
    baseline = stored_rows(backend.source_path)
    archive = tmp_path / "private" / "satori.state"
    use_cases = recovery(migrated_database, project_root)
    result = use_cases.export(archive)
    assert stored_rows(backend.source_path) == baseline
    assert use_cases.preview(archive) == result
    assert result.manifest.identity_id == before.identity.identity_id
    assert result.manifest.table_counts["episodic_memories"] > 0
    assert len(result.manifest.table_counts) == len(baseline)

    target = tmp_path / "restored" / "satori.db"
    use_cases.restore(archive, target, expected_sha256=result.archive_sha256)
    assert stored_rows(target) == baseline
    reopened = create_database(f"sqlite+pysqlite:///{target}")
    try:
        from satori.composition import build_initial_self_services

        assert build_initial_self_services(reopened).get_self.execute() == before
        assert stored_rows(target) == baseline
    finally:
        reopened.dispose()
    receipt = json.loads(target.with_name(target.name + ".receipt.json").read_bytes())
    assert receipt["status"] == "published"
    assert receipt["operation"] == "restore"
    assert "Пользователь" not in json.dumps(receipt, ensure_ascii=False)
    assert receipt["archive_sha256"] == result.archive_sha256
    with pytest.raises(RecoveryError, match="restore_target_exists"):
        use_cases.restore(archive, target, expected_sha256=result.archive_sha256)
    assert stored_rows(target) == baseline


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"format": "future-format"}, "invalid_archive"),
        ({"app_version": None}, "invalid_archive"),
        ({"credential_tokens": "untrusted extra field"}, "invalid_archive"),
        ({"snapshot_sha256": "0" * 64}, "snapshot_checksum_mismatch"),
        ({"identity_id": "wrong-identity"}, "manifest_inventory_mismatch"),
        ({"created_at": "2026-10-02T12:00:00"}, "invalid_archive"),
    ],
)
def test_invalid_archive_cannot_publish_or_change_current_state(
    migrated_database: Database,
    project_root: Path,
    tmp_path: Path,
    changes: dict[str, object],
    reason: str,
) -> None:
    activate(migrated_database)
    use_cases = recovery(migrated_database, project_root)
    archive = tmp_path / "export.state"
    use_cases.export(archive)
    damaged = tmp_path / "damaged.state"
    damaged.write_bytes(altered_archive(archive, manifest_changes=changes))
    target = tmp_path / "new.db"
    backend = SQLiteStoredSnapshot(
        str(migrated_database.engine.url), alembic_config=project_root / "alembic.ini"
    )
    baseline = stored_rows(backend.source_path)
    with pytest.raises(RecoveryError, match=reason):
        use_cases.restore(
            damaged, target, expected_sha256=hashlib.sha256(damaged.read_bytes()).hexdigest()
        )
    assert not target.exists()
    assert stored_rows(backend.source_path) == baseline


def test_reviewed_hash_and_existing_targets_are_enforced(
    migrated_database: Database, project_root: Path, tmp_path: Path
) -> None:
    activate(migrated_database)
    use_cases = recovery(migrated_database, project_root)
    archive = tmp_path / "export.state"
    result = use_cases.export(archive)
    target = tmp_path / "target.db"
    with pytest.raises(RecoveryError, match="reviewed_archive_changed"):
        use_cases.restore(archive, target, expected_sha256="0" * 64)
    target.write_bytes(b"existing installation")
    with pytest.raises(RecoveryError, match="restore_target_exists"):
        use_cases.restore(archive, target, expected_sha256=result.archive_sha256)
    assert target.read_bytes() == b"existing installation"
    source = SQLiteStoredSnapshot(
        str(migrated_database.engine.url), alembic_config=project_root / "alembic.ini"
    ).source_path
    with pytest.raises(RecoveryError, match="canonical_database_target"):
        use_cases.export(source, overwrite=True)
    with pytest.raises(RecoveryError, match="canonical_database_target"):
        use_cases.export(Path(str(source) + "-wal"), overwrite=True)


@pytest.mark.parametrize("payload", [b"broken", b"PK\x03\x04"])
def test_truncated_archives_are_safe(
    migrated_database: Database, project_root: Path, tmp_path: Path, payload: bytes
) -> None:
    path = tmp_path / "broken.state"
    path.write_bytes(payload)
    with pytest.raises(RecoveryError, match="invalid_archive"):
        recovery(migrated_database, project_root).preview(path)


def test_unknown_archive_member_is_not_extracted(
    migrated_database: Database, project_root: Path, tmp_path: Path
) -> None:
    path = tmp_path / "injected.state"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("../injected", b"instructions")
        archive.writestr("manifest.json", b"{}")
    with pytest.raises(RecoveryError, match="invalid_archive_members"):
        recovery(migrated_database, project_root).preview(path)
    assert not (tmp_path.parent / "injected").exists()


def test_restore_failure_before_database_publication_preserves_target_and_records_failure(
    migrated_database: Database,
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activate(migrated_database)
    use_cases = recovery(migrated_database, project_root)
    archive = tmp_path / "export.state"
    result = use_cases.export(archive)
    target = tmp_path / "target.db"
    original = PrivateArtifactStore.write

    def fail_publish(
        self: PrivateArtifactStore, path: Path, payload: bytes, *, overwrite: bool = False
    ) -> None:
        if path == target:
            raise OSError("injected_publish_failure")
        original(self, path, payload, overwrite=overwrite)

    monkeypatch.setattr(PrivateArtifactStore, "write", fail_publish)
    with pytest.raises(OSError, match="injected_publish_failure"):
        use_cases.restore(archive, target, expected_sha256=result.archive_sha256)
    assert not target.exists()
    receipt_path = target.with_name(target.name + ".receipt.json")
    assert json.loads(receipt_path.read_bytes())["status"] == "failed"


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_sidecar_created_during_restore_preparation_prevents_database_publication(
    migrated_database: Database,
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
) -> None:
    activate(migrated_database)
    use_cases = recovery(migrated_database, project_root)
    archive = tmp_path / "export.state"
    result = use_cases.export(archive)
    backend = SQLiteStoredSnapshot(
        str(migrated_database.engine.url), alembic_config=project_root / "alembic.ini"
    )
    baseline = stored_rows(backend.source_path)
    target = tmp_path / "target.db"
    sidecar = Path(str(target) + suffix)
    receipt_path = target.with_name(target.name + ".receipt.json")
    original = PrivateArtifactStore.write

    def occupy_target_family(
        self: PrivateArtifactStore, path: Path, payload: bytes, *, overwrite: bool = False
    ) -> None:
        original(self, path, payload, overwrite=overwrite)
        if path == receipt_path and json.loads(payload)["status"] == "prepared":
            sidecar.write_bytes(b"another process owns this target family")

    monkeypatch.setattr(PrivateArtifactStore, "write", occupy_target_family)
    with pytest.raises(RecoveryError, match="restore_target_exists"):
        use_cases.restore(archive, target, expected_sha256=result.archive_sha256)
    assert not target.exists()
    assert sidecar.read_bytes() == b"another process owns this target family"
    assert json.loads(receipt_path.read_bytes())["status"] == "failed"
    assert stored_rows(backend.source_path) == baseline


@pytest.mark.parametrize("fail_publication", [False, True])
def test_receipt_failure_never_reports_success_or_changes_source(
    migrated_database: Database,
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_publication: bool,
) -> None:
    activate(migrated_database)
    use_cases = recovery(migrated_database, project_root)
    archive = tmp_path / "export.state"
    result = use_cases.export(archive)
    backend = SQLiteStoredSnapshot(
        str(migrated_database.engine.url), alembic_config=project_root / "alembic.ini"
    )
    baseline = stored_rows(backend.source_path)
    target = tmp_path / "target.db"
    receipt_path = target.with_name(target.name + ".receipt.json")
    original = PrivateArtifactStore.write

    def fail_receipt(
        self: PrivateArtifactStore, path: Path, payload: bytes, *, overwrite: bool = False
    ) -> None:
        if path == target and fail_publication:
            raise OSError("injected_publish_failure")
        if path == receipt_path and json.loads(payload)["status"] != "prepared":
            raise OSError("injected_receipt_failure")
        original(self, path, payload, overwrite=overwrite)

    monkeypatch.setattr(PrivateArtifactStore, "write", fail_receipt)
    expected_error = OSError if fail_publication else RecoveryError
    with pytest.raises(expected_error):
        use_cases.restore(archive, target, expected_sha256=result.archive_sha256)
    assert target.exists() is not fail_publication
    assert json.loads(receipt_path.read_bytes())["status"] == "prepared"
    assert stored_rows(backend.source_path) == baseline
    if target.exists():
        with pytest.raises(RecoveryError, match="restore_target_exists"):
            use_cases.restore(archive, target, expected_sha256=result.archive_sha256)
