"""Expected application-policy errors for conversation lifecycle."""


class ConversationError(Exception):
    """Base error for deterministic conversation orchestration policy."""


class ConversationInputError(ConversationError):
    """The current user turn violates the bounded input contract."""


class ContextBudgetExceeded(ConversationError):
    """The trusted runtime projection cannot fit the configured character budget."""


class ConversationSessionNotFound(ConversationError):
    """The requested explicit conversation session does not exist."""


class ConversationSessionClosed(ConversationError):
    """A new interaction was requested for a closed session."""


class InteractionIdempotencyConflict(ConversationError):
    """A client request ID was replayed with different canonical input."""


class UnsupportedPastClaim(ConversationError):
    """A provider declared a shared-past claim without available prior evidence."""


class AffectiveFinalizeConflict(ConversationError):
    """A concurrent event changed affect after tentative response generation."""
