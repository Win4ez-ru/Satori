"""One foreground, RAM-only source question in the selected conversation."""

# ruff: noqa: RUF001 - intentional Russian user-facing text.

import json
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from PySide6.QtCore import QObject, QTimer, Signal

from satori.application.assistant.action_contracts import AssistantIntent
from satori.application.assistant.routing import AssistantReadRoute, route_intent
from satori.application.assistant.source_answer_contracts import (
    SourceCoverage,
    SourceName,
    SourceObservation,
    SourceSearchPlan,
    SourceTaskAnswer,
    SourceTaskCommand,
    SourceTaskLease,
    SourceTaskRequest,
    SourceTaskScope,
    SourceTaskSnapshot,
)
from satori.application.assistant.source_answer_intake import (
    classify_source_answer_followup,
    is_source_answer_followup_question,
    source_answer_sources,
    source_only_followup,
)
from satori.application.assistant.source_task_reads import (
    SOURCE_TASK_REFINEMENT,
    plan_mail_chat_read,
    source_task_window,
)
from satori.infrastructure.assistant.source_answer_lease import SourceTaskLeaseOwner


class SourceTaskPort(Protocol):
    """The already connected source owner; no extra connector or client."""

    @property
    def task_account_binding(self) -> tuple[object, ...] | None: ...

    def start_task_search(
        self,
        request: SourceTaskRequest,
        plan: SourceSearchPlan,
        *,
        guard: Callable[[], bool],
        finished: Callable[[SourceTaskSnapshot], None],
    ) -> bool: ...

    def cancel_task_search(self, task_id: UUID) -> None: ...


@dataclass(frozen=True)
class SourceConversationCard:
    request_id: UUID
    query: str
    text: str
    status: str
    details: tuple[str, ...] = ()
    offer_call_inbox: bool = False


def _now() -> datetime:
    return datetime.now(UTC)


class ConversationSourceController(QObject):
    """Coordinate plan → bounded reads → expression, without canonical Send."""

    changed = Signal()
    command_requested = Signal(dict)
    cancel_requested = Signal(dict)
    revocation_failed = Signal()
    route_ready = Signal(object, object, float)

    def __init__(
        self,
        mail: SourceTaskPort,
        telegram: SourceTaskPort,
        data_directory: Path,
        profile_id: UUID,
        parent: QObject | None = None,
        *,
        foreground: Callable[[], bool],
        context_current: Callable[[SourceTaskScope, str], bool] = lambda scope, provider: True,
        supported: Callable[[], bool] = lambda: True,
        clock: Callable[[], datetime] = _now,
        monotonic: Callable[[], float] = time.monotonic,
        lease_owner: SourceTaskLeaseOwner | None = None,
    ) -> None:
        super().__init__(parent)
        self._ports: dict[SourceName, SourceTaskPort] = {"mail": mail, "telegram": telegram}
        self._profile, self._profile_id = data_directory, profile_id
        self._clock, self._monotonic, self._foreground = clock, monotonic, foreground
        self._context_current = context_current
        self._supported = supported
        self._owner = lease_owner or SourceTaskLeaseOwner(
            data_directory, clock=clock, monotonic=monotonic
        )
        self._context: tuple[UUID, UUID, str] | None = None
        self._request: SourceTaskRequest | None = None
        self._lease: SourceTaskLease | None = None
        self._local_mail_deadline: float | None = None
        self._plan: SourceSearchPlan | None = None
        self._snapshot: SourceTaskSnapshot | None = None
        self._card: SourceConversationCard | None = None
        self._pending: str | None = None
        self._source_pending: set[SourceName] = set()
        self._batches: dict[SourceName, SourceTaskSnapshot] = {}
        self._bindings: dict[SourceName, tuple[object, ...] | None] = {}
        self._wall_floor: datetime | None = None
        self._tick_floor: float | None = None
        self._advancing = False
        self._answer_started = False
        self._clearing = False
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.tick)
        self._timer.start()

    @property
    def card(self) -> SourceConversationCard | None:
        return self._card

    @property
    def busy(self) -> bool:
        return self._pending is not None or bool(self._source_pending)

    @property
    def pending_clarification(self) -> bool:
        return self._card is not None and self._card.status in {"clarify", "route_clarify"}

    @property
    def pending_route_clarification(self) -> bool:
        return self._card is not None and self._card.status == "route_clarify"

    def composer_changed(self) -> None:
        if self._pending == "route":
            self.clear()

    @property
    def provider(self) -> str:
        return self._lease.provider_id if self._lease is not None else ""

    def bind_context(self, identity_id: str, session_id: str, provider: str) -> None:
        try:
            context = (UUID(identity_id), UUID(session_id), provider)
            if not context[0].int or not context[1].int or not provider:
                raise ValueError("source_context_invalid")
        except (ValueError, TypeError):
            self.clear()
            self._context = None
            return
        if context != self._context:
            self.clear()
            self._context = context

    def set_profile(self, data_directory: Path, profile_id: UUID) -> None:
        if (data_directory, profile_id) == (self._profile, self._profile_id):
            return
        self.clear()
        self._profile, self._profile_id = data_directory, profile_id
        self._context = None
        self._owner = SourceTaskLeaseOwner(
            data_directory, clock=self._clock, monotonic=self._monotonic
        )

    def unbind_context(self) -> None:
        self.clear()
        self._context = None

    def _current(self, request: SourceTaskRequest | None = None) -> bool:
        try:
            return self._current_checked(request)
        except (ValueError, TypeError, RuntimeError, OverflowError):
            return False

    def _current_checked(self, request: SourceTaskRequest | None = None) -> bool:
        active, lease, context = self._request, self._lease, self._context
        deadline = lease.monotonic_deadline if lease is not None else self._local_mail_deadline
        if (
            active is None
            or context is None
            or deadline is None
            or (request is not None and request != active)
        ):
            return False
        provider = lease.provider_id if lease is not None else context[2]
        now, tick = self._clock(), self._monotonic()
        if (
            self._clearing
            or not self._foreground()
            or (lease is not None and not self._supported())
            or not self._context_current(active.scope, provider)
            or context != (active.scope.identity_id, active.scope.session_id, provider)
            or active.scope.profile_id != self._profile_id
            or not active.created_at <= now < active.expires_at
            or not math.isfinite(tick)
            or not deadline - (active.expires_at - active.created_at).total_seconds()
            <= tick
            < deadline
            or (self._wall_floor is not None and now < self._wall_floor)
            or (self._tick_floor is not None and tick < self._tick_floor)
            or any(
                self._ports[source].task_account_binding != binding
                for source, binding in self._bindings.items()
            )
        ):
            return False
        self._wall_floor, self._tick_floor = now, tick
        return True

    def try_dispatch(self, intent: AssistantIntent) -> bool:
        return self._dispatch(intent)

    def try_route(self, text: str, *, timezone: str = "Europe/Moscow") -> bool:
        return self._dispatch(
            AssistantIntent(
                request_id=uuid4(), kind="source_answer", query=text, timezone=timezone
            ),
            sources=("mail", "telegram", "calls"),
            routing=True,
        )

    def try_dispatch_routed(
        self, request: SourceTaskRequest, route: AssistantReadRoute, deadline: float
    ) -> bool:
        intent = route_intent(request, route)
        if intent is None or intent.kind != "source_answer":
            return False
        # Route and execution are separate single-attempt operations on the same
        # original clock budget; the route cannot replay its provider request ID.
        return self._dispatch(
            intent.model_copy(update={"request_id": uuid4()}),
            sources=route.sources,
            expires_at=request.expires_at,
            monotonic_deadline=deadline,
            created_at=request.created_at,
            sender_literal=route.sender_literal,
        )

    def try_dispatch_mailbox(self, text: str, intent: AssistantIntent) -> bool:
        if intent.kind != "mailbox_search" or intent.source != "mail" or intent.query is None:
            return False
        return self._dispatch(
            AssistantIntent(
                request_id=intent.request_id,
                kind="source_answer",
                timezone=intent.timezone,
                query=text,
                source="mail",
            ),
            sources=("mail",),
            local_mail_query=intent.query,
        )

    def _dispatch(
        self,
        intent: AssistantIntent,
        *,
        expires_at: datetime | None = None,
        monotonic_deadline: float | None = None,
        sources: tuple[SourceName, ...] | None = None,
        local_mail_query: str | None = None,
        routing: bool = False,
        created_at: datetime | None = None,
        sender_literal: str | None = None,
    ) -> bool:
        if intent.kind != "source_answer" or intent.query is None:
            return False
        context = self._context
        if context is None or not self._foreground():
            return False
        self.clear()
        now, tick = self._clock(), self._monotonic()
        sources = sources if sources is not None else source_answer_sources(intent)
        calls_only = sources == ("calls",)
        calls_unavailable = not routing and calls_only and "calls" not in self._ports
        if calls_unavailable or (local_mail_query is None and not self._supported()):
            text = (
                "У меня нет доступа к журналу звонков оператора. "
                "Можно посмотреть сообщения, которые я сохранила в этом профиле."
                if calls_unavailable
                else "Для такой проверки нужен подключённый GPT. "
                "Настрой его, и я смогу разобраться с письмами и сообщениями."
            )
            if "calls" in sources and not calls_unavailable:
                text += " Звонки пока не подключены."
            self._card = SourceConversationCard(
                intent.request_id,
                intent.query,
                text,
                "unsupported" if calls_unavailable else "gpt_required",
                offer_call_inbox=calls_unavailable,
            )
            self.changed.emit()
            return True
        try:
            bindings = (
                {}
                if routing
                else {
                    source: self._ports[source].task_account_binding
                    for source in sources
                    if source in self._ports
                }
            )
            request = SourceTaskRequest(
                request_id=intent.request_id,
                scope=SourceTaskScope(
                    identity_id=context[0],
                    profile_id=self._profile_id,
                    session_id=context[1],
                    generation=uuid4(),
                    timezone=intent.timezone,
                ),
                user_text=intent.query,
                sources=sources,
                created_at=created_at or now,
                expires_at=expires_at or now + timedelta(seconds=120),
                sender_literal=sender_literal,
            )
            deadline = monotonic_deadline or tick + 120
            lease, local_plan = None, None
            if local_mail_query is not None:
                local_plan = plan_mail_chat_read(request)
                if local_plan is None or local_plan.mail_query != local_mail_query:
                    return False
            else:
                lease = SourceTaskLease(
                    lease_id=uuid4(),
                    request_id=request.request_id,
                    scope=request.scope,
                    request_digest=request.digest,
                    sources=request.sources,
                    provider_id=context[2],
                    disclosure_authorized=True,
                    issued_at=now,
                    expires_at=request.expires_at,
                    monotonic_deadline=deadline,
                    ui_pid=os.getpid(),
                    os_uid=os.getuid(),
                )
                self._owner.issue(lease)
        except (ValueError, OSError, RuntimeError, TypeError):
            try:
                self._owner.revoke()
            except (ValueError, OSError):
                self.revocation_failed.emit()
            return False
        self._request, self._lease, self._bindings = request, lease, bindings
        self._wall_floor, self._tick_floor = now, tick
        if local_plan is not None:
            self._local_mail_deadline, self._plan = deadline, local_plan
            self._start_sources(request, local_plan)
        else:
            self._card = SourceConversationCard(
                request.request_id, request.user_text, "Сейчас посмотрю…", "planning"
            )
            self._emit_command("route" if routing else "plan")
        return self._request is not None and self._request.request_id == request.request_id

    def answer_clarification(self, text: str) -> bool:
        if classify_source_answer_followup(text) != "clarification":
            return False
        if self.pending_route_clarification:
            request, lease = self._request, self._lease
            if request is None or lease is None or not self._current():
                return False
            combined = request.user_text + SOURCE_TASK_REFINEMENT + text
            if len(combined) > 8000:
                return False
            return self._dispatch(
                AssistantIntent(
                    request_id=uuid4(),
                    kind="source_answer",
                    query=combined,
                    timezone=request.scope.timezone,
                ),
                sources=request.sources,
                routing=True,
                created_at=request.created_at,
                expires_at=request.expires_at,
                monotonic_deadline=lease.monotonic_deadline,
            )
        return self._refine(text, "clarify")

    def answer_followup(self, text: str) -> bool:
        if not is_source_answer_followup_question(text):
            return False
        return self._refine(text, "answered")

    def answer_source_choice(self, text: str) -> bool:
        source = source_only_followup(text)
        if source is None:
            return False
        return self._refine(text, None, sources=(source,))

    def _refine(
        self, text: str, status: str | None, *, sources: tuple[SourceName, ...] | None = None
    ) -> bool:
        request, lease = self._request, self._lease
        if (
            self._card is None
            or (status is not None and self._card.status != status)
            or request is None
            or lease is None
            or not self._current()
        ):
            return False
        selected = request.sources if sources is None else sources
        combined = request.user_text + SOURCE_TASK_REFINEMENT + text
        if not text.strip() or len(combined) > 8000 or not set(selected) <= set(request.sources):
            return False
        return self._dispatch(
            AssistantIntent(
                request_id=uuid4(),
                kind="source_answer",
                timezone=request.scope.timezone,
                query=combined,
                source="mail"
                if selected == ("mail",)
                else "telegram"
                if selected == ("telegram",)
                else None,
            ),
            expires_at=request.expires_at,
            monotonic_deadline=lease.monotonic_deadline,
            sources=selected,
            created_at=request.created_at,
            sender_literal=request.sender_literal if sources is not None else None,
        )

    def _emit_command(self, operation: str) -> None:
        request, lease = self._request, self._lease
        if request is None or lease is None or not self._current():
            self.clear()
            return
        command = SourceTaskCommand.model_validate(
            {"operation": operation, "request": request, "snapshot": self._snapshot}
        )
        self._pending = operation
        self.changed.emit()
        self.command_requested.emit(
            {
                "session_id": str(request.scope.session_id),
                "request_id": str(request.request_id),
                "source_answer": command.model_dump(mode="json"),
                "source_answer_lease": lease.model_dump(mode="json"),
            }
        )

    def accept_error(self, request_id: str, operation: str) -> None:
        if (
            self._request is None
            or str(self._request.request_id) != request_id
            or self._pending != operation
        ):
            return
        if not self._current():
            self.clear()
            return
        self._pending = None
        self._card = SourceConversationCard(
            self._request.request_id,
            self._request.user_text,
            "Сейчас не получилось завершить проверку. Попробуем ещё раз чуть позже?",
            "error",
            self._details(),
        )
        self.changed.emit()

    def accept_result(self, request_id: str, operation: str, result: dict[str, object]) -> None:
        request = self._request
        if request is None or str(request.request_id) != request_id or self._pending != operation:
            return
        if not self._current():
            self.clear()
            return
        try:
            if result.get("request_id") != request_id:
                raise ValueError("source_result_request_invalid")
            if operation == "route" and result.get("status") == "routed":
                route = AssistantReadRoute.model_validate_json(json.dumps(result.get("route")))
                intent = route_intent(request, route)
                if intent is not None and intent.kind == "clarification":
                    self._pending = None
                    self._card = SourceConversationCard(
                        request.request_id,
                        request.user_text,
                        intent.clarification or "",
                        "route_clarify",
                    )
                    self.changed.emit()
                    return
                assert self._lease is not None
                deadline = self._lease.monotonic_deadline
                self.clear()
                self.route_ready.emit(request, route, deadline)
                return
            if operation == "plan" and result.get("status") == "planned":
                plan = SourceSearchPlan.model_validate_json(json.dumps(result.get("plan")))
                authorized_start, authorized_end = source_task_window(request)
                if (
                    plan.mail_headers_only
                    or (plan.mail_query and "mail" not in request.sources)
                    or (plan.telegram_query and "telegram" not in request.sources)
                    or plan.window_start < authorized_start
                    or plan.window_end > authorized_end
                ):
                    raise ValueError("source_plan_scope_invalid")
                self._plan, self._pending = plan, None
                if plan.status == "clarify":
                    self._card = SourceConversationCard(
                        request.request_id, request.user_text, plan.clarification or "", "clarify"
                    )
                    self.changed.emit()
                    return
                self._start_sources(request, plan)
                return
            if operation == "answer" and result.get("status") == "answered":
                answer = SourceTaskAnswer.model_validate_json(json.dumps(result.get("answer")))
                snapshot = self._snapshot
                if snapshot is None or not set(answer.citations) <= {
                    item.source_ref for item in snapshot.items
                }:
                    raise ValueError("source_answer_refs_invalid")
                if answer.verdict == "no_match" and (
                    snapshot.items or any(c.status != "searched" for c in snapshot.coverage)
                ):
                    raise ValueError("source_answer_absence_invalid")
                if answer.verdict == "found" and not answer.citations:
                    raise ValueError("source_answer_evidence_required")
                self._pending = None
                needs_contact = any(
                    coverage.source == "telegram" and coverage.detail == "peer_ambiguous"
                    for coverage in snapshot.coverage
                )
                self._card = SourceConversationCard(
                    request.request_id,
                    request.user_text,
                    answer.text,
                    "clarify" if needs_contact else "answered",
                    self._details(),
                )
                self.changed.emit()
                return
            raise ValueError("source_result_invalid")
        except (ValueError, TypeError):
            self.accept_error(request_id, operation)

    def _start_sources(self, request: SourceTaskRequest, plan: SourceSearchPlan) -> None:
        self._source_pending = set(request.sources)
        self._card = SourceConversationCard(
            request.request_id, request.user_text, "Проверяю, что удалось найти…", "searching"
        )
        self.changed.emit()
        for source in request.sources:
            if not self._current(request):
                self.clear()
                return
            port = self._ports.get(source)
            query = (
                plan.mail_query
                if source == "mail"
                else plan.telegram_query
                if source == "telegram"
                else None
            )
            started = bool(
                port is not None
                and self._bindings.get(source) is not None
                and query is not None
                and port.start_task_search(
                    request,
                    plan,
                    guard=lambda: self._current(request),
                    finished=self._receiver(source, request),
                )
            )
            if not started and source in self._source_pending:
                self._unavailable(source, request, plan)
        self._advance()

    def _receiver(
        self, source: SourceName, request: SourceTaskRequest
    ) -> Callable[[SourceTaskSnapshot], None]:
        def receive(batch: SourceTaskSnapshot) -> None:
            self._source_result(source, request, batch)

        return receive

    def _unavailable(
        self, source: SourceName, request: SourceTaskRequest, plan: SourceSearchPlan
    ) -> None:
        self._source_result(
            source,
            request,
            SourceTaskSnapshot(
                request_id=request.request_id,
                scope=request.scope,
                plan_digest=plan.digest,
                observed_at=self._clock(),
                coverage=(
                    SourceCoverage(
                        source=source,
                        status="unavailable",
                        query=plan.mail_query
                        if source == "mail"
                        else plan.telegram_query
                        if source == "telegram"
                        else None,
                        window_start=plan.window_start,
                        window_end=plan.window_end,
                        detail="not_connected_or_busy",
                    ),
                ),
            ),
        )

    def _source_result(
        self, source: SourceName, request: SourceTaskRequest, result: SourceTaskSnapshot
    ) -> None:
        if source not in self._source_pending or not self._current(request):
            return
        plan = self._plan
        try:
            batch = SourceTaskSnapshot.model_validate(result)
            if (
                plan is None
                or batch.request_id != request.request_id
                or batch.scope != request.scope
                or batch.plan_digest != plan.digest
                or len(batch.coverage) != 1
                or batch.coverage[0].source != source
                or batch.coverage[0].window_start != plan.window_start
                or batch.coverage[0].window_end != plan.window_end
                or batch.coverage[0].query
                != (
                    plan.mail_query
                    if source == "mail"
                    else plan.telegram_query
                    if source == "telegram"
                    else None
                )
                or any(item.source != source for item in batch.items)
                or (
                    plan.mail_headers_only
                    and any(
                        item.text
                        or item.completeness != "metadata_only"
                        or item.event_at is None
                        or not plan.window_start <= item.event_at < plan.window_end
                        for item in batch.items
                    )
                )
                or any(
                    not request.created_at
                    <= item.observed_at
                    <= batch.observed_at + timedelta(seconds=5)
                    for item in batch.items
                )
                or not request.created_at
                <= batch.observed_at
                <= self._clock() + timedelta(seconds=5)
            ):
                raise ValueError("source_batch_invalid")
        except (ValueError, TypeError):
            if plan is not None:
                self._unavailable(source, request, plan)
            return
        self._batches[source] = batch
        self._source_pending.remove(source)
        self._advance()

    def _advance(self) -> None:
        if (
            self._advancing
            or self._answer_started
            or self._source_pending
            or self._pending is not None
        ):
            return
        request, plan = self._request, self._plan
        if request is None or plan is None or plan.status != "search" or not self._current():
            return
        if set(self._batches) != set(request.sources):
            return
        self._advancing = True
        try:
            items: list[SourceObservation] = []
            coverage: list[SourceCoverage] = []
            size = 0
            for source in request.sources:
                batch = self._batches[source]
                truncated = False
                for item in batch.items:
                    text_size = len(item.text.encode("utf-8"))
                    if len(items) >= 20 or size + text_size > 32768:
                        truncated = True
                        continue
                    items.append(item)
                    size += text_size
                entry = batch.coverage[0]
                coverage.append(
                    entry.model_copy(update={"status": "partial", "more": True})
                    if truncated
                    else entry
                )
            while True:
                try:
                    self._snapshot = SourceTaskSnapshot(
                        request_id=request.request_id,
                        scope=request.scope,
                        plan_digest=plan.digest,
                        observed_at=self._clock(),
                        items=tuple(items),
                        coverage=tuple(coverage),
                    )
                    break
                except ValueError:
                    if not items:
                        raise
                    omitted_source = items.pop().source
                    coverage = [
                        entry.model_copy(update={"status": "partial", "more": True})
                        if entry.source == omitted_source
                        else entry
                        for entry in coverage
                    ]
            if plan.mail_headers_only:
                self._finish_local_mail(request, self._snapshot)
                return
            self._card = SourceConversationCard(
                request.request_id,
                request.user_text,
                "Собираю ответ по тому, что нашла…",
                "answering",
                self._details(),
            )
            self._answer_started = True
            self._emit_command("answer")
        except (ValueError, TypeError):
            self._pending = "answer"
            self.accept_error(str(request.request_id), "answer")
        finally:
            self._advancing = False

    def _finish_local_mail(self, request: SourceTaskRequest, snapshot: SourceTaskSnapshot) -> None:
        coverage = snapshot.coverage[0]
        zone = ZoneInfo(request.scope.timezone)
        if coverage.status == "unavailable":
            text = (
                "Gmail сейчас не подключён. Подключи его в «Настройки и подключения → Gmail»."
                if self._bindings.get("mail") is None
                else "Gmail подключён, но сейчас занят или недоступен. Повтори проверку чуть позже."
            )
        elif coverage.status == "error":
            text = "Запрос к Gmail не завершился. Пока не могу сказать, какие письма пришли."
        elif not snapshot.items:
            text = (
                "За проверенный период в Gmail подходящих писем нет."
                if coverage.status == "searched"
                else "В проверенной части Gmail подходящих писем нет. Проверка была неполной."
            )
        else:
            rows = ["Вот что нашла в Gmail:"]
            for item in snapshot.items[:5]:
                sender = " ".join(item.sender.split())[:100] or "Отправитель не указан"
                title = " ".join(item.title.split())[:160] or "Без темы"
                received = (
                    item.event_at.astimezone(zone).strftime("%d.%m %H:%M")
                    if item.event_at is not None
                    else ""
                )
                rows.append(f"• {received + ' · ' if received else ''}{sender} — {title}")
            if len(snapshot.items) > 5:
                rows.append("Остальные найденные письма — в «Что проверила».")
            if coverage.status == "partial":
                rows.append("Проверка была неполной.")
            text = "\n".join(rows)
        self._answer_started = True
        self._card = SourceConversationCard(
            request.request_id, request.user_text, text, "answered", self._details()
        )
        self.changed.emit()

    def _details(self) -> tuple[str, ...]:
        request, snapshot, plan = self._request, self._snapshot, self._plan
        if request is None or snapshot is None or plan is None:
            return ()
        zone = ZoneInfo(request.scope.timezone)
        names = {"mail": "Gmail", "telegram": "Telegram", "calls": "Звонки"}
        statuses = {
            "searched": "проверено",
            "partial": "проверено частично",
            "unavailable": "сейчас недоступно",
            "error": "проверка не завершилась",
        }
        lines = []
        for coverage in snapshot.coverage:
            count = sum(item.source == coverage.source for item in snapshot.items)
            start = coverage.window_start.astimezone(zone).date()
            end = (coverage.window_end - timedelta(microseconds=1)).astimezone(zone).date()
            period = f"{start:%d.%m.%Y}" if start == end else f"{start:%d.%m.%Y} — {end:%d.%m.%Y}"
            scope = (
                " Все папки, включая спам и корзину; до 20 совпадений, текст до 3 писем."
                if coverage.source == "mail"
                else " Личные чаты; ограниченная страница совпадений и ближайший контекст."
                if coverage.source == "telegram"
                else ""
            )
            if coverage.source == "telegram" and plan.telegram_match == "peer":
                scope = {
                    "peer_incoming": " Входящие из одного личного чата.",
                    "peer_history_incomplete": " Проверена часть входящих из личного чата.",
                    "peer_ambiguous": " Нашлось несколько собеседников. Уточни фамилию или @имя.",
                    "peer_not_found": " Среди проверенных совпадений подходящего чата не было.",
                    "peer_lookup_incomplete": " Список совпадений по имени получен частично.",
                    "peer_unresolved": " Не удалось подтвердить собеседника по этому имени.",
                }.get(coverage.detail, "")
            if coverage.source == "mail" and plan.mail_headers_only:
                scope = (
                    " Входящие; до 20 заголовков."
                    if plan.mail_query in {"in:inbox", "in:inbox is:unread"}
                    else " Все папки, включая спам и корзину; до 20 заголовков."
                )
            lines.append(
                f"{names[coverage.source]}: {statuses[coverage.status]}. "
                f"Период: {period}."
                + (
                    scope + f" В ответе источников: {count}."
                    if coverage.status in {"searched", "partial"}
                    else ""
                )
                + (
                    "\nЗапрос: " + coverage.query
                    if coverage.query and not plan.mail_headers_only
                    else ""
                )
                + (" Есть ещё результаты; запрос можно уточнить." if coverage.more else "")
            )
        for item in snapshot.items:
            lines.append(
                f"[{item.source_ref}] {names[item.source]} · {item.title}\n"
                f"{item.sender}\n"
                + (f"Дата: {item.event_at.isoformat()}\n" if item.event_at else "")
                + f"Полнота: {item.completeness}. Проверено: {item.observed_at.isoformat()}"
            )
        return tuple(lines)

    def tick(self) -> None:
        if self._request is not None and not self._current():
            self.clear()

    def clear(self) -> None:
        if self._clearing:
            return
        self._clearing = True
        request, lease = self._request, self._lease
        self._request, self._lease, self._plan, self._snapshot = None, None, None, None
        self._local_mail_deadline = None
        self._card, self._pending = None, None
        self._source_pending.clear()
        self._batches.clear()
        self._bindings.clear()
        self._answer_started = False
        self._wall_floor, self._tick_floor = None, None
        try:
            if request is not None:
                for port in self._ports.values():
                    try:
                        port.cancel_task_search(request.request_id)
                    except (ValueError, RuntimeError):
                        # A retired callback still fails the exact request guard.
                        # Revoke the Core mirror even if a source owner is stopping.
                        continue
            try:
                self._owner.revoke()
            except (ValueError, OSError):
                self.revocation_failed.emit()
            if request is not None and lease is not None:
                command = SourceTaskCommand(operation="cancel", request=request)
                self.cancel_requested.emit(
                    {
                        "session_id": str(request.scope.session_id),
                        "request_id": str(request.request_id),
                        "source_answer": command.model_dump(mode="json"),
                    }
                )
        finally:
            self._clearing = False
            self.changed.emit()
