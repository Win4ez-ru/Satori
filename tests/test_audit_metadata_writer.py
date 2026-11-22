"""Prospective sidecars remain atomic, optional certification of owner-created audits."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select

from satori.domain.audit_metadata import AuditMetadataFamily
from satori.domain.memory import EpisodeDecisionKind, EpisodeFormationDecision
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.audit_metadata_writer import bind_metadata
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.migrations import upgrade_database
from satori.infrastructure.persistence.models.audit_metadata import AuditMetadataBindingRow
from satori.infrastructure.persistence.models.initial_self import AuditEventRow
from satori.infrastructure.persistence.models.memory import EpisodeFormationDecisionRow
from satori.infrastructure.persistence.repositories.memory import SQLAlchemyEpisodicMemoryRepository
from tests.test_stage4_conversation_memory import INTERACTION_TIME, activate
from tests.test_stage11_positions_integration import create_interaction


def _decision(database: Database) -> tuple[str, EpisodeFormationDecision]:
    identity_id = activate(database).identity.identity_id
    interaction_id = create_interaction(
        database,
        counterparty_id="alice",
        content="SYNTHETIC_AUDIT_OWNER_SOURCE",
        prefix="audit-owner",
        day=1,
    )
    return identity_id, EpisodeFormationDecision(
        decision_id="audit-episode-decision",
        idempotency_key="audit-episode-idempotency",
        source_interaction_id=interaction_id,
        formation_version=1,
        policy_version=1,
        kind=EpisodeDecisionKind.SKIPPED,
        reason_code="provider_selected_skip",
        decided_at=INTERACTION_TIME + timedelta(days=1, minutes=1),
        trace_id="audit-owner-trace",
        formation_method="synthetic_test",
        provider="fake",
        model="fake",
        memory=None,
    )


def _event(decision: EpisodeFormationDecision, event_id: str) -> AuditEventRow:
    return AuditEventRow(
        event_id=event_id,
        schema_version=1,
        event_type="memory.episode_skipped",
        aggregate_type="interaction",
        aggregate_id=decision.source_interaction_id,
        occurred_at=decision.decided_at,
        trace_id=decision.trace_id,
        details={
            "source_interaction_id": decision.source_interaction_id,
            "formation_version": decision.formation_version,
            "policy_version": decision.policy_version,
            "decision_id": decision.decision_id,
            "reason_code": decision.reason_code,
        },
    )


def test_owner_commit_records_original_event_and_explicit_sidecar(
    migrated_database: Database,
) -> None:
    identity_id, decision = _decision(migrated_database)
    with migrated_database.session_factory() as session:
        recorded = SQLAlchemyEpisodicMemoryRepository(session).record_decision(
            decision, audit_event_id="audit-owner-event"
        )
        assert recorded
        session.commit()
    with migrated_database.session_factory() as session:
        event = session.get(AuditEventRow, "audit-owner-event")
        binding = session.get(AuditMetadataBindingRow, "audit-owner-event")
        assert event is not None
        assert binding is not None
        assert event.details == _event(decision, "unused").details
        assert binding.identity_id == identity_id
        assert binding.episode_decision_id == decision.decision_id
        assert binding.family == AuditMetadataFamily.EPISODE_FORMATION.value
        assert binding.source_input_id is None


def test_precommit_fault_rolls_back_owner_event_and_binding(
    migrated_database: Database,
) -> None:
    _identity_id, decision = _decision(migrated_database)

    def fail_before_commit() -> None:
        with migrated_database.session_factory() as session, session.begin():
            assert SQLAlchemyEpisodicMemoryRepository(session).record_decision(
                decision, audit_event_id="audit-fault-event"
            )
            session.flush()
            assert session.get(AuditMetadataBindingRow, "audit-fault-event") is not None
            raise RuntimeError("synthetic_precommit_fault")

    with pytest.raises(RuntimeError, match="synthetic_precommit_fault"):
        fail_before_commit()
    with migrated_database.session_factory() as session:
        assert session.get(EpisodeFormationDecisionRow, decision.decision_id) is None
        assert session.get(AuditEventRow, "audit-fault-event") is None
        assert session.get(AuditMetadataBindingRow, "audit-fault-event") is None


def test_unknown_reason_preserves_owner_and_original_audit_without_certification(
    migrated_database: Database,
) -> None:
    _identity_id, original = _decision(migrated_database)
    decision = replace(original, reason_code="SYNTHETIC_UNCLASSIFIED_REASON")
    with migrated_database.session_factory() as session:
        assert SQLAlchemyEpisodicMemoryRepository(session).record_decision(
            decision, audit_event_id="audit-unknown-event"
        )
        session.commit()
    with migrated_database.session_factory() as session:
        receipt = session.get(EpisodeFormationDecisionRow, decision.decision_id)
        event = session.get(AuditEventRow, "audit-unknown-event")
        assert receipt is not None
        assert event is not None
        assert receipt.reason_code == decision.reason_code
        assert event.details == _event(decision, "unused").details
        assert session.get(AuditMetadataBindingRow, "audit-unknown-event") is None


def test_explicit_owner_mismatch_never_receives_a_sidecar(
    migrated_database: Database,
) -> None:
    identity_id, decision = _decision(migrated_database)
    with migrated_database.session_factory() as session:
        event = _event(decision, "audit-wrong-owner")
        returned = bind_metadata(
            session,
            event,
            family=AuditMetadataFamily.EPISODE_FORMATION,
            identity_id=identity_id,
            owner_record_id="different-explicit-owner",
        )
        assert returned is event
        session.commit()
    with migrated_database.session_factory() as session:
        assert session.get(AuditEventRow, "audit-wrong-owner") is not None
        assert session.get(AuditMetadataBindingRow, "audit-wrong-owner") is None


def test_binder_does_not_flush_or_commit_pending_owner_state(
    migrated_database: Database,
) -> None:
    identity_id, decision = _decision(migrated_database)
    with migrated_database.session_factory() as session:
        event = _event(decision, "audit-unflushed-event")
        with (
            patch.object(session, "flush", side_effect=AssertionError("implicit_flush")),
            patch.object(session, "commit", side_effect=AssertionError("implicit_commit")),
        ):
            assert (
                bind_metadata(
                    session,
                    event,
                    family=AuditMetadataFamily.EPISODE_FORMATION,
                    identity_id=identity_id,
                    owner_record_id=decision.decision_id,
                )
                is event
            )
        pending = [row for row in session.new if isinstance(row, AuditMetadataBindingRow)]
        assert len(pending) == 1
        assert pending[0].episode_decision_id == decision.decision_id
        session.rollback()
    with migrated_database.session_factory() as session:
        assert (
            session.scalar(
                select(AuditEventRow.event_id).where(
                    AuditEventRow.event_id == "audit-unflushed-event"
                )
            )
            is None
        )


def test_existing_legacy_event_is_not_retroactively_certified_or_loaded(
    migrated_database: Database,
) -> None:
    identity_id, decision = _decision(migrated_database)
    with migrated_database.session_factory() as session:
        # Preserve the historical owner transaction before prospective sealing.
        with patch(
            "satori.infrastructure.persistence.repositories.memory.bind_metadata",
            side_effect=lambda _session, event, **_kwargs: event,
        ):
            assert SQLAlchemyEpisodicMemoryRepository(session).record_decision(
                decision, audit_event_id="audit-legacy-event"
            )
        session.commit()
    with migrated_database.session_factory() as session:
        event = session.get(AuditEventRow, "audit-legacy-event")
        assert event is not None
        session.expire(event)
        with patch.object(session, "execute", side_effect=AssertionError("legacy_body_read")):
            assert (
                bind_metadata(
                    session,
                    event,
                    family=AuditMetadataFamily.EPISODE_FORMATION,
                    identity_id=identity_id,
                    owner_record_id=decision.decision_id,
                )
                is event
            )
        session.commit()
    with migrated_database.session_factory() as session:
        event = session.get(AuditEventRow, "audit-legacy-event")
        assert event is not None
        assert event.details == _event(decision, "unused").details
        assert session.get(AuditMetadataBindingRow, "audit-legacy-event") is None


@pytest.mark.parametrize(
    "revision", ["0012_personality_evolution", "0013_conversation_failure_reason"]
)
def test_unmanaged_historical_activation_retains_original_audit_without_seal(
    sqlite_url: str, project_root: Path, revision: str
) -> None:
    upgrade_database(sqlite_url, config_path=project_root / "alembic.ini", revision=revision)
    database = create_database(sqlite_url)
    try:
        identity_id = activate(database).identity.identity_id
        with database.session_factory() as session:
            events = tuple(session.scalars(select(AuditEventRow)))
            assert len(events) == 1
            assert events[0].aggregate_id == identity_id
            assert events[0].event_type == "satori.activation"
        with database.engine.connect() as connection:
            tables = tuple(
                connection.exec_driver_sql(
                    "SELECT name FROM sqlite_schema WHERE type='table'"
                ).scalars()
            )
            assert "audit_metadata_bindings" not in tables
    finally:
        database.dispose()


def test_forged_early_revision_with_policy_tables_has_no_unmanaged_fallback(
    migrated_database: Database,
) -> None:
    identity_id, decision = _decision(migrated_database)
    with migrated_database.engine.begin() as connection:
        # Disposable damaged fixture: an early revision label cannot hide current
        # policy/body tables when the new sidecar table has been removed.
        connection.exec_driver_sql("DROP TABLE audit_metadata_bindings")
        connection.exec_driver_sql(
            "UPDATE alembic_version SET version_num='0013_conversation_failure_reason'"
        )
    with migrated_database.session_factory() as session:
        event = _event(decision, "forged-early-event")
        with pytest.raises(SourcePolicyError, match="erasure_schema_unsupported"):
            bind_metadata(
                session,
                event,
                family=AuditMetadataFamily.EPISODE_FORMATION,
                identity_id=identity_id,
                owner_record_id=decision.decision_id,
            )
        session.rollback()
