"""A damaged protected schema cannot acquire the unmanaged historical-core path."""

from pathlib import Path

import pytest
from sqlalchemy import select

from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.memory_uow import SQLAlchemyEpisodicMemoryUnitOfWork
from satori.infrastructure.persistence.migrations import upgrade_database
from satori.infrastructure.persistence.models import SatoriIdentityRow
from satori.infrastructure.persistence.retrieval_uow import SQLAlchemyEpisodicMemoryIndexUnitOfWork
from satori.infrastructure.persistence.semantic_uow import SQLAlchemySemanticMemoryUnitOfWork
from satori.infrastructure.persistence.unit_of_work import SQLAlchemyUnitOfWork
from tests.test_recovery_whole_state import stored_rows
from tests.test_stage4_conversation_memory import activate

SOURCE_TABLES = (
    "source_policy_bindings",
    "source_excluded_roots",
    "source_inputs",
    "source_payload_receipts",
    "owner_source_withdrawals",
    "source_owner_repairs",
)
ERASURE_TABLES = ("erasure_policy_bindings", "erasure_preparations")


def _remove_policy(database: Database, *, remove_erasure: bool = True) -> None:
    with database.engine.begin() as connection:
        for table in SOURCE_TABLES:
            connection.exec_driver_sql(f"DROP TABLE {table}")
        if remove_erasure:
            for table in ERASURE_TABLES:
                connection.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")


def _deny_without_writes(
    database: Database, unit_type: type[SQLAlchemyUnitOfWork] = SQLAlchemyUnitOfWork
) -> None:
    path = Path(str(database.engine.url.database))
    before = stored_rows(path)
    before_bytes = path.read_bytes()
    unit = unit_type(database.session_factory)
    for _ in range(2):
        with (
            pytest.raises(SourcePolicyError, match=r"^source_policy_schema_incomplete$"),
            unit,
        ):
            pytest.fail("damaged source/erasure schema must deny before repositories or providers")
        with pytest.raises(RuntimeError, match="unit of work is not active"):
            _ = unit.session
    assert stored_rows(path) == before
    assert path.read_bytes() == before_bytes


@pytest.mark.parametrize(
    ("revision", "retain_erasure"),
    [
        ("0016_source_policy", False),
        ("0017_erasure_intent", False),
        ("0017_erasure_intent", True),
    ],
)
def test_known_protected_revision_denies_when_all_source_tables_are_missing(
    sqlite_url: str, project_root: Path, revision: str, retain_erasure: bool
) -> None:
    upgrade_database(sqlite_url, config_path=project_root / "alembic.ini", revision=revision)
    database = create_database(sqlite_url)
    try:
        activate(database)
        _remove_policy(database, remove_erasure=not retain_erasure)
        # There is deliberately no runtime or persisted source binding left to detect.
        assert "source_runtime" not in database.session_factory.kw.get("info", {})
        _deny_without_writes(database)
    finally:
        database.dispose()


@pytest.mark.parametrize("survivor", ERASURE_TABLES)
def test_partial_erasure_tables_deny_even_without_source_or_revision_metadata(
    migrated_database: Database, survivor: str
) -> None:
    activate(migrated_database)
    _remove_policy(migrated_database, remove_erasure=False)
    with migrated_database.engine.begin() as connection:
        for table in ERASURE_TABLES:
            if table != survivor:
                connection.exec_driver_sql(f"DROP TABLE {table}")
        connection.exec_driver_sql("DROP TABLE alembic_version")
    _deny_without_writes(migrated_database)


@pytest.mark.parametrize(
    "unit_type",
    [
        SQLAlchemyEpisodicMemoryUnitOfWork,
        SQLAlchemyEpisodicMemoryIndexUnitOfWork,
        SQLAlchemySemanticMemoryUnitOfWork,
    ],
)
def test_memory_retrieval_and_semantic_boundaries_inherit_damaged_erasure_denial(
    migrated_database: Database, unit_type: type[SQLAlchemyUnitOfWork]
) -> None:
    activate(migrated_database)
    _remove_policy(migrated_database)
    _deny_without_writes(migrated_database, unit_type)


def test_known_protected_revision_cannot_hide_behind_first_prepolicy_version_row(
    migrated_database: Database,
) -> None:
    _remove_policy(migrated_database)
    with migrated_database.engine.begin() as connection:
        connection.exec_driver_sql("DELETE FROM alembic_version")
        connection.exec_driver_sql(
            "INSERT INTO alembic_version(version_num) VALUES('0015_memory_lifecycle')"
        )
        connection.exec_driver_sql(
            "INSERT INTO alembic_version(version_num) VALUES('0017_erasure_intent')"
        )
    _deny_without_writes(migrated_database)


@pytest.mark.parametrize("revision", ["0014_memory_scope", "0015_memory_lifecycle"])
def test_explicit_prepolicy_core_fixture_still_commits_typed_initial_self(
    sqlite_url: str, project_root: Path, revision: str
) -> None:
    upgrade_database(sqlite_url, config_path=project_root / "alembic.ini", revision=revision)
    database = create_database(sqlite_url)
    try:
        identity = activate(database).identity.identity_id
        with SQLAlchemyUnitOfWork(database.session_factory) as unit:
            assert unit.source_admission is None
            assert unit.session.scalar(select(SatoriIdentityRow.identity_id)) == identity
        path = Path(str(database.engine.url.database))
        tables = stored_rows(path)
        assert set(tables).isdisjoint(SOURCE_TABLES)
        assert set(tables).isdisjoint(ERASURE_TABLES)
        assert tables["audit_events"]
    finally:
        database.dispose()
