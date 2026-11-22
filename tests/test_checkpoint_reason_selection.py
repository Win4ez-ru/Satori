"""Exact identity selectors use the real owner, main store and independent floor."""

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest

from satori.application.erasure.personality_reason_contracts import (
    CheckpointReasonCommand,
    CheckpointReasonRequest,
)
from satori.application.erasure.personality_reason_use_cases import CheckpointReasonManager
from satori.application.personality.use_cases import (
    ApprovePersonalityCheckpoint,
    PersonalityCheckpointApprovalProposal,
    RestorePersonalityCheckpoint,
)
from satori.core.personality import PersonalityRestoreProposal
from satori.domain.erasure_personality_reasons import (
    CheckpointReasonAction,
    CheckpointReasonKind,
    CheckpointReasonScope,
    CheckpointReasonSelector,
)
from satori.domain.personality_evolution import PersonalityManager
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure_personality_reasons import (
    SQLiteCheckpointReasonBodyStore,
)
from satori.infrastructure.persistence.repositories.personality import (
    SQLAlchemyPersonalityRepository,
)
from satori.infrastructure.source_policy.installation import enroll_installation
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime
from tests.fakes import FrozenClock
from tests.test_checkpoint_reason_owner import create_reason_records
from tests.test_recovery_whole_state import stored_rows
from tests.test_stage14_personality_persistence import NOW, PrefixIds, _uow


@dataclass(frozen=True, slots=True)
class ReasonCase:
    database: Database
    runtime: SourcePolicyRuntime
    manager: CheckpointReasonManager
    request: CheckpointReasonRequest

    def command(self, request_id: str = "reason-selection-commit") -> CheckpointReasonCommand:
        preview = self.manager.preview(self.request)
        assert preview.readiness == "ready", preview.blockers
        return CheckpointReasonCommand(
            identity_id=self.request.identity_id,
            selectors=self.request.selectors,
            request_id=request_id,
            expected_generation=preview.generation,
            expected_erasure_revision=preview.erasure_revision,
            expected_body_revision=preview.body_revision,
            approval_digest=preview.approval_digest,
        )


def create_reason_case(database: Database, directory: Path) -> ReasonCase:
    """Actual Personality owners on controlled synthetic accepted evidence, no providers."""
    scope, _approval, _restore = create_reason_records(database)
    runtime = enroll_installation(database, directory, reviewed=True, new_installation=True)
    runtime.upgrade_checkpoint_reason_policy()
    with database.session_factory() as session:
        owner = SQLAlchemyPersonalityRepository(session)
        selectors = tuple(
            sorted(
                (
                    *(
                        CheckpointReasonSelector(
                            CheckpointReasonKind.CHECKPOINT_APPROVAL, item.approval_id
                        )
                        for item in owner.list_checkpoint_approval_headers(scope.identity_id)
                    ),
                    *(
                        CheckpointReasonSelector(
                            CheckpointReasonKind.PERSONALITY_RESTORE, item.restore_id
                        )
                        for item in owner.list_restore_headers(scope.identity_id)
                    ),
                ),
                key=lambda item: item.key,
            )
        )
    manager = CheckpointReasonManager(runtime, SQLiteCheckpointReasonBodyStore(database))
    return ReasonCase(
        database,
        runtime,
        manager,
        CheckpointReasonRequest(identity_id=scope.identity_id, selectors=selectors),
    )


@pytest.fixture
def case(migrated_database: Database, tmp_path: Path) -> ReasonCase:
    return create_reason_case(migrated_database, tmp_path / "reason-selection-floor")


def test_preview_is_read_only_repeatable_and_selects_only_exact_reasons(case: ReasonCase) -> None:
    before = stored_rows(Path(str(case.database.engine.url.database)))
    floor = case.runtime.ledger.database_path.read_bytes()
    preview = case.manager.preview(case.request)
    assert preview.readiness == "ready", preview.blockers
    assert preview.approval_reasons == 1
    assert preview.restore_reasons == 1
    assert preview.metadata_references == 51
    assert "SYNTHETIC_PRIVATE" not in preview.model_dump_json()
    assert preview.physical_erasure is False
    assert preview.execution_authorized is False
    assert case.manager.preview(case.request) == preview
    assert stored_rows(Path(str(case.database.engine.url.database))) == before
    assert case.runtime.ledger.database_path.read_bytes() == floor


def test_different_terminal_selector_changes_review_even_when_all_public_counts_match(
    case: ReasonCase,
) -> None:
    initial = case.command("initial-two-reasons")
    assert case.manager.commit(initial).main_projection_current
    with case.database.session_factory() as session:
        owner = SQLAlchemyPersonalityRepository(session)
        prior = owner.get_checkpoint_for_version(case.request.identity_id, 2)
        assert prior is not None
    assert (
        RestorePersonalityCheckpoint(
            lambda: _uow(case.database),
            PersonalityManager(),
            FrozenClock(NOW + timedelta(days=5)),
            PrefixIds("selection-future-restore"),
        )
        .execute(
            case.request.identity_id,
            PersonalityRestoreProposal(
                checkpoint_id=prior.snapshot.checkpoint_id,
                checkpoint_hash=prior.snapshot.checkpoint_hash,
                expected_personality_version=3,
                reason="SYNTHETIC_THIRD_REASON",
            ),
            trace_id="selection-third-reason",
        )
        .restored
    )
    with case.database.session_factory() as session:
        owner = SQLAlchemyPersonalityRepository(session)
        restored = owner.list_restore_headers(case.request.identity_id)
        newest = next(item for item in restored if item.after_aggregate_version == 4)
        checkpoint = owner.get_checkpoint_for_version(case.request.identity_id, 4)
        assert checkpoint is not None
    third = CheckpointReasonSelector(CheckpointReasonKind.PERSONALITY_RESTORE, newest.restore_id)
    third_request = CheckpointReasonRequest(
        identity_id=case.request.identity_id, selectors=(third,)
    )
    third_case = ReasonCase(case.database, case.runtime, case.manager, third_request)
    assert case.manager.commit(third_case.command("third-reason")).main_projection_current
    approval = ApprovePersonalityCheckpoint(
        lambda: _uow(case.database),
        FrozenClock(NOW + timedelta(days=6)),
        PrefixIds("selection-future-approval"),
    ).execute(
        case.request.identity_id,
        PersonalityCheckpointApprovalProposal(
            checkpoint_id=checkpoint.snapshot.checkpoint_id,
            checkpoint_hash=checkpoint.snapshot.checkpoint_hash,
            expected_personality_version=4,
            reason="SYNTHETIC_AVAILABLE_FOURTH_REASON",
        ),
        trace_id="selection-fourth-reason",
    )
    available = CheckpointReasonSelector(
        CheckpointReasonKind.CHECKPOINT_APPROVAL, approval.approval_id
    )
    old_terminal = next(
        selector
        for selector in case.request.selectors
        if selector.kind is CheckpointReasonKind.PERSONALITY_RESTORE
    )
    left = CheckpointReasonRequest(
        identity_id=case.request.identity_id, selectors=(available, old_terminal)
    )
    right = CheckpointReasonRequest(
        identity_id=case.request.identity_id, selectors=(available, third)
    )
    left_preview, right_preview = case.manager.preview(left), case.manager.preview(right)
    assert left_preview.readiness == "ready"
    assert right_preview.readiness == "ready"
    assert left_preview.approval_reasons == right_preview.approval_reasons
    assert left_preview.restore_reasons == right_preview.restore_reasons
    assert left_preview.already_erased == right_preview.already_erased
    assert left_preview.metadata_references == right_preview.metadata_references
    assert left_preview.approval_digest != right_preview.approval_digest
    command = ReasonCase(case.database, case.runtime, case.manager, left).command(
        "wrong-terminal-set"
    )
    floor = case.runtime.ledger.database_path.read_bytes()
    with pytest.raises(SourcePolicyError, match="body_erasure_review_stale"):
        case.manager.commit(command.model_copy(update={"selectors": right.selectors}))
    assert case.runtime.ledger.database_path.read_bytes() == floor


@pytest.mark.parametrize("wrong", ["identity", "selector", "lineage"])
def test_scope_and_exact_selector_are_proved_before_private_store_census(
    case: ReasonCase, wrong: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import satori.infrastructure.persistence.erasure_personality_reasons as implementation

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("private census happened before identity-selector proof")

    monkeypatch.setattr(implementation, "_load_store", forbidden)
    request = case.request
    reason = "checkpoint_reason_selector_unavailable"
    if wrong == "identity":
        request = request.model_copy(update={"identity_id": "foreign-identity"})
        reason = "checkpoint_reason_scope_unavailable"
    elif wrong == "selector":
        request = request.model_copy(
            update={
                "selectors": (
                    CheckpointReasonSelector(CheckpointReasonKind.CHECKPOINT_APPROVAL, "missing"),
                )
            }
        )
    with case.runtime.exclusive() as snapshot:
        store = SQLiteCheckpointReasonBodyStore(case.database)
        scope = CheckpointReasonScope(request.identity_id, snapshot.lineage_id)
        if wrong == "lineage":
            scope = CheckpointReasonScope(scope.identity_id, "foreign-lineage")
            reason = "checkpoint_reason_scope_unavailable"
        with pytest.raises(SourcePolicyError, match=reason):
            store.preview(scope, request.selectors, snapshot=snapshot)


def test_real_commit_restart_and_noop_resume_leave_accountability_bytes_unchanged(
    case: ReasonCase,
) -> None:
    before = stored_rows(Path(str(case.database.engine.url.database)))
    command = case.command()
    result = case.manager.commit(command)
    assert result.main_projection_current
    assert result.selected_bodies_removed
    after = stored_rows(Path(str(case.database.engine.url.database)))
    for table in (
        "audit_events",
        "audit_metadata_bindings",
        "satori_personality_traits",
        "satori_personality_states",
        "personality_checkpoint_traits",
        "personality_checkpoints",
        "personality_revisions",
    ):
        assert after[table] == before[table]
    restarted = SourcePolicyRuntime(
        case.runtime.ledger.directory,
        case.request.identity_id,
        verify_mirror=case.runtime.verify_mirror,
    )
    manager = CheckpointReasonManager(restarted, SQLiteCheckpointReasonBodyStore(case.database))
    assert manager.status(case.request, request_id=command.request_id) == result
    assert manager.resume(case.request, request_id=command.request_id) == result
    assert manager.commit(command) == result
    assert stored_rows(Path(str(case.database.engine.url.database))) == after


def test_partial_selection_preserves_unselected_operator_reason(case: ReasonCase) -> None:
    request = CheckpointReasonRequest(
        identity_id=case.request.identity_id, selectors=(case.request.selectors[0],)
    )
    selected = ReasonCase(case.database, case.runtime, case.manager, request)
    result = case.manager.commit(selected.command("one-note"))
    assert result.selected_bodies_removed
    with case.database.engine.connect() as connection:
        remaining = connection.exec_driver_sql(
            "SELECT reason,reason_state FROM personality_restore_events"
        ).one()
        assert remaining[0] == "SYNTHETIC_PRIVATE_RESTORE_REASON"
        assert remaining[1] == "available"
    preview = case.manager.preview(case.request)
    assert preview.already_erased == 1
    assert preview.approval_reasons == 0
    assert preview.restore_reasons == 1
    assert preview.metadata_references == 51
    # The existing terminal approval remains in the reviewed exact selector set.
    mixed_command = case.command("mixed-terminal-continuation")
    continued = case.manager.commit(mixed_command)
    assert continued.main_projection_current
    assert case.manager.commit(mixed_command) == continued
    with case.runtime.shared():
        action = case.runtime.body_action(mixed_command.request_id)
        assert isinstance(action, CheckpointReasonAction)
        assert (
            tuple(target.selector for target in action.proposal.targets) == case.request.selectors
        )
