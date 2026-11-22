"""Identity/query/window and single-attempt admission around public source results."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest

from satori.application.assistant.source_answer_contracts import (
    SourceAnswerError,
    SourceCoverage,
    SourceSearchPlan,
    SourceTaskAnswer,
    SourceTaskRequest,
    SourceTaskSnapshot,
)
from satori.application.assistant.source_answer_reasoning import (
    CoreSourceAnswerReasoner,
    PreparedSourceContext,
)
from satori.application.assistant.source_answer_service import SourceAnswerService
from tests.test_source_answer_contracts import public_request

NOW = datetime(2026, 11, 6, 9, tzinfo=UTC)


class PublicReasoner:
    def __init__(self) -> None:
        self.plan_calls = self.answer_calls = 0
        self.plan_value = SourceSearchPlan(
            status="search",
            mail_query="PUBLIC interview",
            telegram_query="PUBLIC interview",
            window_start=NOW - timedelta(days=30),
            window_end=NOW,
        )

    def require_scope(self, _scope: object) -> None:
        pass

    async def prepare_context(
        self,
        _request: object,
        guard: object,
    ) -> PreparedSourceContext:
        return cast(PreparedSourceContext, object())

    async def plan(
        self,
        _request: object,
        _context: object,
        _guard: object,
    ) -> SourceSearchPlan:
        self.plan_calls += 1
        return self.plan_value

    async def answer(
        self,
        _request: object,
        _plan: object,
        _snapshot: object,
        _context: object,
        _guard: object,
    ) -> SourceTaskAnswer:
        self.answer_calls += 1
        return SourceTaskAnswer(text="PUBLIC natural answer", verdict="no_match")


def case() -> tuple[SourceAnswerService, PublicReasoner, SourceTaskRequest, SourceTaskSnapshot]:
    reasoner, request = PublicReasoner(), public_request()
    service = SourceAnswerService(
        request=request,
        reasoner=cast(CoreSourceAnswerReasoner, reasoner),
        require_live=lambda: None,
        clock=lambda: NOW,
    )
    plan = reasoner.plan_value
    snapshot = SourceTaskSnapshot(
        request_id=request.request_id,
        scope=request.scope,
        plan_digest=plan.digest,
        observed_at=NOW,
        coverage=tuple(
            SourceCoverage(
                source=source,
                status="searched",
                query="PUBLIC interview",
                window_start=plan.window_start,
                window_end=plan.window_end,
            )
            for source in request.sources
        ),
    )
    return service, reasoner, request, snapshot


@pytest.mark.parametrize(
    "change", ["request", "scope", "plan", "missing", "query", "window", "old"]
)
def test_changed_snapshot_is_rejected_before_synthesis(change: str) -> None:
    async def scenario() -> None:
        service, reasoner, _request, snapshot = case()
        await service.plan()
        if change == "request":
            snapshot = snapshot.model_copy(update={"request_id": uuid4()})
        elif change == "scope":
            snapshot = snapshot.model_copy(
                update={
                    "scope": snapshot.scope.model_copy(update={"generation": uuid4()}),
                }
            )
        elif change == "plan":
            snapshot = snapshot.model_copy(update={"plan_digest": "0" * 64})
        elif change == "missing":
            snapshot = snapshot.model_copy(update={"coverage": snapshot.coverage[:1]})
        elif change == "old":
            snapshot = snapshot.model_copy(update={"observed_at": NOW - timedelta(seconds=1)})
        else:
            item = snapshot.coverage[0].model_copy(
                update={
                    "query": "PUBLIC foreign query"
                    if change == "query"
                    else snapshot.coverage[0].query,
                    "window_start": NOW - timedelta(days=29)
                    if change == "window"
                    else snapshot.coverage[0].window_start,
                }
            )
            snapshot = snapshot.model_copy(update={"coverage": (item, *snapshot.coverage[1:])})
        with pytest.raises(SourceAnswerError):
            await service.answer(snapshot)
        assert reasoner.answer_calls == 0

    asyncio.run(scenario())


def test_one_plan_and_one_answer_attempt_then_context_is_retired() -> None:
    async def scenario() -> None:
        service, reasoner, _request, snapshot = case()
        await service.plan()
        with pytest.raises(SourceAnswerError, match="already_attempted"):
            await service.plan()
        await service.answer(snapshot)
        assert service._context is None
        assert service._plan is None
        with pytest.raises(SourceAnswerError):
            await service.answer(snapshot)
        assert reasoner.plan_calls == reasoner.answer_calls == 1

    asyncio.run(scenario())
