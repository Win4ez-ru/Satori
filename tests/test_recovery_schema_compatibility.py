"""Recovery supports declared prior/current schemas without activating migration."""

import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path

import pytest

from satori.application.recovery.contracts import RecoveryError
from satori.application.recovery.use_cases import WholeStateRecovery
from satori.infrastructure.artifacts import PrivateArtifactStore
from satori.infrastructure.persistence.migrations import upgrade_database
from satori.infrastructure.persistence.recovery import (
    SUPPORTED_SNAPSHOT_REVISIONS,
    SQLiteStoredSnapshot,
)


@pytest.mark.parametrize("revision", SUPPORTED_SNAPSHOT_REVISIONS)
def test_restore_preserves_supported_schema_and_bytes_until_explicit_upgrade(
    revision: str, tmp_path: Path, project_root: Path
) -> None:
    source = tmp_path / "source.db"
    upgrade_database(
        f"sqlite+pysqlite:///{source}", config_path=project_root / "alembic.ini", revision=revision
    )
    before = source.read_bytes()
    backend = SQLiteStoredSnapshot(
        f"sqlite+pysqlite:///{source}", alembic_config=project_root / "alembic.ini"
    )
    recovery = WholeStateRecovery(backend, PrivateArtifactStore())
    archive = tmp_path / "recovery.state"
    exported = recovery.export(archive)
    assert exported.manifest.alembic_revision == revision
    assert recovery.preview(archive) == exported
    assert source.read_bytes() == before
    target = tmp_path / "new-target.db"
    recovery.restore(archive, target, expected_sha256=exported.archive_sha256)
    with zipfile.ZipFile(archive) as bundle:
        assert target.read_bytes() == bundle.read("snapshot.sqlite")
    assert source.read_bytes() == before
    with closing(sqlite3.connect(target)) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            revision,
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(conversation_sessions)")}
        assert ("scope_verified" in columns) == (revision != "0013_conversation_failure_reason")
        episode_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(episodic_memories)")
        }
        assert ("aggregate_version" in episode_columns) == (
            revision
            in {
                "0015_memory_lifecycle",
                "0016_source_policy",
                "0017_erasure_intent",
                "0018_erasure_bodies",
                "0019_dialogue_bodies",
                "0020_knowledge_bodies",
                "0021_reflection_bodies",
                "0022_appraisal_bodies",
                "0023_audit_metadata",
                "0024_checkpoint_reasons",
            }
        )
        assert connection.execute("SELECT count(*) FROM audit_events").fetchone() == (0,)
    upgrade_database(f"sqlite+pysqlite:///{target}", config_path=project_root / "alembic.ini")
    with closing(sqlite3.connect(target)) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0024_checkpoint_reasons",
        )


def test_recovery_rejects_undeclared_revision_without_migration(
    tmp_path: Path, project_root: Path
) -> None:
    source = tmp_path / "unsupported.db"
    upgrade_database(
        f"sqlite+pysqlite:///{source}",
        config_path=project_root / "alembic.ini",
        revision="0012_personality_evolution",
    )
    before = source.read_bytes()
    backend = SQLiteStoredSnapshot(
        f"sqlite+pysqlite:///{source}", alembic_config=project_root / "alembic.ini"
    )
    with pytest.raises(RecoveryError, match=r"^unsupported_snapshot_schema$"):
        backend.inspect(source)
    assert source.read_bytes() == before


def test_recovery_rejects_revision_spoofing_and_weakened_scope_check(
    tmp_path: Path, project_root: Path
) -> None:
    source = tmp_path / "current.db"
    upgrade_database(f"sqlite+pysqlite:///{source}", config_path=project_root / "alembic.ini")
    backend = SQLiteStoredSnapshot(
        f"sqlite+pysqlite:///{source}", alembic_config=project_root / "alembic.ini"
    )
    with closing(sqlite3.connect(source)) as connection:
        connection.execute(
            "UPDATE alembic_version SET version_num='0013_conversation_failure_reason'"
        )
        connection.commit()
    with pytest.raises(RecoveryError, match=r"^unsupported_snapshot_revision$"):
        backend.inspect(source)
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("UPDATE alembic_version SET version_num='0015_memory_lifecycle'")
        connection.execute("PRAGMA writable_schema=ON")
        connection.execute(
            "UPDATE sqlite_schema SET sql=replace(sql, 'scope_verified IN (0, 1)', "
            "'scope_verified IN (0, 1, 2)') WHERE name='conversation_sessions'"
        )
        connection.commit()
    with pytest.raises(RecoveryError, match=r"^unsupported_snapshot_schema$"):
        backend.inspect(source)
