"""One complete calendar planning/review/effect path over the same Core."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from satori.application.assistant.calendar_actions import CalendarActionOwner
from satori.application.assistant.calendar_actions_contracts import (
    CalendarActionRecord,
    CalendarActionScope,
    CalendarCreateInput,
)
from satori.application.assistant.calendar_admission import require_calendar_host_admission
from satori.application.assistant.calendar_disclosure import (
    CalendarDisclosureOwner,
    CalendarDisclosurePermit,
)
from satori.application.assistant.calendar_planning_contracts import (
    CalendarPlanningError,
    CalendarPlanningRequest,
    CalendarPlanningSnapshot,
    available_slots,
    context_digest,
)
from satori.application.assistant.calendar_planning_source import CalendarPlanningSource
from satori.application.assistant.calendar_reasoning import (
    CalendarReasoningResult,
    CoreCalendarReasoner,
)


@dataclass(slots=True)
class _PendingPlan:
    request: CalendarPlanningRequest
    snapshot: CalendarPlanningSnapshot
    permit: CalendarDisclosurePermit | None = None
    result: CalendarReasoningResult | None = None
    inference_claimed: bool = False
    exact_context_digest: str | None = None


class CalendarAssistantService:
    """Transient source/plans plus the existing dedicated durable action owner.

    The enclosing backend serializes this service with canonical operations.
    Restart clears disclosure/plans/approvals; durable effects reconcile by their
    action owner. It cannot silently reactivate an old disclosure or model call.
    """

    def __init__(
        self,
        *,
        reasoner: CoreCalendarReasoner,
        source: CalendarPlanningSource,
        actions: CalendarActionOwner,
        clock: Callable[[], datetime],
        live_scope: Callable[[], CalendarActionScope],
    ) -> None:
        self.reasoner = reasoner
        self.source = source
        self.actions = actions
        self._clock = clock
        self._live_scope = live_scope
        self._pending: dict[UUID, _PendingPlan] = {}
        self._closed = False
        self.disclosures = CalendarDisclosureOwner(
            clock=clock, live_guard=self._require_live, source_guard=self._require_context
        )

    def _require_live(self) -> None:
        if self._closed:
            raise CalendarPlanningError("calendar_service_closed")
        require_calendar_host_admission()
        self.source.require(self._live_scope())

    def current_scope(self) -> CalendarActionScope:
        self._require_live()
        return self._live_scope()

    def now(self) -> datetime:
        return self._clock()

    def _require_context(self, snapshot: CalendarPlanningSnapshot) -> None:
        self._require_scope(snapshot.scope)
        self.source.require(snapshot.scope, read_grant_id=snapshot.read_grant_id)

    def _require_scope(self, scope: CalendarActionScope) -> None:
        self._require_live()
        if scope != self._live_scope():
            raise CalendarPlanningError("calendar_service_scope_changed")
        self.source.require(scope)
        self.reasoner.require_scope(scope)

    def _plan(self, request_id: UUID) -> _PendingPlan:
        pending = self._pending.get(request_id)
        if pending is None:
            raise CalendarPlanningError("calendar_plan_unavailable_after_restart")
        self._require_scope(pending.request.scope)
        self._require_context(pending.snapshot)
        self.reasoner.require_session(pending.request)
        return pending

    async def preview(self, request: CalendarPlanningRequest) -> CalendarPlanningSnapshot:
        self._require_scope(request.scope)
        self.reasoner.require_session(request)
        existing = self._pending.get(request.request_id)
        if existing is not None:
            if existing.request != request:
                raise CalendarPlanningError("calendar_request_id_conflict")
            self._require_context(existing.snapshot)
            existing.snapshot.require_for(request, self._clock())
            return existing.snapshot
        if len(self._pending) >= 128:
            raise CalendarPlanningError("calendar_plan_capacity")
        snapshot = await self.source.fetch(request)
        self._require_scope(request.scope)
        snapshot.require_for(request, self._clock())
        self._pending[request.request_id] = _PendingPlan(request, snapshot)
        return snapshot

    def disclose(
        self, request_id: UUID, *, explicit_disclosure: bool = False
    ) -> CalendarDisclosurePermit:
        pending = self._plan(request_id)
        if pending.exact_context_digest is not None:
            raise CalendarPlanningError("calendar_exact_request_has_no_model_disclosure")
        if pending.permit is not None:
            self.disclosures.require(
                pending.permit,
                pending.request,
                pending.snapshot,
                provider=self.reasoner.provider_id,
            )
            return pending.permit
        permit = self.disclosures.approve(
            pending.request,
            pending.snapshot,
            provider=self.reasoner.provider_id,
            explicit_disclosure=explicit_disclosure,
        )
        pending.permit = permit
        return permit

    async def prepare_exact(self, request: CalendarPlanningRequest) -> CalendarActionRecord:
        """An explicitly supplied exact interval needs no model or source disclosure."""
        request = CalendarPlanningRequest.model_validate(request)
        if (
            request.window_end - request.window_start
        ).total_seconds() != request.duration_minutes * 60:
            raise CalendarPlanningError("calendar_exact_interval_required")
        snapshot = await self.preview(request)
        pending = self._plan(request.request_id)
        if pending.result is not None or pending.permit is not None:
            raise CalendarPlanningError("calendar_request_mode_conflict")
        slots = available_slots(request, snapshot, not_before=self._clock())
        if (
            len(slots) != 1
            or slots[0].starts_at != request.window_start
            or slots[0].ends_at != request.window_end
        ):
            raise CalendarPlanningError("calendar_exact_interval_unavailable")
        digest = context_digest(
            {
                "kind": "user_exact_calendar_intent",
                "request": request.digest,
                "snapshot": snapshot.digest,
            }
        )
        catalog = self.source.catalog(request.scope)
        self.actions.accept_catalog(catalog)
        record = self.actions.propose(
            CalendarCreateInput(
                request_id=request.request_id,
                title=request.title,
                starts_at=request.window_start,
                ends_at=request.window_end,
                timezone=request.scope.timezone,
                context_digest=digest,
            ),
            scope=request.scope,
            catalog=catalog,
        )
        pending.exact_context_digest = digest
        return record

    async def _fresh(self, pending: _PendingPlan) -> None:
        self._require_scope(pending.request.scope)
        current = await self.source.fetch(pending.request)
        self._require_scope(pending.request.scope)
        current.require_for(pending.request, self._clock())
        if current.digest != pending.snapshot.digest:
            raise CalendarPlanningError("calendar_source_changed_replan_required")

    async def plan(
        self, request_id: UUID
    ) -> tuple[CalendarReasoningResult, CalendarActionRecord | None]:
        pending = self._plan(request_id)
        if pending.exact_context_digest is not None:
            raise CalendarPlanningError("calendar_exact_request_has_no_model_plan")
        if pending.result is not None:
            pending.snapshot.require_for(pending.request, self._clock())
            if pending.permit is None:
                raise CalendarPlanningError("calendar_disclosure_required")
            self.disclosures.require(
                pending.permit,
                pending.request,
                pending.snapshot,
                provider=self.reasoner.provider_id,
            )
            return pending.result, self.actions.get(request_id, scope=pending.request.scope)
        if pending.inference_claimed:
            raise CalendarPlanningError("calendar_inference_unknown_no_replay")
        if pending.permit is None:
            raise CalendarPlanningError("calendar_disclosure_required")
        await self._fresh(pending)
        pending.inference_claimed = True
        result = await self.reasoner.choose(
            pending.request, pending.snapshot, pending.permit, self.disclosures
        )
        await self._fresh(pending)
        record = None
        if result.choice.status == "suggested":
            slot = next(
                (
                    s
                    for s in available_slots(
                        pending.request, pending.snapshot, not_before=self._clock()
                    )
                    if s.slot_id == result.choice.slot_id
                ),
                None,
            )
            if slot is None:
                raise CalendarPlanningError("calendar_selected_slot_expired")
            catalog = self.source.catalog(pending.request.scope)
            self.actions.accept_catalog(catalog)
            record = self.actions.propose(
                CalendarCreateInput(
                    request_id=request_id,
                    title=pending.request.title,
                    starts_at=slot.starts_at,
                    ends_at=slot.ends_at,
                    timezone=pending.request.scope.timezone,
                    context_digest=result.core_input_digest,
                ),
                scope=pending.request.scope,
                catalog=catalog,
            )
        pending.result = result
        return result, record

    async def approve(self, request_id: UUID, review_digest: str) -> CalendarActionRecord:
        pending = self._plan(request_id)
        if pending.exact_context_digest is None and (
            pending.result is None or pending.result.choice.status != "suggested"
        ):
            raise CalendarPlanningError("calendar_review_required")
        await self._fresh(pending)
        catalog = self.source.catalog(pending.request.scope)
        self.actions.accept_catalog(catalog)
        return self.actions.approve(
            request_id, review_digest, scope=pending.request.scope, catalog=catalog
        )

    async def create(self, request_id: UUID, approval_id: UUID) -> CalendarActionRecord:
        pending = self._plan(request_id)
        existing = self.actions.get(request_id, scope=pending.request.scope)
        if existing is not None and existing.state in {"created", "reconciled_present"}:
            return existing
        await self._fresh(pending)
        catalog = self.source.catalog(pending.request.scope)
        self.actions.accept_catalog(catalog)
        record = await self.actions.dispatch(
            request_id,
            approval_id,
            scope=pending.request.scope,
            catalog=catalog,
            precondition_guard=lambda: self._guard_effect(pending),
        )
        self._require_context(pending.snapshot)
        return record

    def _guard_effect(self, pending: _PendingPlan) -> None:
        self._require_context(pending.snapshot)
        self.reasoner.require_session(pending.request)
        pending.snapshot.require_for(pending.request, self._clock())
        if pending.result is None and pending.exact_context_digest is None:
            raise CalendarPlanningError("calendar_review_required")
        record = self.actions.get(pending.request.request_id, scope=pending.request.scope)
        if record is None or record.proposal.command.context_digest != (
            pending.exact_context_digest
            if pending.exact_context_digest is not None
            else pending.result.core_input_digest
            if pending.result is not None
            else None
        ):
            raise CalendarPlanningError("calendar_plan_binding_changed")

    async def reconcile(self, request_id: UUID) -> CalendarActionRecord:
        scope = self._live_scope()
        self._require_scope(scope)
        catalog = self.source.catalog(scope)
        self.actions.accept_catalog(catalog)
        record = await self.actions.reconcile(
            request_id,
            scope=scope,
            catalog=catalog,
            precondition_guard=lambda: self._require_scope(scope),
        )
        self._require_scope(scope)
        return record

    def close(self) -> None:
        self._closed = True
        self.disclosures.revoke()
        self._pending.clear()
        self.actions.close()
