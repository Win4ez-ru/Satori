"""Deterministic priority and cancellation checks for local inference scheduling."""

import asyncio
from contextlib import suppress

from satori.infrastructure.providers.inference_scheduler import (
    InferencePriority,
    OllamaInferenceScheduler,
)


def test_scheduler_serializes_and_prioritizes_foreground() -> None:
    async def scenario() -> tuple[list[str], int]:
        scheduler = OllamaInferenceScheduler(background_aging_seconds=60.0)
        release_holder = asyncio.Event()
        holder_started = asyncio.Event()
        order: list[str] = []
        active = 0
        maximum_active = 0

        async def holder() -> None:
            nonlocal active, maximum_active
            async with scheduler.reserve(InferencePriority.EPISODE):
                active += 1
                maximum_active = max(maximum_active, active)
                holder_started.set()
                await release_holder.wait()
                active -= 1

        async def work(name: str, priority: InferencePriority) -> None:
            nonlocal active, maximum_active
            async with scheduler.reserve(priority):
                active += 1
                maximum_active = max(maximum_active, active)
                order.append(name)
                await asyncio.sleep(0)
                active -= 1

        holder_task = asyncio.create_task(holder())
        await holder_started.wait()
        semantic = asyncio.create_task(work("semantic", InferencePriority.SEMANTIC))
        await asyncio.sleep(0)
        relationship = asyncio.create_task(work("relationship", InferencePriority.RELATIONSHIP))
        await asyncio.sleep(0)
        appraisal = asyncio.create_task(work("appraisal", InferencePriority.APPRAISAL))
        await asyncio.sleep(0)
        conversation = asyncio.create_task(work("conversation", InferencePriority.CONVERSATION))
        await asyncio.sleep(0)
        release_holder.set()
        await asyncio.gather(holder_task, semantic, relationship, appraisal, conversation)
        return order, maximum_active

    order, maximum_active = asyncio.run(scenario())

    assert order == ["conversation", "appraisal", "relationship", "semantic"]
    assert maximum_active == 1


def test_scheduler_ages_background_without_leaking_parallelism() -> None:
    async def scenario() -> list[str]:
        now = [0.0]
        scheduler = OllamaInferenceScheduler(
            background_aging_seconds=10.0,
            monotonic=lambda: now[0],
        )
        release_holder = asyncio.Event()
        holder_started = asyncio.Event()
        order: list[str] = []

        async def holder() -> None:
            async with scheduler.reserve(InferencePriority.CONVERSATION):
                holder_started.set()
                await release_holder.wait()

        async def work(name: str, priority: InferencePriority) -> None:
            async with scheduler.reserve(priority):
                order.append(name)

        holder_task = asyncio.create_task(holder())
        await holder_started.wait()
        background = asyncio.create_task(work("episode", InferencePriority.EPISODE))
        await asyncio.sleep(0)
        foreground = asyncio.create_task(work("conversation", InferencePriority.CONVERSATION))
        await asyncio.sleep(0)
        now[0] = 11.0
        release_holder.set()
        await asyncio.gather(holder_task, background, foreground)
        return order

    assert asyncio.run(scenario()) == ["episode", "conversation"]


def test_cancelled_waiter_does_not_block_next_reservation() -> None:
    async def scenario() -> tuple[int, bool]:
        scheduler = OllamaInferenceScheduler()
        release_holder = asyncio.Event()
        holder_started = asyncio.Event()
        successor_ran = False

        async def holder() -> None:
            async with scheduler.reserve(InferencePriority.CONVERSATION):
                holder_started.set()
                await release_holder.wait()

        async def wait_forever() -> None:
            async with scheduler.reserve(InferencePriority.APPRAISAL):
                raise AssertionError("cancelled waiter must never acquire")

        async def successor() -> None:
            nonlocal successor_ran
            async with scheduler.reserve(InferencePriority.EPISODE):
                successor_ran = True

        holder_task = asyncio.create_task(holder())
        await holder_started.wait()
        cancelled = asyncio.create_task(wait_forever())
        await asyncio.sleep(0)
        cancelled.cancel()
        with suppress(asyncio.CancelledError):
            await cancelled
        release_holder.set()
        await holder_task
        await successor()
        return scheduler.waiting_count, successor_ran

    assert asyncio.run(scenario()) == (0, True)


def test_background_grace_allows_new_foreground_to_take_free_slot() -> None:
    async def scenario() -> list[str]:
        now = [0.0]
        scheduler = OllamaInferenceScheduler(
            background_grace_seconds=1.0,
            monotonic=lambda: now[0],
        )
        order: list[str] = []

        async def background() -> None:
            async with scheduler.reserve(InferencePriority.EPISODE):
                order.append("background")

        async def foreground() -> None:
            async with scheduler.reserve(InferencePriority.CONVERSATION):
                order.append("foreground")
                now[0] = 2.0

        background_task = asyncio.create_task(background())
        await asyncio.sleep(0)
        foreground_task = asyncio.create_task(foreground())
        await asyncio.gather(background_task, foreground_task)
        return order

    assert asyncio.run(scenario()) == ["foreground", "background"]


def test_relationship_grace_allows_immediate_conversation_to_take_free_slot() -> None:
    async def scenario() -> list[str]:
        now = [0.0]
        scheduler = OllamaInferenceScheduler(
            background_grace_seconds=1.0,
            monotonic=lambda: now[0],
        )
        order: list[str] = []

        async def relationship() -> None:
            async with scheduler.reserve(InferencePriority.RELATIONSHIP):
                order.append("relationship")

        async def conversation() -> None:
            async with scheduler.reserve(InferencePriority.CONVERSATION):
                order.append("conversation")
                now[0] = 2.0

        relationship_task = asyncio.create_task(relationship())
        await asyncio.sleep(0)
        conversation_task = asyncio.create_task(conversation())
        await asyncio.gather(relationship_task, conversation_task)
        return order

    assert asyncio.run(scenario()) == ["conversation", "relationship"]
