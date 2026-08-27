"""Priority gate for heavy calls sharing one local inference resource."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from enum import IntEnum


class InferencePriority(IntEnum):
    """Lower values are foreground work closer to a user-visible reply."""

    CONVERSATION = 0
    APPRAISAL = 1
    RELATIONSHIP = 5
    EPISODE = 10
    SEMANTIC = 20


@dataclass(frozen=True, slots=True)
class _Waiter:
    sequence: int
    priority: InferencePriority
    enqueued_at: float
    eligible_at: float


class OllamaInferenceScheduler:
    """Serialize heavy local inference with foreground priority and bounded aging."""

    def __init__(
        self,
        *,
        background_aging_seconds: float = 30.0,
        background_grace_seconds: float = 0.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if background_aging_seconds <= 0 or background_grace_seconds < 0:
            raise ValueError("scheduler timing limits are invalid")
        self._background_aging_seconds = background_aging_seconds
        self._background_grace_seconds = background_grace_seconds
        self._monotonic = monotonic
        self._condition = asyncio.Condition()
        self._waiters: list[_Waiter] = []
        self._sequence = 0
        self._active = False

    @asynccontextmanager
    async def reserve(self, priority: InferencePriority) -> AsyncIterator[None]:
        """Wait for the one inference slot and release it on success or cancellation."""

        waiter = await self._acquire(priority)
        try:
            yield
        finally:
            await self._release(waiter)

    async def _acquire(self, priority: InferencePriority) -> _Waiter:
        if not isinstance(priority, InferencePriority):
            raise ValueError("priority must be an InferencePriority")
        async with self._condition:
            now = self._monotonic()
            grace = (
                self._background_grace_seconds
                if priority >= InferencePriority.RELATIONSHIP
                else 0.0
            )
            waiter = _Waiter(self._sequence, priority, now, now + grace)
            self._sequence += 1
            self._waiters.append(waiter)
            self._condition.notify_all()
            try:
                while self._active or self._selected_waiter() != waiter:
                    delay = self._next_eligibility_delay()
                    if delay is None:
                        await self._condition.wait()
                    else:
                        with suppress(TimeoutError):
                            await asyncio.wait_for(self._condition.wait(), timeout=delay)
            except BaseException:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
                    self._condition.notify_all()
                raise
            self._waiters.remove(waiter)
            self._active = True
            return waiter

    async def _release(self, _waiter: _Waiter) -> None:
        async with self._condition:
            if not self._active:
                raise RuntimeError("inference scheduler released without an active reservation")
            self._active = False
            self._condition.notify_all()

    def _selected_waiter(self) -> _Waiter | None:
        if not self._waiters:
            return None
        now = self._monotonic()
        eligible = [waiter for waiter in self._waiters if waiter.eligible_at <= now]
        if not eligible:
            return None
        aged_background = [
            waiter
            for waiter in eligible
            if waiter.priority >= InferencePriority.RELATIONSHIP
            and now - waiter.enqueued_at >= self._background_aging_seconds
        ]
        if aged_background:
            return min(aged_background, key=lambda waiter: waiter.sequence)
        return min(eligible, key=lambda waiter: (waiter.priority, waiter.sequence))

    def _next_eligibility_delay(self) -> float | None:
        if self._active or not self._waiters:
            return None
        now = self._monotonic()
        future = [waiter.eligible_at - now for waiter in self._waiters if waiter.eligible_at > now]
        return max(0.001, min(future)) if future else None

    @property
    def waiting_count(self) -> int:
        """Expose non-sensitive queue depth for status/debug observability."""

        return len(self._waiters)
