"""Three-row paired body invariants and metadata-first restoration rejection."""

import copy
import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import cast

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.appraisal_material import (
    AppraisalMaterialIndex,
    read_appraisal_material,
)
from satori.infrastructure.persistence.erasure_inventory import Row, Store
from tests.appraisal_material_support import appraisal_store, erase_pair


def test_paired_terminal_material_retains_exact_numeric_history_and_actual_input() -> None:
    store = appraisal_store()
    before = copy.deepcopy(store)
    index = AppraisalMaterialIndex.build(store)
    original = index.material("transition", terminal=False)
    terminal = index.material("transition", terminal=True)
    old_row = dict(cast(Row, original["transition"]))
    new_row = dict(cast(Row, terminal["transition"]))
    old_row.pop("reason_codes")
    old_row.pop("explanation_state")
    new_row.pop("reason_codes")
    new_row.pop("explanation_state")
    assert old_row == new_row
    assert terminal["source_input"] == original["source_input"]
    assert index.metadata_reference_count("transition") == 3
    assert store == before
    reviewed_terminal = index.digest("transition", terminal=True)
    erase_pair(store)
    assert AppraisalMaterialIndex.build(store).digest("transition") == reviewed_terminal
    assert "SYNTHETIC_REASON" not in json.dumps(terminal)


@pytest.mark.parametrize("change", ["numeric", "snapshot", "input", "audit-metadata"])
def test_mutation_of_each_retained_row_stales_the_review(change: str) -> None:
    store = appraisal_store()
    previous = AppraisalMaterialIndex.build(store).digest("transition")
    if change == "numeric":
        values = json.loads(str(store["affective_transitions"][0]["appraisal_payload"]))
        values["novelty"] = 0.5
        store["affective_transitions"][0]["appraisal_payload"] = json.dumps(values)
    elif change == "snapshot":
        values = json.loads(str(store["affective_transitions"][0]["state_before"]))
        values["fast"]["interest"] = 0.5
        store["affective_transitions"][0]["state_before"] = json.dumps(values)
    elif change == "input":
        store["source_inputs"][0]["policy_generation"] = 1
    else:
        store["audit_events"][0]["operator_metadata"] = "bounded_new_metadata"
    assert AppraisalMaterialIndex.build(store).digest("transition") != previous


@pytest.mark.parametrize(
    "change", ["extra-key", "wrong-copy", "duplicate", "duplicate-json", "wrong-trace", "wrong-id"]
)
def test_ambiguous_or_unmatched_owned_audit_never_grants_erasure(change: str) -> None:
    store = appraisal_store()
    row = store["audit_events"][0]
    details = json.loads(str(row["details"]))
    if change == "extra-key":
        details["unknown_private_text"] = "SYNTHETIC_BODY"
    elif change == "wrong-copy":
        details["appraisal_reason_codes"] = ["DIFFERENT_REASON"]
    elif change == "duplicate":
        store["audit_events"] += (dict(row, event_id="forged-audit"),)
    elif change == "duplicate-json":
        row["details"] = str(row["details"])[:-1] + ',"transition_id":"transition"}'
    elif change == "wrong-trace":
        row["trace_id"] = "other-trace"
    else:
        details["interaction_id"] = "other-interaction"
    if change not in {"duplicate-json", "duplicate"}:
        row["details"] = json.dumps(details)
    with pytest.raises(SourcePolicyError):
        AppraisalMaterialIndex.build(store).digest("transition")


@pytest.mark.parametrize(
    "change",
    [
        "extra-score",
        "string-score",
        "bool-score",
        "unknown-input",
        "duplicate-ref-key",
        "duplicate-root",
    ],
)
def test_untyped_numeric_or_input_metadata_fails_closed(change: str) -> None:
    store = appraisal_store()
    row = store["affective_transitions"][0]
    values = json.loads(str(row["appraisal_payload"]))
    if change == "extra-score":
        values["private_reason"] = "SYNTHETIC_BODY"
    elif change == "string-score":
        values["salience"] = "0.5"
    elif change == "bool-score":
        values["salience"] = True
    elif change == "unknown-input":
        store["source_inputs"][0]["proof_status"] = "unknown"
    elif change == "duplicate-ref-key":
        store["source_inputs"][0]["payload_refs"] = (
            '[{"kind":"episode","kind":"episode","payload_id":"memory","version":1}]'
        )
    else:
        store["source_inputs"][0]["root_message_ids"] = '["message","message"]'
    row["appraisal_payload"] = json.dumps(values)
    with pytest.raises(SourcePolicyError):
        AppraisalMaterialIndex.build(store).digest("transition")


@pytest.mark.parametrize("link", ["interaction_id", "source_message_id"])
def test_forged_audit_other_transition_id_cannot_hide_an_owned_duplicate(link: str) -> None:
    store = appraisal_store()
    row = dict(store["audit_events"][0], event_id="forged-audit", aggregate_id="forged-identity")
    details = json.loads(str(row["details"]))
    details.update(
        transition_id="forged-transition",
        interaction_id="other-interaction",
        source_message_id="other-message",
    )
    details[link] = store["affective_transitions"][0][link]
    row["details"] = json.dumps(details)
    store["audit_events"] += (row,)
    with pytest.raises(SourcePolicyError, match="appraisal_audit_pair_unproven"):
        AppraisalMaterialIndex.build(store).digest("transition")


@dataclass
class _Database:
    connection: Connection
    statements: list[str]


def _write_rows(connection: Connection, store: Store) -> None:
    for table, rows in store.items():
        if not rows:
            continue
        columns = tuple(rows[0])
        kinds = {
            column: (
                "INTEGER"
                if type(rows[0][column]) is int
                else "REAL"
                if type(rows[0][column]) is float
                else "TEXT"
            )
            for column in columns
        }
        declarations = ",".join(f'"{column}" {kinds[column]}' for column in columns)
        connection.exec_driver_sql(f'CREATE TABLE "{table}" ({declarations})')
        names = ",".join(f'"{column}"' for column in columns)
        marks = ",".join("?" for _ in columns)
        for row in rows:
            connection.exec_driver_sql(
                f'INSERT INTO "{table}" ({names}) VALUES ({marks})',
                tuple(row[column] for column in columns),
            )


@pytest.fixture
def database() -> Iterator[_Database]:
    # Projection-only corruption fixture: no admitted schema or floor authority is invented.
    store = appraisal_store()
    erase_pair(store)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.connect() as connection:
        _write_rows(connection, store)
        connection.commit()
        statements: list[str] = []

        @event.listens_for(engine, "before_cursor_execute")
        def capture(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: object,
        ) -> None:
            statements.append(statement)

        yield _Database(connection, statements)
    engine.dispose()


def test_sql_terminal_reader_returns_only_exact_paired_terminal_material(
    database: _Database,
) -> None:
    with Session(bind=database.connection) as session:
        index = read_appraisal_material(session, ("transition",))
    assert index.transitions["transition"]["reason_codes"] is None
    assert "appraisal_reason_codes" not in json.loads(str(index.audits["audit"]["details"]))


@pytest.mark.parametrize(
    "change",
    [
        "restored-reasons",
        "audit-json-null",
        "audit-restored-copy",
        "second-duplicate",
        "scope-unproven",
        "receipt-unknown",
        "forged-interaction-copy",
        "forged-source-copy",
        "unexpected-audit-key",
    ],
)
def test_sql_rejects_restored_or_ambiguous_pair_before_selecting_body(
    database: _Database, change: str
) -> None:
    connection = database.connection
    if change == "restored-reasons":
        connection.exec_driver_sql(
            "UPDATE affective_transitions SET reason_codes='[\"SYNTHETIC_REASON\"]'"
        )
    elif change in {"audit-json-null", "audit-restored-copy"}:
        connection.exec_driver_sql(
            "UPDATE audit_events SET details=json_set(details,'$.appraisal_reason_codes',json(?))",
            ("null" if change == "audit-json-null" else '["SYNTHETIC_REASON"]',),
        )
    elif change in {"second-duplicate", "forged-interaction-copy", "forged-source-copy"}:
        connection.exec_driver_sql(
            "INSERT INTO audit_events SELECT 'forged-audit',schema_version,event_type,"
            "aggregate_type,aggregate_id,trace_id,occurred_at,details FROM audit_events"
        )
        if change != "second-duplicate":
            connection.exec_driver_sql(
                "UPDATE audit_events SET aggregate_id='forged-identity',"
                "details=json_set(details,'$.transition_id','forged-transition',?,'unrelated') "
                "WHERE event_id='forged-audit'",
                (
                    "$.source_message_id"
                    if change == "forged-interaction-copy"
                    else "$.interaction_id",
                ),
            )
    elif change == "unexpected-audit-key":
        connection.exec_driver_sql(
            "UPDATE audit_events SET details=json_set(details,"
            "'$.unknown_private_text','SYNTHETIC_BODY')"
        )
    elif change == "scope-unproven":
        connection.exec_driver_sql("UPDATE conversation_sessions SET scope_verified=0")
    else:
        connection.exec_driver_sql("UPDATE source_inputs SET proof_status='unknown'")
    connection.commit()
    database.statements.clear()
    with Session(bind=connection) as session, pytest.raises(SourcePolicyError):
        read_appraisal_material(session, ("transition",))
    assert not any(statement.upper().startswith("SELECT *") for statement in database.statements)
