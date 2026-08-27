"""Trace context propagation independent from any HTTP framework."""

from contextvars import ContextVar, Token

_TRACE_ID: ContextVar[str | None] = ContextVar("satori_trace_id", default=None)


def current_trace_id() -> str | None:
    """Return the trace identifier bound to the current context, if any."""

    return _TRACE_ID.get()


def set_trace_id(trace_id: str) -> Token[str | None]:
    """Bind a non-empty trace identifier and return its reset token."""

    normalized = trace_id.strip()
    if not normalized:
        raise ValueError("trace_id must not be blank")
    return _TRACE_ID.set(normalized)


def reset_trace_id(token: Token[str | None]) -> None:
    """Restore the trace context represented by a token."""

    _TRACE_ID.reset(token)
