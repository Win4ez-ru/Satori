"""Core memory/recent context → selected calendar → reviewed one-effect lifecycle."""

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from satori.application.assistant.calendar_actions_contracts import CalendarActionError
from satori.application.assistant.calendar_planning_contracts import (
    CalendarObservedEvent,
    CalendarPlanningError,
)
from satori.domain.memory_scope import MemoryScope
from satori.infrastructure.persistence.database import Database
from tests.calendar_planning_fixture import NOW, make_request, make_service, seeded_core


def test_actual_core_memory_after_restart_calendar_review_create_and_no_learning(
    migrated_database: Database,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database, core, model, clock, scope = await seeded_core(migrated_database, tmp_path)
        service, _source, effects = make_service(core, scope, clock, tmp_path)
        request = make_request(scope)
        before_self = core.talk.get_self.execute()
        before_history = core.history.execute()
        before_memory = core.memories.execute(
            scope=MemoryScope(str(scope.identity_id), "local-default")
        )
        before_affect = core.emotion_status.execute(str(scope.identity_id))
        snapshot = await service.preview(request)
        assert snapshot.complete
        assert effects.effects == 0
        assert len(model.source_packets) == 0
        with pytest.raises(CalendarPlanningError, match="disclosure_required"):
            await service.plan(request.request_id)
        with pytest.raises(CalendarPlanningError, match="disclosure_required"):
            service.disclose(request.request_id)
        service.disclose(request.request_id, explicit_disclosure=True)
        result, proposed = await service.plan(request.request_id)
        assert proposed is not None
        assert result.retrieved_memory_ids == model.memory_ids
        assert result.recent_turns == 1
        assert proposed.proposal.command.starts_at.hour == 16
        assert proposed.proposal.command.title == request.title
        assert result.choice.used_memory_ids == model.memory_ids
        assert effects.effects == 0
        assert model.wire_sends == 1
        again, same = await service.plan(request.request_id)
        assert again == result
        assert same == proposed
        assert model.wire_sends == 1
        with pytest.raises(CalendarActionError):
            await service.approve(request.request_id, "f" * 64)
        reviewed = await service.approve(request.request_id, proposed.proposal.review_digest)
        assert reviewed.approval is not None
        assert effects.effects == 0
        committed = await service.create(request.request_id, reviewed.approval.approval_id)
        assert committed.state == "created"
        assert effects.effects == 1
        repeated = await service.create(request.request_id, reviewed.approval.approval_id)
        assert repeated == committed
        assert effects.effects == 1
        # Neither external source contents nor generated planning text entered
        # canonical USER/assistant history, episodes, self or affect evidence.
        assert core.history.execute() == before_history
        assert (
            core.memories.execute(scope=MemoryScope(str(scope.identity_id), "local-default"))
            == before_memory
        )
        assert core.talk.get_self.execute() == before_self
        assert core.emotion_status.execute(str(scope.identity_id)) == before_affect
        assert "PUBLIC_BUSY_TITLE" not in repr(core.history.execute())
        service.close()
        database.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "problem",
    [
        "partial",
        "changed_before_plan",
        "changed_after_model",
        "rotated_during_model",
        "invalid_model",
    ],
)
def test_incomplete_stale_revoked_or_invented_data_never_creates(
    migrated_database: Database,
    tmp_path: Path,
    problem: str,
) -> None:
    async def scenario() -> None:
        database, core, model, clock, scope = await seeded_core(migrated_database, tmp_path)
        service, source, effects = make_service(core, scope, clock, tmp_path)
        request = make_request(scope)
        if problem == "partial":
            source.complete = False
            with pytest.raises(CalendarPlanningError, match="partial"):
                await service.preview(request)
            assert model.wire_sends == effects.effects == 0
            service.close()
            database.dispose()
            return
        await service.preview(request)
        service.disclose(request.request_id, explicit_disclosure=True)

        def change_events() -> None:
            source.events = (
                *source.events,
                CalendarObservedEvent(
                    event_ref="public-new-event",
                    calendar_id=scope.calendar_id,
                    title="PUBLIC_NEW_EVENT",
                    starts_at=NOW + timedelta(days=1, hours=6),
                    ends_at=NOW + timedelta(days=1, hours=8),
                    status="confirmed",
                ),
            )

        if problem == "changed_before_plan":
            change_events()
        elif problem == "changed_after_model":
            model.before_response = change_events
        elif problem == "rotated_during_model":
            from uuid import uuid4

            model.before_response = lambda: setattr(source, "read_grant_id", uuid4())
        else:
            model.invalid_choice = True
        with pytest.raises(CalendarPlanningError):
            await service.plan(request.request_id)
        assert effects.effects == 0
        assert service.actions.get(request.request_id, scope=scope) is None
        if problem in {"changed_before_plan", "rotated_during_model"}:
            assert model.wire_sends == 0
        if problem == "invalid_model":
            with pytest.raises(CalendarPlanningError, match="unknown_no_replay"):
                await service.plan(request.request_id)
            assert model.wire_sends == 1
        service.close()
        database.dispose()

    asyncio.run(scenario())


def test_effect_timeout_reconciles_without_second_creation(
    migrated_database: Database,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database, core, _, clock, scope = await seeded_core(migrated_database, tmp_path)
        service, _, effects = make_service(core, scope, clock, tmp_path)
        request = make_request(scope)
        await service.preview(request)
        service.disclose(request.request_id, explicit_disclosure=True)
        _, proposed = await service.plan(request.request_id)
        assert proposed is not None
        reviewed = await service.approve(request.request_id, proposed.proposal.review_digest)
        assert reviewed.approval is not None
        effects.fail_after_create = True
        unknown = await service.create(request.request_id, reviewed.approval.approval_id)
        assert unknown.state == "unknown"
        assert effects.effects == 1
        reconciled = await service.reconcile(request.request_id)
        assert reconciled.state == "reconciled_present"
        assert effects.effects == effects.create_calls == 1
        service.close()
        database.dispose()

    asyncio.run(scenario())
