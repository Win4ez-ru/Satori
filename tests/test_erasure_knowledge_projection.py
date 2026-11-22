"""Corrupt partial bodies cannot be materialized by the terminal proof reader."""

from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Connection

from satori.domain.erasure_knowledge import KnowledgeBodyKind
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure_bodies import _ReadBudget
from satori.infrastructure.persistence.knowledge_material import KNOWLEDGE_OWNER_SPECS
from satori.infrastructure.persistence.knowledge_projection import terminal_knowledge_index
from tests.test_erasure_knowledge_owner import _apply, _knowledge_fixture
from tests.test_recovery_whole_state import stored_rows


@pytest.mark.parametrize("kind", list(KnowledgeBodyKind))
def test_partial_terminal_parent_is_denied_before_any_full_body_select(
    migrated_database: Database, kind: KnowledgeBodyKind
) -> None:
    _scope, action = _knowledge_fixture(migrated_database)
    _apply(migrated_database, action)
    target = next(item for item in action.proposal.targets if item.kind is kind)
    spec = KNOWLEDGE_OWNER_SPECS[kind]
    # This deliberately damages an isolated fixture. The proof reader must not
    # depend solely on SQL guards to keep partially resurrected content unread.
    with migrated_database.engine.begin() as connection:
        triggers = tuple(
            connection.exec_driver_sql(
                "SELECT name FROM sqlite_schema WHERE type='trigger' AND tbl_name=?",
                (spec.table,),
            ).scalars()
        )
        for trigger in triggers:
            connection.exec_driver_sql(f'DROP TRIGGER "{trigger}"')
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
        connection.exec_driver_sql(
            f'UPDATE "{spec.table}" SET "{spec.content_columns[-1]}"=? WHERE "{spec.key}"=?',
            ("SYNTHETIC_SURVIVING_PROTECTED_BODY", target.aggregate_id),
        )
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")
    before = stored_rows(Path(str(migrated_database.engine.url.database)))
    statements: list[str] = []

    def observe(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        many: bool,
    ) -> None:
        statements.append(statement)

    event.listen(migrated_database.engine, "before_cursor_execute", observe)
    try:
        with (
            migrated_database.engine.connect() as connection,
            pytest.raises(SourcePolicyError, match="body_reconciliation_required"),
        ):
            terminal_knowledge_index(
                connection, frozenset(((kind, target.aggregate_id),)), _ReadBudget()
            )
    finally:
        event.remove(migrated_database.engine, "before_cursor_execute", observe)
    assert not any(f'SELECT * FROM "{spec.table}"' in query for query in statements)
    assert any("body_state='erased'" in query for query in statements)
    assert stored_rows(Path(str(migrated_database.engine.url.database))) == before
