"""ORM dependency order and whole-transaction rollback, without metadata certification."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import InvalidRequestError

from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.models import AuditEventRow, SatoriIdentityRow
from satori.infrastructure.persistence.models.audit_metadata import AuditMetadataBindingRow
from satori.infrastructure.persistence.models.source_policy import (
    OwnerSourceWithdrawalRow,
    SourceInputRow,
)


@pytest.mark.parametrize("fail_before_binding", [False, True])
def test_pending_owner_event_identity_and_input_are_ordered_atomically(
    migrated_database: Database, fail_before_binding: bool
) -> None:
    now = datetime(2026, 10, 13, tzinfo=UTC)
    identity = SatoriIdentityRow(
        identity_id="pending-identity",
        installation_slot=1,
        name="Synthetic",
        activation_time=now,
        identity_version=1,
        seed_id="synthetic-seed",
        seed_schema_version=1,
        seed_content_hash="a" * 64,
    )
    audit = AuditEventRow(
        event_id="pending-event",
        schema_version=1,
        event_type="source_support_withdrawn",
        aggregate_type="positions",
        aggregate_id="synthetic-aggregate",
        occurred_at=now,
        trace_id="synthetic-request",
        details={},
    )
    receipt = OwnerSourceWithdrawalRow(
        receipt_id="pending-receipt",
        owner="positions",
        aggregate_id="synthetic-aggregate",
        request_id="synthetic-request",
        policy_generation=1,
        before_status="active",
        after_status="retracted",
        before_version=1,
        after_version=2,
        root_message_ids=[],
        recorded_at=now,
    )
    source = SourceInputRow(
        input_id="pending-input",
        identity_id=identity.identity_id,
        policy_generation=1,
        proof_status="unknown",
        legacy=False,
        root_message_ids=[],
        payload_refs=[],
    )
    binding = AuditMetadataBindingRow(
        event_id=audit.event_id,
        identity_id=identity.identity_id,
        contract_version=1,
        family="source_withdrawal",
        event_digest="b" * 64,
        source_input_id=source.input_id,
        withdrawal_receipt_id=receipt.receipt_id,
        audit_event=audit,
        identity=identity,
        source_input=source,
        withdrawal_receipt=receipt,
    )
    writes: list[str] = []

    def before_write(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _many: bool,
    ) -> None:
        if statement.startswith("INSERT INTO "):
            table = statement.split()[2]
            writes.append(table)
            if fail_before_binding and table == "audit_metadata_bindings":
                raise RuntimeError("injected_binding_write_failure")

    event.listen(migrated_database.engine, "before_cursor_execute", before_write)
    try:
        with migrated_database.session_factory() as session:
            # Reverse insertion order deliberately; only declared ORM dependencies
            # establish the physical order. These rows do not claim certification.
            session.add_all([binding, receipt, source, audit, identity])
            if fail_before_binding:
                with pytest.raises(RuntimeError, match="injected_binding_write_failure"):
                    session.commit()
                session.rollback()
            else:
                session.commit()
                session.expire(binding, ["audit_event"])
                loaded = session.scalar(select(AuditMetadataBindingRow))
                assert loaded is not None
                with pytest.raises(InvalidRequestError, match="lazy='raise'"):
                    _ = loaded.audit_event
    finally:
        event.remove(migrated_database.engine, "before_cursor_execute", before_write)
    binding_position = writes.index("audit_metadata_bindings")
    for parent in (
        "satori_identities",
        "audit_events",
        "source_inputs",
        "owner_source_withdrawals",
    ):
        assert writes.index(parent) < binding_position
    with migrated_database.engine.connect() as connection:
        for table in (
            "satori_identities",
            "audit_events",
            "source_inputs",
            "owner_source_withdrawals",
            "audit_metadata_bindings",
        ):
            count = connection.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar_one()
            assert count == (0 if fail_before_binding else 1)
