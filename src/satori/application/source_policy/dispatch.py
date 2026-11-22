"""Coroutine-local input binding; every immediate transport attempt re-admits it."""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from typing import cast

from satori.application.source_policy.admission import admit_source_dispatch
from satori.domain.source_policy import SourcePolicyError

_DispatchCheck = Callable[[], None]
_current_dispatch: ContextVar[_DispatchCheck | None] = ContextVar(
    "satori_source_dispatch_check", default=None
)
_operation_dispatch: ContextVar[_DispatchCheck | None] = ContextVar(
    "satori_operation_dispatch_check", default=None
)


@contextmanager
def operation_dispatch_admission(check: _DispatchCheck) -> Iterator[None]:
    """Compose a live operation grant with nested canonical-source admission."""

    previous = _operation_dispatch.get()

    def combined() -> None:
        if previous is not None:
            previous()
        check()

    token = _operation_dispatch.set(combined)
    try:
        yield
    finally:
        _operation_dispatch.reset(token)


@contextmanager
def source_dispatch_admission(
    unit_of_work_factory: Callable[[], object],
    input_id: str | None,
) -> Iterator[None]:
    """Bind one inference call; a fresh UoW is opened only at actual dispatch."""

    def check() -> None:
        unit_context = cast(AbstractContextManager[object], unit_of_work_factory())
        with unit_context as unit:
            if getattr(unit, "source_admission", None) is not None and input_id is None:
                raise SourcePolicyError("source_input_required")
            admit_source_dispatch(unit, input_id)
        # The transaction and short admission lease are closed before transport I/O.

    token = _current_dispatch.set(check)
    try:
        yield
    finally:
        _current_dispatch.reset(token)


def admit_provider_dispatch() -> None:
    """Single-use immediate admission; queues, retry and fallback call this anew."""

    operation = _operation_dispatch.get()
    if operation is not None:
        operation()
    check = _current_dispatch.get()
    if check is not None:
        check()
