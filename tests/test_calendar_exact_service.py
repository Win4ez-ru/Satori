"""User-authored exact intervals create through the same action owner without a model."""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from satori.application.assistant.calendar_actions_contracts import CalendarActionScope
from satori.application.assistant.calendar_planning_contracts import (
    CalendarObservedEvent,
    CalendarPlanningError,
    CalendarPlanningRequest,
)
from satori.infrastructure.persistence.database import Database
from tests.calendar_planning_fixture import make_request, make_service, seeded_core


def test_exact_event_has_review_no_model_no_canonical_learning_and_one_effect(
    migrated_database: Database,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database, core, model, clock, scope = await seeded_core(migrated_database, tmp_path)
        service, source, effects = make_service(core, scope, clock, tmp_path)
        source.events = ()
        base = make_request(scope)
        request = CalendarPlanningRequest.model_validate(
            base.model_copy(update={"window_end": base.window_start + timedelta(hours=1)})
        )
        before = core.history.execute()
        calls = len(model.requests)
        record = await service.prepare_exact(request)
        assert record.state == "proposed"
        assert record.proposal.command.starts_at == request.window_start
        assert record.proposal.command.ends_at == request.window_end
        assert record.proposal.command.title == request.title
        assert len(model.requests) == calls
        assert model.wire_sends == 0
        assert effects.effects == 0
        with pytest.raises(CalendarPlanningError, match="no_model_disclosure"):
            service.disclose(request.request_id, explicit_disclosure=True)
        with pytest.raises(CalendarPlanningError, match="no_model_plan"):
            await service.plan(request.request_id)
        reviewed = await service.approve(request.request_id, record.proposal.review_digest)
        assert reviewed.approval is not None
        result = await service.create(request.request_id, reviewed.approval.approval_id)
        assert result.state == "created"
        again = await service.create(request.request_id, reviewed.approval.approval_id)
        assert again == result
        assert effects.create_calls == effects.effects == 1
        assert core.history.execute() == before
        assert len(model.requests) == calls
        service.close()
        database.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "problem", ["wider_window", "busy", "partial", "changed_after_review", "rotated_grant"]
)
def test_exact_intent_never_overrides_missing_time_or_source_authority(
    migrated_database: Database,
    tmp_path: Path,
    problem: str,
) -> None:
    async def scenario() -> None:
        database, core, model, clock, scope = await seeded_core(migrated_database, tmp_path)
        service, source, effects = make_service(core, scope, clock, tmp_path)
        base = make_request(scope)
        request = (
            base
            if problem == "wider_window"
            else CalendarPlanningRequest.model_validate(
                base.model_copy(update={"window_end": base.window_start + timedelta(hours=1)})
            )
        )
        if problem != "busy":
            source.events = ()
        if problem == "partial":
            source.complete = False
        if problem in {"wider_window", "busy", "partial"}:
            with pytest.raises(CalendarPlanningError):
                await service.prepare_exact(request)
        else:
            record = await service.prepare_exact(request)
            approval = await service.approve(request.request_id, record.proposal.review_digest)
            assert approval.approval is not None
            if problem == "changed_after_review":
                source.events = make_service_events(scope, base.window_start)
            else:
                source.read_grant_id = uuid4()
            with pytest.raises(CalendarPlanningError):
                await service.create(request.request_id, approval.approval.approval_id)
        assert effects.effects == 0
        assert model.wire_sends == 0
        service.close()
        database.dispose()

    asyncio.run(scenario())


def make_service_events(
    scope: CalendarActionScope,
    start: datetime,
) -> tuple[CalendarObservedEvent, ...]:
    return (
        CalendarObservedEvent(
            event_ref="public-changed",
            calendar_id=scope.calendar_id,
            title="PUBLIC CHANGED",
            starts_at=start,
            ends_at=start + timedelta(hours=1),
            status="confirmed",
        ),
    )
