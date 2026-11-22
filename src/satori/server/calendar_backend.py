"""Default-off typed calendar operations in the existing owned backend lane."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from satori.application.assistant.calendar_actions import CalendarActionOwner
from satori.application.assistant.calendar_actions_contracts import (
    CalendarActionDTO,
    CalendarActionError,
    CalendarActionScope,
)
from satori.application.assistant.calendar_chat_intake import calendar_chat_intake
from satori.application.assistant.calendar_planning_contracts import (
    CalendarPlanningError,
    CalendarPlanningRequest,
)
from satori.application.assistant.calendar_planning_source import CalendarPlanningSource
from satori.application.assistant.calendar_reasoning import CoreCalendarReasoner
from satori.application.assistant.calendar_service import CalendarAssistantService
from satori.application.assistant.calendar_text import (
    CalendarTextClarification,
)
from satori.composition import ConversationServices


class CalendarBackendCommand(CalendarActionDTO):
    operation: Literal[
        "preview",
        "exact_preview",
        "preview_text",
        "disclose",
        "plan",
        "approve",
        "create",
        "reconcile",
    ]
    request: CalendarPlanningRequest | None = Field(default=None, repr=False)
    text: str | None = Field(default=None, min_length=1, max_length=8000, repr=False)
    explicit_disclosure: bool | None = None
    review_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    approval_id: UUID | None = None

    @model_validator(mode="after")
    def exact_fields(self) -> Self:
        expected = {
            "preview": {"request"},
            "exact_preview": {"request"},
            "preview_text": {"text"},
            "disclose": {"explicit_disclosure"},
            "plan": set(),
            "approve": {"review_digest"},
            "create": {"approval_id"},
            "reconcile": set(),
        }[self.operation]
        if self.model_fields_set - {"operation"} != expected or any(
            getattr(self, name) is None for name in expected
        ):
            raise ValueError("calendar_backend_command_scope_invalid")
        return self


@dataclass(frozen=True, slots=True)
class CalendarRuntimeBindings:
    """Trusted host-supplied grants/ports, never fields in an untrusted command."""

    source: CalendarPlanningSource
    actions: CalendarActionOwner
    clock: Callable[[], datetime]
    live_scope: Callable[[], CalendarActionScope]

    def build(self, core: ConversationServices, *, provider_id: str) -> CalendarAssistantService:
        return CalendarAssistantService(
            reasoner=CoreCalendarReasoner(core.talk, core.emotion_status, provider_id, self.clock),
            source=self.source,
            actions=self.actions,
            clock=self.clock,
            live_scope=self.live_scope,
        )


async def execute_calendar_command(
    service: CalendarAssistantService,
    command: CalendarBackendCommand,
    *,
    request_id: UUID,
    session_id: UUID,
) -> dict[str, object]:
    try:
        return await _execute_calendar_command(
            service, command, request_id=request_id, session_id=session_id
        )
    except (CalendarPlanningError, CalendarActionError):
        # An effect may already have happened when its receipt becomes unavailable.
        # Never turn an exception after dispatch into proof of absence.
        uncertain = command.operation in {"create", "reconcile"}
        return {
            "status": "unknown" if uncertain else "unavailable",
            "reason": "calendar_operation_unavailable",
            "event_created": None if uncertain else False,
            "reconcile_required": uncertain,
        }


async def _execute_calendar_command(
    service: CalendarAssistantService,
    command: CalendarBackendCommand,
    *,
    request_id: UUID,
    session_id: UUID,
) -> dict[str, object]:
    scope = service.current_scope()
    if scope.session_id != session_id:
        raise ValueError("calendar_backend_session_mismatch")
    match command.operation:
        case "exact_preview":
            assert command.request is not None
            if command.request.request_id != request_id or command.request.scope != scope:
                raise ValueError("calendar_backend_request_mismatch")
            record = await service.prepare_exact(command.request)
            return {
                "status": "proposed",
                "request_id": str(request_id),
                "proposal": record.proposal.model_dump(mode="json"),
                "mode": "user_exact_interval",
                "model_called": False,
                "event_created": False,
            }
        case "preview" | "preview_text":
            request = command.request
            if command.operation == "preview_text":
                assert command.text is not None
                parsed = calendar_chat_intake(
                    command.text, request_id=request_id, scope=scope, now=service.now()
                )
                if isinstance(parsed, CalendarTextClarification):
                    return parsed.model_dump(mode="json")
                request = parsed.request
                if parsed.mode == "exact":
                    record = await service.prepare_exact(request)
                    return {
                        "status": "proposed",
                        "request_id": str(request_id),
                        "proposal": record.proposal.model_dump(mode="json"),
                        "mode": "user_exact_interval",
                        "model_called": False,
                        "event_created": False,
                    }
            assert request is not None
            if request.request_id != request_id or request.scope != scope:
                raise ValueError("calendar_backend_request_mismatch")
            observation = await service.preview(request)
            return {
                "status": "disclosure_review_required",
                "request_id": str(request_id),
                "snapshot_digest": observation.digest,
                "event_count": len(observation.events),
                "calendar_count": len(observation.selected_calendar_ids),
                "provider": service.reasoner.provider_id,
                "disclosure_includes": [
                    "selected_event_titles_and_times",
                    "bounded_conversation",
                    "eligible_memory",
                    "typed_self_projections",
                ],
                "expires_at": observation.expires_at.isoformat(),
                "request": request.model_dump(mode="json"),
                "model_called": False,
                "event_created": False,
            }
        case "disclose":
            permit = service.disclose(
                request_id, explicit_disclosure=command.explicit_disclosure is True
            )
            return {
                "status": "disclosure_approved",
                "permit_id": str(permit.permit_id),
                "expires_at": permit.expires_at.isoformat(),
                "event_created": False,
            }
        case "plan":
            result, action = await service.plan(request_id)
            return {
                "status": "proposed" if action is not None else result.choice.status,
                "choice": result.choice.model_dump(mode="json"),
                "proposal": action.proposal.model_dump(mode="json") if action is not None else None,
                "retrieved_memory_ids": list(result.retrieved_memory_ids),
                "recent_turns": result.recent_turns,
                "event_created": False,
            }
        case "approve":
            assert command.review_digest is not None
            record = await service.approve(request_id, command.review_digest)
            return record.model_dump(mode="json")
        case "create":
            assert command.approval_id is not None
            record = await service.create(request_id, command.approval_id)
            return record.model_dump(mode="json")
        case "reconcile":
            record = await service.reconcile(request_id)
            return record.model_dump(mode="json")
    raise AssertionError("closed_calendar_operation")
