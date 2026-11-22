"""Finite source-answer commands over the already owned serial Core worker."""

import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from satori.application.assistant.source_answer_contracts import (
    SourceAnswerError,
    SourceTaskCommand,
    SourceTaskLease,
)
from satori.application.assistant.source_answer_service import SourceAnswerService
from satori.desktop.backend import DesktopBackend
from satori.infrastructure.assistant.source_answer_lease import SourceTaskLeaseReader


def _now() -> datetime:
    return datetime.now(UTC)


class DesktopSourceAnswerOperations:
    """No account access or new Core: host sources run through their actual owners."""

    def __init__(
        self,
        backend: DesktopBackend,
        *,
        clock: Callable[[], datetime] = _now,
        monotonic: Callable[[], float] = time.monotonic,
        parent_pid: Callable[[], int] = os.getppid,
    ) -> None:
        self._backend, self._clock = backend, clock
        self._monotonic, self._parent_pid = monotonic, parent_pid
        self._service: SourceAnswerService | None = None
        self._lease: SourceTaskLease | None = None
        self._attempted: dict[UUID, datetime] = {}

    async def execute(
        self,
        command: SourceTaskCommand,
        *,
        request_id: UUID,
        session_id: UUID,
        lease: SourceTaskLease | None,
    ) -> dict[str, object]:
        try:
            command = SourceTaskCommand.model_validate(command)
            request = command.request
            if request.request_id != request_id or request.scope.session_id != session_id:
                raise SourceAnswerError("source_command_correlation_invalid")
            if command.operation == "cancel":
                if self._lease is not None and self._lease.request_id == request_id:
                    self.close()
                return {"status": "cancelled", "request_id": str(request_id)}
            if lease is None:
                raise SourceAnswerError("source_task_lease_required")
            lease = SourceTaskLease.model_validate(lease)
            if (
                lease.request_id != request_id
                or lease.scope != request.scope
                or lease.scope.profile_id != uuid5(NAMESPACE_URL, str(self._backend.data_directory))
                or lease.request_digest != request.digest
                or lease.sources != request.sources
                or not request.created_at
                <= lease.issued_at
                < lease.expires_at
                <= request.expires_at
                or lease.provider_id
                != self._backend.settings.conversation_provider.value
                + ":"
                + self._backend.settings.conversation_model
            ):
                raise SourceAnswerError("source_task_lease_scope_invalid")
            if command.operation in {"route", "plan"}:
                self._attempted = {
                    key: expiry for key, expiry in self._attempted.items() if expiry > self._clock()
                }
                if request_id in self._attempted or len(self._attempted) >= 128:
                    raise SourceAnswerError("source_task_replay_rejected")
                self._attempted[request_id] = request.expires_at
                self.close()
                reader = SourceTaskLeaseReader(
                    self._backend.data_directory,
                    lease,
                    clock=self._clock,
                    monotonic=self._monotonic,
                    parent_pid=self._parent_pid,
                )

                def require_live() -> None:
                    reader.require()
                    if self._backend.status().get("session_id") != str(session_id):
                        raise SourceAnswerError("source_task_session_changed")

                require_live()
                self._service = self._backend.build_source_answer(
                    request=request, require_live=require_live, clock=self._clock
                )
                self._lease = lease
                if command.operation == "route":
                    route = await self._service.route()
                    return {
                        "status": "routed",
                        "request_id": str(request_id),
                        "route": route.model_dump(mode="json"),
                    }
                plan = await self._service.plan()
                return {
                    "status": "planned",
                    "request_id": str(request_id),
                    "plan": plan.model_dump(mode="json"),
                }
            if self._service is None or self._lease != lease:
                raise SourceAnswerError("source_task_not_prepared")
            assert command.snapshot is not None
            answer = await self._service.answer(command.snapshot)
            return {
                "status": "answered",
                "request_id": str(request_id),
                "answer": answer.model_dump(mode="json"),
                "coverage": [item.model_dump(mode="json") for item in command.snapshot.coverage],
            }
        except (SourceAnswerError, ValueError):
            self.close()
            return {
                "status": "unavailable",
                "request_id": str(request_id),
                "reason": "source_task_unavailable",
            }

    def close(self) -> None:
        if self._service is not None:
            self._service.close()
        self._service = None
        self._lease = None
