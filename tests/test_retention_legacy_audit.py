"""An actual unsupported backlog withdrawal stays UNKNOWN after six body actions."""

from pathlib import Path

from satori.application.erasure.personality_reason_contracts import (
    CheckpointReasonCommand,
    CheckpointReasonRequest,
)
from satori.application.erasure.personality_reason_use_cases import CheckpointReasonManager
from satori.application.erasure.retention_contracts import RetentionStatusRequest
from satori.application.erasure.retention_use_cases import RetentionPlanner
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure_personality_reasons import (
    SQLiteCheckpointReasonBodyStore,
)
from satori.infrastructure.persistence.retention_inventory import SQLiteRetentionInventory
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime
from tests.checkpoint_reason_support import create_checkpoint_reason_fixture
from tests.retention_scenario import _bytes, _cognitive_counts
from tests.test_recovery_whole_state import stored_rows


def test_actual_unsupported_relationship_backlog_is_not_certified_or_backfilled(
    migrated_database: Database, tmp_path: Path
) -> None:
    # The established fixture intentionally has no relationship provider. Its
    # undecided first interaction produces a real backlog withdrawal on exclusion.
    fixture = create_checkpoint_reason_fixture(migrated_database, tmp_path / "legacy-floor")
    case = fixture.case
    case.runtime.upgrade_checkpoint_reason_policy()
    reasons = CheckpointReasonManager(
        case.runtime, SQLiteCheckpointReasonBodyStore(migrated_database)
    )
    request = CheckpointReasonRequest(
        identity_id=case.scope.identity_id, selectors=fixture.selectors
    )
    preview = reasons.preview(request)
    assert preview.readiness == "ready", preview.blockers
    committed = reasons.commit(
        CheckpointReasonCommand(
            identity_id=request.identity_id,
            selectors=request.selectors,
            request_id="legacy-backlog-reason-action",
            expected_generation=preview.generation,
            expected_erasure_revision=preview.erasure_revision,
            expected_body_revision=preview.body_revision,
            approval_digest=preview.approval_digest,
        )
    )
    assert committed.main_projection_current
    path = Path(str(migrated_database.engine.url.database))
    rows = stored_rows(path)
    files = _bytes(path, case.runtime)
    providers = _cognitive_counts(case.services)
    with migrated_database.engine.connect() as connection:
        unsupported = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM owner_source_withdrawals AS w "
            "JOIN audit_events AS a ON json_extract(a.details,'$.receipt_id')=w.receipt_id "
            "LEFT JOIN audit_metadata_bindings AS b ON b.event_id=a.event_id "
            "WHERE w.owner='relationship_backlog' AND a.aggregate_type=w.owner "
            "AND a.event_type='source_support_withdrawn' AND b.event_id IS NULL"
        ).scalar_one()
    assert unsupported == 1

    # The standalone runtime deliberately lacks a mirror callback. The adapter
    # must still prove the actual six-action main before issuing its partial report.
    bare = SourcePolicyRuntime(case.directory, case.scope.identity_id)
    planner = RetentionPlanner(bare, SQLiteRetentionInventory(migrated_database))
    status_request = RetentionStatusRequest(identity_id=case.scope.identity_id)
    status = planner.status(status_request)
    assert status.body_revision == 6
    assert status.whole_head_current is True
    assert status.coverage == "partial"
    assert status.logical_readiness == "blocked_unknown"
    assert status.unknown_rows == 1
    assert [(item.reason.value, item.rows) for item in status.reason_counts] == [
        ("audit_contract_unproven", 1)
    ]
    assert (
        next(item for item in status.table_counts if item.table == "audit_events").unknown_rows == 1
    )
    assert status.physical_erasure is False
    assert status.execution_authorized is False
    assert planner.status(status_request) == status
    assert stored_rows(path) == rows
    assert _bytes(path, case.runtime) == files
    assert _cognitive_counts(case.services) == providers
