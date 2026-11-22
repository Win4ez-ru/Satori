"""Local desktop adapter over existing application owners; no UI or domain policy."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from satori.application.assistant.calendar_service import CalendarAssistantService
    from satori.application.assistant.source_answer_contracts import SourceTaskRequest
    from satori.application.assistant.source_answer_service import SourceAnswerService
    from satori.application.retrieval.adoption import MemoryIndexAdoptionRequest
    from satori.desktop.openai_runtime import DesktopOpenAIRuntime
    from satori.server.calendar_backend import CalendarRuntimeBindings

from satori.application.affect.use_cases import AffectiveAppraisalProvider
from satori.application.conversation.contracts import TalkInput
from satori.application.conversation.errors import (
    ConversationSessionNotFound,
    InteractionIdempotencyConflict,
)
from satori.application.conversation.use_cases import ConversationProvider
from satori.application.memory.use_cases import EpisodeFormationProvider
from satori.application.models.use_cases import ModelFormationProvider
from satori.application.positions.use_cases import PositionFormationProvider
from satori.application.reflection.ports import ReflectionGenerationPort
from satori.application.relationship.use_cases import RelationshipProvider
from satori.application.retrieval.use_cases import EmbeddingProvider
from satori.application.semantic.use_cases import SemanticFormationProvider
from satori.composition import (
    ConversationServices,
    InitialSelfServices,
    build_conversation_services,
    build_initial_self_services,
)
from satori.config import ConversationProviderKind, Settings
from satori.desktop.voice.expression import project_voice_delivery
from satori.domain.conversation_history import (
    ConversationHistorySnapshot,
    InteractionStatus,
    SessionStatus,
)
from satori.domain.errors import NotActivated
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.migrations import upgrade_database
from satori.infrastructure.providers.inference_scheduler import OllamaInferenceScheduler
from satori.infrastructure.providers.ollama import OllamaConversationAdapter
from satori.infrastructure.providers.ollama_affect import OllamaAffectiveAppraisalAdapter
from satori.infrastructure.providers.ollama_embedding import OllamaEmbeddingAdapter
from satori.infrastructure.providers.ollama_episode import OllamaEpisodeFormationAdapter
from satori.infrastructure.providers.ollama_http import OllamaHttpClient
from satori.infrastructure.providers.ollama_models import OllamaModelFormationAdapter
from satori.infrastructure.providers.ollama_positions import OllamaPositionFormationAdapter
from satori.infrastructure.providers.ollama_reflection import OllamaReflectionAdapter
from satori.infrastructure.providers.ollama_relationship import OllamaRelationshipAppraisalAdapter
from satori.infrastructure.providers.ollama_semantic import OllamaSemanticFormationAdapter
from satori.infrastructure.seeds.loader import JsonSeedLoader
from satori.infrastructure.source_policy.installation import (
    bind_installation,
    enroll_installation,
    installation_identity,
)
from satori.observability.logging import bind_trace_id

MAX_HISTORY_INTERACTIONS = 200
MAX_HISTORY_SESSIONS = 100
MAX_MEMORIES = 200
MAX_READ_CHARS = 400_000


class _LocalPortSettings(TypedDict):
    base_url: str
    keep_alive: str
    http_client: OllamaHttpClient
    scheduler: OllamaInferenceScheduler


class DesktopBackendError(ValueError):
    """A stable adapter failure code, safe to put on the private local wire."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class DesktopProviders:
    """Replaceable existing capability ports, also used by offline adapter tests."""

    conversation: ConversationProvider
    episode: EpisodeFormationProvider
    embedding: EmbeddingProvider | None = None
    semantic: SemanticFormationProvider | None = None
    appraisal: AffectiveAppraisalProvider | None = None
    relationship: RelationshipProvider | None = None
    models: ModelFormationProvider | None = None
    positions: PositionFormationProvider | None = None
    reflection: ReflectionGenerationPort | None = None


class DesktopBackend:
    """One process owns one canonical runtime and drains derived work on shutdown.

    Closing this adapter leaves explicit conversation sessions open, so restarting
    the desktop can resume them. Only activation creates a new identity. Existing
    installations must already have their valid policy ledger; startup never enrolls
    or repairs them. Provider construction performs no network/model operation.
    """

    def __init__(
        self,
        data_directory: Path,
        repository_directory: Path,
        model: str = "qwen3:4b-instruct",
        *,
        providers: DesktopProviders | None = None,
        runtime_settings: Settings | None = None,
        openai_runtime: "DesktopOpenAIRuntime | None" = None,
    ) -> None:
        self.data_directory = data_directory.expanduser().resolve()
        self.repository_directory = repository_directory.expanduser().resolve()
        # Pass every default explicitly: neither a repository .env nor inherited
        # SATORI_* settings can enable cloud disclosure or Character candidates.
        if runtime_settings is not None:
            if not isinstance(runtime_settings, Settings):
                raise DesktopBackendError("runtime_settings_invalid")
            explicit_values = {
                name: getattr(runtime_settings, name) for name in Settings.model_fields
            }
            try:
                explicit_settings = Settings(_env_file=None, **explicit_values)  # type: ignore[call-arg]
            except ValueError:
                raise DesktopBackendError("runtime_settings_invalid") from None
            if (
                explicit_settings.database_url
                != f"sqlite+pysqlite:///{self.data_directory / 'satori.db'}"
                or explicit_settings.source_policy_directory
                != self.data_directory / "source-policy"
                or explicit_settings.chat_log_path
                != str(self.data_directory / "satori-runtime.jsonl")
            ):
                raise DesktopBackendError("runtime_settings_path_mismatch")
            if (
                explicit_settings.conversation_provider is not ConversationProviderKind.OLLAMA
                and not explicit_settings.all_openai_enabled
            ):
                raise DesktopBackendError("runtime_settings_mode_unsupported")
            if explicit_settings.all_openai_enabled and (
                providers is None
                or any(
                    getattr(providers, name) is None
                    for name in DesktopProviders.__dataclass_fields__
                )
            ):
                raise DesktopBackendError("all_openai_provider_bundle_required")
            self.settings = explicit_settings
        else:
            self.settings = self._default_settings(model)
        self._providers = providers
        self._openai_runtime = openai_runtime
        if openai_runtime is not None and not openai_runtime.owns(
            self.data_directory, self.settings, providers
        ):
            raise DesktopBackendError("openai_runtime_binding_invalid")
        self._index_adoption_attempts: set[UUID] = set()
        self._http_client: OllamaHttpClient | None = None
        self._database: Database | None = None
        self._initial: InitialSelfServices | None = None
        self._services: ConversationServices | None = None
        self._session_id: str | None = None
        self._post_tasks: set[asyncio.Task[None]] = set()
        self._turn_tasks: set[asyncio.Task[dict[str, object]]] = set()
        self._close_task: asyncio.Task[dict[str, object]] | None = None
        self._turn_lock = asyncio.Lock()
        self._post_lock = asyncio.Lock()
        self._post_failures: set[str] = set()
        self._closing = False
        self._closed = False

    def _default_settings(self, model: str) -> Settings:
        values = {
            name: field.get_default(call_default_factory=True)
            for name, field in Settings.model_fields.items()
        }
        values.update(
            database_url=f"sqlite+pysqlite:///{self.data_directory / 'satori.db'}",
            source_policy_directory=self.data_directory / "source-policy",
            chat_log_path=str(self.data_directory / "satori-runtime.jsonl"),
            conversation_model=model,
        )
        # BaseSettings accepts this runtime-only keyword; the generated Settings
        # constructor signature used by mypy lists model fields alone.
        return Settings(_env_file=None, **values)  # type: ignore[call-arg]

    def open(self) -> dict[str, object]:
        """Migrate explicit storage and bind existing policy, without activation."""
        if self._closed or self._closing:
            raise DesktopBackendError("runtime_closed")
        if self._database is None:
            config = self.repository_directory / "alembic.ini"
            upgrade_database(self.settings.database_url, config_path=config)
            self._database = create_database(self.settings.database_url)
            self._initial = build_initial_self_services(self._database)
            try:
                try:
                    installation_identity(self._database)
                except NotActivated:
                    pass
                else:
                    bind_installation(self._database, self.settings.source_policy_directory)
            except Exception:
                self._database.dispose()
                self._database = self._initial = None
                raise
        return self.status()

    def status(self) -> dict[str, object]:
        """Return installation metadata without initializing affect or a provider."""
        initial = self._require_initial()
        identity_id: str | None = None
        name: str | None = None
        try:
            identity = initial.get_identity.execute()
        except NotActivated:
            pass
        else:
            identity_id, name = identity.identity_id, identity.name
        return {
            "activated": identity_id is not None,
            "identity_id": identity_id,
            "name": name,
            "data_directory": str(self.data_directory),
            "provider": self.settings.conversation_provider.value,
            "model": self.settings.conversation_model,
            "supports_source_answers": self.supports_source_answers,
            "openai_runtime": self._openai_runtime.status()
            if self._openai_runtime is not None
            else {"owned_openai_runtime": False, "supports_source_answers": False},
            "session_id": self._session_id,
            "post_response_pending": len(self._post_tasks),
            "post_response_failures": sorted(self._post_failures),
        }

    def build_calendar_assistant(
        self, bindings: "CalendarRuntimeBindings"
    ) -> "CalendarAssistantService":
        """A default-off operation uses this exact Core and its scoped readers."""

        return bindings.build(
            self._conversation(),
            provider_id=self.settings.conversation_provider.value
            + ":"
            + self.settings.conversation_model,
        )

    @property
    def supports_source_answers(self) -> bool:
        owner = self._openai_runtime
        return (
            owner is not None
            and owner.owns(self.data_directory, self.settings, self._providers)
            and owner.status().get("supports_source_answers") is True
        )

    async def adopt_openai_memory_index(
        self, request: "MemoryIndexAdoptionRequest"
    ) -> dict[str, object]:
        from satori.application.retrieval.adoption import adopt_openai_memory_index

        if not self.supports_source_answers or self._closing or self._closed:
            raise DesktopBackendError("openai_runtime_unavailable")
        if self._session_id != str(request.session_id):
            raise DesktopBackendError("memory_index_scope_mismatch")

        def require_live() -> None:
            if (
                not self.supports_source_answers
                or self._closing
                or self._closed
                or self._session_id != str(request.session_id)
            ):
                raise DesktopBackendError("openai_runtime_unavailable")

        async def execute() -> dict[str, object]:
            async with self._turn_lock:
                report = await adopt_openai_memory_index(
                    self._conversation(),
                    request,
                    attempted_operations=self._index_adoption_attempts,
                    require_live=require_live,
                )
                return report.model_dump(mode="json")

        task = asyncio.create_task(execute())
        self._turn_tasks.add(task)
        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                self._turn_tasks.discard(task)
            else:
                task.add_done_callback(self._turn_tasks.discard)

    def build_source_answer(
        self,
        *,
        request: "SourceTaskRequest",
        require_live: Callable[[], None],
        clock: Callable[[], datetime],
    ) -> "SourceAnswerService":
        """Use this exact Core and budget owner without canonical Send."""
        from satori.application.assistant.source_answer_reasoning import CoreSourceAnswerReasoner
        from satori.application.assistant.source_answer_service import SourceAnswerService

        if not self.supports_source_answers:
            raise DesktopBackendError("source_answer_api_runtime_required")
        core = self._conversation()
        return SourceAnswerService(
            request=request,
            reasoner=CoreSourceAnswerReasoner(
                core.talk,
                core.emotion_status,
                self.settings.conversation_provider.value + ":" + self.settings.conversation_model,
                clock,
            ),
            require_live=require_live,
            clock=clock,
        )

    def activate(self) -> dict[str, object]:
        """Explicit onboarding command; never enroll an existing identity."""
        initial = self._require_initial()
        try:
            initial.get_identity.execute()
        except NotActivated:
            try:
                initial.activate.execute(JsonSeedLoader().load_canonical(), trace_id=str(uuid4()))
                assert self._database is not None
                enroll_installation(
                    self._database,
                    self.settings.source_policy_directory,
                    reviewed=True,
                    new_installation=True,
                )
            except Exception:
                # Identity creation and external policy enrollment are separate
                # commits. Preserve any created identity, but invalidate readiness;
                # reopening must validate existing policy, never invent a new floor.
                assert self._database is not None
                self._database.dispose()
                self._database = self._initial = None
                raise
        else:
            assert self._database is not None
            bind_installation(self._database, self.settings.source_policy_directory)
        return self.status()

    def history(self, session_id: str | None = None) -> dict[str, object]:
        """Project immutable owner history into a small local UI response."""
        reader = self._conversation().history
        all_history = reader.execute()
        history = reader.execute(session_id=session_id) if session_id is not None else all_history
        # Deliver newest whole interactions within a conservative UTF-8 budget.
        # The owner remains authoritative; this is explicit UI truncation only.
        selected = []
        chars = 0
        for item in reversed(history.interactions[-MAX_HISTORY_INTERACTIONS:]):
            item_chars = len(item.user_message.content) + (
                len(item.assistant_message.content) if item.assistant_message else 0
            )
            if chars + item_chars > MAX_READ_CHARS:
                break
            chars += item_chars
            selected.append(item)
        sessions = all_history.sessions[-MAX_HISTORY_SESSIONS:]
        return {
            "sessions": [
                {
                    "session_id": session.session_id,
                    "status": session.status.value,
                    "started_at": session.started_at.isoformat(),
                    "ended_at": session.ended_at.isoformat() if session.ended_at else None,
                }
                for session in sessions
            ],
            "interactions": [
                {
                    "interaction_id": item.interaction_id,
                    "client_request_id": item.client_request_id,
                    "session_id": item.session_id,
                    "status": item.status.value,
                    "user_text": item.user_message.content,
                    "assistant_text": (
                        item.assistant_message.content if item.assistant_message else None
                    ),
                    "started_at": item.started_at.isoformat(),
                }
                for item in reversed(selected)
            ],
            "truncated": (
                len(selected) < len(history.interactions)
                or len(sessions) < len(all_history.sessions)
            ),
        }

    def new_session(self) -> dict[str, object]:
        self._session_id = self._conversation().start_session.execute().session_id
        return {"session_id": self._session_id}

    def resume_session(self, session_id: str) -> dict[str, object]:
        snapshot = self._conversation().history.execute(session_id=session_id)
        if not snapshot.sessions or snapshot.sessions[0].status is SessionStatus.CLOSED:
            raise DesktopBackendError("session_closed")
        self._session_id = session_id
        return {"session_id": session_id}

    def memories(self) -> dict[str, object]:
        """Read episodes through their owner; never expose a second write path."""
        memories = self._conversation().memories.execute(include_archived=True)
        selected = []
        chars = 0
        for memory in reversed(memories[-MAX_MEMORIES:]):
            if chars + len(memory.summary) > MAX_READ_CHARS:
                break
            chars += len(memory.summary)
            selected.append(memory)
        return {
            "memories": [
                {
                    "memory_id": memory.memory_id,
                    "summary": memory.summary,
                    "occurred_at": memory.occurred_at.isoformat(),
                    "importance": memory.importance,
                    "confidence": memory.confidence,
                    "lifecycle_status": memory.lifecycle_status.value,
                    "source_interaction_id": memory.source_interaction_id,
                }
                for memory in reversed(selected)
            ],
            "truncated": len(selected) < len(memories),
        }

    async def send(self, text: str, request_id: str) -> dict[str, object]:
        """Return only an owner-committed reply; request IDs keep retries idempotent."""
        session_id = self._session_id
        return await self._owned_turn(lambda: self._send(text, request_id, session_id))

    async def new_mobile_session(
        self, *, dispatch_guard: Callable[[], None] | None = None
    ) -> dict[str, object]:
        """Create a canonical session without changing the desktop selection.

        The gateway owns durable request journaling; an unknown create outcome
        must be reconciled there rather than blindly calling this method again.
        """

        async def create() -> dict[str, object]:
            session = self._conversation().start_session.execute()
            return {"session_id": session.session_id}

        return await self._owned_turn(create, dispatch_guard=dispatch_guard)

    async def send_for_session(
        self,
        text: str,
        request_id: str,
        session_id: str,
        *,
        dispatch_guard: Callable[[], None] | None = None,
    ) -> dict[str, object]:
        """Send to an exact existing open session through the same Core owner."""
        self._require_mobile_field(text, limit=8000)
        self._require_mobile_field(request_id, limit=128, identifier=True)
        self._require_mobile_field(session_id, limit=128, identifier=True)

        async def send_bound() -> dict[str, object]:
            snapshot = self._session_snapshot(session_id)
            if snapshot.sessions[0].status is SessionStatus.CLOSED:
                raise DesktopBackendError("session_closed")
            try:
                return await self._send(text, request_id, session_id)
            except InteractionIdempotencyConflict as error:
                raise DesktopBackendError("request_id_conflict") from error

        return await self._owned_turn(send_bound, dispatch_guard=dispatch_guard)

    def request_status(self, session_id: str, request_id: str) -> dict[str, object]:
        """Reconcile a request read-only; absent history never authorizes a retry.

        Read the scoped owner, not the capped desktop history projection. Erased
        or withdrawn interactions may be absent and consequently remain unknown.
        Closed sessions are readable for reconciliation but cannot receive sends.
        """
        self._require_mobile_field(session_id, limit=128, identifier=True)
        self._require_mobile_field(request_id, limit=128, identifier=True)
        snapshot = self._session_snapshot(session_id)
        result: dict[str, object] = {
            "state": "unknown",
            "session_id": session_id,
            "client_request_id": request_id,
            "interaction_id": None,
            "text": None,
        }
        for interaction in snapshot.interactions:
            if interaction.client_request_id != request_id:
                continue
            result["state"] = interaction.status.value
            result["interaction_id"] = interaction.interaction_id
            if (
                interaction.status is InteractionStatus.COMPLETED
                and interaction.assistant_message is not None
            ):
                result["text"] = interaction.assistant_message.content
            break
        return result

    @staticmethod
    def _require_mobile_field(value: str, *, limit: int, identifier: bool = False) -> None:
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > limit
            or (identifier and value != value.strip())
        ):
            raise DesktopBackendError("invalid_request")

    def _session_snapshot(self, session_id: str) -> ConversationHistorySnapshot:
        try:
            snapshot = self._conversation().history.execute(session_id=session_id)
        except ConversationSessionNotFound as error:
            raise DesktopBackendError("session_missing") from error
        if not snapshot.sessions:
            raise DesktopBackendError("session_missing")
        return snapshot

    async def _owned_turn(
        self,
        operation: Callable[[], Awaitable[dict[str, object]]],
        *,
        dispatch_guard: Callable[[], None] | None = None,
    ) -> dict[str, object]:
        if self._closing or self._closed:
            raise DesktopBackendError("runtime_closed")

        async def run() -> dict[str, object]:
            # The owned task retains the lock even if its caller is cancelled.
            # A disconnect is not evidence that inference or commit stopped.
            async with self._turn_lock:
                if self._closing or self._closed:
                    raise DesktopBackendError("runtime_closed")
                if dispatch_guard is not None:
                    dispatch_guard()
                return await operation()

        task = asyncio.create_task(run())
        self._turn_tasks.add(task)
        task.add_done_callback(self._turn_finished)
        return await asyncio.shield(task)

    def _turn_finished(self, task: asyncio.Task[dict[str, object]]) -> None:
        self._turn_tasks.discard(task)
        if not task.cancelled():
            # Observe failures after a caller has disconnected, without logging
            # exception text or changing the result for a remaining awaiter.
            task.exception()

    async def _send(self, text: str, request_id: str, session_id: str | None) -> dict[str, object]:
        services = self._conversation()
        if session_id is None:
            # Concurrent initial desktop turns share the first explicit session.
            # Existing selected IDs were captured before entering the queue.
            session_id = self._session_id
            if session_id is None:
                session_id = services.start_session.execute().session_id
                self._session_id = session_id
        trace_id = str(uuid4())
        with bind_trace_id(trace_id):
            reply = await services.talk.execute(TalkInput(text, trace_id, request_id, session_id))
        if not reply.replayed:
            task = asyncio.create_task(self._post_response(reply.interaction_id, trace_id))
            self._post_tasks.add(task)
            task.add_done_callback(self._post_tasks.discard)
        return {
            "text": reply.text,
            "session_id": reply.session_id,
            "interaction_id": reply.interaction_id,
            "client_request_id": reply.client_request_id,
            "replayed": reply.replayed,
            "provider": reply.provider,
            "model": reply.model,
            "timings": asdict(reply.timings),
            "post_response_pending": len(self._post_tasks),
            "voice_delivery": (
                project_voice_delivery(reply.context_manifest).model_dump(mode="json")
                if not reply.replayed
                else None
            ),
        }

    async def _post_response(self, interaction_id: str, trace_id: str) -> None:
        async with self._post_lock:
            try:
                with bind_trace_id(trace_id):
                    report = await self._conversation(allow_closing=True).post_response.execute(
                        interaction_id, trace_id=trace_id
                    )
                self._post_failures.update(report.failure_phases)
            except Exception:
                self._post_failures.add("worker_failure")

    async def close(self) -> dict[str, object]:
        """Gracefully finish in-flight owners before disposing transports and DB."""
        if self._closed:
            return {"closed": True, "post_response_failures": sorted(self._post_failures)}
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._drain_close())
        return await asyncio.shield(self._close_task)

    async def _drain_close(self) -> dict[str, object]:
        # Pending owned tasks reject at the guard; active owners finish. Every
        # accepted task remains tracked independently of its awaiting caller.
        while self._turn_tasks:
            await asyncio.gather(*tuple(self._turn_tasks), return_exceptions=True)
        while self._post_tasks:
            await asyncio.gather(*tuple(self._post_tasks))
        if self._http_client is not None:
            self._http_client.close()
        if self._openai_runtime is not None:
            await self._openai_runtime.aclose()
        if self._database is not None:
            self._database.dispose()
        self._closed = True
        return {"closed": True, "post_response_failures": sorted(self._post_failures)}

    def _require_initial(self) -> InitialSelfServices:
        if self._initial is None or self._closed:
            raise DesktopBackendError("runtime_not_open")
        return self._initial

    def _conversation(self, *, allow_closing: bool = False) -> ConversationServices:
        if self._closing and not allow_closing:
            raise DesktopBackendError("runtime_closed")
        initial = self._require_initial()
        initial.get_identity.execute()
        if self._services is None:
            assert self._database is not None
            providers = self._providers or self._local_providers()
            self._services = build_conversation_services(
                self._database,
                initial,
                providers.conversation,
                providers.episode,
                self.settings,
                embedding_provider=providers.embedding,
                semantic_provider=providers.semantic,
                appraisal_provider=providers.appraisal,
                relationship_provider=providers.relationship,
                model_provider=providers.models,
                position_provider=providers.positions,
                reflection_provider=providers.reflection,
            )
        return self._services

    def _local_providers(self) -> DesktopProviders:
        settings = self.settings
        if settings.all_openai_enabled:
            raise DesktopBackendError("all_openai_provider_bundle_required")
        client = self._http_client = OllamaHttpClient(settings.conversation_provider_base_url)
        scheduler = OllamaInferenceScheduler(
            background_aging_seconds=settings.ollama_background_aging_seconds,
            background_grace_seconds=settings.ollama_background_grace_seconds,
        )
        common: _LocalPortSettings = {
            "base_url": settings.conversation_provider_base_url,
            "keep_alive": settings.ollama_keep_alive,
            "http_client": client,
            "scheduler": scheduler,
        }
        return DesktopProviders(
            conversation=OllamaConversationAdapter(
                **common,
                model=settings.conversation_model,
                timeout_seconds=settings.conversation_timeout_seconds,
            ),
            episode=OllamaEpisodeFormationAdapter(
                **common,
                model=settings.episode_formation_model,
                timeout_seconds=settings.conversation_timeout_seconds,
                max_output_tokens=settings.episode_formation_max_output_tokens,
            ),
            embedding=OllamaEmbeddingAdapter(
                base_url=settings.embedding_provider_base_url,
                model=settings.embedding_model,
                dimensions=settings.embedding_dimensions,
                input_schema_version=1,
                timeout_seconds=settings.embedding_timeout_seconds,
                http_client=client,
            ),
            semantic=OllamaSemanticFormationAdapter(
                **common,
                model=settings.semantic_formation_model,
                timeout_seconds=settings.conversation_timeout_seconds,
                max_output_tokens=settings.semantic_formation_max_output_tokens,
            ),
            appraisal=OllamaAffectiveAppraisalAdapter(
                **common,
                model=settings.affective_appraisal_model,
                timeout_seconds=settings.affective_appraisal_timeout_seconds,
                max_output_tokens=settings.affective_appraisal_max_output_tokens,
                context_window=settings.affective_appraisal_context_window,
            ),
            relationship=OllamaRelationshipAppraisalAdapter(
                **common,
                model=settings.relationship_appraisal_model,
                timeout_seconds=settings.relationship_appraisal_timeout_seconds,
                max_output_tokens=settings.relationship_appraisal_max_output_tokens,
                context_window=settings.relationship_appraisal_context_window,
            ),
            models=OllamaModelFormationAdapter(
                **common,
                model=settings.model_formation_model,
                timeout_seconds=settings.conversation_timeout_seconds,
                max_output_tokens=settings.model_formation_max_output_tokens,
            ),
            positions=OllamaPositionFormationAdapter(
                **common,
                model=settings.position_formation_model,
                timeout_seconds=settings.conversation_timeout_seconds,
                max_output_tokens=settings.position_formation_max_output_tokens,
            ),
            reflection=OllamaReflectionAdapter(
                **common,
                model=settings.reflection_model,
                timeout_seconds=settings.reflection_timeout_seconds,
                max_output_tokens=settings.reflection_max_output_tokens,
            ),
        )
