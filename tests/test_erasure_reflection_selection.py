"""Actual managed Reflection selection never grants destructive authority to malformed lineage."""

from pathlib import Path

import pytest

from satori.infrastructure.persistence.database import Database
from tests.erasure_reflection_support import create_reflection_fixture, prepare_reflection
from tests.erasure_support import provider_counts
from tests.test_recovery_whole_state import stored_rows


@pytest.mark.parametrize(
    ("sql", "arguments"),
    [
        ("UPDATE reflection_runs SET source_set_hash=?", ("0" * 64,)),
        ("UPDATE reflection_runs SET run_key=?", ("0" * 64,)),
        ("UPDATE reflection_sources SET evidence_edge_version=2", ()),
        ("UPDATE reflection_sources SET evidence_edge_id=? WHERE ordinal=0", ("missing-edge",)),
        ("UPDATE reflection_sources SET root_counterparty_id=? WHERE ordinal=0", ("bob",)),
        (
            "UPDATE reflection_proposals SET evidence_source_ids=? WHERE ordinal=0",
            ('["missing-source"]',),
        ),
        (
            "UPDATE source_payload_receipts SET proof_status='unknown' "
            "WHERE kind='reflection_proposal'",
            (),
        ),
    ],
)
def test_corrupt_or_unknown_full_lineage_is_blocked_without_body_floor_or_provider_mutations(
    migrated_database: Database,
    tmp_path: Path,
    sql: str,
    arguments: tuple[str, ...],
) -> None:
    fixture = create_reflection_fixture(migrated_database, tmp_path / "selection-floor")
    manager, request = prepare_reflection(fixture)
    with migrated_database.engine.begin() as connection:
        connection.exec_driver_sql(sql, arguments)
    before = stored_rows(fixture.case.path)
    floor = fixture.case.runtime.ledger.database_path.read_bytes()
    calls = provider_counts(fixture.case.services), len(fixture.provider.requests)
    preview = manager.preview(request)
    assert preview.readiness == "blocked"
    assert "reflection_body_provenance_unproven" in preview.blockers
    assert stored_rows(fixture.case.path) == before
    assert fixture.case.runtime.ledger.database_path.read_bytes() == floor
    assert (provider_counts(fixture.case.services), len(fixture.provider.requests)) == calls
