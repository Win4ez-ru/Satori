"""Normal body admission pins fresh DDL without rerunning operator route inventory."""

from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Connection

from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure import _admit_schema
from satori.infrastructure.persistence.recovery import (
    _cached_canonical_table_sql,
    _canonical_table_sql,
    _canonical_table_sql_uncached,
)
from satori.infrastructure.persistence.source_policy import verify_mirror
from tests.erasure_support import create_case
from tests.test_erasure_bodies_integration import command_for, prepare


@pytest.mark.parametrize("body_revision", [0, 1])
def test_normal_body_admission_keeps_exact_pin_without_all_route_column_queries(
    migrated_database: Database, tmp_path: Path, body_revision: int
) -> None:
    case = create_case(migrated_database, tmp_path / "normal-admission-floor")
    manager, request = prepare(case)
    if body_revision:
        assert manager.commit(command_for(manager, request)).main_projection_current
    statements: list[str] = []

    def observe(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _many: bool,
    ) -> None:
        statements.append(statement.lower())

    before = case.runtime.ledger.database_path.read_bytes()
    event.listen(case.database.engine, "before_cursor_execute", observe)
    try:
        with case.runtime.exclusive() as snapshot, case.database.session_factory() as session:
            assert snapshot.body_revision == body_revision
            verify_mirror(session, snapshot)
    finally:
        event.remove(case.database.engine, "before_cursor_execute", observe)
    schema_reads = [item for item in statements if "select type,name,tbl_name,sql" in item]
    column_reads = [item for item in statements if "pragma" in item and "table_" in item]
    assert len(schema_reads) == 1
    column_counts = Counter(
        item.split("(", 1)[1].removesuffix(")").strip('"') for item in column_reads
    )
    # Every head must census all five knowledge aggregates, Reflection proposals
    # and Appraisal explanations plus both identity-owned operator reason tables
    # to detect unauthorized terminal remnants. Their evidence/revision/source
    # tables remain outside this episode-only admission; full inventory is operator-only.
    knowledge_tables = {
        "semantic_claims",
        "user_model_claims",
        "world_model_claims",
        "satori_positions",
        "satori_inclinations",
    }
    reflection_tables = {"reflection_proposals", "affective_transitions"}
    reason_tables = {"personality_checkpoint_approvals", "personality_restore_events"}
    legacy_tables = {"erasure_policy_bindings", "conversation_messages"}
    if not body_revision:
        legacy_tables |= {
            "episodic_memories",
            "memory_evidence",
            "erasure_body_bindings",
            "erasure_body_applications",
        }
    assert (
        set(column_counts) == knowledge_tables | reflection_tables | reason_tables | legacy_tables
    )
    assert all(count == 1 for count in column_counts.values())
    assert len(column_reads) <= 15
    assert case.runtime.ledger.database_path.read_bytes() == before

    # The separate operator inventory still validates every registered route's
    # content/key columns; removing it from normal admission is not a waiver.
    statements.clear()
    event.listen(case.database.engine, "before_cursor_execute", observe)
    try:
        with case.database.engine.connect() as connection:
            _admit_schema(connection)
    finally:
        event.remove(case.database.engine, "before_cursor_execute", observe)
    assert any("pragma" in item and "semantic_claims" in item for item in statements)
    assert any("pragma" in item and "satori_positions" in item for item in statements)


@pytest.mark.parametrize("body_revision", [0, 1])
def test_warm_schema_transform_never_caches_missing_guard_admission(
    migrated_database: Database, tmp_path: Path, body_revision: int
) -> None:
    case = create_case(migrated_database, tmp_path / "fresh-ddl-floor")
    manager, request = prepare(case)
    if body_revision:
        assert manager.commit(command_for(manager, request)).main_projection_current
    with case.runtime.exclusive() as snapshot, case.database.session_factory() as session:
        verify_mirror(session, snapshot)
    before = case.runtime.ledger.database_path.read_bytes()
    with case.database.engine.begin() as connection:
        guard = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_schema WHERE type='trigger' AND name='episode_vector_update'"
        ).scalar_one()
        assert isinstance(guard, str)
        connection.exec_driver_sql("DROP TRIGGER episode_vector_update")
    with (
        case.runtime.exclusive() as snapshot,
        case.database.session_factory() as session,
        pytest.raises(SourcePolicyError, match="erasure_schema_unsupported"),
    ):
        verify_mirror(session, snapshot)
    with case.database.engine.begin() as connection:
        connection.exec_driver_sql(guard)
    with case.runtime.exclusive() as snapshot, case.database.session_factory() as session:
        verify_mirror(session, snapshot)
    assert case.runtime.ledger.database_path.read_bytes() == before


def test_bounded_pure_transform_matches_uncached_all_current_table_ddl(
    migrated_database: Database,
) -> None:
    _cached_canonical_table_sql.cache_clear()
    with migrated_database.engine.connect() as connection:
        declarations = tuple(
            str(row[0])
            for row in connection.exec_driver_sql(
                "SELECT sql FROM sqlite_schema WHERE type='table' ORDER BY name"
            )
        )
    assert declarations
    for sql in declarations:
        assert _canonical_table_sql(sql) == _canonical_table_sql_uncached(sql)
    misses = _cached_canonical_table_sql.cache_info().misses
    for sql in declarations:
        assert _canonical_table_sql(sql) == _canonical_table_sql_uncached(sql)
    assert _cached_canonical_table_sql.cache_info().misses == misses
    assert _cached_canonical_table_sql.cache_info().hits >= len(declarations)


def test_transform_key_is_exact_raw_ddl_and_oversized_input_is_not_retained() -> None:
    _cached_canonical_table_sql.cache_clear()
    original = "CREATE TABLE synthetic(x TEXT, CONSTRAINT bounds CHECK(length(x)>0))"
    changed = "CREATE TABLE synthetic(x TEXT, CONSTRAINT bounds CHECK(length(x)>1))"
    assert _canonical_table_sql(original) == _canonical_table_sql_uncached(original)
    assert _canonical_table_sql(changed) == _canonical_table_sql_uncached(changed)
    assert _canonical_table_sql(original) != _canonical_table_sql(changed)
    assert _cached_canonical_table_sql.cache_info().misses == 2
    before = _cached_canonical_table_sql.cache_info()
    oversized = "CREATE TABLE synthetic(x TEXT DEFAULT '" + "a" * 16_384 + "')"
    assert _canonical_table_sql(oversized) == _canonical_table_sql_uncached(oversized)
    assert _cached_canonical_table_sql.cache_info() == before
