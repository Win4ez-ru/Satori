"""Retained Reflection evidence hashes survive paired explanation disposition."""

from pathlib import Path

from satori.domain.reflection import affective_signal_hash
from satori.infrastructure.persistence.affect_uow import SQLAlchemyAffectiveStateUnitOfWork
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.reflection_uow import SQLAlchemyReflectionUnitOfWork
from tests.erasure_appraisal_support import (
    appraisal_command,
    create_appraisal_fixture,
    prepare_appraisal,
)


def retained_signals(database: Database, run_id: str) -> tuple[str, ...]:
    """Use existing production hash with body-free numerical and source interfaces."""
    with SQLAlchemyReflectionUnitOfWork(database.session_factory) as unit:
        sources = unit.reflection.list_sources(run_id)
    with SQLAlchemyAffectiveStateUnitOfWork(database.session_factory) as affect:
        transitions = {
            item.transition_id: item for item in affect.affective_state.list_numerical_history()
        }
    result: list[str] = []
    for source in sources:
        assert source.affective_transition_id is not None
        transition = transitions[source.affective_transition_id]
        signal = affective_signal_hash(
            transition_id=transition.transition_id,
            identity_id=transition.identity_id,
            interaction_id=transition.interaction_id,
            source_message_id=transition.source_message_id,
            resulting_state_version=transition.after.state_version,
            source=source,
            appraisal_schema_version=transition.after.appraisal_schema_version,
            appraisal_payload=dict(transition.appraisal_scores),
            appraisal_confidence=transition.appraisal_confidence,
            applied_delta=transition.applied_delta.as_mapping(),
        )
        assert signal == source.affective_signal_hash
        result.append(signal)
    return tuple(result)


def test_actual_explanation_removal_preserves_all_existing_reflection_signal_hashes(
    migrated_database: Database, tmp_path: Path
) -> None:
    fixture = create_appraisal_fixture(migrated_database, tmp_path / "signal-floor")
    manager, request = prepare_appraisal(fixture)
    before = retained_signals(migrated_database, fixture.run.run_id)
    assert len(before) == 4
    assert manager.commit(appraisal_command(manager, request)).main_projection_current
    assert retained_signals(migrated_database, fixture.run.run_id) == before
