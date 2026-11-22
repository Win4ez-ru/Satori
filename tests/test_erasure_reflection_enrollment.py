"""Explicit legacy schema paths inspect Reflection metadata without mapping payloads."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.database import create_database
from satori.infrastructure.persistence.migrations import upgrade_database
from satori.infrastructure.source_policy.installation import _available_reflection_ids


@pytest.mark.parametrize(
    "revision",
    [
        "0016_source_policy",
        "0017_erasure_intent",
        "0018_erasure_bodies",
        "0019_dialogue_bodies",
        "0020_knowledge_bodies",
    ],
)
def test_exact_old_schemas_have_explicit_metadata_only_legacy_path(
    sqlite_url: str,
    project_root: Path,
    revision: str,
) -> None:
    upgrade_database(sqlite_url, config_path=project_root / "alembic.ini", revision=revision)
    database = create_database(sqlite_url)
    statements: list[str] = []

    def capture(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        many: bool,
    ) -> None:
        statements.append(statement)

    event.listen(database.engine, "before_cursor_execute", capture)
    try:
        with database.session_factory() as session:
            assert _available_reflection_ids(session) is None
        assert not any("FROM reflection_proposals" in sql for sql in statements)
    finally:
        event.remove(database.engine, "before_cursor_execute", capture)
        database.engine.dispose()


@pytest.mark.parametrize("markers", [False, True])
def test_claimed_current_schema_missing_or_partial_markers_has_no_legacy_fallback(
    markers: bool,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.connect() as connection:
        connection.exec_driver_sql("CREATE TABLE alembic_version(version_num TEXT)")
        connection.exec_driver_sql("INSERT INTO alembic_version VALUES('0021_reflection_bodies')")
        columns = "proposal_id TEXT,payload TEXT" + (",body_state TEXT" if markers else "")
        connection.exec_driver_sql("CREATE TABLE reflection_proposals(" + columns + ")")
        with Session(bind=connection) as session, pytest.raises(SourcePolicyError):
            _available_reflection_ids(session)
    engine.dispose()
