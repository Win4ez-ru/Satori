"""Owned JSONL worker process for the desktop, with no network listener."""

import argparse
import asyncio
import logging
import sys
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Literal, TextIO

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from satori.application.assistant.calendar_host_lease import CalendarHostLease
from satori.application.assistant.source_answer_contracts import SourceTaskCommand, SourceTaskLease
from satori.application.conversation.errors import ConversationError
from satori.application.retrieval.adoption import MemoryIndexAdoptionRequest
from satori.core.conversation import ConversationProviderError
from satori.desktop.backend import DesktopBackend, DesktopBackendError
from satori.desktop.calendar_bridge import CalendarReadRPC, DesktopCalendarOperations
from satori.desktop.openai_runtime import DesktopOpenAIRuntime
from satori.desktop.source_answer_bridge import DesktopSourceAnswerOperations
from satori.desktop.worker_ingress import route_ingress
from satori.domain.errors import NotActivated
from satori.domain.source_policy import SourcePolicyError
from satori.server.calendar_backend import CalendarBackendCommand

MAX_COMMAND_CHARS = 64_000
MAX_RESPONSE_BYTES = 4_000_000


class _Correlation(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    id: str = Field(min_length=1, max_length=128)


class CommandData(BaseModel):
    """Only documented command inputs cross this private process boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    text: str | None = Field(default=None, min_length=1, max_length=8000)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    calendar: CalendarBackendCommand | None = Field(default=None, repr=False)
    calendar_lease: CalendarHostLease | None = Field(default=None, repr=False)
    source_answer: SourceTaskCommand | None = Field(default=None, repr=False)
    source_answer_lease: SourceTaskLease | None = Field(default=None, repr=False)
    memory_index_adoption: MemoryIndexAdoptionRequest | None = Field(default=None, repr=False)


class WorkerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    protocol_version: Literal[1] = 1
    id: str = Field(min_length=1, max_length=128)
    command: Literal[
        "initialize",
        "status",
        "activate",
        "history",
        "new_session",
        "resume_session",
        "send",
        "send_for_session",
        "new_mobile_session",
        "request_status",
        "memories",
        "shutdown",
        "calendar",
        "source_answer",
        "memory_index_adopt",
    ]
    data: CommandData = Field(default_factory=CommandData)

    @model_validator(mode="after")
    def require_command_fields(self) -> "WorkerRequest":
        fields = self.data.model_fields_set
        allowed = {
            "history": {"session_id"},
            "resume_session": {"session_id"},
            "send": {"text", "request_id"},
            "send_for_session": {"text", "request_id", "session_id"},
            "new_mobile_session": {"request_id"},
            "request_status": {"session_id", "request_id"},
            "calendar": {"session_id", "request_id", "calendar", "calendar_lease"},
            "source_answer": {"session_id", "request_id", "source_answer", "source_answer_lease"},
            "memory_index_adopt": {"memory_index_adoption"},
        }.get(self.command, set())
        if fields - allowed:
            raise ValueError("unexpected_command_data")
        if self.command in {"resume_session", "send_for_session", "request_status"} and (
            self.data.session_id is None
        ):
            raise ValueError("session_id_required")
        if self.command in {"send", "send_for_session"} and (
            self.data.text is None or self.data.request_id is None
        ):
            raise ValueError("text_and_request_id_required")
        if self.command == "request_status" and self.data.request_id is None:
            raise ValueError("request_id_required")
        if self.command == "calendar" and any(
            value is None
            for value in (self.data.session_id, self.data.request_id, self.data.calendar)
        ):
            raise ValueError("calendar_command_required")
        if self.command == "source_answer" and any(
            value is None
            for value in (self.data.session_id, self.data.request_id, self.data.source_answer)
        ):
            raise ValueError("source_answer_command_required")
        if self.command == "memory_index_adopt" and self.data.memory_index_adoption is None:
            raise ValueError("memory_index_adoption_required")
        return self


class WorkerError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: str
    message: str


class WorkerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    protocol_version: Literal[1] = 1
    id: str
    event: Literal["result", "error", "calendar_host_request"]
    data: dict[str, object] | None = None
    error: WorkerError | None = None


def safe_error(error: Exception) -> WorkerError:
    """Never echo exception strings: they may contain user/provider material."""
    if isinstance(error, DesktopBackendError):
        return WorkerError(code=error.code, message="Операция недоступна в текущем состоянии.")
    if isinstance(error, SourcePolicyError):
        return WorkerError(
            code="source_policy_rejected",
            message="Проверка хранилища не пройдена. Нужна явная диагностика установки.",
        )
    if isinstance(error, NotActivated):
        return WorkerError(code="not_activated", message="Сначала явно активируй новую Сатори.")
    if isinstance(error, ConversationProviderError):
        return WorkerError(
            code="provider_unavailable",
            message="Модель недоступна. Проверь её подключение в настройках.",
        )
    if isinstance(error, (ValidationError, ConversationError, ValueError)):
        return WorkerError(code="command_rejected", message="Команда отклонена.")
    return WorkerError(
        code="runtime_failure", message="Операция не завершена. Проверь диагностику."
    )


async def dispatch(
    backend: DesktopBackend,
    request: WorkerRequest,
    *,
    calendar: DesktopCalendarOperations | None = None,
    source_answer: DesktopSourceAnswerOperations | None = None,
) -> dict[str, object]:
    """Dispatch to application adapters without SQL or Character policy."""
    match request.command:
        case "initialize":
            return backend.open()
        case "status":
            return backend.status()
        case "activate":
            return backend.activate()
        case "history":
            return backend.history(request.data.session_id)
        case "new_session":
            return backend.new_session()
        case "resume_session":
            assert request.data.session_id is not None
            return backend.resume_session(request.data.session_id)
        case "send":
            assert request.data.text is not None
            assert request.data.request_id is not None
            return await backend.send(request.data.text, request.data.request_id)
        case "send_for_session":
            assert request.data.text is not None
            assert request.data.request_id is not None
            assert request.data.session_id is not None
            return await backend.send_for_session(
                request.data.text, request.data.request_id, request.data.session_id
            )
        case "new_mobile_session":
            return await backend.new_mobile_session()
        case "request_status":
            assert request.data.session_id is not None
            assert request.data.request_id is not None
            return backend.request_status(request.data.session_id, request.data.request_id)
        case "memories":
            return backend.memories()
        case "calendar":
            if calendar is None:
                return {"status": "unavailable", "event_created": False}
            assert request.data.calendar is not None
            assert request.data.request_id is not None
            assert request.data.session_id is not None
            from uuid import UUID

            return await calendar.execute(
                request.data.calendar,
                request_id=UUID(request.data.request_id),
                session_id=UUID(request.data.session_id),
                parent_command=request.id,
                lease=request.data.calendar_lease,
            )
        case "source_answer":
            if source_answer is None:
                return {"status": "unavailable", "reason": "source_task_unavailable"}
            assert request.data.source_answer is not None
            assert request.data.request_id is not None
            assert request.data.session_id is not None
            from uuid import UUID

            return await source_answer.execute(
                request.data.source_answer,
                request_id=UUID(request.data.request_id),
                session_id=UUID(request.data.session_id),
                lease=request.data.source_answer_lease,
            )
        case "memory_index_adopt":
            assert request.data.memory_index_adoption is not None
            return await backend.adopt_openai_memory_index(request.data.memory_index_adoption)
        case "shutdown":
            return await backend.close()


async def run_worker(
    backend: DesktopBackend,
    source: TextIO,
    destination: TextIO,
    *,
    calendar_action_helper: Path | None = None,
    calendar_factory: Callable[[DesktopBackend, CalendarReadRPC], DesktopCalendarOperations]
    | None = None,
) -> int:
    """One serial foreground dispatcher; background owner work shares this loop."""

    def emit_host(parent: str, data: dict[str, object]) -> None:
        frame = WorkerResponse(id=parent, event="calendar_host_request", data=data)
        destination.write(frame.model_dump_json(exclude_none=True) + "\n")
        destination.flush()

    rpc = CalendarReadRPC(emit_host)
    calendar = (
        calendar_factory(backend, rpc)
        if calendar_factory is not None
        else DesktopCalendarOperations(backend, rpc, action_helper=calendar_action_helper)
    )
    source_answer = DesktopSourceAnswerOperations(backend)
    commands: asyncio.Queue[str | None] = asyncio.Queue(maxsize=16)
    reader = asyncio.create_task(route_ingress(source, commands, rpc))
    try:
        while True:
            # Waiting for UI input must not starve post-response processing.
            line = await commands.get()
            if line is None:
                break
            request: WorkerRequest | None = None
            correlation_id = "invalid"
            try:
                if len(line) > MAX_COMMAND_CHARS or not line.endswith("\n"):
                    raise DesktopBackendError("invalid_record")
                correlation_id = _Correlation.model_validate_json(line).id
                request = WorkerRequest.model_validate_json(line)
                response = WorkerResponse(
                    id=request.id,
                    event="result",
                    data=await dispatch(
                        backend, request, calendar=calendar, source_answer=source_answer
                    ),
                )
            except Exception as error:
                response = WorkerResponse(
                    id=correlation_id,
                    event="error",
                    error=safe_error(error),
                )
            serialized = response.model_dump_json(exclude_none=True)
            if len(serialized.encode("utf-8")) > MAX_RESPONSE_BYTES:
                serialized = WorkerResponse(
                    id=correlation_id,
                    event="error",
                    error=WorkerError(
                        code="response_too_large", message="Ответ превышает лимит интерфейса."
                    ),
                ).model_dump_json(exclude_none=True)
            destination.write(serialized + "\n")
            destination.flush()
            stop = request is not None and request.command == "shutdown"
            # Release source-bearing command material before waiting for the next line.
            request = None
            line = ""
            if stop:
                break
    finally:
        rpc.close()
        reader.cancel()
        with suppress(asyncio.CancelledError):
            await reader
        try:
            calendar.close()
            source_answer.close()
        finally:
            await backend.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Satori private desktop worker")
    parser.add_argument("--data-directory", type=Path, required=True)
    parser.add_argument("--repository-directory", type=Path, required=True)
    parser.add_argument("--model", default="qwen3:4b-instruct")
    parser.add_argument("--calendar-action-helper", type=Path)
    parser.add_argument("--runtime-provider", choices=("local", "openai"))
    parser.add_argument("--openai-runtime-config", type=Path)
    arguments = parser.parse_args(argv)
    # stdout belongs solely to the protocol. The desktop owns metadata-only
    # diagnostics; inherited logging configuration must not print exception data.
    logging.disable(logging.CRITICAL)
    owned_api: DesktopOpenAIRuntime | None = None
    backend: DesktopBackend | None = None
    try:
        mode = arguments.runtime_provider or (
            "openai" if arguments.openai_runtime_config else "local"
        )
        if mode == "openai":
            if arguments.openai_runtime_config is None:
                raise ValueError("openai_configuration_required")
            owned_api = DesktopOpenAIRuntime(
                arguments.data_directory, arguments.openai_runtime_config
            ).open()
            backend = DesktopBackend(
                arguments.data_directory,
                arguments.repository_directory,
                providers=owned_api.providers,
                runtime_settings=owned_api.settings,
                openai_runtime=owned_api,
            )
        else:
            if arguments.openai_runtime_config is not None:
                raise ValueError("runtime_mode_conflict")
            backend = DesktopBackend(
                arguments.data_directory, arguments.repository_directory, arguments.model
            )
        return asyncio.run(
            run_worker(
                backend,
                sys.stdin,
                sys.stdout,
                calendar_action_helper=arguments.calendar_action_helper,
            )
        )
    except Exception:
        sys.stderr.write("desktop_worker_startup_failed\n")
        return 1
    finally:
        if owned_api is not None and (backend is None or backend._closed):
            owned_api.close()


if __name__ == "__main__":
    raise SystemExit(main())
