"""Unmanaged historical core compatibility never downgrades a damaged current policy schema."""

from pathlib import Path

import pytest

from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.unit_of_work import SQLAlchemyUnitOfWork


@pytest.mark.parametrize("remove_all", [False, True])
def test_missing_policy_tables_at_current_revision_never_become_unmanaged(
    migrated_database: Database, remove_all: bool
) -> None:
    tables = ["source_policy_bindings"]
    if remove_all:
        tables.extend(
            [
                "source_excluded_roots",
                "source_inputs",
                "source_payload_receipts",
                "owner_source_withdrawals",
                "source_owner_repairs",
            ]
        )
    with migrated_database.engine.begin() as connection:
        for table in tables:
            connection.exec_driver_sql(f"DROP TABLE {table}")
    with (
        pytest.raises(SourcePolicyError, match="source_policy_schema_incomplete"),
        SQLAlchemyUnitOfWork(migrated_database.session_factory),
    ):
        pytest.fail("damaged policy schema cannot acquire a permissive core unit")


def test_bound_main_requires_runtime_even_with_copied_static_snapshot(
    migrated_database: Database, tmp_path: Path
) -> None:
    from tests.test_source_policy_installation import _enrolled

    runtime = _enrolled(migrated_database, tmp_path)
    with runtime.shared() as snapshot:
        pass
    migrated_database.session_factory.configure(info={"source_snapshot": snapshot})
    with (
        pytest.raises(SourcePolicyError, match="source_policy_binding_required"),
        SQLAlchemyUnitOfWork(migrated_database.session_factory),
    ):
        pytest.fail("copied projection is not an independently trusted runtime")
