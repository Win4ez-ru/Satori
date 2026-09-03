"""Human-readable long-lived CLI chat runtime over application use cases."""

import asyncio
import hashlib
import sys
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from itertools import cycle
from typing import TextIO

from satori.application.conversation.contracts import SatoriReply, TalkInput
from satori.application.conversation.errors import ConversationError, UnsupportedPastClaim
from satori.application.conversation.post_processing import PostResponseReport
from satori.composition import ConversationServices
from satori.core.conversation import (
    ConversationProviderError,
    ConversationProviderFailureReason,
)
from satori.core.ids import IdGenerator
from satori.core.provider_metrics import ProviderExecutionMetrics
from satori.domain.conversation_history import SessionStatus
from satori.observability.logging import bind_trace_id

CHAT_HELP_TEXT = """Команды:
  /help — показать эту справку
  /status — показать сессию, фоновые задачи и провайдер ответа
  /new — начать новый разговор
  /exit, /quit — сохранить разговор и выйти"""

_TYPING_FRAMES = (".", "..", "...")
_RECOVERY_COPY_VERSION = "satori.interactive.recovery.v1"
_TRANSIENT_PROVIDER_FAILURES = frozenset(
    {
        ConversationProviderFailureReason.TRANSPORT_UNAVAILABLE,
        ConversationProviderFailureReason.TEMPORARILY_UNAVAILABLE,
    }
)
_RESPONSE_RECOVERY_FAILURES = frozenset(
    {
        ConversationProviderFailureReason.OUTPUT_TOKEN_LIMIT,
        ConversationProviderFailureReason.INCOMPLETE_UNKNOWN,
        ConversationProviderFailureReason.GENERATION_FAILED,
        ConversationProviderFailureReason.GENERATION_CANCELLED,
        ConversationProviderFailureReason.RESPONSE_TOO_LARGE,
        ConversationProviderFailureReason.RESPONSE_MALFORMED,
        ConversationProviderFailureReason.MISSING_ASSISTANT_TEXT,
        ConversationProviderFailureReason.USAGE_METADATA_INVALID,
        ConversationProviderFailureReason.VISIBLE_OUTPUT_LIMIT_EXCEEDED,
        ConversationProviderFailureReason.RESPONSE_CHARACTER_LIMIT_EXCEEDED,
        ConversationProviderFailureReason.ADAPTER_CONTRACT_VIOLATION,
    }
)
_EXPLICIT_PROVIDER_FAILURE_MESSAGES = {
    ConversationProviderFailureReason.CREDENTIALS_REJECTED: (
        "Провайдер ответа отклонил доступ. Проверьте API-ключ и права."
    ),
    ConversationProviderFailureReason.RESOURCE_NOT_FOUND: (
        "Провайдер не нашёл настроенную модель."
    ),
    ConversationProviderFailureReason.RATE_OR_QUOTA_LIMITED: (
        "Провайдер ответа отклонил запрос из-за лимита или баланса."
    ),
    ConversationProviderFailureReason.REQUEST_REJECTED: (
        "Провайдер ответа отклонил запрос. Проверьте настройки."
    ),
    ConversationProviderFailureReason.RESPONSE_REFUSED: ("Сатори не ответила на этот запрос."),
}
_TRANSIENT_RECOVERY_MESSAGES = (
    "Мм, связь опять решила покапризничать. Попробуй ещё раз чуть позже.",
    "Похоже, нас перебила связь. Повтори через минуту — я никуда не делась.",
    "На этот раз виновата связь, а не я. Попробуй ещё раз чуть позже — и не спорь.",  # noqa: RUF001
)
_RESPONSE_RECOVERY_MESSAGES = (
    "Хм… сейчас ответ у меня не сложился. Повтори — попробую зайти с другой стороны.",  # noqa: RUF001
    (
        "Я даже не знаю, что тебе сейчас сказать: мысль рассыпалась по дороге. "
        "Скажи ещё раз — попробую собрать её иначе."
    ),
    (
        "Не хочу отдавать тебе полусобранный ответ. Повтори — я попробую ещё раз. "  # noqa: RUF001
        "И нет, это не повод так довольно смотреть."
    ),
)


def _select_recovery_message(
    messages: Sequence[str],
    *,
    selection_key: str,
    failure_bucket: str,
) -> str:
    """Choose stable presentation copy without user text or process-global randomness."""

    if not messages:
        raise ValueError("recovery message collection must not be empty")
    material = f"{_RECOVERY_COPY_VERSION}\0{failure_bucket}\0{selection_key}".encode()
    digest = hashlib.sha256(material).digest()
    index = int.from_bytes(digest[:8], byteorder="big") % len(messages)
    return messages[index]


@dataclass(slots=True)
class _ProgressIndicator:
    stream: TextIO
    text: str = "Сатори печатает"
    interval_seconds: float = 0.4
    sleep: Callable[[float], Awaitable[None]] = field(
        default=asyncio.sleep,
        repr=False,
    )
    _animation_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _active: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.interval_seconds <= 0:
            raise ValueError("typing indicator interval must be positive")

    @property
    def _render_width(self) -> int:
        return len(self.text) + len(_TYPING_FRAMES[-1])

    @asynccontextmanager
    async def visible(self) -> AsyncIterator[None]:
        self.show()
        try:
            yield
        finally:
            await self.clear()

    def show(self) -> None:
        if self._active:
            raise RuntimeError("typing indicator is already active")
        if not self.stream.isatty():
            self.stream.write(self.text + _TYPING_FRAMES[-1] + "\n")
            self.stream.flush()
            self._active = True
            return
        self._render(_TYPING_FRAMES[0])
        self._animation_task = asyncio.create_task(self._animate())
        self._active = True

    async def _animate(self) -> None:
        remaining_frames = _TYPING_FRAMES[1:] + _TYPING_FRAMES[:1]
        for frame in cycle(remaining_frames):
            await self.sleep(self.interval_seconds)
            self._render(frame)

    def _render(self, frame: str) -> None:
        rendered = (self.text + frame).ljust(self._render_width)
        self.stream.write("\r" + rendered)
        self.stream.flush()

    async def clear(self) -> None:
        if not self._active:
            return
        self._active = False
        task = self._animation_task
        self._animation_task = None
        try:
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
        finally:
            if self.stream.isatty():
                self.stream.write("\r" + (" " * self._render_width) + "\r")
                self.stream.flush()


@dataclass(slots=True)
class InteractiveChat:
    """Keep one application/provider runtime and one explicit session alive."""

    services: ConversationServices
    id_generator: IdGenerator
    foreground_provider: str
    foreground_model: str
    debug: bool = False
    runtime_startup_ms: float = 0.0
    database_bootstrap_ms: float = 0.0
    input_fn: Callable[[str], str] = input
    stdout: TextIO = field(default_factory=lambda: sys.stdout)
    stderr: TextIO = field(default_factory=lambda: sys.stderr)
    _reports: list[PostResponseReport] = field(default_factory=list, init=False)
    _post_response_in_flight: int = field(default=0, init=False)

    async def run(self, *, session_id: str | None = None) -> int:
        """Run until an exact command, EOF, or cancellation requests graceful shutdown."""

        current_session_id = self._open_or_resume(session_id)
        queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()
        worker = asyncio.create_task(self._post_response_worker(queue))
        indicator = _ProgressIndicator(self.stdout)
        print("Сатори готова.", file=self.stdout)
        print(
            f"Провайдер ответа: {self.foreground_provider}/{self.foreground_model}\n",
            file=self.stdout,
            flush=True,
        )
        if self.debug:
            print(
                "[runtime] "
                f"startup={self.runtime_startup_ms:.3f}ms "
                f"database/bootstrap={self.database_bootstrap_ms:.3f}ms "
                f"session={current_session_id}",
                file=self.stderr,
            )

        try:
            while True:
                try:
                    line = await asyncio.to_thread(self.input_fn, "Ты: ")
                except EOFError:
                    break
                command = line.strip()
                if not command:
                    continue
                if command in {"/exit", "/quit"}:
                    break
                if command == "/help":
                    print(CHAT_HELP_TEXT, file=self.stdout)
                    continue
                if command == "/status":
                    pending = queue.qsize() + self._post_response_in_flight
                    failed = sum(not report.succeeded for report in self._reports)
                    print(f"Сессия: {current_session_id}", file=self.stdout)
                    print(
                        "Фоновые задачи памяти для этого запуска: "
                        f"ожидают завершения={pending}, ошибок={failed}",
                        file=self.stdout,
                    )
                    print(
                        f"Провайдер ответа: {self.foreground_provider}/{self.foreground_model}",
                        file=self.stdout,
                    )
                    continue
                if command == "/new":
                    await asyncio.to_thread(
                        self.services.close_session.execute,
                        current_session_id,
                    )
                    new_session = await asyncio.to_thread(self.services.start_session.execute)
                    current_session_id = new_session.session_id
                    print(f"Новый разговор: {current_session_id}", file=self.stdout)
                    continue

                trace_id = self.id_generator.new()
                request_id = self.id_generator.new()
                try:
                    async with indicator.visible():
                        with bind_trace_id(trace_id):
                            reply = await self.services.talk.execute(
                                TalkInput(
                                    user_text=line,
                                    trace_id=trace_id,
                                    client_request_id=request_id,
                                    session_id=current_session_id,
                                )
                            )
                except ConversationProviderError as error:
                    self._print_provider_error(error, selection_key=request_id)
                    continue
                except UnsupportedPastClaim:
                    self._print_recovery_message(
                        _RESPONSE_RECOVERY_MESSAGES,
                        selection_key=request_id,
                        failure_bucket="grounding",
                    )
                    continue
                except (ConversationError, ValueError) as error:
                    print(f"Реплика отклонена: {error}", file=self.stderr)
                    continue
                except asyncio.CancelledError:
                    break
                except Exception:
                    print("Не удалось сохранить ответ.", file=self.stderr)  # noqa: RUF001
                    if self.debug:
                        traceback.print_exc(file=self.stderr)
                    continue

                print(f"Сатори: {reply.text}\n", file=self.stdout, flush=True)
                if self.debug:
                    self._print_debug_timings(reply)
                if not reply.replayed:
                    queue.put_nowait((reply.interaction_id, trace_id))
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await asyncio.shield(queue.join())
            queue.put_nowait(None)
            await asyncio.shield(worker)
            self.services.close_session.execute(current_session_id)

        failures = sum(not report.succeeded for report in self._reports)
        if failures:
            print(
                "Разговор сохранён. Часть обработки памяти можно повторить.",
                file=self.stdout,
            )
        else:
            print("Разговор сохранён.", file=self.stdout)
        return 0

    def _open_or_resume(self, session_id: str | None) -> str:
        if session_id is None:
            return self.services.start_session.execute().session_id
        history = self.services.history.execute(session_id=session_id)
        if not history.sessions or history.sessions[0].status is SessionStatus.CLOSED:
            raise ValueError(f"сессия разговора уже закрыта: {session_id}")
        return session_id

    async def _post_response_worker(self, queue: asyncio.Queue[tuple[str, str] | None]) -> None:
        while True:
            work = await queue.get()
            counted_in_flight = False
            try:
                if work is None:
                    return
                self._post_response_in_flight += 1
                counted_in_flight = True
                interaction_id, trace_id = work
                try:
                    with bind_trace_id(trace_id):
                        report = await self.services.post_response.execute(
                            interaction_id, trace_id=trace_id
                        )
                except Exception:
                    report = PostResponseReport(
                        interaction_id=interaction_id,
                        episode_formation_ms=0.0,
                        episode_embedding_ms=0.0,
                        semantic_consolidation_ms=0.0,
                        total_ms=0.0,
                        failure_phases=("worker_failure",),
                    )
                    if self.debug:
                        traceback.print_exc(file=self.stderr)
                self._reports.append(report)
                if self.debug:
                    print(
                        "[post-response] "
                        f"episode={report.episode_formation_ms:.3f}ms "
                        f"embedding={report.episode_embedding_ms:.3f}ms "
                        f"semantic={report.semantic_consolidation_ms:.3f}ms "
                        f"relationship_appraisal={report.relationship_appraisal_ms:.3f}ms "
                        f"relationship_commit={report.relationship_commit_ms:.3f}ms "
                        f"relationship_total={report.relationship_total_ms:.3f}ms "
                        f"models={report.model_formation_ms:.3f}ms "
                        f"positions={report.position_formation_ms:.3f}ms "
                        f"total={report.total_ms:.3f}ms "
                        f"failures={','.join(report.failure_phases) or 'none'}",
                        file=self.stderr,
                    )
            finally:
                if counted_in_flight:
                    self._post_response_in_flight -= 1
                queue.task_done()

    def _print_provider_error(
        self,
        error: ConversationProviderError,
        *,
        selection_key: str,
    ) -> None:
        if error.reason in _EXPLICIT_PROVIDER_FAILURE_MESSAGES:
            message = _EXPLICIT_PROVIDER_FAILURE_MESSAGES[error.reason]
        elif error.reason in _TRANSIENT_PROVIDER_FAILURES:
            self._print_recovery_message(
                _TRANSIENT_RECOVERY_MESSAGES,
                selection_key=selection_key,
                failure_bucket="connection",
            )
            message = None
        elif error.reason in _RESPONSE_RECOVERY_FAILURES:
            self._print_recovery_message(
                _RESPONSE_RECOVERY_MESSAGES,
                selection_key=selection_key,
                failure_bucket="response",
            )
            message = None
        else:
            message = "Провайдер ответа вернул неизвестную ошибку."
        if message is not None:
            print(message, file=self.stderr)
        if self.debug:
            print(
                "[provider] "
                f"provider={error.provider} "
                f"model={error.model} "
                f"error_type={type(error).__name__} "
                f"failure_reason={error.reason.value}",
                file=self.stderr,
            )
            self._print_provider_budget(error.metrics)

    def _print_recovery_message(
        self,
        messages: Sequence[str],
        *,
        selection_key: str,
        failure_bucket: str,
    ) -> None:
        message = _select_recovery_message(
            messages,
            selection_key=selection_key,
            failure_bucket=failure_bucket,
        )
        print(f"[Сатори не ответила] {message}", file=self.stderr)

    def _print_debug_timings(self, reply: SatoriReply) -> None:
        input_tokens = reply.usage.input_tokens if reply.usage is not None else None
        output_tokens = reply.usage.output_tokens if reply.usage is not None else None
        print(
            "[provider] "
            f"provider={reply.provider} "
            f"model={reply.model} "
            f"finish={reply.finish_status} "
            f"selected_input_tokens={input_tokens if input_tokens is not None else 'unknown'} "
            f"selected_output_tokens={output_tokens if output_tokens is not None else 'unknown'} "
            f"provider_attempts={2 if reply.context_manifest.regeneration_attempted else 1} "
            f"replayed={str(reply.replayed).lower()}",
            file=self.stderr,
        )
        self._print_provider_budget(reply.provider_metrics)
        timing = reply.timings
        print(
            "[turn] "
            f"intake={timing.intake_ms:.3f}ms "
            f"recent={timing.recent_context_ms:.3f}ms "
            f"relationship_projection={timing.relationship_projection_ms:.3f}ms "
            f"retrieval_embedding={timing.retrieval_embedding_ms:.3f}ms "
            f"retrieval_search/rank={timing.retrieval_search_ranking_ms:.3f}ms "
            f"affect_materialization={timing.affect_materialization_ms:.3f}ms "
            f"appraisal_request={timing.appraisal_request_build_ms:.3f}ms "
            f"appraisal={timing.emotion_appraisal_ms:.3f}ms "
            f"cognition={timing.cognition_planning_ms:.3f}ms "
            f"context={timing.context_assembly_ms:.3f}ms "
            f"generation={timing.conversation_generation_ms:.3f}ms "
            f"response_regeneration={timing.response_regeneration_ms:.3f}ms "
            f"grounding={timing.grounding_validation_ms:.3f}ms "
            f"commit={timing.canonical_commit_ms:.3f}ms "
            f"committed_reply={timing.committed_reply_ms:.3f}ms",
            file=self.stderr,
        )
        manifest = reply.context_manifest
        print(
            "[context] "
            f"mode={manifest.disclosure_primary_mode} "
            f"facets={','.join(manifest.disclosure_facets) or 'none'} "
            f"request_kind={manifest.disclosure_request_kind} "
            f"same_user_count={manifest.consecutive_same_user_message_count} "
            f"assistant_high_similarity={manifest.recent_assistant_high_similarity} "
            f"generic_question_count={manifest.recent_generic_question_count} "
            f"style_corrections={','.join(manifest.active_style_corrections) or 'none'} "
            f"relationship_profile={manifest.relationship_expression_profile or 'none'} "
            f"affect_profile={manifest.affect_expression_profile or 'none'} "
            f"position_status={manifest.position_context_status} "
            f"position_ids={','.join(manifest.position_context_ids) or 'none'} "
            f"duplicate={manifest.duplicate_response_detected} "
            f"regenerated={manifest.response_regenerated} "
            f"regeneration_reason={manifest.regeneration_reason or 'none'}",
            file=self.stderr,
        )
        if reply.cognition_trace is not None:
            cognition = reply.cognition_trace
            needs = ",".join(
                f"{item.dimension.value}:{item.weight:.2f}" for item in cognition.need_mix.needs
            )
            signals = ",".join(signal.value for signal in cognition.perception.signals) or "none"
            print(
                "[cognition] "
                f"schema={cognition.schema_version} "
                f"status={cognition.status.value} "
                f"topics={','.join(topic.value for topic in cognition.perception.topics)} "
                f"signals={signals} "
                f"needs={needs} "
                f"uncertainty={cognition.need_mix.uncertainty:.2f} "
                f"retrieval={cognition.retrieval_plan.query_mode.value} "
                f"appraisal={cognition.appraisal.status.value} "
                f"position={cognition.internal_position.stance.value} "
                f"intent={cognition.intent.primary_tag} "
                f"strategy={cognition.response_strategy.tone.value}/"
                f"{cognition.response_strategy.verbosity.value} "
                f"fallbacks={','.join(cognition.fallback_reasons) or 'none'} "
                f"total={cognition.timings.total_ms:.3f}ms",
                file=self.stderr,
            )
        if reply.provider_metrics is not None:
            metrics = reply.provider_metrics.as_log_fields()
            print(
                "[provider generation] "
                + " ".join(f"{key}={value}" for key, value in metrics.items()),
                file=self.stderr,
            )
        if reply.appraisal_provider_metrics is not None:
            metrics = reply.appraisal_provider_metrics.as_log_fields()
            print(
                "[ollama appraisal] "
                + " ".join(f"{key}={value}" for key, value in metrics.items()),
                file=self.stderr,
            )
        if reply.retrieval_provider_metrics is not None:
            metrics = reply.retrieval_provider_metrics.as_log_fields()
            print(
                "[ollama retrieval embedding] "
                + " ".join(f"{key}={value}" for key, value in metrics.items()),
                file=self.stderr,
            )

    def _print_provider_budget(self, metrics: ProviderExecutionMetrics | None) -> None:
        if metrics is None:
            return
        fields = (
            ("requested_visible_output_tokens", metrics.requested_output_token_limit),
            ("wire_max_output_tokens", metrics.provider_output_token_limit),
            ("reasoning_tokens", metrics.reasoning_output_tokens),
            ("visible_output_tokens", metrics.visible_output_tokens),
        )
        if not any(value is not None for _, value in fields):
            return
        print(
            "[provider-budget] "
            + " ".join(
                f"{key}={value if value is not None else 'unknown'}" for key, value in fields
            ),
            file=self.stderr,
        )
