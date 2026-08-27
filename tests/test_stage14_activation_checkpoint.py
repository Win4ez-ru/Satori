"""Stage 14 activation-checkpoint parity and atomicity."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from satori.composition import InitialSelfServices, build_initial_self_services
from satori.domain.audit import ActivationAuditEvent
from satori.domain.initial_self import InitialSelfSnapshot
from satori.domain.personality_evolution import PersonalityCheckpointKind, checkpoint_hash
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.models.personality import (
    PersonalityCheckpointRow,
    PersonalityCheckpointTraitRow,
)
from satori.infrastructure.persistence.repositories.initial_self import (
    SQLAlchemyInitialSelfRepository,
)
from satori.infrastructure.seeds.loader import JsonSeedLoader
from tests.fakes import FrozenClock, SequenceIdGenerator

ACTIVATION_TIME = datetime(2026, 8, 23, 9, 0, tzinfo=UTC)


def _services(database: Database) -> InitialSelfServices:
    return build_initial_self_services(
        database,
        clock=FrozenClock(ACTIVATION_TIME),
        id_generator=SequenceIdGenerator("stage14-identity", "stage14-activation-audit"),
    )


def test_fresh_activation_commits_exact_deterministic_checkpoint(
    migrated_database: Database,
) -> None:
    snapshot = _services(migrated_database).activate.execute(
        JsonSeedLoader().load_canonical(),
        trace_id="stage14-activation",
    )
    expected_hash = checkpoint_hash(
        identity_id=snapshot.identity.identity_id,
        checkpoint_kind=PersonalityCheckpointKind.ACTIVATION,
        personality=snapshot.personality,
    )

    with migrated_database.session_factory() as session:
        checkpoint = session.execute(select(PersonalityCheckpointRow)).scalar_one()
        checkpoint_traits = tuple(
            session.execute(
                select(PersonalityCheckpointTraitRow).order_by(
                    PersonalityCheckpointTraitRow.trait_key
                )
            ).scalars()
        )

    assert checkpoint.checkpoint_id == f"personality-checkpoint-{expected_hash}"
    assert checkpoint.identity_id == snapshot.identity.identity_id
    assert checkpoint.personality_schema_version == snapshot.personality.schema_version
    assert checkpoint.source_aggregate_version == snapshot.personality.aggregate_version
    assert checkpoint.checkpoint_kind == PersonalityCheckpointKind.ACTIVATION.value
    assert checkpoint.hash_schema_version == 1
    assert checkpoint.checkpoint_hash == expected_hash
    assert checkpoint.created_at == snapshot.identity.activation_time
    assert tuple(
        (row.trait_key, row.value, row.baseline_value) for row in checkpoint_traits
    ) == tuple(
        (trait.key, trait.value, trait.baseline_value) for trait in snapshot.personality.traits
    )


def test_activation_failure_rolls_back_checkpoint_with_initial_self(
    migrated_database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_add = SQLAlchemyInitialSelfRepository.add

    def add_then_fail(
        repository: SQLAlchemyInitialSelfRepository,
        snapshot: InitialSelfSnapshot,
        event: ActivationAuditEvent,
    ) -> bool:
        assert original_add(repository, snapshot, event)
        raise RuntimeError("simulated Stage 14 activation failure")

    monkeypatch.setattr(SQLAlchemyInitialSelfRepository, "add", add_then_fail)

    with pytest.raises(RuntimeError, match="simulated Stage 14 activation failure"):
        _services(migrated_database).activate.execute(
            JsonSeedLoader().load_canonical(),
            trace_id="stage14-activation-failed",
        )

    with migrated_database.engine.connect() as connection:
        for table_name in (
            "satori_identities",
            "satori_personality_states",
            "satori_personality_traits",
            "satori_value_sets",
            "satori_values",
            "personality_checkpoints",
            "personality_checkpoint_traits",
            "audit_events",
        ):
            assert connection.execute(text(f"SELECT count(*) FROM {table_name}")).scalar_one() == 0
