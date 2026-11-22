"""Bounded cancellable pipe ingress; no thread remains blocked on stdin at exit."""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import TextIO

from satori.desktop.calendar_bridge import MAX_HOST_FRAME_CHARS, CalendarHostResult, CalendarReadRPC


async def pipe_records(source: TextIO) -> AsyncIterator[str]:
    """POSIX readiness or finite in-memory streams used by offline callers."""
    try:
        descriptor = source.fileno()
    except (OSError, ValueError):
        while text_line := source.readline(MAX_HOST_FRAME_CHARS + 1):
            if len(text_line) > MAX_HOST_FRAME_CHARS:
                while text_line and not text_line.endswith("\n"):
                    text_line = source.readline(MAX_HOST_FRAME_CHARS + 1)
                yield ""
            else:
                yield text_line
            await asyncio.sleep(0)
        return
    loop = asyncio.get_running_loop()
    buffer = bytearray()
    discarding = False
    while True:
        ready: asyncio.Future[None] = loop.create_future()

        def wake(waiter: asyncio.Future[None] = ready) -> None:
            if not waiter.done():
                waiter.set_result(None)

        loop.add_reader(descriptor, wake)
        try:
            await ready
        finally:
            loop.remove_reader(descriptor)
        part = os.read(descriptor, 8192)
        if not part:
            if buffer or discarding:
                yield ""
            return
        buffer.extend(part)
        while b"\n" in buffer:
            line, _, tail = buffer.partition(b"\n")
            buffer = bytearray(tail)
            if discarding or len(line) > MAX_HOST_FRAME_CHARS:
                yield ""
            else:
                try:
                    yield line.decode("utf-8") + "\n"
                except UnicodeDecodeError:
                    yield ""
            discarding = False
        if len(buffer) > MAX_HOST_FRAME_CHARS:
            buffer.clear()
            discarding = True


async def route_ingress(
    source: TextIO,
    commands: asyncio.Queue[str | None],
    rpc: CalendarReadRPC,
) -> None:
    try:
        async for line in pipe_records(source):
            try:
                value: object = json.loads(line)
            except ValueError:
                value = None
            if isinstance(value, dict) and value.get("kind") == "calendar_host_result":
                with suppress(ValueError):
                    rpc.accept(CalendarHostResult.model_validate_json(line))
            else:
                await commands.put(line)
    except asyncio.CancelledError:
        raise
    except Exception:
        # A broken input channel retires the host; no private exception is echoed.
        pass
    finally:
        rpc.close()
    await commands.put(None)
