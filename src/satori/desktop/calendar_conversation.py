"""Immutable host records for one foreground, user-authored Calendar task."""

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from satori.application.assistant.calendar_actions_contracts import CalendarCreateProposal
from satori.application.assistant.calendar_chat_fields import CalendarChatFields
from satori.application.assistant.calendar_planning_contracts import (
    CalendarPlanningRequest,
    context_digest,
)

CALENDAR_TASK_SECONDS = 15 * 60


@dataclass(frozen=True)
class CalendarConversationTask:
    task_id: UUID
    revision: UUID
    context: tuple[str, str, str]
    profile_id: UUID
    anchor_date: date
    issued_monotonic: float
    expires_monotonic: float
    direct_create: bool


@dataclass(frozen=True)
class CalendarConversationAuthority:
    """Created by the host from resolved user fields, never from a model result."""

    task_id: UUID
    revision: UUID
    context: tuple[str, str, str]
    request: CalendarPlanningRequest
    fields_digest: str
    expires_monotonic: float

    def admits(
        self,
        task: CalendarConversationTask | None,
        fields: CalendarChatFields,
        context: tuple[str, str, str] | None,
        request: CalendarPlanningRequest | None,
        proposal: CalendarCreateProposal,
        monotonic_now: float,
    ) -> bool:
        if task is None or request is None:
            return False
        command = proposal.command
        return bool(
            task.direct_create
            and task.task_id == self.task_id
            and task.revision == self.revision
            and task.context == self.context == context
            and task.profile_id == request.scope.profile_id
            and task.issued_monotonic <= monotonic_now
            and monotonic_now < min(task.expires_monotonic, self.expires_monotonic)
            and self.fields_digest == context_digest(fields.model_dump(mode="json"))
            and self.request == request
            and fields.mode == "exact"
            and not fields.needs_clarification
            and proposal.scope == request.scope
            and command.request_id == request.request_id
            and command.title == request.title
            and command.timezone == request.scope.timezone
            and command.starts_at == request.window_start
            and command.ends_at == request.window_end
        )
