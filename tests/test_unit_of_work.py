"""Commit and rollback semantics for the SQLAlchemy Unit of Work."""

import pytest
from sqlalchemy import text

from satori.application.unit_of_work import UnitOfWork
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.unit_of_work import SQLAlchemyUnitOfWork


def prepare_test_table(database: Database) -> None:
    """Create a test-only table that never enters production migrations."""

    with database.engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE foundation_uow_test (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        )


def stored_values(database: Database) -> list[str]:
    """Read committed values outside the Unit of Work under test."""

    with database.engine.connect() as connection:
        return list(
            connection.execute(text("SELECT value FROM foundation_uow_test ORDER BY id")).scalars()
        )


def insert_then_fail(unit_of_work: SQLAlchemyUnitOfWork) -> None:
    """Write inside a boundary, then force its exception rollback path."""

    with unit_of_work:
        unit_of_work.session.execute(
            text("INSERT INTO foundation_uow_test (value) VALUES (:value)"),
            {"value": "rolled-back"},
        )
        raise RuntimeError("force rollback")


def test_unit_of_work_commit_persists_changes(sqlite_url: str) -> None:
    """Explicit commit makes transaction work durable."""

    database = create_database(sqlite_url)
    prepare_test_table(database)
    unit_of_work = SQLAlchemyUnitOfWork(database.session_factory)
    assert isinstance(unit_of_work, UnitOfWork)

    with unit_of_work:
        unit_of_work.session.execute(
            text("INSERT INTO foundation_uow_test (value) VALUES (:value)"),
            {"value": "committed"},
        )
        unit_of_work.commit()

    assert stored_values(database) == ["committed"]
    database.dispose()


def test_unit_of_work_error_rolls_back_changes(sqlite_url: str) -> None:
    """An exception before commit leaves no partial write."""

    database = create_database(sqlite_url)
    prepare_test_table(database)
    unit_of_work = SQLAlchemyUnitOfWork(database.session_factory)

    with pytest.raises(RuntimeError, match="force rollback"):
        insert_then_fail(unit_of_work)

    assert stored_values(database) == []
    database.dispose()


def test_unit_of_work_without_commit_rolls_back(sqlite_url: str) -> None:
    """Leaving a boundary without explicit commit is rollback-safe."""

    database = create_database(sqlite_url)
    prepare_test_table(database)
    unit_of_work = SQLAlchemyUnitOfWork(database.session_factory)

    with unit_of_work:
        unit_of_work.session.execute(
            text("INSERT INTO foundation_uow_test (value) VALUES (:value)"),
            {"value": "not-committed"},
        )

    assert stored_values(database) == []
    database.dispose()
