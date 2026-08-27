"""Stage 4 InteractionLog owner use cases."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from satori.application.conversation.contracts import (
    RecentConversationContext,
    RecentConversationTurn,
    TalkInput,
)
from satori.application.conversation.errors import (
    ConversationSessionClosed,
    ConversationSessionNotFound,
    InteractionIdempotencyConflict,
)
from satori.application.conversation.ports import ConversationHistoryUnitOfWork
from satori.application.initial_self.use_cases import GetInitialSelfSnapshot
from satori.core.clock import Clock
from satori.core.ids import IdGenerator
from satori.domain.conversation_history import (
    ConversationHistorySnapshot,
    ConversationInteraction,
    ConversationSession,
    HistoricalMessage,
    HistoricalMessageRole,
    InteractionProviderMetadata,
    InteractionStatus,
    SessionKind,
    SessionStatus,
)

SESSION_SCHEMA_VERSION = 1
INTERACTION_SCHEMA_VERSION = 1
MESSAGE_SCHEMA_VERSION = 1
RECENT_CONVERSATION_SCHEMA_VERSION = 1
ConversationHistoryUnitOfWorkFactory = Callable[[], ConversationHistoryUnitOfWork]


def _log_fields(**fields: object) -> dict[str, object]:
    return {"satori_fields": fields}


@dataclass(slots=True)
class StartConversationSession:
    """Explicitly open a stable multi-turn conversation container."""

    get_self: GetInitialSelfSnapshot
    unit_of_work_factory: ConversationHistoryUnitOfWorkFactory
    clock: Clock
    id_generator: IdGenerator
    default_counterparty_id: str = "local-default"
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("satori.history"))

    def execute(self) -> ConversationSession:
        """Create one explicit open session."""

        identity_id = self.get_self.execute().identity.identity_id
        session = ConversationSession(
            session_id=self.id_generator.new(),
            identity_id=identity_id,
            schema_version=SESSION_SCHEMA_VERSION,
            kind=SessionKind.EXPLICIT,
            status=SessionStatus.OPEN,
            started_at=self.clock.now(),
            counterparty_id=self.default_counterparty_id,
        )
        with self.unit_of_work_factory() as unit_of_work:
            if not unit_of_work.conversation_history.add_session(session):
                raise RuntimeError("ID generator returned an existing session_id")
            unit_of_work.commit()
        self.logger.info(
            "session_started",
            extra=_log_fields(session_id=session.session_id, session_kind=session.kind.value),
        )
        return session


@dataclass(slots=True)
class CloseConversationSession:
    """Close an explicit or recovered open session without deleting history."""

    unit_of_work_factory: ConversationHistoryUnitOfWorkFactory
    clock: Clock
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("satori.history"))

    def execute(self, session_id: str) -> ConversationSession:
        """Close idempotently; a missing session is a typed error."""

        with self.unit_of_work_factory() as unit_of_work:
            existing = unit_of_work.conversation_history.get_session(session_id)
            if existing is None:
                raise ConversationSessionNotFound(f"conversation session not found: {session_id}")
            if existing.status is SessionStatus.CLOSED:
                return existing
            closed = unit_of_work.conversation_history.close_session(
                session_id,
                ended_at=self.clock.now(),
            )
            if closed is None:
                raise RuntimeError("conversation session disappeared while closing")
            unit_of_work.commit()
        self.logger.info(
            "session_closed",
            extra=_log_fields(session_id=closed.session_id, session_kind=closed.kind.value),
        )
        return closed


@dataclass(frozen=True, slots=True)
class GetConversationHistory:
    """Return immutable raw-history read models, optionally scoped to one session."""

    unit_of_work_factory: ConversationHistoryUnitOfWorkFactory

    def execute(self, *, session_id: str | None = None) -> ConversationHistorySnapshot:
        """Load history without provider prompts, ORM objects, or memory projections."""

        with self.unit_of_work_factory() as unit_of_work:
            if (
                session_id is not None
                and unit_of_work.conversation_history.get_session(session_id) is None
            ):
                raise ConversationSessionNotFound(f"conversation session not found: {session_id}")
            return unit_of_work.conversation_history.get_history(session_id=session_id)


@dataclass(frozen=True, slots=True)
class GetRecentConversation:
    """Build a bounded recent-session read projection from canonical completed pairs."""

    unit_of_work_factory: ConversationHistoryUnitOfWorkFactory
    max_turns: int
    max_chars: int

    def execute(
        self, *, session_id: str, excluded_interaction_id: str
    ) -> RecentConversationContext:
        """Keep newest whole turns and deterministically drop older/oversize pairs."""

        with self.unit_of_work_factory() as unit_of_work:
            candidates = unit_of_work.conversation_history.list_recent_completed(
                session_id=session_id,
                excluded_interaction_id=excluded_interaction_id,
                limit=self.max_turns,
            )
        selected_reversed: list[RecentConversationTurn] = []
        content_chars = 0
        for interaction in reversed(candidates):
            assistant = interaction.assistant_message
            if assistant is None:
                raise RuntimeError("completed recent interaction is missing assistant message")
            turn_chars = len(interaction.user_message.content) + len(assistant.content)
            if content_chars + turn_chars > self.max_chars:
                break
            selected_reversed.append(
                RecentConversationTurn(
                    interaction_id=interaction.interaction_id,
                    user_message_id=interaction.user_message.message_id,
                    user_content=interaction.user_message.content,
                    assistant_message_id=assistant.message_id,
                    assistant_content=assistant.content,
                )
            )
            content_chars += turn_chars
        turns = tuple(reversed(selected_reversed))
        return RecentConversationContext(
            schema_version=RECENT_CONVERSATION_SCHEMA_VERSION,
            turns=turns,
            content_chars=content_chars,
            excluded_turn_count=len(candidates) - len(turns),
        )


@dataclass(slots=True)
class InteractionLog:
    """Create/finalize retryable interactions through one append-only owner."""

    unit_of_work_factory: ConversationHistoryUnitOfWorkFactory
    clock: Clock
    id_generator: IdGenerator
    default_counterparty_id: str = "local-default"
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("satori.history"))

    def get_session(self, session_id: str) -> ConversationSession | None:
        """Read the canonical session partition without exposing the repository."""

        with self.unit_of_work_factory() as unit_of_work:
            return unit_of_work.conversation_history.get_session(session_id)

    def begin(self, command: TalkInput, *, identity_id: str) -> ConversationInteraction:
        """Idempotently persist an attempted user turn before provider inference."""

        with self.unit_of_work_factory() as unit_of_work:
            repository = unit_of_work.conversation_history
            existing = repository.get_by_client_request_id(command.client_request_id)
            if existing is not None:
                self._validate_replay(existing, command)
                return existing

            now = self.clock.now()
            if command.session_id is None:
                created_implicit_session = True
                session = ConversationSession(
                    session_id=self.id_generator.new(),
                    identity_id=identity_id,
                    schema_version=SESSION_SCHEMA_VERSION,
                    kind=SessionKind.IMPLICIT,
                    status=SessionStatus.OPEN,
                    started_at=now,
                    counterparty_id=self.default_counterparty_id,
                )
                if not repository.add_session(session):
                    raise RuntimeError("ID generator returned an existing session_id")
            else:
                created_implicit_session = False
                stored_session = repository.get_session(command.session_id)
                if stored_session is None:
                    raise ConversationSessionNotFound(
                        f"conversation session not found: {command.session_id}"
                    )
                session = stored_session
                if session.identity_id != identity_id:
                    raise ConversationSessionNotFound(
                        f"conversation session not found: {command.session_id}"
                    )
                if session.status is SessionStatus.CLOSED:
                    raise ConversationSessionClosed(
                        f"conversation session is closed: {command.session_id}"
                    )

            interaction_id = self.id_generator.new()
            interaction = ConversationInteraction(
                interaction_id=interaction_id,
                session_id=session.session_id,
                client_request_id=command.client_request_id,
                trace_id=command.trace_id,
                schema_version=INTERACTION_SCHEMA_VERSION,
                status=InteractionStatus.PENDING,
                started_at=now,
                user_message=HistoricalMessage(
                    message_id=self.id_generator.new(),
                    session_id=session.session_id,
                    interaction_id=interaction_id,
                    schema_version=MESSAGE_SCHEMA_VERSION,
                    role=HistoricalMessageRole.USER,
                    content=command.user_text,
                    created_at=now,
                    sequence=1,
                ),
            )
            if not repository.add_interaction(interaction):
                raise InteractionIdempotencyConflict("client_request_id was concurrently claimed")
            unit_of_work.commit()
        if created_implicit_session:
            self.logger.info(
                "session_started",
                extra=_log_fields(
                    session_id=interaction.session_id,
                    session_kind=SessionKind.IMPLICIT.value,
                ),
            )
        return interaction

    def mark_failed(self, interaction_id: str, *, failure_kind: str) -> None:
        """Persist retryable failure metadata without message content."""

        with self.unit_of_work_factory() as unit_of_work:
            unit_of_work.conversation_history.mark_failed(
                interaction_id,
                failure_kind=failure_kind,
            )
            unit_of_work.commit()

    def get(self, interaction_id: str) -> ConversationInteraction | None:
        """Load one canonical interaction for downstream idempotent processing."""

        with self.unit_of_work_factory() as unit_of_work:
            return unit_of_work.conversation_history.get_interaction(interaction_id)

    def complete(
        self,
        interaction: ConversationInteraction,
        *,
        assistant_text: str,
        provider_metadata: InteractionProviderMetadata,
    ) -> ConversationInteraction:
        """Atomically append the assistant message and mark the pair completed."""

        completed_at = self.clock.now()
        assistant_message = HistoricalMessage(
            message_id=self.id_generator.new(),
            session_id=interaction.session_id,
            interaction_id=interaction.interaction_id,
            schema_version=MESSAGE_SCHEMA_VERSION,
            role=HistoricalMessageRole.ASSISTANT,
            content=assistant_text,
            created_at=completed_at,
            sequence=2,
        )
        with self.unit_of_work_factory() as unit_of_work:
            session = unit_of_work.conversation_history.get_session(interaction.session_id)
            if session is None:
                raise RuntimeError("interaction session is missing during finalize")
            completed = unit_of_work.conversation_history.complete_interaction(
                interaction.interaction_id,
                assistant_message=assistant_message,
                completed_at=completed_at,
                provider_metadata=provider_metadata,
                close_session=session.kind is SessionKind.IMPLICIT,
            )
            unit_of_work.commit()
        if session.kind is SessionKind.IMPLICIT:
            self.logger.info(
                "session_closed",
                extra=_log_fields(
                    session_id=session.session_id,
                    session_kind=session.kind.value,
                ),
            )
        return completed

    @staticmethod
    def _validate_replay(existing: ConversationInteraction, command: TalkInput) -> None:
        if existing.user_message.content != command.user_text:
            raise InteractionIdempotencyConflict(
                "client_request_id was replayed with different user_text"
            )
        if command.session_id is not None and existing.session_id != command.session_id:
            raise InteractionIdempotencyConflict(
                "client_request_id was replayed with a different session_id"
            )
