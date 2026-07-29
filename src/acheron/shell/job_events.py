"""Bounded per-job progress event broker for live monitoring."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from acheron.core.schemas import JobLogEvent

_SENTINEL = object()


class JobEventBroker:
    """Publish-subscribe broker for per-job progress events.

    Maintains a bounded ring buffer of recent events per job and fans them
    out to active subscribers.  ``finish()`` sends a sentinel so that follow
    streams terminate cleanly.
    """

    def __init__(self, *, max_events: int = 128) -> None:
        self._max_events = max_events
        self._buffer: dict[str, deque[JobLogEvent]] = {}
        self._subscribers: dict[str, list[asyncio.Queue[object]]] = {}
        self._lock = asyncio.Lock()

    async def publish(self, event: JobLogEvent) -> None:
        """Store the event and fan it out to active subscribers."""
        async with self._lock:
            buf = self._buffer.setdefault(event.job_id, deque(maxlen=self._max_events))
            buf.append(event)
            for q in self._subscribers.get(event.job_id, ()):
                await q.put(event)

    def subscribe(self, job_id: str) -> asyncio.Queue[object]:
        """Return a queue that receives buffered + live events for *job_id*."""
        q: asyncio.Queue[object] = asyncio.Queue()
        buf = self._buffer.get(job_id, deque())
        for event in buf:
            q.put_nowait(event)
        self._subscribers.setdefault(job_id, []).append(q)
        return q

    async def finish(self, job_id: str) -> None:
        """Send a sentinel to all subscribers and remove active queues."""
        async with self._lock:
            for q in self._subscribers.pop(job_id, ()):
                await q.put(_SENTINEL)


async def iter_events(queue: asyncio.Queue[object]) -> AsyncIterator[JobLogEvent]:
    """Yield :class:`JobLogEvent` items from *queue* until the finish sentinel."""
    from acheron.core.schemas import JobLogEvent as _JobLogEvent  # noqa: PLC0415

    while True:
        item = await queue.get()
        if item is _SENTINEL:
            return
        if isinstance(item, _JobLogEvent):
            yield item
