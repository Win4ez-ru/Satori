"""Full-head work limits deny new intent append and preserve durable pending outcomes."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

import satori.application.erasure.intent_use_cases as intent_application
import satori.infrastructure.persistence.erasure as erasure_adapter
from satori.domain.erasure_preparation import ErasureOwnerPreparation
from satori.domain.source_policy import SourceEligibilitySnapshot, SourcePolicyError
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.models import AuditEventRow
from tests.erasure_support import create_case, provider_counts, stored_state
from tests.test_erasure_intent_integration import (
    _command,
    _head,
    _manager,
    _owner_rows,
    _request,
)
from tests.test_source_exclusion_production import _exclude


def test_proposed_head_budget_rejects_preview_and_commit_before_floor_append(
    migrated_database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = create_case(migrated_database, tmp_path / "intent-limit-floor")
    _exclude(case.database, case.runtime, case.derived_message_id)
    manager = _manager(case)
    first_request = _request(case)
    first = manager.commit(_command(manager, first_request, request_id="first-intent"))
    assert first.main_projection_current is True
    second_request = _request(case, message_id=case.derived_message_id)
    second_command = _command(manager, second_request, request_id="second-intent")
    single_rows = manager.preview(second_request).inventory.scanned_rows
    assert single_rows > 0
    before = stored_state(case)
    # One root fits the census budget; adding it to the approved head does not.
    monkeypatch.setattr(intent_application, "MAX_ERASURE_ASSESSMENT_WORK", single_rows)
    monkeypatch.setattr(erasure_adapter, "MAX_ERASURE_ASSESSMENT_WORK", single_rows)

    with pytest.raises(SourcePolicyError, match=r"^erasure_assessment_capacity_exceeded$"):
        manager.preview(second_request)
    with pytest.raises(SourcePolicyError, match=r"^erasure_assessment_capacity_exceeded$"):
        manager.commit(second_command)

    assert stored_state(case) == before
    with case.runtime.exclusive() as snapshot:
        assert snapshot.erasure_revision == 1
        assert case.runtime.erasure_intent("second-intent") is None
    assert provider_counts(case.services) == case.initial_provider_calls


def test_store_growth_over_resume_budget_keeps_intent_and_reports_pending(
    migrated_database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = create_case(migrated_database, tmp_path / "intent-growth-floor")
    manager = _manager(case)
    request = _request(case)
    command = _command(manager, request)
    committed = manager.commit(command)
    assert committed.main_projection_current is True
    with case.runtime.exclusive() as snapshot:
        rows = manager.inventory.inspect_many((committed.intent.root,), snapshot=snapshot)[
            0
        ].scanned_rows
    monkeypatch.setattr(intent_application, "MAX_ERASURE_ASSESSMENT_WORK", rows)
    monkeypatch.setattr(erasure_adapter, "MAX_ERASURE_ASSESSMENT_WORK", rows)
    with case.database.session_factory() as session:
        session.add(
            AuditEventRow(
                event_id="growth-event",
                schema_version=1,
                event_type="synthetic_growth",
                aggregate_type="fixture",
                aggregate_id="growth",
                occurred_at=datetime(2026, 10, 4, tzinfo=UTC),
                trace_id="growth-trace",
                details={},
            )
        )
        session.commit()
    before = stored_state(case)

    pending = manager.resume(request, request_id=command.request_id)

    assert pending.intent == committed.intent
    assert pending.phase == "intent_committed_preparation_pending"
    assert pending.failure_reason == "erasure_assessment_capacity_exceeded"
    assert pending.main_projection_current is False
    assert pending.preparation_current is False
    assert pending.physical_erasure is False
    assert pending.execution_authorized is False
    assert stored_state(case) == before
    assert provider_counts(case.services) == case.initial_provider_calls


def test_unsafe_owner_port_reason_cannot_lose_durable_pending_receipt_or_leak_text(
    migrated_database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = create_case(migrated_database, tmp_path / "intent-port-error-floor")
    manager = _manager(case)
    request = _request(case)
    command = _command(manager, request)
    before = _owner_rows(case.path)
    private_detail = "private_owner_payload_must_not_escape"

    def fail(
        preparations: tuple[ErasureOwnerPreparation, ...], *, snapshot: SourceEligibilitySnapshot
    ) -> None:
        assert snapshot.erasure_revision == 1
        assert preparations
        raise SourcePolicyError(f"unsafe port: {private_detail}")

    monkeypatch.setattr(manager.preparations, "persist", fail)
    pending = manager.commit(command)

    assert pending.intent.request_id == command.request_id
    assert pending.intent.result_erasure_revision == 1
    assert pending.phase == "intent_committed_preparation_pending"
    assert pending.failure_reason == "erasure_preparation_unavailable"
    assert pending.main_projection_current is False
    assert pending.execution_authorized is False
    assert pending.physical_erasure is False
    assert private_detail not in pending.model_dump_json()
    assert _head(case) is None
    assert _owner_rows(case.path) == before
    with case.runtime.exclusive():
        assert case.runtime.erasure_intent(command.request_id) == pending.intent
    assert provider_counts(case.services) == case.initial_provider_calls
