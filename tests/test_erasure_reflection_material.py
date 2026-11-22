"""Closed Reflection fingerprints and SQL metadata-first internal proof boundaries."""

import copy
import json
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from satori.domain.source_policy import PayloadRef, SourcePolicyError
from satori.infrastructure.persistence.erasure_inventory import Store
from satori.infrastructure.persistence.reflection_material import (
    ReflectionMaterialIndex,
    read_reflection_material,
)
from satori.infrastructure.persistence.source_policy import require_current_payloads


def _store() -> Store:
    return {
        "reflection_proposals": (
            {
                "proposal_id": "proposal",
                "run_id": "run",
                "ordinal": 0,
                "target_owner": "values",
                "payload": json.dumps({"observation": "SYNTHETIC_BODY"}),
                "evidence_source_ids": '["source"]',
                "created_at": "2026-10-01",
                "body_state": "available",
                "body_action_id": None,
            },
        ),
        "reflection_runs": (
            {
                "run_id": "run",
                "identity_id": "identity",
                "status": "exhausted",
                "aggregate_version": 3,
                "attempt_count": 1,
            },
        ),
        "reflection_sources": ({"source_id": "source", "run_id": "run", "content_hash": "a" * 64},),
        "reflection_attempts": (
            {"attempt_id": "attempt", "run_id": "run", "metrics": '{"tokens":10}'},
        ),
        "reflection_outcomes": (),
    }


def test_terminal_material_removes_only_payload_and_normalizes_only_future_action_id() -> None:
    store = _store()
    before = copy.deepcopy(store)
    index = ReflectionMaterialIndex.build(store)
    original = index.material("proposal", terminal=False)
    terminal = index.material("proposal", terminal=True)
    assert original["run"] == terminal["run"]
    assert original["sources"] == terminal["sources"]
    assert original["attempts"] == terminal["attempts"]
    assert original["outcomes"] == terminal["outcomes"] == []
    assert index.metadata_reference_count("proposal") == 4
    assert store == before
    body = dict(
        store["reflection_proposals"][0],
        payload=None,
        body_state="erased",
        body_action_id="assigned-action",
    )
    actual = ReflectionMaterialIndex.build(dict(store, reflection_proposals=(body,)))
    assert actual.digest("proposal") == index.digest("proposal", terminal=True)
    assert actual.digest("proposal") != index.digest("proposal")


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("reflection_runs", "aggregate_version", 4),
        ("reflection_runs", "status", "completed"),
        ("reflection_sources", "content_hash", "b" * 64),
        ("reflection_attempts", "metrics", '{"tokens":11}'),
        ("reflection_proposals", "evidence_source_ids", '["different"]'),
    ],
)
def test_every_reviewed_parent_and_selected_metadata_mutation_changes_digest(
    table: str,
    column: str,
    value: object,
) -> None:
    store = _store()
    digest = ReflectionMaterialIndex.build(store).digest("proposal")
    changed = copy.deepcopy(store)
    changed[table][0][column] = value
    assert ReflectionMaterialIndex.build(changed).digest("proposal") != digest


def test_empty_outcome_history_is_bound_and_new_outcome_stales_review() -> None:
    store = _store()
    index = ReflectionMaterialIndex.build(store)
    changed: Store = dict(
        store,
        reflection_outcomes=(
            {
                "outcome_id": "outcome",
                "proposal_id": "proposal",
                "decision": "rejected",
                "reason_code": "owner_disabled",
            },
        ),
    )
    next_index = ReflectionMaterialIndex.build(changed)
    assert next_index.metadata_reference_count("proposal") == 5
    assert next_index.digest("proposal") != index.digest("proposal")


@dataclass
class _Database:
    connection: Connection
    statements: list[str]


@pytest.fixture
def database() -> Iterator[_Database]:
    # A deliberately small internal-reader fixture; it grants no operational schema/floor authority.
    engine = create_engine("sqlite+pysqlite:///:memory:")
    statements: list[str] = []
    with engine.connect() as connection:
        for table, rows in _store().items():
            keys = tuple(rows[0]) if rows else ("outcome_id", "proposal_id")
            integer_columns = {"ordinal", "aggregate_version", "attempt_count"}
            columns = ",".join(
                f'"{key}" {"INTEGER" if key in integer_columns else "TEXT"}' for key in keys
            )
            connection.exec_driver_sql(f'CREATE TABLE "{table}" ({columns})')
            for row in rows:
                marks = ",".join("?" for _ in keys)
                connection.exec_driver_sql(
                    f'INSERT INTO "{table}" VALUES ({marks})', tuple(row[key] for key in keys)
                )
        connection.commit()

        def capture(
            connection: Connection,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            many: bool,
        ) -> None:
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", capture)
        yield _Database(connection, statements)
        event.remove(engine, "before_cursor_execute", capture)
    engine.dispose()


def _raw_payload_reads(statements: list[str]) -> list[str]:
    return [s for s in statements if s.lstrip().startswith('SELECT * FROM "reflection_proposals"')]


@pytest.mark.parametrize("state", ["available", "restored"])
def test_ordinary_reader_denies_available_or_restored_body_before_select_star(
    database: _Database,
    state: str,
) -> None:
    if state == "restored":
        database.connection.exec_driver_sql(
            "UPDATE reflection_proposals SET body_state='erased',body_action_id='action'"
        )
    database.statements.clear()
    with (
        Session(bind=database.connection) as session,
        pytest.raises(SourcePolicyError, match="body_reconciliation_required"),
    ):
        read_reflection_material(session, ("proposal",))
    assert _raw_payload_reads(database.statements) == []


def test_privileged_preimage_still_denies_nonterminal_parent_before_payload_read(
    database: _Database,
) -> None:
    database.connection.exec_driver_sql("UPDATE reflection_runs SET status='applying'")
    database.statements.clear()
    with (
        Session(bind=database.connection) as session,
        pytest.raises(SourcePolicyError, match="body_reconciliation_required"),
    ):
        read_reflection_material(session, ("proposal",), require_terminal=False)
    assert _raw_payload_reads(database.statements) == []


def test_batched_reader_reads_shared_parent_once_and_matches_census_digest(
    database: _Database,
) -> None:
    with Session(bind=database.connection) as session:
        actual = read_reflection_material(session, ("proposal",), require_terminal=False)
    assert actual.digest("proposal") == ReflectionMaterialIndex.build(_store()).digest("proposal")
    assert sum('SELECT * FROM "reflection_runs"' in sql for sql in database.statements) == 1


@pytest.mark.parametrize("version", [1, 2])
def test_current_input_guard_never_materializes_erased_payload_even_at_logical_version_one(
    database: _Database,
    version: int,
) -> None:
    database.connection.exec_driver_sql(
        "UPDATE reflection_proposals SET payload=NULL,body_state='erased',body_action_id='action'"
    )
    database.statements.clear()
    with Session(bind=database.connection) as session, pytest.raises(SourcePolicyError):
        require_current_payloads(session, (PayloadRef("reflection_proposal", "proposal", version),))
    assert not any("SELECT *" in sql for sql in database.statements)
    assert not any("reflection_proposals.payload" in sql for sql in database.statements)
