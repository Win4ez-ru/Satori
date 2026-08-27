"""Explicit, atomic, restart-safe Stage 2 activation tests."""

import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import pytest
from sqlalchemy import text

from satori.composition import build_initial_self_services
from satori.domain.audit import ActivationAuditEvent
from satori.domain.errors import AlreadyActivated, CorruptSatoriState, NotActivated
from satori.domain.initial_self import InitialSelfSnapshot, SeedTrait, activate_from_seed
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.initial_self_uow import SQLAlchemyInitialSelfUnitOfWork
from satori.infrastructure.persistence.repositories.initial_self import (
    SQLAlchemyInitialSelfRepository,
)
from satori.infrastructure.seeds.loader import JsonSeedLoader
from satori.observability.logging import bind_trace_id, configure_logging
from tests.fakes import FrozenClock, SequenceIdGenerator

ACTIVATION_TIME = datetime(2026, 7, 27, 8, 30, tzinfo=UTC)


def activate(database: Database, *, stream: StringIO | None = None) -> InitialSelfSnapshot:
    """Activate a deterministic identity in a migrated test database."""

    if stream is not None:
        configure_logging("INFO", stream=stream)
    services = build_initial_self_services(
        database,
        clock=FrozenClock(ACTIVATION_TIME),
        id_generator=SequenceIdGenerator("identity-1", "audit-1"),
    )
    with bind_trace_id("trace-activation-1"):
        return services.activate.execute(
            JsonSeedLoader().load_canonical(),
            trace_id="trace-activation-1",
        )


def table_count(database: Database, table_name: str) -> int:
    """Count records in a fixed, test-controlled table name."""

    allowed = {
        "audit_events",
        "satori_identities",
        "satori_personality_states",
        "satori_personality_traits",
        "satori_value_sets",
        "satori_values",
    }
    assert table_name in allowed
    with database.engine.connect() as connection:
        return int(connection.execute(text(f"SELECT count(*) FROM {table_name}")).scalar_one())


def test_fresh_database_is_not_activated_and_read_does_not_create_state(
    migrated_database: Database,
) -> None:
    """The negative golden path is explicit and mutation-free."""

    services = build_initial_self_services(migrated_database)

    with pytest.raises(NotActivated):
        services.get_self.execute()

    assert table_count(migrated_database, "satori_identities") == 0
    assert table_count(migrated_database, "satori_personality_traits") == 0
    assert table_count(migrated_database, "satori_values") == 0


def test_activation_persists_complete_initial_self_and_audit(
    migrated_database: Database,
) -> None:
    """Identity, personality, values, provenance, and audit commit together."""

    stream = StringIO()
    snapshot = activate(migrated_database, stream=stream)

    assert snapshot.identity.identity_id == "identity-1"
    assert snapshot.identity.name == "Satori"
    assert snapshot.identity.activation_time == ACTIVATION_TIME
    assert snapshot.identity.seed_provenance.seed_id == "satori.initial.v1"
    assert snapshot.identity.seed_provenance.seed_schema_version == 1
    assert len(snapshot.identity.seed_provenance.seed_content_hash) == 64
    assert len(snapshot.personality.traits) == 15
    assert snapshot.personality.trait("curiosity").value == 0.92
    assert snapshot.personality.trait("curiosity").baseline_value == 0.92
    assert len(snapshot.values.items) == 9
    assert snapshot.values.value("intellectual_honesty").strength == 1.0

    assert table_count(migrated_database, "satori_identities") == 1
    assert table_count(migrated_database, "satori_personality_states") == 1
    assert table_count(migrated_database, "satori_personality_traits") == 15
    assert table_count(migrated_database, "satori_value_sets") == 1
    assert table_count(migrated_database, "satori_values") == 9
    assert table_count(migrated_database, "audit_events") == 1

    with migrated_database.engine.connect() as connection:
        audit = (
            connection.execute(
                text("SELECT event_type, aggregate_id, trace_id, details FROM audit_events")
            )
            .mappings()
            .one()
        )
    assert audit["event_type"] == "satori.activation"
    assert audit["aggregate_id"] == "identity-1"
    assert audit["trace_id"] == "trace-activation-1"
    assert json.loads(audit["details"]) == {
        "seed_id": "satori.initial.v1",
        "seed_schema_version": 1,
        "seed_content_hash": snapshot.identity.seed_provenance.seed_content_hash,
    }

    log_records = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [record["message"] for record in log_records] == [
        "activation_attempted",
        "activation_succeeded",
    ]
    assert all(record["trace_id"] == "trace-activation-1" for record in log_records)
    assert log_records[-1]["fields"] == {
        "identity_id": "identity-1",
        "seed_id": "satori.initial.v1",
        "seed_schema_version": 1,
    }
    assert "personality" not in stream.getvalue()


def test_repeat_activation_is_typed_error_and_cannot_reset_live_state(
    migrated_database: Database,
) -> None:
    """Changing seed input after activation cannot rewrite the living identity."""

    first = activate(migrated_database)
    services = build_initial_self_services(
        migrated_database,
        clock=FrozenClock(datetime(2030, 1, 1, tzinfo=UTC)),
        id_generator=SequenceIdGenerator("must-not-be-used"),
    )
    canonical = JsonSeedLoader().load_canonical()
    changed_traits = tuple(
        SeedTrait(trait.key, 0.01 if trait.key == "curiosity" else trait.value)
        for trait in canonical.traits
    )
    changed_seed = replace(canonical, traits=changed_traits)

    with pytest.raises(AlreadyActivated) as error:
        services.activate.execute(changed_seed, trace_id="trace-repeat")

    assert error.value.identity_id == "identity-1"
    assert services.get_self.execute() == first
    assert table_count(migrated_database, "satori_identities") == 1
    assert table_count(migrated_database, "satori_personality_traits") == 15
    assert table_count(migrated_database, "satori_values") == 9
    assert table_count(migrated_database, "audit_events") == 1


def test_failure_after_staging_activation_rolls_back_everything(
    migrated_database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No partial identity survives a failure between staging and commit."""

    original_add = SQLAlchemyInitialSelfRepository.add

    def add_then_fail(
        repository: SQLAlchemyInitialSelfRepository,
        snapshot: InitialSelfSnapshot,
        event: ActivationAuditEvent,
    ) -> bool:
        assert original_add(repository, snapshot, event)
        raise RuntimeError("simulated activation failure")

    monkeypatch.setattr(SQLAlchemyInitialSelfRepository, "add", add_then_fail)
    services = build_initial_self_services(
        migrated_database,
        clock=FrozenClock(ACTIVATION_TIME),
        id_generator=SequenceIdGenerator("identity-failed", "audit-failed"),
    )

    with pytest.raises(RuntimeError, match="simulated activation failure"):
        services.activate.execute(
            JsonSeedLoader().load_canonical(),
            trace_id="trace-failed",
        )

    for table in (
        "satori_identities",
        "satori_personality_states",
        "satori_personality_traits",
        "satori_value_sets",
        "satori_values",
        "audit_events",
    ):
        assert table_count(migrated_database, table) == 0


def test_identity_continuity_survives_full_runtime_reconstruction(
    sqlite_url: str,
    project_root: Path,
) -> None:
    """Golden path: close all runtime objects, rebuild, and load the same self."""

    from satori.infrastructure.persistence.migrations import upgrade_database

    upgrade_database(sqlite_url, config_path=project_root / "alembic.ini")
    first_database = create_database(sqlite_url)
    expected = activate(first_database)
    first_database.dispose()

    second_database = create_database(sqlite_url)
    try:
        actual = build_initial_self_services(second_database).get_self.execute()
    finally:
        second_database.dispose()

    assert actual == expected
    assert actual.identity.identity_id == "identity-1"
    assert actual.identity.activation_time == ACTIVATION_TIME
    assert actual.identity.seed_provenance == expected.identity.seed_provenance


def test_snapshot_is_frozen_and_contains_no_orm_objects(
    migrated_database: Database,
) -> None:
    """Future cognition gets immutable domain state rather than write-capable ORM rows."""

    snapshot = activate(migrated_database)

    with pytest.raises(FrozenInstanceError):
        snapshot.identity.name = "Different"  # type: ignore[misc]

    object_modules = {
        type(snapshot).__module__,
        type(snapshot.identity).__module__,
        type(snapshot.personality).__module__,
        type(snapshot.personality.traits[0]).__module__,
        type(snapshot.values).__module__,
        type(snapshot.values.items[0]).__module__,
    }
    assert all(module.startswith("satori.domain") for module in object_modules)


def test_missing_activation_audit_is_detected_as_corruption(
    migrated_database: Database,
) -> None:
    """A core mutation without its required audit cannot load as healthy state."""

    activate(migrated_database)
    with migrated_database.engine.begin() as connection:
        connection.execute(text("DELETE FROM audit_events"))

    with pytest.raises(CorruptSatoriState):
        build_initial_self_services(migrated_database).get_self.execute()


def test_database_singleton_constraint_rejects_stale_activation_contender(
    migrated_database: Database,
) -> None:
    """The primary-slot constraint closes the two-activator race after a stale read."""

    activate(migrated_database)
    seed = JsonSeedLoader().load_canonical()
    contender = activate_from_seed(
        seed,
        identity_id="identity-contender",
        activation_time=datetime(2026, 7, 27, 8, 31, tzinfo=UTC),
    )
    event = ActivationAuditEvent(
        event_id="audit-contender",
        schema_version=1,
        identity_id="identity-contender",
        occurred_at=contender.identity.activation_time,
        trace_id="trace-contender",
        seed_provenance=contender.identity.seed_provenance,
    )

    with SQLAlchemyInitialSelfUnitOfWork(migrated_database.session_factory) as unit_of_work:
        assert unit_of_work.initial_self.add(contender, event) is False

    assert build_initial_self_services(
        migrated_database
    ).get_self.execute().identity.identity_id == ("identity-1")
    assert table_count(migrated_database, "satori_identities") == 1
