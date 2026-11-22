"""Single-attempt transient source question, with no canonical reply or learning."""

from collections.abc import Callable
from datetime import datetime, timedelta

from satori.application.assistant.routing import AssistantReadRoute
from satori.application.assistant.source_answer_contracts import (
    SourceAnswerError,
    SourceSearchPlan,
    SourceTaskAnswer,
    SourceTaskRequest,
    SourceTaskSnapshot,
)
from satori.application.assistant.source_answer_reasoning import (
    CoreSourceAnswerReasoner,
    PreparedSourceContext,
)


class SourceAnswerService:
    """One owner of RAM-only plan/context; every await is revocably fenced."""

    def __init__(
        self,
        *,
        request: SourceTaskRequest,
        reasoner: CoreSourceAnswerReasoner,
        require_live: Callable[[], None],
        clock: Callable[[], datetime],
    ) -> None:
        self.request = SourceTaskRequest.model_validate(request)
        self._reasoner, self._require_live, self._clock = reasoner, require_live, clock
        self._context: PreparedSourceContext | None = None
        self._plan: SourceSearchPlan | None = None
        self._planning_started = self._answer_started = self._closed = False

    def require(self) -> None:
        self._require_live()
        if self._closed or not self.request.created_at <= self._clock() < self.request.expires_at:
            raise SourceAnswerError("source_task_expired")
        self._reasoner.require_scope(self.request.scope)

    async def plan(self) -> SourceSearchPlan:
        self.require()
        if self._planning_started:
            raise SourceAnswerError("source_plan_already_attempted")
        self._planning_started = True
        try:
            context = await self._reasoner.prepare_context(self.request, self.require)
            self.require()
            plan = await self._reasoner.plan(self.request, context, self.require)
            self.require()
            self._context, self._plan = context, plan
            return plan
        except BaseException:
            self.close()
            raise

    async def route(self) -> AssistantReadRoute:
        self.require()
        if self._planning_started:
            raise SourceAnswerError("source_plan_already_attempted")
        self._planning_started = True
        try:
            route = await self._reasoner.route(self.request, self.require)
            self.require()
            return route
        finally:
            self.close()

    def require_snapshot(self, snapshot: SourceTaskSnapshot) -> SourceTaskSnapshot:
        self.require()
        value = SourceTaskSnapshot.model_validate(snapshot)
        plan, request = self._plan, self.request
        if (
            plan is None
            or plan.status != "search"
            or value.request_id != request.request_id
            or value.scope != request.scope
            or value.plan_digest != plan.digest
            or {item.source for item in value.coverage} != set(request.sources)
            or not request.created_at <= value.observed_at <= self._clock() + timedelta(seconds=5)
            or any(
                item.window_start != plan.window_start or item.window_end != plan.window_end
                for item in value.coverage
            )
        ):
            raise SourceAnswerError("source_snapshot_scope_invalid")
        queries = {"mail": plan.mail_query, "telegram": plan.telegram_query, "calls": None}
        statuses = {item.source: item.status for item in value.coverage}
        if any(
            item.query != queries[item.source]
            or (item.status in {"searched", "partial"} and queries[item.source] is None)
            for item in value.coverage
        ) or any(
            statuses[item.source] not in {"searched", "partial"}
            or not request.created_at <= item.observed_at <= value.observed_at
            or (
                item.event_at is not None
                and not plan.window_start <= item.event_at <= plan.window_end
            )
            for item in value.items
        ):
            raise SourceAnswerError("source_snapshot_coverage_invalid")
        return value

    async def answer(self, snapshot: SourceTaskSnapshot) -> SourceTaskAnswer:
        value = self.require_snapshot(snapshot)
        if self._answer_started or self._context is None or self._plan is None:
            raise SourceAnswerError("source_answer_not_prepared")
        self._answer_started = True
        try:
            answer = await self._reasoner.answer(
                self.request, self._plan, value, self._context, self.require
            )
            self.require()
            return answer
        finally:
            self.close()

    def close(self) -> None:
        self._closed = True
        self._context = self._plan = None
