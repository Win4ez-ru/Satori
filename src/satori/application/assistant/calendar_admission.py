"""Coroutine-local trusted host admission for every Calendar transport boundary."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from satori.application.assistant.calendar_planning_contracts import CalendarPlanningError

_host_guard: ContextVar[Callable[[], None] | None] = ContextVar(
    "satori_calendar_host_guard", default=None
)


@contextmanager
def calendar_host_admission(check: Callable[[], None] | None) -> Iterator[None]:
    """Keep a trusted caller's live grant through queued reads, models and effects."""
    previous = _host_guard.get()

    def combined() -> None:
        if previous is not None:
            previous()
        if check is not None:
            check()

    token = _host_guard.set(combined)
    try:
        yield
    finally:
        _host_guard.reset(token)


def require_calendar_host_admission() -> None:
    guard = _host_guard.get()
    if guard is not None:
        try:
            guard()
        except Exception:
            raise CalendarPlanningError("calendar_host_admission_revoked") from None
