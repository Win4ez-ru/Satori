"""Clean, incremental, and reversible migration tests."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from satori.composition import build_initial_self_services
from satori.domain.personality_evolution import PersonalityCheckpointKind, checkpoint_hash
from satori.infrastructure.persistence.conversation_uow import (
    SQLAlchemyConversationHistoryUnitOfWork,
)
from satori.infrastructure.persistence.database import create_database
from satori.infrastructure.persistence.migrations import downgrade_database, upgrade_database
from satori.infrastructure.seeds.loader import JsonSeedLoader

STAGE_2_TABLES = {
    "alembic_version",
    "audit_events",
    "satori_identities",
    "satori_personality_states",
    "satori_personality_traits",
    "satori_value_sets",
    "satori_values",
}
STAGE_4_TABLES = STAGE_2_TABLES | {
    "conversation_sessions",
    "conversation_interactions",
    "conversation_messages",
    "episodic_memories",
    "memory_evidence",
    "episode_formation_decisions",
}
STAGE_5_TABLES = STAGE_4_TABLES | {"episodic_memory_embeddings"}
STAGE_6_TABLES = STAGE_5_TABLES | {
    "semantic_claims",
    "semantic_claim_evidence",
    "semantic_formation_decisions",
    "semantic_claim_revisions",
}
STAGE_7_TABLES = STAGE_6_TABLES | {"affective_states", "affective_transitions"}
STAGE_8_TABLES = STAGE_7_TABLES | {
    "relationship_states",
    "relationship_decisions",
    "relationship_transitions",
}
STAGE_9_TABLES = STAGE_8_TABLES | {
    "model_formation_decisions",
    "user_model_claims",
    "user_model_claim_evidence",
    "user_model_claim_revisions",
    "world_model_claims",
    "world_model_claim_evidence",
    "world_model_claim_revisions",
}
STAGE_11_TABLES = STAGE_9_TABLES | {
    "position_formation_decisions",
    "satori_positions",
    "satori_position_evidence",
    "satori_position_revisions",
}
STAGE_12_TABLES = STAGE_11_TABLES | {
    "reflection_attempts",
    "reflection_outcomes",
    "reflection_proposals",
    "reflection_runs",
    "reflection_sources",
}
STAGE_13_TABLES = STAGE_12_TABLES | {
    "satori_inclination_evidence",
    "satori_inclination_revisions",
    "satori_inclinations",
}
STAGE_14_TABLES = STAGE_13_TABLES | {
    "personality_checkpoint_approvals",
    "personality_checkpoint_traits",
    "personality_checkpoints",
    "personality_evidence",
    "personality_restore_events",
    "personality_revisions",
}
ACTIVE_SCHEMA_REVISION = "0013_conversation_failure_reason"


def current_revision(database_url: str) -> str | None:
    """Read Alembic's current revision from a migrated database."""

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()


def table_names(database_url: str) -> set[str]:
    """Inspect the complete physical schema."""

    engine = create_engine(database_url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def column_names(database_url: str, table_name: str) -> set[str]:
    engine = create_engine(database_url)
    try:
        return {column["name"] for column in inspect(engine).get_columns(table_name)}
    finally:
        engine.dispose()


def test_migration_upgrade_from_clean_database(
    sqlite_url: str,
    project_root: Path,
) -> None:
    """A new database reaches the active Stage 14 schema without fabricating self."""

    upgrade_database(sqlite_url, config_path=project_root / "alembic.ini")

    assert table_names(sqlite_url) == STAGE_14_TABLES
    assert current_revision(sqlite_url) == ACTIVE_SCHEMA_REVISION
    assert {
        "retrieval_status",
        "retrieved_memory_ids",
        "semantic_retrieval_status",
        "retrieved_semantic_claim_ids",
        "emotion_appraisal_status",
        "emotion_context_schema_version",
        "emotion_state_version",
        "mood_state_version",
        "emotion_state_as_of",
        "relationship_processing_required",
        "relationship_context_schema_version",
        "relationship_state_version",
        "model_processing_required",
        "model_context_status",
        "user_model_context_schema_version",
        "user_model_context_claim_ids",
        "world_model_context_schema_version",
        "world_model_context_claim_ids",
        "position_processing_required",
        "position_context_status",
        "position_context_schema_version",
        "position_context_ids",
        "inclination_context_status",
        "inclination_context_schema_version",
        "inclination_context_ids",
        "inclination_curiosity_influence",
        "personality_aggregate_version",
        "personality_expression_schema_version",
        "personality_expression_cues",
        "failure_reason",
    } <= column_names(
        sqlite_url,
        "conversation_interactions",
    )
    assert {
        "affective_transition_id",
        "affective_state_version",
        "affective_signal_hash",
        "upstream_lineage_kind",
        "upstream_lineage_id",
    } <= column_names(sqlite_url, "reflection_sources")

    engine = create_engine(sqlite_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM personality_checkpoints")
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text("SELECT count(*) FROM personality_checkpoint_traits")
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()


def test_migration_upgrades_existing_stage_1_database(
    sqlite_url: str,
    project_root: Path,
) -> None:
    """The first real domain schema upgrades the accepted Foundation baseline."""

    config_path = project_root / "alembic.ini"
    upgrade_database(sqlite_url, config_path=config_path, revision="0001_foundation")
    assert current_revision(sqlite_url) == "0001_foundation"
    assert table_names(sqlite_url) == {"alembic_version"}

    upgrade_database(sqlite_url, config_path=config_path)
    assert current_revision(sqlite_url) == ACTIVE_SCHEMA_REVISION
    assert table_names(sqlite_url) == STAGE_14_TABLES


def test_migration_upgrades_existing_stage_3_database(
    sqlite_url: str,
    project_root: Path,
) -> None:
    """Stage 3 physical head 0002 upgrades in place without touching initial self data."""

    config_path = project_root / "alembic.ini"
    upgrade_database(sqlite_url, config_path=config_path, revision="0002_initial_self")
    assert current_revision(sqlite_url) == "0002_initial_self"
    assert table_names(sqlite_url) == STAGE_2_TABLES

    upgrade_database(sqlite_url, config_path=config_path)

    assert current_revision(sqlite_url) == ACTIVE_SCHEMA_REVISION
    assert table_names(sqlite_url) == STAGE_14_TABLES


def test_stage_5_migration_downgrade_and_reupgrade(
    sqlite_url: str,
    project_root: Path,
) -> None:
    """Stage 5 can drop only derived retrieval state and then reapply cleanly."""

    config_path = project_root / "alembic.ini"
    upgrade_database(sqlite_url, config_path=config_path)
    downgrade_database(sqlite_url, config_path=config_path, revision="0003_conversation_memory")
    assert current_revision(sqlite_url) == "0003_conversation_memory"
    assert table_names(sqlite_url) == STAGE_4_TABLES

    upgrade_database(sqlite_url, config_path=config_path)
    assert current_revision(sqlite_url) == ACTIVE_SCHEMA_REVISION
    assert table_names(sqlite_url) == STAGE_14_TABLES


def test_full_stage_4_and_5_chain_can_return_to_stage_2(
    sqlite_url: str,
    project_root: Path,
) -> None:
    """The cumulative conversation/memory/retrieval schema remains fully reversible."""

    config_path = project_root / "alembic.ini"
    upgrade_database(sqlite_url, config_path=config_path)
    downgrade_database(sqlite_url, config_path=config_path, revision="0002_initial_self")
    assert current_revision(sqlite_url) == "0002_initial_self"
    assert table_names(sqlite_url) == STAGE_2_TABLES

    upgrade_database(sqlite_url, config_path=config_path)
    assert current_revision(sqlite_url) == ACTIVE_SCHEMA_REVISION
    assert table_names(sqlite_url) == STAGE_14_TABLES


def test_stage_6_migration_downgrade_preserves_stage_5(
    sqlite_url: str,
    project_root: Path,
) -> None:
    """Removing semantic state preserves history, episodes, and retrieval indexes."""

    config_path = project_root / "alembic.ini"
    upgrade_database(sqlite_url, config_path=config_path)
    downgrade_database(sqlite_url, config_path=config_path, revision="0004_episodic_retrieval")

    assert current_revision(sqlite_url) == "0004_episodic_retrieval"
    assert table_names(sqlite_url) == STAGE_5_TABLES

    upgrade_database(sqlite_url, config_path=config_path)
    assert current_revision(sqlite_url) == ACTIVE_SCHEMA_REVISION
    assert table_names(sqlite_url) == STAGE_14_TABLES


def test_stage_7_migration_downgrade_preserves_stage_6(
    sqlite_url: str,
    project_root: Path,
) -> None:
    """Removing affect leaves all accepted Stage 0-6 state and then reapplies cleanly."""

    config_path = project_root / "alembic.ini"
    upgrade_database(sqlite_url, config_path=config_path)
    downgrade_database(sqlite_url, config_path=config_path, revision="0005_semantic_memory")

    assert current_revision(sqlite_url) == "0005_semantic_memory"
    assert table_names(sqlite_url) == STAGE_6_TABLES

    upgrade_database(sqlite_url, config_path=config_path)
    assert current_revision(sqlite_url) == ACTIVE_SCHEMA_REVISION
    assert table_names(sqlite_url) == STAGE_14_TABLES


def test_stage_8_migration_downgrade_preserves_stage_7(
    sqlite_url: str,
    project_root: Path,
) -> None:
    """Removing relationship state preserves all accepted Stage 0-7 state."""

    config_path = project_root / "alembic.ini"
    upgrade_database(sqlite_url, config_path=config_path)
    downgrade_database(sqlite_url, config_path=config_path, revision="0006_affective_state")
    assert current_revision(sqlite_url) == "0006_affective_state"
    assert table_names(sqlite_url) == STAGE_7_TABLES

    upgrade_database(sqlite_url, config_path=config_path)
    assert current_revision(sqlite_url) == ACTIVE_SCHEMA_REVISION
    assert table_names(sqlite_url) == STAGE_14_TABLES


def test_stage_9_migration_downgrade_preserves_stage_8(
    sqlite_url: str,
    project_root: Path,
) -> None:
    """Removing current models preserves the accepted relationship schema."""

    config_path = project_root / "alembic.ini"
    upgrade_database(sqlite_url, config_path=config_path)
    downgrade_database(sqlite_url, config_path=config_path, revision="0007_relationship_state")
    assert current_revision(sqlite_url) == "0007_relationship_state"
    assert table_names(sqlite_url) == STAGE_8_TABLES

    upgrade_database(sqlite_url, config_path=config_path)
    assert current_revision(sqlite_url) == ACTIVE_SCHEMA_REVISION
    assert table_names(sqlite_url) == STAGE_14_TABLES


def test_stage_12_migration_downgrade_preserves_stage_11(
    sqlite_url: str,
    project_root: Path,
) -> None:
    """Removing reflection records preserves all position-owned state."""

    config_path = project_root / "alembic.ini"
    upgrade_database(sqlite_url, config_path=config_path)
    downgrade_database(sqlite_url, config_path=config_path, revision="0009_satori_positions")
    assert current_revision(sqlite_url) == "0009_satori_positions"
    assert table_names(sqlite_url) == STAGE_11_TABLES

    upgrade_database(sqlite_url, config_path=config_path)
    assert current_revision(sqlite_url) == ACTIVE_SCHEMA_REVISION
    assert table_names(sqlite_url) == STAGE_14_TABLES


def test_stage_13_migration_downgrade_preserves_stage_12(
    sqlite_url: str,
    project_root: Path,
) -> None:
    """Removing inclinations restores the exact accepted Reflection V1 schema."""

    config_path = project_root / "alembic.ini"
    upgrade_database(sqlite_url, config_path=config_path)
    downgrade_database(sqlite_url, config_path=config_path, revision="0010_reflection_runs")

    assert current_revision(sqlite_url) == "0010_reflection_runs"
    assert table_names(sqlite_url) == STAGE_12_TABLES
    assert "affective_transition_id" not in column_names(sqlite_url, "reflection_sources")
    assert {
        "inclination_context_status",
        "inclination_context_schema_version",
        "inclination_context_ids",
        "inclination_curiosity_influence",
    }.isdisjoint(column_names(sqlite_url, "conversation_interactions"))

    upgrade_database(sqlite_url, config_path=config_path)
    assert current_revision(sqlite_url) == ACTIVE_SCHEMA_REVISION
    assert table_names(sqlite_url) == STAGE_14_TABLES


def test_provider_failure_diagnostics_migration_is_reversible(
    sqlite_url: str,
    project_root: Path,
) -> None:
    """The optional safe diagnosis can be discarded without losing the failed attempt."""

    config_path = project_root / "alembic.ini"
    upgrade_database(sqlite_url, config_path=config_path)
    database = create_database(sqlite_url)
    try:
        snapshot = build_initial_self_services(database).activate.execute(
            JsonSeedLoader().load_canonical(),
            trace_id="provider-failure-migration-fixture",
        )
    finally:
        database.dispose()

    instant = datetime(2026, 8, 28, tzinfo=UTC)
    engine = create_engine(sqlite_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO conversation_sessions "
                    "(session_id, identity_id, counterparty_id, schema_version, kind, status, "
                    "started_at, ended_at) VALUES "
                    "('failure-session', :identity, 'local-default', 1, 'explicit', 'open', "
                    ":instant, NULL)"
                ),
                {"identity": snapshot.identity.identity_id, "instant": instant},
            )
            connection.execute(
                text(
                    "INSERT INTO conversation_interactions "
                    "(interaction_id, session_id, client_request_id, trace_id, schema_version, "
                    "status, started_at, provider, model, failure_kind, failure_reason, "
                    "relationship_processing_required, model_processing_required, "
                    "position_processing_required) VALUES "
                    "('failure-interaction', 'failure-session', 'failure-request', "
                    "'failure-trace', 1, 'failed', :instant, 'openai', 'fixture-model', "
                    "'GenerationFailed', 'output_token_limit', 0, 0, 0)"
                ),
                {"instant": instant},
            )
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE conversation_interactions SET failure_reason = 'free-form' "
                    "WHERE interaction_id = 'failure-interaction'"
                )
            )
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE conversation_interactions SET model = NULL "
                    "WHERE interaction_id = 'failure-interaction'"
                )
            )
    finally:
        engine.dispose()

    downgrade_database(
        sqlite_url,
        config_path=config_path,
        revision="0012_personality_evolution",
    )
    assert current_revision(sqlite_url) == "0012_personality_evolution"
    assert "failure_reason" not in column_names(sqlite_url, "conversation_interactions")
    engine = create_engine(sqlite_url)
    try:
        with engine.connect() as connection:
            preserved = connection.execute(
                text(
                    "SELECT status, failure_kind, provider, model "
                    "FROM conversation_interactions WHERE interaction_id = 'failure-interaction'"
                )
            ).one()
        assert tuple(preserved) == ("failed", "GenerationFailed", None, None)
    finally:
        engine.dispose()

    upgrade_database(sqlite_url, config_path=config_path)
    assert current_revision(sqlite_url) == ACTIVE_SCHEMA_REVISION
    engine = create_engine(sqlite_url)
    try:
        with engine.connect() as connection:
            restored = connection.execute(
                text(
                    "SELECT failure_reason, provider, model FROM conversation_interactions "
                    "WHERE interaction_id = 'failure-interaction'"
                )
            ).one()
        assert tuple(restored) == (None, None, None)
    finally:
        engine.dispose()


def test_stage_14_downgrade_and_reupgrade_backfills_exact_activation_checkpoint(
    sqlite_url: str,
    project_root: Path,
) -> None:
    """An existing Stage 13 self receives the same checkpoint as a fresh activation."""

    config_path = project_root / "alembic.ini"
    upgrade_database(sqlite_url, config_path=config_path)
    database = create_database(sqlite_url)
    try:
        snapshot = build_initial_self_services(database).activate.execute(
            JsonSeedLoader().load_canonical(),
            trace_id="stage14-migration-activation",
        )
    finally:
        database.dispose()

    expected_hash = checkpoint_hash(
        identity_id=snapshot.identity.identity_id,
        checkpoint_kind=PersonalityCheckpointKind.ACTIVATION,
        personality=snapshot.personality,
    )
    engine = create_engine(sqlite_url)
    try:
        with engine.connect() as connection:
            authoritative_before = tuple(
                connection.execute(
                    text(
                        "SELECT trait_key, value, baseline_value "
                        "FROM satori_personality_traits ORDER BY trait_key"
                    )
                )
            )
            values_before = tuple(
                connection.execute(
                    text(
                        "SELECT value_key, strength, description, origin "
                        "FROM satori_values ORDER BY value_key"
                    )
                )
            )
            fresh_checkpoint = connection.execute(
                text(
                    "SELECT checkpoint_id, checkpoint_hash, checkpoint_kind, "
                    "source_aggregate_version FROM personality_checkpoints"
                )
            ).one()
    finally:
        engine.dispose()

    assert tuple(fresh_checkpoint) == (
        f"personality-checkpoint-{expected_hash}",
        expected_hash,
        "activation",
        snapshot.personality.aggregate_version,
    )

    downgrade_database(sqlite_url, config_path=config_path, revision="0011_satori_inclinations")
    assert current_revision(sqlite_url) == "0011_satori_inclinations"
    assert table_names(sqlite_url) == STAGE_13_TABLES
    assert {
        "personality_aggregate_version",
        "personality_expression_schema_version",
        "personality_expression_cues",
    }.isdisjoint(column_names(sqlite_url, "conversation_interactions"))

    upgrade_database(sqlite_url, config_path=config_path)
    assert current_revision(sqlite_url) == ACTIVE_SCHEMA_REVISION
    assert table_names(sqlite_url) == STAGE_14_TABLES

    engine = create_engine(sqlite_url)
    try:
        with engine.connect() as connection:
            authoritative_after = tuple(
                connection.execute(
                    text(
                        "SELECT trait_key, value, baseline_value "
                        "FROM satori_personality_traits ORDER BY trait_key"
                    )
                )
            )
            values_after = tuple(
                connection.execute(
                    text(
                        "SELECT value_key, strength, description, origin "
                        "FROM satori_values ORDER BY value_key"
                    )
                )
            )
            backfilled_checkpoint = connection.execute(
                text(
                    "SELECT checkpoint_id, checkpoint_hash, checkpoint_kind, "
                    "source_aggregate_version FROM personality_checkpoints"
                )
            ).one()
            checkpoint_traits = tuple(
                connection.execute(
                    text(
                        "SELECT trait_key, value, baseline_value "
                        "FROM personality_checkpoint_traits ORDER BY trait_key"
                    )
                )
            )
            assert (
                connection.execute(text("SELECT count(*) FROM personality_revisions")).scalar_one()
                == 0
            )
    finally:
        engine.dispose()

    assert authoritative_after == authoritative_before
    assert values_after == values_before
    assert tuple(backfilled_checkpoint) == tuple(fresh_checkpoint)
    assert checkpoint_traits == authoritative_before


def test_stage_14_upgrade_refuses_incomplete_activation_vector_before_ddl(
    sqlite_url: str,
    project_root: Path,
) -> None:
    """Backfill never hides a corrupt pre-Stage-14 personality behind a partial checkpoint."""

    config_path = project_root / "alembic.ini"
    upgrade_database(sqlite_url, config_path=config_path)
    database = create_database(sqlite_url)
    try:
        build_initial_self_services(database).activate.execute(
            JsonSeedLoader().load_canonical(),
            trace_id="stage14-corrupt-backfill-fixture",
        )
    finally:
        database.dispose()
    downgrade_database(sqlite_url, config_path=config_path, revision="0011_satori_inclinations")

    engine = create_engine(sqlite_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM satori_personality_traits WHERE trait_key = 'warmth'")
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="canonical trait vector is incomplete or invalid"):
        upgrade_database(sqlite_url, config_path=config_path)

    assert current_revision(sqlite_url) == "0011_satori_inclinations"
    assert table_names(sqlite_url) == STAGE_13_TABLES


@pytest.mark.parametrize(
    ("corruption_statement", "expected_error"),
    [
        (
            "UPDATE satori_personality_states SET aggregate_version = 2",
            "pre-Stage-14 aggregate version is not 1",
        ),
        (
            "UPDATE satori_personality_traits SET value = value - 0.005 WHERE trait_key = 'warmth'",
            "pre-Stage-14 current traits differ from their activation baselines",
        ),
    ],
)
def test_stage_14_upgrade_refuses_unprovenanced_legacy_personality_before_ddl(
    sqlite_url: str,
    project_root: Path,
    corruption_statement: str,
    expected_error: str,
) -> None:
    """Migration never blesses an unexplained legacy change as an activation checkpoint."""

    config_path = project_root / "alembic.ini"
    upgrade_database(sqlite_url, config_path=config_path)
    database = create_database(sqlite_url)
    try:
        build_initial_self_services(database).activate.execute(
            JsonSeedLoader().load_canonical(),
            trace_id="stage14-unprovenanced-backfill-fixture",
        )
    finally:
        database.dispose()
    downgrade_database(sqlite_url, config_path=config_path, revision="0011_satori_inclinations")

    relevant_tables = (
        "conversation_interactions",
        "reflection_runs",
        "reflection_sources",
    )
    schema_columns_before = {
        table_name: column_names(sqlite_url, table_name) for table_name in relevant_tables
    }
    engine = create_engine(sqlite_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(corruption_statement))
        with engine.connect() as connection:
            source_rows_before = (
                tuple(
                    connection.execute(
                        text(
                            "SELECT identity_id, schema_version, aggregate_version, created_at "
                            "FROM satori_personality_states ORDER BY identity_id"
                        )
                    )
                ),
                tuple(
                    connection.execute(
                        text(
                            "SELECT identity_id, trait_key, value, baseline_value "
                            "FROM satori_personality_traits ORDER BY identity_id, trait_key"
                        )
                    )
                ),
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match=expected_error):
        upgrade_database(sqlite_url, config_path=config_path)

    assert current_revision(sqlite_url) == "0011_satori_inclinations"
    assert table_names(sqlite_url) == STAGE_13_TABLES
    assert {
        table_name: column_names(sqlite_url, table_name) for table_name in relevant_tables
    } == schema_columns_before

    engine = create_engine(sqlite_url)
    try:
        with engine.connect() as connection:
            source_rows_after = (
                tuple(
                    connection.execute(
                        text(
                            "SELECT identity_id, schema_version, aggregate_version, created_at "
                            "FROM satori_personality_states ORDER BY identity_id"
                        )
                    )
                ),
                tuple(
                    connection.execute(
                        text(
                            "SELECT identity_id, trait_key, value, baseline_value "
                            "FROM satori_personality_traits ORDER BY identity_id, trait_key"
                        )
                    )
                ),
            )
    finally:
        engine.dispose()

    assert source_rows_after == source_rows_before


def test_stage_14_downgrade_refuses_personality_reflection_run(
    sqlite_url: str,
    project_root: Path,
) -> None:
    """A V3 personality run cannot outlive the schema that explains its purpose."""

    config_path = project_root / "alembic.ini"
    upgrade_database(sqlite_url, config_path=config_path)
    database = create_database(sqlite_url)
    try:
        snapshot = build_initial_self_services(database).activate.execute(
            JsonSeedLoader().load_canonical(),
            trace_id="stage14-v3-downgrade-fixture",
        )
    finally:
        database.dispose()
    instant = datetime(2026, 8, 23, 12, tzinfo=UTC)
    engine = create_engine(sqlite_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO reflection_runs "
                    "(run_id, run_key, identity_id, schema_version, policy_version, purpose, "
                    "trigger_kind, source_set_hash, status, aggregate_version, attempt_count, "
                    "created_at, updated_at, completed_at) VALUES "
                    "('personality-run', :run_key, :identity, 3, 3, "
                    "'personality_evolution', 'explicit_local', :source_hash, 'completed', "
                    "1, 0, :instant, :instant, :instant)"
                ),
                {
                    "run_key": "a" * 64,
                    "identity": snapshot.identity.identity_id,
                    "source_hash": "b" * 64,
                    "instant": instant,
                },
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="Reflection V3/personality run"):
        downgrade_database(
            sqlite_url,
            config_path=config_path,
            revision="0011_satori_inclinations",
        )
    assert current_revision(sqlite_url) == "0012_personality_evolution"


def test_stage_14_downgrade_refuses_non_activation_checkpoint(
    sqlite_url: str,
    project_root: Path,
) -> None:
    """A restorable owner checkpoint is never dropped while current state remains."""

    config_path = project_root / "alembic.ini"
    upgrade_database(sqlite_url, config_path=config_path)
    database = create_database(sqlite_url)
    try:
        snapshot = build_initial_self_services(database).activate.execute(
            JsonSeedLoader().load_canonical(),
            trace_id="stage14-checkpoint-downgrade-fixture",
        )
    finally:
        database.dispose()
    manual_hash = checkpoint_hash(
        identity_id=snapshot.identity.identity_id,
        checkpoint_kind=PersonalityCheckpointKind.MANUAL,
        personality=snapshot.personality,
    )
    engine = create_engine(sqlite_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO personality_checkpoints "
                    "(checkpoint_id, identity_id, personality_schema_version, "
                    "source_aggregate_version, checkpoint_kind, hash_schema_version, "
                    "checkpoint_hash, created_at) VALUES "
                    "(:checkpoint, :identity, :schema, :aggregate, 'manual', 1, :hash, :created)"
                ),
                {
                    "checkpoint": f"personality-checkpoint-{manual_hash}",
                    "identity": snapshot.identity.identity_id,
                    "schema": snapshot.personality.schema_version,
                    "aggregate": snapshot.personality.aggregate_version,
                    "hash": manual_hash,
                    "created": snapshot.identity.activation_time,
                },
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="non-activation personality checkpoint"):
        downgrade_database(
            sqlite_url,
            config_path=config_path,
            revision="0011_satori_inclinations",
        )
    assert current_revision(sqlite_url) == "0012_personality_evolution"


def test_stage_8_migration_does_not_fabricate_relationship_from_old_history(
    sqlite_url: str,
    project_root: Path,
) -> None:
    """Pre-Stage-8 completed turns remain ineligible and create no relationship rows."""

    config_path = project_root / "alembic.ini"
    upgrade_database(sqlite_url, config_path=config_path)
    database = create_database(sqlite_url)
    initial = build_initial_self_services(database)
    initial.activate.execute(JsonSeedLoader().load_canonical(), trace_id="migration-activate")
    identity_id = initial.get_identity.execute().identity_id
    database.dispose()
    downgrade_database(sqlite_url, config_path=config_path, revision="0006_affective_state")
    database = create_database(sqlite_url)
    instant = datetime(2026, 8, 1, tzinfo=UTC)
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO conversation_sessions "
                "(session_id, identity_id, schema_version, kind, status, started_at, ended_at) "
                "VALUES (:session, :identity, 1, 'explicit', 'closed', :instant, :instant)"
            ),
            {"session": "old-session", "identity": identity_id, "instant": instant},
        )
        connection.execute(
            text(
                "INSERT INTO conversation_interactions "
                "(interaction_id, session_id, client_request_id, trace_id, schema_version, "
                "status, started_at, completed_at, provider, model, finish_status, "
                "context_schema_version, context_manifest_schema_version, policy_id, "
                "policy_schema_version, retrieval_status, retrieved_memory_ids, "
                "semantic_retrieval_status, retrieved_semantic_claim_ids, "
                "emotion_appraisal_status) VALUES "
                "('old-interaction', 'old-session', 'old-request', 'old-trace', 1, "
                "'completed', :instant, :instant, 'fixture', 'fixture', 'stop', 9, 9, "
                "'satori.conversation.behavior.v7', 7, 'not_requested', '[]', "
                "'not_requested', '[]', 'not_requested')"
            ),
            {"instant": instant},
        )
        for message_id, role, content, sequence in (
            ("old-user", "user", "Привет", 1),
            ("old-assistant", "assistant", "Привет", 2),
        ):
            connection.execute(
                text(
                    "INSERT INTO conversation_messages "
                    "(message_id, session_id, interaction_id, schema_version, role, content, "
                    "created_at, sequence) VALUES "
                    "(:message, 'old-session', 'old-interaction', 1, :role, :content, "
                    ":instant, :sequence)"
                ),
                {
                    "message": message_id,
                    "role": role,
                    "content": content,
                    "instant": instant,
                    "sequence": sequence,
                },
            )
    database.dispose()

    upgrade_database(sqlite_url, config_path=config_path)
    engine = create_engine(sqlite_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT relationship_processing_required FROM conversation_interactions "
                        "WHERE interaction_id = 'old-interaction'"
                    )
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(text("SELECT count(*) FROM relationship_states")).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text(
                        "SELECT model_processing_required FROM conversation_interactions "
                        "WHERE interaction_id = 'old-interaction'"
                    )
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(text("SELECT count(*) FROM user_model_claims")).scalar_one() == 0
            )
            assert (
                connection.execute(text("SELECT count(*) FROM world_model_claims")).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text(
                        "SELECT position_processing_required FROM conversation_interactions "
                        "WHERE interaction_id = 'old-interaction'"
                    )
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(text("SELECT count(*) FROM satori_positions")).scalar_one() == 0
            )
            assert (
                connection.execute(text("SELECT count(*) FROM satori_inclinations")).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text("SELECT count(*) FROM satori_inclination_evidence")
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text("SELECT count(*) FROM satori_inclination_revisions")
                ).scalar_one()
                == 0
            )
            compatibility = connection.execute(
                text(
                    "SELECT inclination_context_status, "
                    "inclination_context_schema_version, inclination_context_ids, "
                    "inclination_curiosity_influence, personality_aggregate_version, "
                    "personality_expression_schema_version, personality_expression_cues "
                    "FROM conversation_interactions "
                    "WHERE interaction_id = 'old-interaction'"
                )
            ).one()
            assert tuple(compatibility) == (None, None, None, None, None, None, None)
    finally:
        engine.dispose()

    database = create_database(sqlite_url)
    try:
        with SQLAlchemyConversationHistoryUnitOfWork(database.session_factory) as unit:
            historical = unit.conversation_history.get_interaction("old-interaction")
        assert historical is not None
        assert historical.provider_metadata is not None
        assert historical.provider_metadata.inclination_context_status == "not_requested"
        assert historical.provider_metadata.inclination_context_schema_version is None
        assert historical.provider_metadata.inclination_context_ids == ()
        assert historical.provider_metadata.inclination_curiosity_influence == 0.0
        assert historical.provider_metadata.personality_aggregate_version is None
        assert historical.provider_metadata.personality_expression_schema_version is None
        assert historical.provider_metadata.personality_expression_cues == ()
    finally:
        database.dispose()
