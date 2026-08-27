"""Stage 12 reflection lifecycle persistence, replay and restart tests."""

from dataclasses import replace
from datetime import timedelta

from sqlalchemy import event, inspect

from satori.core.reflection import ReflectionPurpose, ReflectionSourceKind
from satori.domain.personality_evolution import PERSONALITY_EVIDENCE_RESERVOIR_LIMIT
from satori.domain.reflection import (
    REFLECTION_POLICY_VERSION,
    REFLECTION_POLICY_VERSION_V3,
    REFLECTION_SCHEMA_VERSION,
    REFLECTION_SCHEMA_VERSION_V3,
    ReflectionAttempt,
    ReflectionAttemptStatus,
    ReflectionRun,
    ReflectionRunStatus,
    ReflectionSourceRecord,
    ReflectionTriggerKind,
    reflection_run_id,
    reflection_run_key,
    source_set_hash,
)
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.models.conversation import (
    ConversationInteractionRow,
    ConversationMessageRow,
    ConversationSessionRow,
)
from satori.infrastructure.persistence.models.positions import (
    PositionEvidenceRow,
    SatoriPositionRow,
)
from satori.infrastructure.persistence.models.reflection import (
    ReflectionRunRow,
    ReflectionSourceRow,
)
from satori.infrastructure.persistence.reflection_uow import SQLAlchemyReflectionUnitOfWork
from tests.test_stage4_conversation_memory import INTERACTION_TIME, activate
from tests.test_stage11_positions_integration import create_interaction


def test_run_sources_and_failed_attempt_survive_restart_without_raw_quote(
    migrated_database: Database,
) -> None:
    snapshot = activate(migrated_database)
    interaction_id = create_interaction(
        migrated_database,
        counterparty_id="alice",
        content="Наблюдение остаётся только в каноническом сообщении.",
        prefix="reflection-persistence",
        day=1,
    )
    with migrated_database.engine.connect() as connection:
        message_id = connection.exec_driver_sql(
            "SELECT message_id FROM conversation_messages "
            "WHERE interaction_id = ? AND role = 'user'",
            (interaction_id,),
        ).scalar_one()

    source = ReflectionSourceRecord(
        source_id="reflection-source-1",
        run_id="placeholder",
        ordinal=0,
        kind=ReflectionSourceKind.POSITION_EVIDENCE,
        evidence_edge_id="position-evidence-1",
        evidence_edge_version=1,
        root_interaction_id=interaction_id,
        root_message_id=message_id,
        root_counterparty_id="alice",
        observed_at=INTERACTION_TIME + timedelta(days=1),
        content_hash="a" * 64,
    )
    digest = source_set_hash((source,))
    key = reflection_run_key(identity_id=snapshot.identity.identity_id, source_hash=digest)
    run_id = reflection_run_id(key)
    source = replace(source, run_id=run_id)
    created_at = INTERACTION_TIME + timedelta(days=2)
    run = ReflectionRun(
        run_id=run_id,
        run_key=key,
        identity_id=snapshot.identity.identity_id,
        schema_version=REFLECTION_SCHEMA_VERSION,
        policy_version=REFLECTION_POLICY_VERSION,
        trigger_kind=ReflectionTriggerKind.EXPLICIT_LOCAL,
        source_set_hash=digest,
        status=ReflectionRunStatus.PENDING_GENERATION,
        aggregate_version=1,
        attempt_count=0,
        created_at=created_at,
        updated_at=created_at,
    )
    with SQLAlchemyReflectionUnitOfWork(migrated_database.session_factory) as unit:
        assert unit.reflection.create_run(run, (source,)) is True
        unit.commit()
    with SQLAlchemyReflectionUnitOfWork(migrated_database.session_factory) as unit:
        assert unit.reflection.create_run(run, (source,)) is False
        unit.commit()

    failed_at = created_at + timedelta(minutes=1)
    failed = replace(
        run,
        status=ReflectionRunStatus.RETRYABLE_FAILURE,
        aggregate_version=2,
        attempt_count=1,
        updated_at=failed_at,
    )
    attempt = ReflectionAttempt(
        attempt_id="reflection-attempt-1",
        run_id=run_id,
        ordinal=1,
        status=ReflectionAttemptStatus.FAILED,
        reason_code="provider_unavailable",
        provider="ollama",
        model="fixture",
        formation_method="ollama.structured_reflection.v1",
        started_at=created_at,
        finished_at=failed_at,
        metrics={},
    )
    with SQLAlchemyReflectionUnitOfWork(migrated_database.session_factory) as unit:
        unit.reflection.record_attempt(failed, attempt, (), expected_run_version=1)
        unit.commit()

    with SQLAlchemyReflectionUnitOfWork(migrated_database.session_factory) as restarted:
        assert restarted.reflection.get_run_by_key(key) == failed
        assert restarted.reflection.list_sources(run_id) == (source,)
        assert restarted.reflection.list_attempts(run_id) == (attempt,)
        assert restarted.reflection.list_proposals(run_id) == ()

    columns = {
        item["name"] for item in inspect(migrated_database.engine).get_columns("reflection_sources")
    }
    assert "quote" not in columns
    assert "content" not in columns


def test_personality_source_queries_bound_each_canonical_kind_before_materialization(
    migrated_database: Database,
) -> None:
    snapshot = activate(migrated_database)
    captured: list[tuple[str, object]] = []

    def capture(
        _connection: object,
        _cursor: object,
        statement: str,
        parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        captured.append((statement, parameters))

    event.listen(migrated_database.engine, "before_cursor_execute", capture)
    try:
        with SQLAlchemyReflectionUnitOfWork(migrated_database.session_factory) as unit:
            assert (
                unit.reflection.list_eligible_sources(
                    identity_id=snapshot.identity.identity_id,
                    limit=PERSONALITY_EVIDENCE_RESERVOIR_LIMIT,
                    purpose=ReflectionPurpose.PERSONALITY_EVOLUTION,
                )
                == ()
            )
    finally:
        event.remove(migrated_database.engine, "before_cursor_execute", capture)

    source_queries = tuple(
        (statement, parameters)
        for statement, parameters in captured
        if "FROM satori_position_evidence" in statement or "FROM memory_evidence" in statement
    )
    assert len(source_queries) == 2
    for statement, parameters in source_queries:
        assert " LIMIT ? OFFSET ?" in statement
        assert isinstance(parameters, tuple)
        assert PERSONALITY_EVIDENCE_RESERVOIR_LIMIT in parameters


def test_personality_source_limit_does_not_let_consumed_roots_shadow_new_evidence(
    migrated_database: Database,
) -> None:
    snapshot = activate(migrated_database)
    identity_id = snapshot.identity.identity_id
    position_id = "personality-reservoir-position"
    session_id = "personality-reservoir-session"
    observed_at = INTERACTION_TIME + timedelta(days=10)
    source_count = PERSONALITY_EVIDENCE_RESERVOIR_LIMIT + 1

    with migrated_database.session_factory() as session:
        session.add(
            ConversationSessionRow(
                session_id=session_id,
                identity_id=identity_id,
                counterparty_id="alice",
                schema_version=1,
                kind="implicit",
                status="closed",
                started_at=observed_at,
                ended_at=observed_at + timedelta(seconds=source_count),
            )
        )
        session.add(
            SatoriPositionRow(
                position_id=position_id,
                position_key="f" * 64,
                identity_id=identity_id,
                schema_version=1,
                aggregate_version=1,
                policy_version=1,
                formation_version=1,
                normalization_version=1,
                proposition="Новые наблюдения не должны скрываться consumed evidence.",
                normalized_proposition="новые наблюдения не должны скрываться consumed evidence",
                kind="belief",
                stance="support",
                confidence=0.8,
                status="active",
                value_key=None,
                competing_with_position_id=None,
                superseded_by_position_id=None,
                created_at=observed_at,
                updated_at=observed_at,
            )
        )
        session.flush()

        interactions: list[ConversationInteractionRow] = []
        messages: list[ConversationMessageRow] = []
        evidence_rows: list[PositionEvidenceRow] = []
        for index in range(source_count):
            suffix = f"{index:03d}"
            item_at = observed_at + timedelta(seconds=index)
            interaction_id = f"personality-reservoir-interaction-{suffix}"
            message_id = f"personality-reservoir-message-{suffix}"
            content = f"Каноническое наблюдение personality reservoir {suffix}."
            interactions.append(
                ConversationInteractionRow(
                    interaction_id=interaction_id,
                    session_id=session_id,
                    client_request_id=f"personality-reservoir-request-{suffix}",
                    trace_id=f"personality-reservoir-trace-{suffix}",
                    schema_version=1,
                    status="completed",
                    started_at=item_at,
                    completed_at=item_at,
                    provider="fixture-provider",
                    model="fixture-model",
                    finish_status="stop",
                    input_tokens=None,
                    output_tokens=None,
                    context_schema_version=1,
                    context_manifest_schema_version=1,
                    policy_id="fixture-policy",
                    policy_schema_version=1,
                    failure_kind=None,
                    relationship_processing_required=False,
                    model_processing_required=False,
                    position_processing_required=False,
                )
            )
            messages.append(
                ConversationMessageRow(
                    message_id=message_id,
                    session_id=session_id,
                    interaction_id=interaction_id,
                    schema_version=1,
                    role="user",
                    content=content,
                    created_at=item_at,
                    sequence=1,
                )
            )
            evidence_rows.append(
                PositionEvidenceRow(
                    evidence_id=f"personality-reservoir-evidence-{suffix}",
                    position_id=position_id,
                    source_message_id=message_id,
                    source_interaction_id=interaction_id,
                    source_counterparty_id="alice",
                    quote=content,
                    normalized_signature=f"personality reservoir signature {suffix}",
                    role="observation",
                    observed_at=item_at,
                )
            )
        session.add_all(interactions)
        session.flush()
        session.add_all(messages)
        session.flush()
        session.add_all(evidence_rows)
        session.flush()

        completed_runs: list[ReflectionRunRow] = []
        consumed_sources: list[ReflectionSourceRow] = []
        for index in range(PERSONALITY_EVIDENCE_RESERVOIR_LIMIT):
            run_index = index // 12
            run_id = f"personality-reservoir-run-{run_index:03d}"
            if index % 12 == 0:
                completed_runs.append(
                    ReflectionRunRow(
                        run_id=run_id,
                        run_key=f"{run_index:064x}",
                        identity_id=identity_id,
                        schema_version=REFLECTION_SCHEMA_VERSION_V3,
                        policy_version=REFLECTION_POLICY_VERSION_V3,
                        purpose=ReflectionPurpose.PERSONALITY_EVOLUTION.value,
                        trigger_kind=ReflectionTriggerKind.EXPLICIT_LOCAL.value,
                        source_set_hash=f"{run_index + 1000:064x}",
                        status=ReflectionRunStatus.COMPLETED.value,
                        aggregate_version=2,
                        attempt_count=1,
                        created_at=observed_at,
                        updated_at=observed_at,
                        completed_at=observed_at,
                    )
                )
            suffix = f"{index:03d}"
            consumed_sources.append(
                ReflectionSourceRow(
                    source_id=f"personality-reservoir-consumed-source-{suffix}",
                    run_id=run_id,
                    ordinal=index % 12,
                    kind=ReflectionSourceKind.POSITION_EVIDENCE.value,
                    evidence_edge_id=f"personality-reservoir-evidence-{suffix}",
                    evidence_edge_version=1,
                    root_interaction_id=f"personality-reservoir-interaction-{suffix}",
                    root_message_id=f"personality-reservoir-message-{suffix}",
                    root_counterparty_id="alice",
                    observed_at=observed_at + timedelta(seconds=index),
                    content_hash="a" * 64,
                    upstream_lineage_kind="position",
                    upstream_lineage_id=position_id,
                    affective_transition_id=None,
                    affective_state_version=None,
                    affective_signal_hash=None,
                )
            )
        session.add_all(completed_runs)
        session.flush()
        session.add_all(consumed_sources)
        session.commit()

    captured: list[str] = []

    def capture(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        captured.append(statement)

    event.listen(migrated_database.engine, "before_cursor_execute", capture)
    try:
        with SQLAlchemyReflectionUnitOfWork(migrated_database.session_factory) as unit:
            selected = unit.reflection.list_eligible_sources(
                identity_id=identity_id,
                limit=PERSONALITY_EVIDENCE_RESERVOIR_LIMIT,
                purpose=ReflectionPurpose.PERSONALITY_EVOLUTION,
            )
    finally:
        event.remove(migrated_database.engine, "before_cursor_execute", capture)

    assert tuple(item.root_message_id for item in selected) == (
        "personality-reservoir-message-256",
    )
    source_queries = tuple(
        statement
        for statement in captured
        if "FROM satori_position_evidence" in statement or "FROM memory_evidence" in statement
    )
    assert len(source_queries) == 2
    for statement in source_queries:
        assert "conversation_messages.message_id NOT IN" in statement
        assert statement.index("conversation_messages.message_id NOT IN") < statement.index(
            " LIMIT"
        )
